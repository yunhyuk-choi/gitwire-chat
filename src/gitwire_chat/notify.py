"""OS 알림 — 브라우저 탭이 닫혀 있어도 뜬다.

이 앱의 백엔드 프로세스는 브라우저와 무관하게 계속 방을 폴링한다. 그래서
탭이 닫혀 있어도 새 메시지를 안다. 그때 **OS 알림**을 띄운다.

OS 중립으로 만드는 법
--------------------
알림 API 는 OS 마다 완전히 다르고 파이썬 표준 라이브러리에는 없다. 서드파티
의존을 늘리지 않으면서 3 OS 를 덮기 위해, **"백엔드 후보 목록을 순서대로
시도하고, 되는 첫 번째를 쓴다"** 는 구조로 간다.

    Windows : PowerShell WinRT 토스트 → 실패 시 NotifyIcon 풍선
    macOS   : osascript (display notification)
    Linux   : notify-send
    전부 실패: 로그로만 남긴다 (앱은 계속 돈다)

세 가지를 특히 신경 썼다:

1. **절대 예외를 밖으로 내보내지 않는다.** 알림은 부가 기능이다. 알림이
   실패했다고 채팅이 멈추면 안 된다. 모든 경로가 try/except 로 닫혀 있고
   서브프로세스에는 타임아웃이 걸린다.
2. **인코딩.** 윈도우 콘솔 코드페이지(cp949 등)에서 한국어를 명령줄로 넘기면
   깨진다. 그래서 PowerShell 은 ``-EncodedCommand``(UTF-16LE base64)로 넘긴다 —
   콘솔 코드페이지를 통과하지 않는다. macOS 는 스크립트를 stdin 으로 주고
   본문은 ``argv`` 로 넘겨 인용부호 문제와 주입을 동시에 피한다. Linux 는
   애초에 argv 라 안전하다.
3. **묶어서 띄운다.** 폴 한 번에 메시지 20건이 들어오면 토스트 20개가 아니라
   "새 메시지 20건" 하나다. 짧은 창(기본 1초) 안의 알림을 방 단위로 합친다.
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

#: 알림 합치기 창(초)
COALESCE_WINDOW = 1.0

#: 서브프로세스 타임아웃(초)
TIMEOUT = 20.0

_MAX_BODY = 220


def _run(args: list[str], *, input_text: str | None = None) -> bool:
    """외부 명령 실행. 성공하면 True. **절대 예외를 던지지 않는다.**"""
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "timeout": TIMEOUT,
    }
    if input_text is None:
        kwargs["stdin"] = subprocess.DEVNULL
    else:
        kwargs["input"] = input_text.encode("utf-8")
    if os.name == "nt":
        # 콘솔 창이 깜빡이지 않게 한다.
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(args, **kwargs).returncode == 0
    except BaseException as exc:  # noqa: BLE001
        log.debug("알림 백엔드 실행 실패: %s (%s)", args[0] if args else "?", exc)
        return False


def _ps_quote(text: str) -> str:
    """PowerShell 작은따옴표 문자열용 이스케이프 (작은따옴표를 두 번)."""
    return text.replace("'", "''")


def _ps_encoded(script: str) -> list[str]:
    """UTF-16LE base64 로 감싼 PowerShell 호출.

    콘솔 코드페이지를 통과하지 않으므로 한국어가 깨지지 않는다.
    """
    blob = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        blob,
    ]


# --------------------------------------------------------------- 백엔드들


def windows_toast(title: str, body: str, app: str) -> bool:
    """Win10+ WinRT 토스트 (알림 센터에 남는다)."""
    if os.name != "nt" or not shutil.which("powershell"):
        return False
    script = f"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] > $null
$tpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
    [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $tpl.GetElementsByTagName('text')
$texts.Item(0).AppendChild($tpl.CreateTextNode('{_ps_quote(title)}')) > $null
$texts.Item(1).AppendChild($tpl.CreateTextNode('{_ps_quote(body)}')) > $null
$toast = [Windows.UI.Notifications.ToastNotification]::new($tpl)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier(
    '{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($toast)
"""
    return _run(_ps_encoded(script))


def windows_balloon(title: str, body: str, app: str) -> bool:
    """WinRT 가 막힌 환경용 폴백 — 트레이 풍선 알림."""
    if os.name != "nt" or not shutil.which("powershell"):
        return False
    script = f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.BalloonTipTitle = '{_ps_quote(title)}'
$n.BalloonTipText = '{_ps_quote(body)}'
$n.Visible = $true
$n.ShowBalloonTip(8000)
Start-Sleep -Seconds 6
$n.Dispose()
"""
    return _run(_ps_encoded(script))


_OSASCRIPT = """on run argv
    display notification (item 2 of argv) with title (item 1 of argv)
end run
"""


def macos_notification(title: str, body: str, app: str) -> bool:
    """osascript. 본문은 argv 로 넘겨 인용부호·주입 문제를 피한다."""
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return False
    return _run(["osascript", "-", title, body], input_text=_OSASCRIPT)


def linux_notify_send(title: str, body: str, app: str) -> bool:
    if not shutil.which("notify-send"):
        return False
    return _run(["notify-send", "--app-name", app, "--", title, body])


def log_only(title: str, body: str, app: str) -> bool:
    """최종 폴백 — 뜨지 않아도 흔적은 남긴다."""
    log.info("[알림] %s — %s", title, body)
    return True


def default_backends() -> list:
    """이 OS 에 맞는 백엔드 후보를 우선순위대로."""
    if os.name == "nt":
        chain = [windows_toast, windows_balloon]
    elif sys.platform == "darwin":
        chain = [macos_notification]
    else:
        chain = [linux_notify_send]
    return [*chain, log_only]


# ----------------------------------------------------------------- 파사드


@dataclass
class _Pending:
    room: str
    count: int = 0
    last_author: str = ""
    last_text: str = ""
    timer: threading.Timer | None = field(default=None, repr=False)


class Notifier:
    """OS 알림 파사드. 스레드 안전하고, 실패해도 조용하다."""

    def __init__(
        self,
        *,
        app_name: str = "gitwire-chat",
        backends: list | None = None,
        enabled: bool = True,
        coalesce_window: float = COALESCE_WINDOW,
    ) -> None:
        self.app_name = app_name
        self.backends = backends if backends is not None else default_backends()
        self.enabled = enabled
        self.coalesce_window = coalesce_window
        self._pending: dict[str, _Pending] = {}
        self._lock = threading.Lock()
        self._working: object | None = None
        """되는 것으로 확인된 백엔드. 한 번 찾으면 계속 그걸 쓴다."""

    # 즉시 1건 — 합치기 없이 바로 띄운다 (테스트·시스템 알림용)
    def send(self, title: str, body: str) -> bool:
        if not self.enabled:
            return False
        body = body[:_MAX_BODY]
        order = [self._working, *self.backends] if self._working else self.backends
        for backend in order:
            if backend is None:
                continue
            try:
                if backend(title, body, self.app_name):
                    self._working = backend
                    return True
            except BaseException:  # noqa: BLE001
                log.debug("알림 백엔드 예외", exc_info=True)
        return False

    def notify_message(self, room_name: str, author: str, text: str) -> None:
        """새 메시지 1건. 짧은 창 안의 같은 방 알림을 합쳐서 한 번만 띄운다."""
        if not self.enabled:
            return
        if self.coalesce_window <= 0:
            self._emit(_Pending(room_name, 1, author, text))
            return
        with self._lock:
            pending = self._pending.get(room_name)
            if pending is None:
                pending = _Pending(room_name)
                self._pending[room_name] = pending
            pending.count += 1
            pending.last_author = author
            pending.last_text = text
            if pending.timer is None:
                timer = threading.Timer(self.coalesce_window, self._flush, (room_name,))
                timer.daemon = True
                pending.timer = timer
                timer.start()

    def _flush(self, room_name: str) -> None:
        with self._lock:
            pending = self._pending.pop(room_name, None)
        if pending and pending.count:
            self._emit(pending)

    def _emit(self, pending: _Pending) -> None:
        title = pending.room or self.app_name
        if pending.count > 1:
            body = (
                f"{pending.last_author}: {pending.last_text}"
                f"  (외 {pending.count - 1}건)"
            )
        else:
            body = f"{pending.last_author}: {pending.last_text}"
        try:
            self.send(title, body)
        except BaseException:  # noqa: BLE001
            log.debug("알림 전송 실패", exc_info=True)

    def close(self) -> None:
        with self._lock:
            pendings = list(self._pending.values())
            self._pending.clear()
        for pending in pendings:
            if pending.timer is not None:
                pending.timer.cancel()

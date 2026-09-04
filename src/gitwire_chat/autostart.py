"""로그인 시 자동 시작 — 등록 · 해제 · 상태 확인.

이 앱의 알림은 브라우저와 무관한 백엔드 프로세스가 띄운다. 그래서 그 프로세스가
**로그인할 때 저절로 떠 있어야** 알림이 제 값을 한다. v1 은 README 에 OS 별
수동 절차만 적어 뒀는데, 그건 사람이 매번 따라 해야 하는 절차였다. 여기서
명령 한 줄로 바꾼다::

    python -m gitwire_chat autostart install
    python -m gitwire_chat autostart status
    python -m gitwire_chat autostart uninstall

OS 별로 무엇을 쓰나 (그리고 왜)
------------------------------
셸 전용 명령에 기대지 않는다. **파일 한 개를 쓰는 것**이 세 OS 를 가장 얇게
덮고, 사용자가 눈으로 확인할 수 있으며, 해제가 "그 파일을 지우는 것"으로
끝난다. OS 서비스 관리자에게 알려야 하는 곳(macOS·Linux)에서만 그 다음 한
단계를 더 밟는다.

============ ============================================ =====================
OS           수단                                          해제
============ ============================================ =====================
Windows      시작 폴더(``shell:startup``)의 ``.cmd``        파일 삭제
macOS        ``~/Library/LaunchAgents`` 의 ``.plist``       ``bootout`` + 삭제
             + ``launchctl bootstrap``
Linux        ``~/.config/systemd/user`` 유닛                ``disable`` + 삭제
             + ``systemctl --user enable --now``
============ ============================================ =====================

**Windows 에서 작업 스케줄러를 쓰지 않는 이유.** 스케줄러는 "로그온 시 실행"
트리거를 제공하므로 후보이긴 하다. 하지만 (1) 등록 수단이 ``schtasks`` 또는
XML + COM 이라 *셸/외부 도구 의존*이 생긴다 — 이 모듈이 피하려는 바로 그것이다.
(2) 그 XML 은 UTF-16 을 요구해 인코딩 함정을 하나 더 만든다. (3) 사용자가
자기 눈으로 확인·삭제하기 어렵다(시작 폴더는 탐색기에서 바로 보인다).
(4) 스케줄러가 주는 추가 능력(지연 시작·재시도·조건)은 "로그인하면 뜬다"는
목적에 필요하지 않다. 그래서 **시작 폴더**를 쓴다. 스케줄러가 필요한 사람을
위해 README 에 수동 절차를 남겨 둔다.

**systemd 가 없는 Linux** (일부 컨테이너·runit·OpenRC 등)에서는 조용히 다른
수단으로 넘어가지 않는다. **명확히 실패**하고 수동 방법을 안내한다 — 등록됐다고
믿게 만드는 것이 가장 나쁘다.

지키는 것들
-----------
* **멱등.** 두 번 등록해도 파일은 하나다. 내용이 같으면 "이미 등록돼 있다"고
  말하고 아무것도 쓰지 않는다. 해제도 마찬가지로 없으면 없다고 말하고 끝난다.
* **먼저 보여준다.** ``--dry-run`` 이 *쓸 파일 경로와 그 내용 전문*을 출력한다.
  실제 등록에서도 같은 것을 출력한 뒤에 쓴다 — 사용자 환경을 바꾸는 동작이
  조용히 일어나지 않는다.
* **지금 이 인터프리터를 박는다.** venv 로 설치한 경우가 흔하다. 로그인 셸의
  ``PATH`` 를 믿지 않고 ``sys.executable`` 의 **절대 경로**를 그대로 쓴다.
  포트·``--home`` 같은 옵션도 등록 시점 값으로 파일에 들어간다.
* **콘솔 창을 띄우지 않는다.** Windows 에서는 같은 디렉토리의 ``pythonw.exe``
  를 쓴다(있는지 실제로 확인하고, 없으면 ``python.exe`` + 최소화로 폴백하며
  그 사실을 보고한다).
* **로그가 남는다.** 백그라운드로 도는 프로세스는 실패해도 화면에 아무것도
  남기지 않는다. 세 OS 모두 표준출력·표준오류를 로그 파일 하나로 모으고
  (``.cmd`` 리다이렉션 / ``StandardOutPath`` / ``StandardOutput=append:``),
  ``status`` 가 그 경로를 알려준다.
* **토큰을 흘리지 않는다.** 등록 파일에 들어가는 것은 이 모듈이 아는 CLI
  옵션뿐이다. 토큰은 이 앱이 원래 **값을 갖지 않고 환경변수 이름만** 다루므로
  (``config.Room.token_env``) 흘릴 값 자체가 없다.

인코딩
------
파일마다 **읽는 쪽**이 다르므로 인코딩도 다르게 쓴다.

* ``.cmd`` — ``cmd.exe`` 는 배치 파일을 *콘솔 OEM 코드페이지*(한국어 윈도우면
  cp949)로 읽는다. 그래서 기본은 **ASCII 로만** 쓴다. 경로에 비 ASCII 가
  섞이면(사용자 이름이 한글인 경우 등) 첫 줄에 ``chcp 65001`` 을 넣고 UTF-8
  (BOM 없음)로 쓴다 — 첫 줄이 ASCII 라 바이트 오프셋이 어긋나지 않는다.
  줄 끝은 **CRLF** 다(배치 파일의 관례이자 안전한 쪽).
* ``.plist`` / systemd 유닛 — 둘 다 **UTF-8 · LF** 가 정본이다. plist 는 XML
  이므로 문자열을 XML 이스케이프한다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from xml.sax.saxutils import escape as _xml_escape

from .config import DEFAULT_PORT, os_data_dir

#: 등록 파일 안에 넣는 표식. "이 파일은 우리가 만든 것인가"를 이걸로 판정한다.
MARKER = "gitwire-chat-autostart"

#: 등록 파일 포맷 버전. 내용 형식을 바꾸면 올린다.
FORMAT_VERSION = 1

MARKER_LINE = f"{MARKER} v{FORMAT_VERSION}"

#: launchd 레이블 (역 DNS) — 파일 이름이자 ``launchctl`` 이 부르는 이름.
LAUNCHD_LABEL = "com.github.yunhyuk-choi.gitwire-chat"

#: systemd 유저 유닛 이름.
SYSTEMD_UNIT = "gitwire-chat.service"

#: Windows 시작 폴더에 놓을 파일 이름.
WINDOWS_FILE = "gitwire-chat.cmd"

#: 로그 파일 이름.
LOG_NAME = "autostart.log"

#: 등록 디렉토리를 갈아끼우는 환경변수 — 테스트가 **실제 시작 폴더를 건드리지
#: 않게** 하는 주입점이다. ``--dir`` 옵션과 같은 값을 가리킨다.
ENV_DIR = "GITWIRE_CHAT_AUTOSTART_DIR"

#: 지원하는 대상 OS 키.
PLATFORMS = ("windows", "macos", "linux")


def host_platform() -> str:
    """지금 돌고 있는 OS 의 키."""
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


# --------------------------------------------------------------- 실행 명세


@dataclass(frozen=True)
class ServeOptions:
    """등록 시점에 고정할 서버 옵션.

    로그인할 때 뜨는 프로세스는 사람이 인자를 다시 넣어 줄 수 없다. 그래서
    등록 시점의 값이 그대로 파일에 박힌다.
    """

    port: int = DEFAULT_PORT
    home: str | None = None
    author: str | None = None
    poll_interval: float | None = None
    notifications: bool = True

    def to_args(self) -> list[str]:
        args = ["--port", str(self.port)]
        if self.home:
            args += ["--home", str(self.home)]
        if self.author:
            args += ["--author", self.author]
        if self.poll_interval is not None:
            args += ["--poll-interval", _num(self.poll_interval)]
        if not self.notifications:
            args.append("--no-notify")
        return args


def _num(value: float) -> str:
    """폴 주기 같은 수를 사람이 읽을 형태로 (``15.0`` → ``15``)."""
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


@dataclass(frozen=True)
class LaunchSpec:
    """"무엇을 어떤 인자로 띄울 것인가" 한 벌."""

    platform: str
    executable: str
    """인터프리터 **절대 경로**. 로그인 셸의 PATH 를 믿지 않는다."""

    app_args: tuple[str, ...]
    log_path: str
    console_free: bool = True
    """Windows 에서 콘솔 없는 인터프리터(``pythonw.exe``)를 찾았나."""

    preview: bool = False
    """지금 OS 가 아닌 대상을 미리보기로 렌더한 것인가."""

    @property
    def command(self) -> list[str]:
        return [self.executable, *self.app_args]

    @property
    def display(self) -> str:
        return " ".join(_quote_display(part) for part in self.command)


def _quote_display(part: str) -> str:
    return f'"{part}"' if " " in part else part


def _windows_python(explicit: str | None) -> tuple[str, bool]:
    """Windows 용 인터프리터 — 콘솔이 뜨지 않는 ``pythonw.exe`` 를 우선한다.

    로그인마다 검은 콘솔 창이 뜨면 쓸 수 없는 기능이 된다. 다만 *있다고 가정하지
    않는다* — 임베디드 배포판이나 일부 스토어 파이썬에는 없다. 실제로 파일을
    확인하고, 없으면 ``python.exe`` 로 폴백하되 그 사실을 보고한다.
    """
    base = Path(explicit or sys.executable)
    if base.name.lower() == "pythonw.exe":
        return str(base), True
    candidate = base.with_name("pythonw.exe")
    if candidate.is_file():
        return str(candidate), True
    return str(base), False


def _preview_home(platform: str) -> str:
    """다른 OS 미리보기에서 쓸 홈 디렉토리 — 그럴듯한 실제 형태로."""
    user = os.environ.get("USER") or os.environ.get("USERNAME") or "user"
    if platform == "macos":
        return f"/Users/{user}"
    if platform == "linux":
        return f"/home/{user}"
    return rf"C:\Users\{user}"


def default_log_path(platform: str, *, preview: bool = False) -> str:
    """OS 관례에 맞는 로그 파일 경로.

    Windows 는 데이터 디렉토리 아래, macOS 는 ``~/Library/Logs``, Linux 는
    XDG state 디렉토리를 쓴다.
    """
    if preview:
        home = _preview_home(platform)
        if platform == "windows":
            return str(PureWindowsPath(home) / "AppData" / "Local" / "gitwire-chat" / "logs" / LOG_NAME)
        if platform == "macos":
            return str(PurePosixPath(home) / "Library" / "Logs" / "gitwire-chat" / LOG_NAME)
        return str(PurePosixPath(home) / ".local" / "state" / "gitwire-chat" / "logs" / LOG_NAME)
    if platform == "macos":
        return str(Path.home() / "Library" / "Logs" / "gitwire-chat" / LOG_NAME)
    if platform == "linux":
        base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
        return str(Path(base) / "gitwire-chat" / "logs" / LOG_NAME)
    return str(os_data_dir("windows") / "logs" / LOG_NAME)


def build_spec(
    platform: str,
    options: ServeOptions | None = None,
    *,
    python: str | None = None,
    log_path: str | None = None,
) -> LaunchSpec:
    """대상 OS 하나에 대한 실행 명세를 만든다."""
    if platform not in PLATFORMS:
        raise ValueError(f"알 수 없는 대상 OS: {platform}")
    options = options or ServeOptions()
    preview = platform != host_platform()
    console_free = True
    if python:
        executable = python
        if platform == "windows":
            executable, console_free = _windows_python(python)
    elif preview:
        # 다른 OS 미리보기 — 그 머신의 인터프리터를 알 수 없으므로 관례적인
        # 자리를 채운다. 실제 등록은 그 머신에서 하며, 그때 sys.executable 이 박힌다.
        if platform == "windows":
            executable = rf"{_preview_home('windows')}\AppData\Local\Programs\Python\Python312\pythonw.exe"
        else:
            executable = "/usr/bin/python3"
    elif platform == "windows":
        executable, console_free = _windows_python(None)
    else:
        executable = sys.executable
    return LaunchSpec(
        platform=platform,
        executable=executable,
        app_args=("-m", "gitwire_chat", *options.to_args()),
        log_path=log_path or default_log_path(platform, preview=preview),
        console_free=console_free,
        preview=preview,
    )


# ------------------------------------------------------------------ 렌더링


def _is_ascii(text: str) -> bool:
    return all(ord(ch) < 128 for ch in text)


def render_windows(spec: LaunchSpec) -> str:
    """시작 폴더에 놓을 ``.cmd``.

    ``start "" /b`` 로 즉시 반환시켜 콘솔 창이 남지 않게 한다(``pythonw.exe``
    는 애초에 콘솔을 만들지 않는다). 표준출력·표준오류는 로그 파일로 이어붙인다
    — ``PYTHONUNBUFFERED`` 를 켜야 그 로그가 제때 보인다.
    """
    command = " ".join(_quote_display(part) for part in spec.command)
    if not spec.console_free:
        # pythonw.exe 를 못 찾았을 때의 폴백 — 최소한 창을 최소화하고 띄운다.
        launch = f'start "gitwire-chat" /min "{spec.executable}" ' + " ".join(
            _quote_display(part) for part in spec.app_args
        )
    else:
        launch = f"start \"\" /b {command}"
    lines = [
        "@echo off",
        f"REM {MARKER_LINE}",
        'REM Generated by: python -m gitwire_chat autostart install',
        'REM To remove: python -m gitwire_chat autostart uninstall',
        'set "PYTHONUNBUFFERED=1"',
        f'{launch} >> "{spec.log_path}" 2>&1',
    ]
    body = "\r\n".join(lines) + "\r\n"
    if not _is_ascii(body):
        # 경로에 비 ASCII 가 있다 — cmd.exe 가 UTF-8 로 읽도록 첫 줄에서 코드페이지를
        # 바꾼다. 첫 줄이 ASCII 라 이 시점까지의 바이트 오프셋은 어긋나지 않는다.
        body = "@echo off\r\nchcp 65001 > nul\r\n" + "\r\n".join(lines[1:]) + "\r\n"
    return body


def render_macos(spec: LaunchSpec) -> str:
    """``~/Library/LaunchAgents`` 에 놓을 launchd ``.plist``."""
    args = "\n".join(
        f"        <string>{_xml_escape(part)}</string>" for part in spec.command
    )
    log = _xml_escape(spec.log_path)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- {MARKER_LINE} -->
<!-- python -m gitwire_chat autostart install 이 만들었다. 해제: ... autostart uninstall -->
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{args}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>ProcessType</key>
    <string>Background</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
    </dict>
    <key>StandardOutPath</key>
    <string>{log}</string>
    <key>StandardErrorPath</key>
    <string>{log}</string>
</dict>
</plist>
"""


def _systemd_quote(part: str) -> str:
    if part and _is_ascii(part) and " " not in part and '"' not in part:
        return part
    return '"' + part.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_linux(spec: LaunchSpec) -> str:
    """``~/.config/systemd/user`` 에 놓을 유저 유닛."""
    exec_start = " ".join(_systemd_quote(part) for part in spec.command)
    return f"""# {MARKER_LINE}
# python -m gitwire_chat autostart install 이 만들었다.
# 해제: python -m gitwire_chat autostart uninstall
[Unit]
Description=gitwire-chat — 로컬-퍼스트 git 채팅 (로그인 시 자동 시작)
Documentation=https://github.com/yunhyuk-choi/gitwire-chat

[Service]
Type=simple
ExecStart={exec_start}
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=5
# 로그는 journald 에도 남지만(journalctl --user -u {SYSTEMD_UNIT}),
# status 가 경로 하나를 알려줄 수 있도록 파일로도 모은다.
StandardOutput=append:{spec.log_path}
StandardError=append:{spec.log_path}

[Install]
WantedBy=default.target
"""


# ------------------------------------------------------------------ 백엔드


@dataclass
class Report:
    """CLI 가 그대로 출력할 결과 한 벌."""

    ok: bool = True
    lines: list[str] = field(default_factory=list)
    changed: bool = False
    installed: bool = False
    path: str = ""

    def say(self, text: str) -> None:
        self.lines.append(text)


class Backend:
    """OS 하나에 대한 등록 수단."""

    platform = ""
    filename = ""
    encoding = "utf-8"
    newline = "\n"
    label = ""

    def __init__(self, spec: LaunchSpec, directory: str | os.PathLike | None = None) -> None:
        self.spec = spec
        self._directory = str(directory) if directory else ""

    # -- 경로 ---------------------------------------------------------

    def default_dir(self) -> str:
        raise NotImplementedError

    @property
    def directory(self) -> str:
        return self._directory or self.default_dir()

    @property
    def custom_dir(self) -> bool:
        """표준 위치가 아닌 곳을 쓰고 있나 (테스트·격리)."""
        return bool(self._directory)

    @property
    def path(self) -> str:
        joiner = PureWindowsPath if self.spec.platform == "windows" else PurePosixPath
        if self.spec.preview:
            return str(joiner(self.directory) / self.filename)
        return str(Path(self.directory) / self.filename)

    # -- 내용 ---------------------------------------------------------

    def render(self) -> str:
        raise NotImplementedError

    def read(self) -> str | None:
        """등록 파일을 **줄 끝까지 그대로** 읽는다.

        ``newline=""`` 이 없으면 파이썬이 CRLF 를 LF 로 바꿔 읽는다(유니버설
        개행). 그러면 CRLF 로 쓴 ``.cmd`` 가 *방금 쓴 내용과도* "다르다"고
        나오고, 멱등 판정이 통째로 무너진다 — 실제로 밟은 함정이다.
        """
        path = Path(self.path)
        if not path.is_file():
            return None
        with open(path, encoding=self.encoding, errors="replace", newline="") as fh:
            return fh.read()

    def is_ours(self, text: str | None) -> bool:
        return bool(text) and MARKER in text

    # -- 동작 ---------------------------------------------------------

    def activate(self, report: Report) -> None:
        """파일을 쓴 뒤 OS 서비스 관리자에게 알린다 (필요한 OS 만)."""

    def deactivate(self, report: Report) -> None:
        """파일을 지우기 전에 OS 서비스 관리자에서 뺀다 (필요한 OS 만)."""

    def runtime_state(self) -> str:
        """지금 서비스 관리자에 올라와 있나 — 사람이 읽을 한 줄."""
        return ""

    def preflight(self, report: Report) -> bool:
        """등록 전에 이 OS 에서 등록이 가능한지 본다. 불가면 명확히 실패한다."""
        return True

    # -- 공통 흐름 ------------------------------------------------------

    def describe(self, report: Report) -> None:
        report.say(f"대상 OS  : {self.platform}")
        report.say(f"수단     : {self.label}")
        report.say(f"등록 파일: {self.path}")
        report.say(f"실행     : {self.spec.display}")
        report.say(f"로그     : {self.spec.log_path}")
        enc = "UTF-8(BOM 없음)" if self.encoding == "utf-8" else self.encoding
        eol = "CRLF" if self.newline == "\r\n" else "LF"
        report.say(f"인코딩   : {enc} · {eol}")
        if not self.spec.console_free:
            report.say(
                "⚠ pythonw.exe 를 찾지 못했다 — python.exe 로 폴백한다."
                " 로그인할 때 창이 최소화된 채로 뜬다."
                " (--python 으로 pythonw.exe 경로를 직접 줄 수 있다)"
            )
        if self.custom_dir:
            report.say("※ 표준 위치가 아닌 디렉토리를 쓰고 있다 (테스트·격리 모드).")
        if self.spec.preview:
            report.say(
                "※ 지금 OS 가 아닌 대상의 **미리보기**다 —"
                " 인터프리터·홈 경로는 그 머신에서 실제로 등록할 때 그 머신 값으로 채워진다."
            )

    def install(self, *, dry_run: bool = False, force: bool = False) -> Report:
        report = Report(path=self.path)
        self.describe(report)
        body = self.render()
        report.say("")
        report.say(f"--- {self.filename} 내용 ---")
        report.lines.extend(body.replace("\r\n", "\n").rstrip("\n").split("\n"))
        report.say("-" * (len(self.filename) + 12))
        report.say("")

        if dry_run:
            report.say("[dry-run] 아무것도 쓰지 않았다.")
            return report
        if self.spec.preview:
            report.ok = False
            report.say(
                "실패: 다른 OS 를 대상으로는 실제 등록을 할 수 없다."
                " 미리보기는 --dry-run 으로만 된다."
            )
            return report
        if not self.preflight(report):
            report.ok = False
            return report

        existing = self.read()
        if existing is not None:
            if existing == body:
                report.installed = True
                report.say(f"이미 같은 내용으로 등록돼 있다 — 아무것도 바꾸지 않았다: {self.path}")
                self.activate(report)
                return report
            if not self.is_ours(existing) and not force:
                report.ok = False
                report.say(
                    f"실패: 같은 이름의 파일이 이미 있는데 이 앱이 만든 것이 아니다: {self.path}"
                )
                report.say("덮어쓰려면 --force 를 준다.")
                return report
            report.say("기존 등록을 발견했다 — 지금 값으로 갱신한다.")

        self._write(body)
        report.changed = True
        report.installed = True
        report.say(f"등록했다: {self.path}")
        self.activate(report)
        report.say("이제 로그인하면 자동으로 뜬다. 해제는 `autostart uninstall`.")
        return report

    def uninstall(self, *, dry_run: bool = False) -> Report:
        report = Report(path=self.path)
        report.say(f"등록 파일: {self.path}")
        existing = self.read()
        if existing is None:
            report.say("등록돼 있지 않다 — 지울 것이 없다.")
            return report
        if not self.is_ours(existing):
            report.say("⚠ 이 앱이 만든 파일이 아닌 것 같다(표식 없음). 그래도 지운다.")
        if dry_run:
            report.installed = True
            report.say("[dry-run] 지우지 않았다.")
            return report
        self.deactivate(report)
        try:
            Path(self.path).unlink()
        except OSError as exc:
            report.ok = False
            report.say(f"실패: 파일을 지우지 못했다 — {exc}")
            return report
        report.changed = True
        report.say("해제했다.")
        return report

    def status(self) -> Report:
        report = Report(path=self.path)
        existing = self.read() if not self.spec.preview else None
        report.installed = existing is not None
        report.say(f"대상 OS  : {self.platform}")
        report.say(f"수단     : {self.label}")
        report.say(f"등록 여부: {'등록됨' if report.installed else '등록되지 않음'}")
        report.say(f"등록 파일: {self.path}")
        report.say(f"로그     : {self.spec.log_path}")
        if existing is not None:
            report.say(f"이 앱이 만든 파일: {'예' if self.is_ours(existing) else '아니오(표식 없음)'}")
            current = self.render()
            if existing != current:
                report.say(
                    "※ 등록된 내용이 지금 옵션과 다르다 —"
                    " 지금 값으로 맞추려면 `autostart install` 을 다시 실행한다."
                )
            report.say("")
            report.say(f"--- 등록된 {self.filename} ---")
            report.lines.extend(existing.replace("\r\n", "\n").rstrip("\n").split("\n"))
            report.say("-" * (len(self.filename) + 12))
            state = self.runtime_state()
            if state:
                report.say(state)
            log = Path(self.spec.log_path)
            if log.is_file():
                report.say(f"로그 크기: {log.stat().st_size} 바이트")
            else:
                report.say("로그 파일은 아직 없다 (한 번도 뜨지 않았거나 출력이 없었다).")
        else:
            report.say("등록하려면: python -m gitwire_chat autostart install")
        return report

    # -- 쓰기 ---------------------------------------------------------

    def _write(self, body: str) -> None:
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        Path(self.spec.log_path).parent.mkdir(parents=True, exist_ok=True)
        # newline="" — 줄 끝을 우리가 정한 그대로 쓴다(플랫폼 변환 금지).
        with open(path, "w", encoding=self.encoding, newline="") as fh:
            fh.write(body)


def _run(args: list[str]) -> tuple[bool, str]:
    """외부 명령 한 번. 예외를 밖으로 내보내지 않는다."""
    try:
        proc = subprocess.run(
            args, capture_output=True, timeout=30, text=True, encoding="utf-8", errors="replace"
        )
    except BaseException as exc:  # noqa: BLE001
        return False, f"{args[0]} 실행 실패: {exc}"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, out.strip()


class WindowsBackend(Backend):
    platform = "windows"
    filename = WINDOWS_FILE
    newline = "\r\n"
    label = "시작 폴더(shell:startup)의 .cmd"

    def default_dir(self) -> str:
        if self.spec.preview:
            return str(
                PureWindowsPath(_preview_home("windows"))
                / "AppData" / "Roaming" / "Microsoft" / "Windows"
                / "Start Menu" / "Programs" / "Startup"
            )
        return _windows_startup_dir()

    def render(self) -> str:
        return render_windows(self.spec)

    def deactivate(self, report: Report) -> None:
        # 시작 폴더에는 감독자가 없다 — 파일을 지우면 "다음 로그인부터" 안 뜬다.
        # 지금 떠 있는 프로세스까지 죽이지는 않는다. 그 사실을 숨기지 않는다.
        report.say(
            "※ 지금 떠 있는 프로세스는 그대로 돈다 — 다음 로그인부터 뜨지 않는다."
            " 지금 끄려면 그 창을 닫거나 작업 관리자에서 pythonw.exe 를 끝낸다."
        )


def _windows_startup_dir() -> str:
    """실제 시작 폴더.

    ``%APPDATA%`` 로 조립해도 대개 맞지만, 폴더가 리디렉트된 환경이 있으므로
    먼저 셸에 **정식으로 물어본다**(``SHGetKnownFolderPath``). 실패하면 조립으로
    폴백한다 — 셸 명령을 띄우지 않고 ctypes 로만 한다.
    """
    try:  # pragma: no cover — 윈도우 전용 경로
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        # FOLDERID_Startup {B97D20BB-F46A-4C97-BA10-5E3608430854}
        folder_id = GUID(
            0xB97D20BB, 0xF46A, 0x4C97,
            (ctypes.c_ubyte * 8)(0xBA, 0x10, 0x5E, 0x36, 0x08, 0x43, 0x08, 0x54),
        )
        out = ctypes.c_wchar_p()
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, None, ctypes.byref(out)
        )
        if hr == 0 and out.value:
            value = out.value
            ctypes.windll.ole32.CoTaskMemFree(out)
            return value
    except Exception:  # noqa: BLE001 — 조립으로 폴백
        pass
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return str(
        Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    )


class MacosBackend(Backend):
    platform = "macos"
    filename = f"{LAUNCHD_LABEL}.plist"
    label = "launchd LaunchAgent (~/Library/LaunchAgents)"

    def default_dir(self) -> str:
        home = _preview_home("macos") if self.spec.preview else str(Path.home())
        return str(PurePosixPath(home) / "Library" / "LaunchAgents")

    def render(self) -> str:
        return render_macos(self.spec)

    def _domain(self) -> str:
        return f"gui/{os.getuid()}"  # pragma: no cover — macOS 전용

    def activate(self, report: Report) -> None:  # pragma: no cover — macOS 전용
        if self.custom_dir:
            report.say("※ 표준 위치가 아니므로 launchctl 등록은 건너뛴다.")
            return
        if not shutil.which("launchctl"):
            report.say("⚠ launchctl 을 찾지 못했다 — 파일만 두었다. 다음 로그인부터 적용된다.")
            return
        # 이미 올라와 있으면 새 내용으로 갈아끼워야 하므로 먼저 내린다 (멱등).
        _run(["launchctl", "bootout", f"{self._domain()}/{LAUNCHD_LABEL}"])
        ok, out = _run(["launchctl", "bootstrap", self._domain(), self.path])
        if not ok:
            # 구형 macOS 는 bootstrap 이 없다 — legacy 경로로 폴백.
            ok, out = _run(["launchctl", "load", "-w", self.path])
        report.say("launchctl 등록: " + ("성공" if ok else f"실패 — {out}"))

    def deactivate(self, report: Report) -> None:  # pragma: no cover — macOS 전용
        if self.custom_dir or not shutil.which("launchctl"):
            return
        ok, _ = _run(["launchctl", "bootout", f"{self._domain()}/{LAUNCHD_LABEL}"])
        if not ok:
            _run(["launchctl", "unload", "-w", self.path])
        report.say("launchctl 에서 내렸다.")

    def runtime_state(self) -> str:  # pragma: no cover — macOS 전용
        if self.custom_dir or not shutil.which("launchctl"):
            return ""
        ok, _ = _run(["launchctl", "print", f"{self._domain()}/{LAUNCHD_LABEL}"])
        return "launchd 상태: " + ("올라와 있다" if ok else "올라와 있지 않다")


class LinuxBackend(Backend):
    platform = "linux"
    filename = SYSTEMD_UNIT
    label = "systemd 유저 유닛 (~/.config/systemd/user)"

    def default_dir(self) -> str:
        if self.spec.preview:
            base = PurePosixPath(_preview_home("linux")) / ".config"
        else:
            base = PurePosixPath(
                os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
            )
        return str(base / "systemd" / "user")

    def render(self) -> str:
        return render_linux(self.spec)

    def preflight(self, report: Report) -> bool:
        """systemd 가 없으면 **조용히 넘어가지 않고** 명확히 실패한다."""
        if self.custom_dir:
            return True
        if shutil.which("systemctl") and Path("/run/systemd/system").exists():
            return True
        report.say("실패: 이 시스템에는 systemd(유저 인스턴스)가 없다.")
        report.say("자동 등록은 systemd 유저 유닛으로만 지원한다. 수동으로 하려면:")
        report.say("  · 데스크톱 환경의 '시작 프로그램'에 아래 명령을 등록한다")
        report.say(f"      {self.spec.display}")
        report.say("  · 또는 ~/.xprofile · ~/.profile 에 같은 명령을 백그라운드로 추가한다")
        report.say(f"      {self.spec.display} >> {self.spec.log_path} 2>&1 &")
        return False

    def activate(self, report: Report) -> None:  # pragma: no cover — Linux 전용
        if self.custom_dir:
            report.say("※ 표준 위치가 아니므로 systemctl 등록은 건너뛴다.")
            return
        _run(["systemctl", "--user", "daemon-reload"])
        ok, out = _run(["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT])
        report.say("systemctl 등록: " + ("성공" if ok else f"실패 — {out}"))

    def deactivate(self, report: Report) -> None:  # pragma: no cover — Linux 전용
        if self.custom_dir or not shutil.which("systemctl"):
            return
        _run(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT])
        report.say("systemctl 에서 내렸다.")

    def runtime_state(self) -> str:  # pragma: no cover — Linux 전용
        if self.custom_dir or not shutil.which("systemctl"):
            return ""
        ok, out = _run(["systemctl", "--user", "is-active", SYSTEMD_UNIT])
        return f"systemd 상태: {out or ('active' if ok else 'inactive')}"


BACKENDS = {
    "windows": WindowsBackend,
    "macos": MacosBackend,
    "linux": LinuxBackend,
}


def make_backend(
    platform: str,
    options: ServeOptions | None = None,
    *,
    directory: str | os.PathLike | None = None,
    python: str | None = None,
    log_path: str | None = None,
) -> Backend:
    """대상 OS 의 백엔드 하나를 만든다."""
    spec = build_spec(platform, options, python=python, log_path=log_path)
    return BACKENDS[platform](spec, directory)

"""돌고 있는 인스턴스 대장 — **누가 어느 포트에서 어떤 옵션으로 떠 있나.**

왜 필요한가
-----------
갱신(`python -m gitwire_chat update`)은 "돌고 있는 것을 멈추고 · 갈아치우고 ·
다시 띄운다"다. 그 세 걸음 전부가 한 가지 사실에 걸려 있다 — **원래 무엇이 어떻게
떠 있었나.** 포트·`--home`·`--author` 를 잃고 다시 띄우면 그건 같은 앱이 아니다
(다른 상태 디렉토리를 보는 다른 앱이 뜬 셈이다).

그래서 서버가 뜰 때 **자기 자신을 파일 한 장으로 적어 둔다.**

    <OS 데이터 디렉토리>/run/<포트>.json

왜 프로세스를 뒤지지 않나
------------------------
후보는 "돌고 있는 프로세스의 인자를 읽는다" 였다. 그러려면 (1) ``psutil`` 같은
의존을 새로 들이거나 (2) OS 별로 ``tasklist``·``ps`` 를 파싱해야 한다. 이 앱은
셸 전용 도구에 기대지 않는 것을 규율로 삼고 있고(자동 시작도 같은 이유로 파일
한 장을 쓴다), 의존은 ``flask`` + ``gitwire`` 둘로 유지한다. **파일 한 장이 세 OS
를 가장 얇게 덮고, 사람이 눈으로 확인할 수 있다.**

왜 ``--home`` 안이 아니라 OS 데이터 디렉토리인가
-----------------------------------------------
갱신 도구는 **어느 home 을 쓰는 인스턴스가 있는지 모른다.** 대장을 home 안에 두면
그것을 알아내려고 대장을 읽어야 하는 순환이 된다. 그래서 머신 단위로 한 곳에
모은다. 포트가 파일 이름이라 인스턴스 두 개(다른 포트)가 서로를 덮지 않는다.

무엇을 적나 — **해석된 값**이다
------------------------------
``--home`` 이나 ``--author`` 를 안 주고 띄운 경우, 그 값은 환경변수·현재
디렉토리에서 나온다. 갱신 도구는 **다른 셸에서** 실행될 수 있으므로 그 환경이
같다고 믿을 수 없다. 그래서 인자 원문이 아니라 ``Settings`` 가 실제로 정한 값을
적는다 — 다시 띄운 앱이 같은 상태 디렉토리·같은 표시 이름을 본다.

살아 있나는 파일이 아니라 **HTTP 로** 확인한다
--------------------------------------------
대장 파일은 프로세스가 갑자기 죽으면(전원·강제 종료) 남는다. 그리고 pid 만으로
생존을 보는 것은 세 OS 에서 안전하지 않다 — ⚠️ Windows 의 ``os.kill(pid, 0)`` 은
**시그널 0 으로 프로세스를 종료시킨다**(CPython 이 ``TerminateProcess`` 를 부른다).
그래서 생존 판정은 ``GET /api/version`` 으로 한다. 응답의 ``pid`` 가 대장에 적힌
것과 같아야 "그 인스턴스"다 — 포트를 남이 물려받은 경우를 가른다.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .config import os_data_dir

log = logging.getLogger(__name__)

#: 대장 디렉토리를 갈아끼우는 환경변수 — 테스트가 **실제 머신 대장을 건드리지
#: 않게** 하는 주입점이다 (자동 시작의 ``GITWIRE_CHAT_AUTOSTART_DIR`` 과 같은 사상).
ENV_DIR = "GITWIRE_CHAT_RUN_DIR"

#: 대장 파일 포맷 버전. 필드를 바꾸면 올린다.
FORMAT = 1

#: 루프백 고정 — 이 앱이 바인드하는 주소 (`__main__.HOST` 와 같은 값).
HOST = "127.0.0.1"

#: 생존 확인 HTTP 타임아웃(초). 로컬이라 짧게 잡는다.
PROBE_TIMEOUT = 2.0


def run_dir(explicit: str | os.PathLike | None = None) -> Path:
    """대장 디렉토리. 우선순위: 인자 → 환경변수 → OS 데이터 디렉토리/run."""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_DIR)
    if env:
        return Path(env).expanduser()
    return os_data_dir() / "run"


def path_for(port: int, *, directory: str | os.PathLike | None = None) -> Path:
    return run_dir(directory) / f"{int(port)}.json"


@dataclass(frozen=True)
class Instance:
    """대장에 적힌 인스턴스 하나 — **다시 띄우기에 필요한 전부.**"""

    port: int
    pid: int
    executable: str
    """인터프리터 **절대 경로**. venv 로 설치한 경우가 흔하므로 PATH 를 믿지 않는다."""

    args: tuple[str, ...] = ()
    """``python -m gitwire_chat`` 뒤에 붙는 인자 — 해석된 값으로 정규화돼 있다."""

    home: str = ""
    author: str = ""
    version: str = ""
    started_at: float = 0.0
    extra: dict = field(default_factory=dict)

    # -- 재기동 ---------------------------------------------------------

    @property
    def command(self) -> list[str]:
        """다시 띄우는 명령. ``-m`` 으로 부른다.

        ⚠️ 콘솔 스크립트(``gitwire-chat.exe``)로 부르지 않는다 — Windows 에서
        pip 가 그 파일을 갈아치우려 할 때 실행 중이면 잠긴다.
        """
        return [self.executable, "-m", "gitwire_chat", *self.args]

    @property
    def display(self) -> str:
        def quote(part: str) -> str:
            return f'"{part}"' if " " in part else part

        return " ".join(quote(p) for p in self.command)

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}/"

    # -- 직렬화 ---------------------------------------------------------

    def to_json(self) -> dict:
        return {
            "format": FORMAT,
            "port": self.port,
            "pid": self.pid,
            "executable": self.executable,
            "args": list(self.args),
            "home": self.home,
            "author": self.author,
            "version": self.version,
            "started_at": self.started_at,
            **({"extra": self.extra} if self.extra else {}),
        }

    @classmethod
    def from_json(cls, data: dict) -> "Instance | None":
        try:
            port = int(data["port"])
            pid = int(data["pid"])
        except (KeyError, TypeError, ValueError):
            return None
        args = data.get("args") or []
        if not isinstance(args, list):
            return None
        return cls(
            port=port,
            pid=pid,
            executable=str(data.get("executable") or ""),
            args=tuple(str(a) for a in args),
            home=str(data.get("home") or ""),
            author=str(data.get("author") or ""),
            version=str(data.get("version") or ""),
            started_at=float(data.get("started_at") or 0.0),
            extra=data.get("extra") if isinstance(data.get("extra"), dict) else {},
        )


# ------------------------------------------------------------------- 쓰기


def record(
    instance: Instance, *, directory: str | os.PathLike | None = None
) -> Path | None:
    """대장에 적는다. **실패해도 서버는 뜬다** — None 을 돌려주고 경고만 남긴다.

    읽기전용 홈·권한 없는 환경이 있다. 대장이 없으면 갱신 도구가 인스턴스를
    자동으로 못 찾을 뿐이고(그때는 사람에게 명확히 말한다), 채팅은 멀쩡히 돈다.
    """
    path = path_for(instance.port, directory=directory)
    text = json.dumps(instance.to_json(), ensure_ascii=False, indent=2)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 원자적 교체 — 읽는 쪽이 반쪽 파일을 보지 않는다.
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text + "\n")
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as exc:
        log.warning(
            "돌고 있는 인스턴스 대장을 쓰지 못했다 (%s) — `update` 가 이 인스턴스를 "
            "자동으로 찾지 못한다. 채팅은 정상 동작한다.",
            exc,
        )
        return None
    return path


def forget(port: int, *, directory: str | os.PathLike | None = None) -> None:
    """정상 종료할 때 대장에서 뺀다."""
    try:
        path_for(port, directory=directory).unlink()
    except OSError:
        pass


# ------------------------------------------------------------------- 읽기


def load(
    port: int, *, directory: str | os.PathLike | None = None
) -> Instance | None:
    path = path_for(port, directory=directory)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return Instance.from_json(raw)


def load_all(*, directory: str | os.PathLike | None = None) -> list[Instance]:
    """대장에 적힌 전부 — 포트 순. **살아 있는지는 보지 않는다**(`probe` 가 본다)."""
    base = run_dir(directory)
    out = []
    if not base.is_dir():
        return out
    for path in sorted(base.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            log.warning("대장 파일을 읽을 수 없다 — 건너뛴다: %s", path)
            continue
        instance = Instance.from_json(raw) if isinstance(raw, dict) else None
        if instance is None:
            log.warning("대장 파일 형식이 낯설다 — 건너뛴다: %s", path)
            continue
        out.append(instance)
    return sorted(out, key=lambda i: i.port)


# ------------------------------------------------------------------- 생존


def probe(port: int, *, timeout: float = PROBE_TIMEOUT) -> dict | None:
    """``GET /api/version`` — 응답이 오면 그 내용, 아니면 None.

    "포트가 열려 있나"가 아니라 **우리 앱이 답하나**를 본다. TCP 연결만 보면
    다른 프로그램이 그 포트를 쓰고 있는 경우를 가르지 못한다.
    """
    url = f"http://{HOST}:{int(port)}/api/version"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    return data if isinstance(data, dict) else None


def probe_legacy(port: int, *, timeout: float = PROBE_TIMEOUT) -> bool:
    """``/api/version`` 이 없던 **옛 버전**이 이 포트에 떠 있나.

    ⚠️ 이게 없으면 큰 구멍이 하나 남는다. 갱신 기능이 없던 버전으로 떠 있는
    인스턴스는 ``/api/version`` 에 404 를 주므로 `probe` 에게 "아무것도 없다"로
    보인다 — 그러면 갱신이 그 앱을 못 본 채 패키지를 갈아치우고, 살아 있는 앱이
    옛 파이썬 코드로 새 정적 파일을 서빙하는 섞인 상태가 된다. 즉 **처음 한 번,
    바로 그 한 번**이 조용히 깨진다.

    그래서 옛 버전에도 있던 경로(``/api/settings``)로 한 번 더 물어본다.
    """
    url = f"http://{HOST}:{int(port)}/api/settings"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return False
    # 이 앱의 설정 응답인가 (다른 프로그램이 그 포트를 쓰는 경우를 가른다).
    return isinstance(data, dict) and "recent_limit" in data


def alive(instance: Instance, *, timeout: float = PROBE_TIMEOUT) -> bool:
    """대장에 적힌 **그 프로세스**가 그 포트에서 답하나.

    pid 까지 맞춰 보는 이유: 인스턴스가 죽은 뒤 다른 프로그램(또는 새로 띄운
    다른 인스턴스)이 같은 포트를 물려받았을 수 있다. 그걸 "살아 있다"로 보면
    갱신 도구가 남의 프로세스를 멈추려 든다.
    """
    seen = probe(instance.port, timeout=timeout)
    if seen is None:
        return False
    return int(seen.get("pid") or 0) == instance.pid


def wait_until_gone(
    instance: Instance, *, timeout: float = 15.0, interval: float = 0.25
) -> bool:
    """그 인스턴스가 응답을 멈출 때까지 기다린다. True = 사라졌다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not alive(instance, timeout=1.0):
            return True
        time.sleep(interval)
    return not alive(instance, timeout=1.0)


def wait_until_up(port: int, *, timeout: float = 30.0, interval: float = 0.3) -> dict | None:
    """그 포트가 답할 때까지 기다린다. 답하면 그 내용, 시간이 다하면 None."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        seen = probe(port, timeout=1.0)
        if seen is not None:
            return seen
        time.sleep(interval)
    return probe(port, timeout=2.0)

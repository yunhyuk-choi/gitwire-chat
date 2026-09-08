"""갱신 — **바뀔 게 있나 보고 · 멈추고 · 갈아치우고 · 다시 띄우고 · 확인한다.**

    python -m gitwire_chat update --dry-run     무엇을 할지 먼저 보여준다
    python -m gitwire_chat update               실제로 갱신한다

이 앱은 GitHub 에서 직접 설치해 쓴다(체크아웃 없음). 그래서 갱신할 때마다 사람이
① 돌고 있는 프로세스 종료 ② ``pip install --force-reinstall --no-deps …``
③ 재기동 ④ 브라우저 강력 새로고침을 손으로 했다. ④ 는 자원 도장
(`assets.py`)이 없앴고, ①~③ 을 여기서 없앤다.

무엇을 지키나 (설계 제약)
------------------------
* **앱 프로세스와 분리해서 돈다.** 서버가 자기 패키지를 갈아치우면서 자기를
  재시작하는 구조로 만들지 않았다 — Windows 에서 파일 락에 걸리고, 실패하면
  되돌릴 주체가 사라진다. 갱신은 **별도 프로세스**(이 CLI)가 하고, 앱은
  그 CLI 가 멈추고 다시 띄우는 대상일 뿐이다.
* **먼저 멈춘 다음 갈아치운다.** 순서를 뒤집으면(설치 먼저) 살아 있는 프로세스가
  옛 파이썬 코드를 메모리에 들고 **새 정적 파일**을 서빙하는 섞인 상태가 생긴다.
  그리고 Windows 에서 콘솔 스크립트(``gitwire-chat.exe``)로 떠 있으면 pip 가
  그 파일을 지우지 못한다.
* **설치가 실패하면 멈춘 것을 되살린다.** 그리고 직전 커밋으로 되돌리는 명령을
  그대로 출력한다 — "깨진 업데이트가 스스로를 못 고치는" 상태로 끝내지 않는다.
* **다시 뜬 것을 확인한다.** ``GET /api/version`` 이 답할 때까지 기다리고,
  답하지 않으면 **실패로 보고**하고 로그 꼬리와 되돌리는 방법을 보여준다.
  조용히 "죽은 채로 종료"하지 않는다.
* **바뀔 게 없으면 아무것도 하지 않는다.** 멀쩡한 앱을 재시작시키지 않는다.
* **OS 중립.** ``pkill``·``taskkill`` 같은 도구에 기대지 않는다. 프로세스 종료는
  ``os.kill``, 재기동은 ``subprocess.Popen`` — 파이썬 하나로 세 OS 를 덮는다.

원래 실행 옵션은 어떻게 아나
---------------------------
서버가 뜰 때 **자기 자신을 대장에 적는다** (`runstate.py`). 프로세스 인자를
뒤지는 방식(psutil·tasklist·ps)은 의존이나 셸 파싱을 들이므로 쓰지 않았다.
대장에는 *해석된* 값이 들어간다 — 갱신은 다른 셸에서 실행될 수 있어서
``GITWIRE_CHAT_HOME`` 같은 환경변수가 같다고 믿을 수 없다.

자동 시작 감독자와의 경합
------------------------
macOS(launchd ``KeepAlive``)·Linux(systemd)는 **죽은 프로세스를 되살린다.**
거기서 프로세스를 직접 죽이면 감독자가 옛 코드로 다시 띄우거나 유닛 상태가
어긋난다. 그래서 감독자가 띄운 인스턴스는 멈춤·재기동을 **감독자에게 맡긴다**
(`autostart.Backend.stop_service` / `start_service`). 그 경로는 옵션 보존도
공짜로 얻는다 — 등록 파일에 박혀 있는 값이 그대로 쓰인다.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import assets, autostart, runstate
from .config import DEFAULT_PORT

#: 배포 이름 (pip 인자·메타데이터 조회 키).
DIST_NAME = "gitwire-chat"

#: 원천을 알 수 없을 때 쓰는 기본 레포. README 가 안내하는 그 주소다.
DEFAULT_REPO = "https://github.com/yunhyuk-choi/gitwire-chat.git"

#: ``git ls-remote`` 타임아웃(초).
GIT_TIMEOUT = 60.0

#: 인스턴스가 멈출 때까지 / 다시 뜰 때까지 기다리는 시간(초).
STOP_TIMEOUT = 20.0
UP_TIMEOUT = 40.0

#: 재기동한 프로세스의 출력을 모을 로그 파일 이름 (앱 상태 디렉토리 안).
RESTART_LOG = "update-restart.log"

#: 재기동마다 로그에 남기는 경계선. 실패 보고의 로그 꼬리가 **이번 시도**부터
#: 시작하게 자른다 — 지난 실행의 정상 출력과 섞이면 사람이 엉뚱한 줄을 읽는다.
RESTART_MARK = "=== gitwire-chat update 재기동"

#: 실패 보고에 붙일 로그 꼬리 줄 수.
LOG_TAIL = 25

#: 대장 밖 인스턴스를 왜 막았나 — 두 사유를 **구분한다.** 모르는 것을 안다고
#: 말하지 않는 것이 이 프로젝트의 규율이다.
SAME_INSTALL = "same"        # /api/version 의 prefix 가 우리와 같다 (확인됨)
UNKNOWN_INSTALL = "unknown"  # 설치 위치를 알려주지 않는다 (옛 버전) — 보수적으로 막는다


class UpdateError(Exception):
    """갱신을 진행할 수 없다 — 사람이 무엇을 하면 되는지 함께 들고 있다."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


# ------------------------------------------------------------------ 설치 원천


@dataclass(frozen=True)
class Source:
    """지금 설치본이 **어디서 왔나** (pip 가 남긴 ``direct_url.json``)."""

    url: str
    commit: str = ""
    revision: str = ""
    """요청된 ref (브랜치·태그). 없으면 기본 브랜치를 뜻한다."""

    editable: bool = False

    @property
    def ref(self) -> str:
        """원격에서 물어볼 ref."""
        return self.revision or "HEAD"

    @property
    def requirement(self) -> str:
        """pip 에 줄 요구사항 문자열."""
        target = f"git+{self.url}"
        if self.revision:
            target += f"@{self.revision}"
        return f"{DIST_NAME} @ {target}"

    def pinned(self, commit: str) -> str:
        """특정 커밋에 못 박은 요구사항 — **되돌리기**에 쓴다."""
        return f"{DIST_NAME} @ git+{self.url}@{commit}"


def installed_version() -> str:
    """설치본 버전 문자열. 없으면 빈 문자열.

    ⚠️ 캐시 무효화에는 쓸 수 없다(같은 버전으로 재배포한다 — `assets.py`).
    여기서는 사람이 읽는 표시용이다.
    """
    try:
        from importlib.metadata import version

        return version(DIST_NAME)
    except Exception:  # noqa: BLE001
        return ""


def _direct_url() -> dict | None:
    """``dist-info/direct_url.json`` 을 읽는다 (PEP 610).

    pip 가 VCS 에서 설치할 때 **어느 URL 의 어느 커밋**인지 여기에 남긴다.
    이 앱은 git 체크아웃 없이 설치되므로 `git describe` 류를 쓸 수 없고,
    설치본이 자기 출처를 아는 유일한 곳이 이 파일이다.
    """
    try:
        from importlib.metadata import distribution

        dist = distribution(DIST_NAME)
    except Exception:  # noqa: BLE001
        return None
    try:
        text = dist.read_text("direct_url.json")
    except Exception:  # noqa: BLE001
        return None
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def installed_source() -> Source:
    """지금 설치본의 출처. 갱신할 수 없는 형태면 `UpdateError`."""
    data = _direct_url()
    if data is None:
        raise UpdateError(
            "지금 설치본이 어디서 왔는지 알 수 없다 "
            "(pip 가 남기는 direct_url.json 이 없다).",
            hint=(
                "git 이 아닌 방식으로 설치됐거나(로컬 디렉토리·sdist) 설치 정보가 "
                "지워졌다. 한 번만 아래로 설치하면 그다음부터는 update 가 동작한다:\n"
                f'  pip install --force-reinstall --no-deps "{DIST_NAME} @ git+{DEFAULT_REPO}"'
            ),
        )
    if isinstance(data.get("dir_info"), dict) and data["dir_info"].get("editable"):
        raise UpdateError(
            "편집 가능(editable) 설치다 — 갱신은 git 이 할 일이다.",
            hint="소스 체크아웃에서 `git pull` 하면 된다. update 는 설치본용이다.",
        )
    vcs = data.get("vcs_info")
    if not isinstance(vcs, dict):
        raise UpdateError(
            f"git 설치본이 아니다 (원천: {data.get('url') or '알 수 없음'}).",
            hint=(
                "update 는 git 레포에서 설치된 경우에만 쓸 수 있다:\n"
                f'  pip install --force-reinstall --no-deps "{DIST_NAME} @ git+{DEFAULT_REPO}"'
            ),
        )
    return Source(
        url=str(data.get("url") or DEFAULT_REPO),
        commit=str(vcs.get("commit_id") or ""),
        revision=str(vcs.get("requested_revision") or ""),
    )


# ------------------------------------------------------------------ 원격 조회


def remote_commit(url: str, ref: str = "HEAD") -> str:
    """``git ls-remote`` 로 원격의 그 ref 가 가리키는 커밋.

    HTTP API 를 쓰지 않는다 — git 은 이 앱이 이미 쓰는 도구이고(전송 계층 전체가
    git 이다), GitHub 말고 다른 호스트에서도 그대로 동작한다.
    """
    git = shutil.which("git")
    if git is None:
        raise UpdateError(
            "git 을 찾지 못했다.",
            hint="이 앱은 git 을 전송 계층으로 쓴다 — git 을 설치하면 된다.",
        )
    env = dict(os.environ)
    # 자격증명 프롬프트로 멈추지 않게 — 갱신은 사람이 지켜보지 않을 수 있다.
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    try:
        proc = subprocess.run(
            [git, "ls-remote", url, ref],
            capture_output=True,
            timeout=GIT_TIMEOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise UpdateError(
            f"원격 조회가 {GIT_TIMEOUT:.0f}초 안에 끝나지 않았다: {url}",
            hint="네트워크를 확인하고 다시 시도한다.",
        ) from None
    except OSError as exc:
        raise UpdateError(f"git 실행에 실패했다 — {exc}") from None
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise UpdateError(
            f"원격을 읽지 못했다: {url}",
            hint=detail or "네트워크·주소·접근 권한을 확인한다.",
        )
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-f]{40}", parts[0]):
            return parts[0]
    raise UpdateError(
        f"원격에 {ref} 가 없다: {url}",
        hint="브랜치·태그 이름을 확인한다.",
    )


def compare_link(url: str, old: str, new: str) -> str:
    """무엇이 바뀌었는지 사람이 볼 수 있는 링크 (GitHub·GitLab 형태)."""
    base = re.sub(r"\.git$", "", url.strip())
    base = re.sub(r"^git\+", "", base)
    if not base.startswith("http") or not old or not new:
        return ""
    if "github.com" in base:
        return f"{base}/compare/{old}...{new}"
    if "gitlab" in base:
        return f"{base}/-/compare/{old}...{new}"
    return ""


# ------------------------------------------------------------------ 확인 결과


@dataclass(frozen=True)
class Check:
    """"바뀔 게 있나" 한 벌. 앱 안 배너(`/api/update/check`)도 이걸 쓴다."""

    url: str
    installed: str
    remote: str
    version: str = ""
    revision: str = ""

    @property
    def behind(self) -> bool:
        return bool(self.remote) and self.remote != self.installed

    @property
    def compare(self) -> str:
        return compare_link(self.url, self.installed, self.remote)

    def to_json(self) -> dict:
        return {
            "url": self.url,
            "installed": self.installed,
            "remote": self.remote,
            "version": self.version,
            "revision": self.revision,
            "behind": self.behind,
            "compare": self.compare,
            "command": "python -m gitwire_chat update",
        }


def check(url: str | None = None) -> Check:
    """설치본과 원격을 비교한다. 네트워크를 **한 번** 쓴다."""
    source = installed_source()
    target = url or source.url
    return Check(
        url=target,
        installed=source.commit,
        remote=remote_commit(target, source.ref),
        version=installed_version(),
        revision=source.revision,
    )


# --------------------------------------------------------------------- 보고


@dataclass
class Report:
    """CLI 가 출력할 결과. `echo` 를 주면 **즉시** 흘려보낸다.

    갱신은 수십 초가 걸린다. 다 끝난 뒤에 한꺼번에 뿌리면 그동안 화면이 조용해
    "멈췄나" 싶어진다 — 그게 이 프로젝트가 싫어하는 그 모양이다.
    """

    ok: bool = True
    changed: bool = False
    lines: list[str] = field(default_factory=list)
    echo: Callable[[str], None] | None = None

    def say(self, text: str = "") -> None:
        self.lines.append(text)
        if self.echo is not None:
            self.echo(text)

    def fail(self, text: str, hint: str = "") -> None:
        self.ok = False
        self.say(f"실패: {text}")
        for line in (hint or "").splitlines():
            if line:
                self.say(f"  {line}")


# ------------------------------------------------------------- 멈춤·재기동


def _supervisor(instance: runstate.Instance) -> autostart.Backend | None:
    """이 인스턴스를 **감독자가 띄웠나.** 그렇다면 그 백엔드.

    판정: (1) 이 OS 의 자동 시작 수단이 죽은 프로세스를 되살리는 종류이고
    (2) 실제로 등록돼 있고 (3) 등록된 포트가 이 인스턴스의 포트와 같다.
    """
    try:
        backend = autostart.make_backend(autostart.host_platform())
    except Exception:  # noqa: BLE001 — 낯선 플랫폼
        return None
    if not backend.supervises:
        return None
    if backend.registered_port() != instance.port:
        return None
    return backend


def stop_instance(
    instance: runstate.Instance,
    report: Report,
    *,
    timeout: float = STOP_TIMEOUT,
) -> bool:
    """인스턴스 하나를 멈춘다. True = 멈췄다(또는 이미 없었다)."""
    supervisor = _supervisor(instance)
    if supervisor is not None:
        report.say(f"  포트 {instance.port}: 감독자({supervisor.label})에게 멈추라고 한다")
        if not supervisor.stop_service(report):
            report.say(
                "  ⚠ 감독자에게 맡기지 못했다 — 프로세스를 직접 종료한다. "
                "감독자가 곧 되살릴 수 있으니 결과를 확인한다."
            )
        elif runstate.wait_until_gone(instance, timeout=timeout):
            return True

    report.say(f"  포트 {instance.port}: pid {instance.pid} 에 종료를 보낸다")
    # ⚠️ OS 중립 — `taskkill`·`pkill` 을 쓰지 않는다. Windows 의 os.kill 은
    # TerminateProcess 로 내려가므로 SIGTERM 도 즉시 종료다(정리 코드는 못 돈다).
    # 상태 파일은 원자적으로 쓰이므로 그 시점에 반쪽으로 남지 않는다.
    try:
        os.kill(instance.pid, signal.SIGTERM)
    except ProcessLookupError:
        report.say("  이미 없다 (대장 파일만 남아 있었다)")
        return True
    except PermissionError as exc:
        report.fail(
            f"포트 {instance.port} (pid {instance.pid}) 를 멈출 권한이 없다 — {exc}",
            hint="그 프로세스를 띄운 사용자로 실행해야 한다.",
        )
        return False
    except OSError as exc:
        report.fail(f"포트 {instance.port} 를 멈추지 못했다 — {exc}")
        return False

    if runstate.wait_until_gone(instance, timeout=timeout):
        report.say("  멈췄다")
        return True

    # POSIX 는 한 단계 더 있다. Windows 는 위 SIGTERM 이 이미 강제 종료다.
    if hasattr(signal, "SIGKILL"):
        report.say("  응답이 없다 — SIGKILL 로 한 번 더 보낸다")
        try:
            os.kill(instance.pid, signal.SIGKILL)
        except OSError:
            pass
        if runstate.wait_until_gone(instance, timeout=timeout):
            report.say("  멈췄다")
            return True
    report.fail(
        f"포트 {instance.port} 가 아직 응답한다 — 멈추지 못했다.",
        hint=f"그 창에서 Ctrl+C 로 끄고 다시 시도한다 (pid {instance.pid}).",
    )
    return False


def restart_log_path(instance: runstate.Instance) -> Path:
    """재기동한 프로세스의 출력을 모을 파일.

    다시 띄운 프로세스는 터미널에서 떼어져 있다 — 출력을 버리면 "안 뜬 이유"가
    사라진다. 앱 상태 디렉토리(`--home`) 안에 둬서 사람이 찾기 쉽게 한다.
    """
    base = Path(instance.home) if instance.home else Path.cwd()
    return base / RESTART_LOG


def start_instance(
    instance: runstate.Instance,
    report: Report,
    *,
    directory: str | os.PathLike | None = None,
) -> bool:
    """인스턴스 하나를 **원래 옵션 그대로** 다시 띄운다 (분리된 프로세스로).

    ``directory`` 는 다시 뜬 프로세스가 **같은 대장**에 자기를 적게 하려고
    환경변수로 넘긴다. 이걸 빼먹으면 갱신은 A 대장을 보고 앱은 B 대장에 적어서,
    다음 갱신이 그 인스턴스를 못 찾는다.
    """
    supervisor = _supervisor(instance)
    if supervisor is not None:
        report.say(f"  포트 {instance.port}: 감독자({supervisor.label})에게 띄우라고 한다")
        if supervisor.start_service(report):
            return True
        report.say("  ⚠ 감독자가 띄우지 못했다 — 직접 띄운다")

    log_path = restart_log_path(instance)
    report.say(f"  포트 {instance.port}: {instance.display}")
    report.say(f"    로그: {log_path}")
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # 이어 쓴다 — 지난 재기동 기록을 지우지 않는다. 대신 **경계선을 남긴다**:
        # 안 뜬 이유를 볼 때 지난 실행의 출력과 섞이면 사람이 엉뚱한 줄을 읽는다
        # (실측에서 로그 꼬리가 지난 두 번의 정상 출력으로 절반이 찼다).
        handle = open(log_path, "a", encoding="utf-8", errors="replace", newline="\n")
        handle.write(f"\n{RESTART_MARK} {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        handle.flush()
    except OSError as exc:
        report.say(f"    ⚠ 로그 파일을 열지 못했다 ({exc}) — 출력을 버리고 띄운다")
        handle = None

    kwargs: dict = {}
    if os.name == "nt":
        # 터미널을 닫아도 앱이 살아 있게, 그리고 콘솔 창이 새로 뜨지 않게.
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        kwargs["start_new_session"] = True

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env[runstate.ENV_DIR] = str(runstate.run_dir(directory))
    try:
        subprocess.Popen(  # noqa: S603 — 명령은 우리가 만든 것이다
            instance.command,
            stdout=handle or subprocess.DEVNULL,
            stderr=subprocess.STDOUT if handle else subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            cwd=str(Path(instance.home).parent) if instance.home else None,
            env=env,
            **kwargs,
        )
    except OSError as exc:
        report.fail(
            f"포트 {instance.port} 를 다시 띄우지 못했다 — {exc}",
            hint=f"직접 띄우려면:\n  {instance.display}",
        )
        return False
    finally:
        if handle is not None:
            handle.close()
    return True


def log_tail(path: Path, lines: int = LOG_TAIL) -> list[str]:
    """실패 보고에 붙일 로그 꼬리 — **이번 재기동 시도부터**. 없으면 빈 목록.

    경계선(`RESTART_MARK`)이 있으면 그 뒤만 자른다. 없으면(로그를 못 썼거나 옛
    형식) 그냥 마지막 몇 줄을 준다 — 아무것도 안 주는 것보다 낫다.

    ⚠️ 자르는 이유: 로그를 이어 쓰기 때문에 지난 재기동의 **정상** 출력이 꼬리의
    절반을 차지한다(실측). 그러면 사람이 엉뚱한 줄을 읽고 "잘 떴는데?" 한다.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    _, mark, current = text.rpartition(RESTART_MARK)
    if mark:
        text = mark + current
    return text.strip("\n").split("\n")[-lines:]


# ------------------------------------------------------------------ pip 설치


def pip_install(
    requirement: str, report: Report, *, python: str | None = None
) -> bool:
    """``pip install --force-reinstall --no-deps <요구사항>``.

    ``--no-deps`` 는 바뀌지 않은 전송 계층(gitwire)까지 다시 받지 않게 한다 —
    README 가 안내하는 손 명령과 같은 인자다.

    출력은 **줄 단위로 흘려보낸다.** 수십 초 동안 화면이 조용하면 사람은 멈춘 줄
    안다. 그리고 실패했을 때 pip 가 무엇을 말했는지가 보고에 그대로 남는다.
    """
    argv = [
        python or sys.executable,
        "-m",
        "pip",
        "install",
        "--force-reinstall",
        "--no-deps",
        requirement,
    ]
    report.say(f"  {' '.join(argv[1:])}")
    try:
        proc = subprocess.Popen(  # noqa: S603
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        report.fail(f"pip 을 실행하지 못했다 — {exc}")
        return False
    assert proc.stdout is not None
    for line in proc.stdout:
        report.say(f"  pip│ {line.rstrip()}")
    code = proc.wait()
    if code != 0:
        report.say("")
        report.fail(f"pip 이 {code} 로 끝났다 — 갈아치우지 못했다.")
        return False
    return True


# --------------------------------------------------------------- 전체 흐름


def _installed_static_dir() -> Path:
    """설치된 패키지의 정적 트리 — 갱신 뒤 **새 도장**을 재려고 쓴다."""
    return Path(__file__).resolve().parent / "static"


def _find_unmanaged(known: set[int], ports: list[int]) -> list[int]:
    """대장에 없는데 응답하고, **우리가 갈아치울 그 설치본에서 온** 포트.

    이 갱신 기능이 없던 버전으로 떠 있는 인스턴스가 여기 걸린다. 그것의 pid 도
    실행 옵션도 모르므로 **멈출 수도 다시 띄울 수도 없다** — 그 상태에서 파일을
    갈아치우면 살아 있는 앱이 옛 파이썬 코드로 새 정적 파일을 서빙하는 섞인
    상태가 된다.

    다른 venv 에서 도는 앱은 세지 않는다(``prefix`` 가 다르다) — 우리가 건드릴
    파일과 무관하므로 막을 이유가 없다. ``prefix`` 를 아예 안 알려주는 옛 버전은
    보수적으로 막되, **사유를 구분해서** 돌려준다 (`SAME_INSTALL` / `UNKNOWN_INSTALL`)
    — "같은 설치본이다"라고 단정하면 실제로는 다른 venv 일 수 있으므로 보고가
    거짓이 된다. 모르는 것은 모른다고 말한다.

    임의 포트를 훑지 않는다(포트 스캔은 하지 않는다). 볼 곳만 본다.

    반환: ``[(포트, 사유), …]``
    """
    out = []
    for port in ports:
        if port in known:
            continue
        seen = runstate.probe(port)
        if seen is None:
            # ⚠️ 갱신 기능이 없던 버전은 /api/version 에 404 를 준다. 그것을
            # "없다"로 보면 **처음 한 번**이 조용히 깨진다 (섞인 상태).
            if runstate.probe_legacy(port):
                out.append((port, UNKNOWN_INSTALL))
            continue
        prefix = str(seen.get("prefix") or "")
        if not prefix:
            out.append((port, UNKNOWN_INSTALL))
        elif Path(prefix) == Path(sys.prefix):
            out.append((port, SAME_INSTALL))
    return out


def update(
    *,
    url: str | None = None,
    directory: str | os.PathLike | None = None,
    dry_run: bool = False,
    force: bool = False,
    restart: bool = True,
    ignore_unmanaged: bool = False,
    port: int | None = None,
    stop_timeout: float = STOP_TIMEOUT,
    up_timeout: float = UP_TIMEOUT,
    echo: Callable[[str], None] | None = None,
) -> Report:
    """갱신 한 번. 반환한 `Report` 의 ``ok`` 가 성공 여부다."""
    report = Report(echo=echo)

    # ---------------------------------------------- 1) 바뀔 게 있나 (먼저 알린다)
    try:
        origin = installed_source()
    except UpdateError as exc:
        report.fail(str(exc), exc.hint)
        return report
    # ⚠️ 두 좌표를 **구분해서** 들고 간다.
    #   origin — 지금 설치본이 실제로 온 곳. **되돌리기**는 여기를 가리켜야 한다
    #            (설치본의 그 커밋은 여기에만 있다).
    #   source — 이번에 조회하고 설치할 곳. `--url` 이 이것만 바꾼다.
    # 원격 조회와 pip 인자가 **같은 주소**를 봐야 하므로 둘을 함께 바꾸고,
    # 되돌리기만 origin 을 쓴다. 하나만 바꾸면 "저쪽을 확인하고 이쪽을 설치하는"
    # 또는 "없는 곳으로 되돌리라고 안내하는" 조용한 어긋남이 된다.
    source = replace(origin, url=url) if url else origin
    target_url = source.url
    version_before = installed_version()
    stamp_before = assets.compute_stamp(_installed_static_dir())

    report.say(f"설치 원천  : {target_url}" + (f" @ {source.revision}" if source.revision else ""))
    report.say(f"지금 설치본: {origin.commit[:12] or '알 수 없음'}  (버전 {version_before or '?'})")
    report.say(f"자원 도장  : {stamp_before}")
    try:
        remote = remote_commit(target_url, source.ref)
    except UpdateError as exc:
        report.fail(str(exc), exc.hint)
        return report
    report.say(f"원격 최신  : {remote[:12]}")
    report.say("")

    if remote == origin.commit and not force:
        report.say("최신이다 — 바뀔 것이 없다.")
        report.say("아무것도 멈추지 않았고, 아무것도 설치하지 않았다.")
        report.say("(그래도 다시 설치하려면 --force)")
        return report
    if remote == origin.commit:
        report.say("최신이지만 --force 라 그대로 다시 설치한다.")
    else:
        report.say(f"새 것이 있다: {origin.commit[:12] or '?'} → {remote[:12]}")
        link = compare_link(target_url, origin.commit, remote)
        if link:
            report.say(f"무엇이 바뀌었나: {link}")
    report.say("")

    # -------------------------------------------------- 2) 무엇을 멈출 것인가
    recorded = runstate.load_all(directory=directory)
    if port is not None:
        recorded = [i for i in recorded if i.port == port]
    live = [i for i in recorded if runstate.alive(i)]
    dead = [i for i in recorded if i not in live]

    watch = [DEFAULT_PORT] if port is None else [DEFAULT_PORT, port]
    unmanaged = _find_unmanaged(
        {i.port for i in runstate.load_all(directory=directory)}, watch
    )
    if unmanaged and not ignore_unmanaged:
        report.ok = False
        report.say(
            "중단: 대장에 없는 인스턴스가 돌고 있다 — 포트 "
            + ", ".join(str(p) for p, _ in unmanaged)
        )
        report.say("")
        report.say("그 프로세스의 pid 도 실행 옵션도 알 수 없다 — 대장에 적혀 있지")
        report.say("않기 때문이다. 즉 멈출 수도, 원래 옵션으로 다시 띄울 수도 없다.")
        for seen_port, reason in unmanaged:
            if reason == SAME_INSTALL:
                report.say(
                    f"  · 포트 {seen_port}: **지금 갈아치울 설치본과 같은 곳**이다"
                    f" ({sys.prefix})."
                )
            else:
                report.say(
                    f"  · 포트 {seen_port}: 설치 위치를 알려주지 않는다 (갱신 기능이"
                    f" 없던 버전이다) — 지금 갈아치울 설치본({sys.prefix})과 같은"
                    f" 곳인지 **알 수 없어서** 막는다."
                )
        report.say("여기서 그냥 갈아치우면 살아 있는 앱이 **옛 파이썬 코드로 새 정적")
        report.say("파일을 서빙하는** 섞인 상태가 된다.")
        report.say("")
        report.say("이렇게 한다 (한 번만):")
        report.say("  1. 그 앱이 도는 창에서 Ctrl+C 로 끈다")
        report.say("  2. python -m gitwire_chat update      ← 다시 실행")
        report.say("  3. 안내에 따라 앱을 다시 띄운다 (그다음부터는 자동으로 된다)")
        report.say("")
        report.say("정말 무시하고 진행하려면: --ignore-unmanaged")
        return report
    if unmanaged:
        report.say(
            "⚠ 대장에 없는 인스턴스(포트 "
            + ", ".join(str(p) for p, _ in unmanaged)
            + ")를 --ignore-unmanaged 로 무시한다 — 그 앱은 갱신 뒤 직접 재기동해야 한다."
        )

    for instance in dead:
        report.say(f"※ 대장에 있지만 응답이 없다 — 무시한다: 포트 {instance.port} (pid {instance.pid})")

    if live:
        report.say("멈출 인스턴스:")
        for instance in live:
            supervisor = _supervisor(instance)
            tail = f"  [감독자: {supervisor.label}]" if supervisor else ""
            report.say(f"  · 포트 {instance.port} · pid {instance.pid} · home {instance.home}{tail}")
    else:
        report.say("돌고 있는 인스턴스가 없다 — 멈출 것이 없다.")
    report.say("")
    report.say("설치할 것:")
    report.say(f"  pip install --force-reinstall --no-deps \"{source.requirement}\"")
    report.say("")
    if restart and live:
        report.say("다시 띄울 것 (원래 옵션 그대로):")
        for instance in live:
            report.say(f"  · {instance.display}")
    elif live:
        report.say("--no-restart 라 다시 띄우지 않는다. 직접 띄우려면:")
        for instance in live:
            report.say(f"  · {instance.display}")
    report.say("")

    if dry_run:
        report.say("[dry-run] 아무것도 멈추지 않았고, 아무것도 설치하지 않았다.")
        return report

    # --------------------------------------------------------- 3) 멈춘다
    stopped: list[runstate.Instance] = []
    if live:
        report.say("── 멈춘다")
        for instance in live:
            if not stop_instance(instance, report, timeout=stop_timeout):
                report.say("")
                report.say("멈추지 못해서 설치를 시작하지 않았다 — 설치본은 그대로다.")
                for done in stopped:
                    start_instance(done, report, directory=directory)
                return report
            stopped.append(instance)
            runstate.forget(instance.port, directory=directory)
        report.say("")

    # ------------------------------------------------------- 4) 갈아치운다
    report.say("── 설치한다")
    if not pip_install(source.requirement, report):
        report.say("")
        report.say("멈춘 인스턴스를 **옛 버전으로** 되살린다 (설치가 실패했으니 코드는 그대로다).")
        for instance in stopped:
            start_instance(instance, report, directory=directory)
            runstate.wait_until_up(instance.port, timeout=up_timeout)
        report.say("")
        report.say("직접 되돌리거나 다시 시도하려면:")
        report.say(f"  pip install --force-reinstall --no-deps \"{origin.pinned(origin.commit)}\"")
        return report
    report.changed = True
    report.say("")

    # ----------------------------------------------- 5) 무엇이 바뀌었나
    version_after = installed_version()
    stamp_after = assets.compute_stamp(_installed_static_dir())
    report.say("── 바뀐 것")
    report.say(f"커밋      : {origin.commit[:12] or '?'} → {remote[:12]}")
    report.say(f"버전      : {version_before or '?'} → {version_after or '?'}")
    if stamp_after != stamp_before:
        report.say(f"자원 도장 : {stamp_before} → {stamp_after}")
        report.say("            (브라우저가 새 JS·CSS 를 알아서 받는다 — 강력 새로고침 불필요)")
    else:
        report.say(f"자원 도장 : {stamp_after} (정적 파일은 그대로 — 받을 것이 없다)")
    link = compare_link(target_url, origin.commit, remote)
    if link:
        report.say(f"차이      : {link}")
    report.say("")

    # ------------------------------------------------------- 6) 다시 띄운다
    if not stopped:
        report.say("돌고 있던 인스턴스가 없어서 다시 띄울 것이 없다.")
        report.say("띄우려면: python -m gitwire_chat")
        return report
    if not restart:
        report.say("--no-restart 라 다시 띄우지 않았다. 직접 띄운다:")
        for instance in stopped:
            report.say(f"  {instance.display}")
        return report

    report.say("── 다시 띄운다")
    for instance in stopped:
        if not start_instance(instance, report, directory=directory):
            _say_rollback(report, origin, instance)
            continue
        seen = runstate.wait_until_up(instance.port, timeout=up_timeout)
        if seen is None:
            report.ok = False
            report.say("")
            report.say(f"⚠ 갱신은 됐지만 포트 {instance.port} 의 앱이 뜨지 않았다.")
            tail = log_tail(restart_log_path(instance))
            if tail:
                report.say(f"  --- 로그 꼬리 ({restart_log_path(instance)}) ---")
                for line in tail:
                    report.say(f"  {line}")
                report.say("  " + "-" * 40)
            else:
                report.say(f"  로그가 비어 있다: {restart_log_path(instance)}")
            _say_rollback(report, origin, instance)
            continue
        report.say(
            f"  포트 {instance.port}: 떴다 — 버전 {seen.get('version') or '?'} · "
            f"도장 {seen.get('asset_stamp') or '?'} · pid {seen.get('pid')}"
        )
        report.say(f"    {instance.url}")

    if report.ok:
        report.say("")
        report.say("갱신 완료. 브라우저는 그냥 새로고침하면 된다 (강력 새로고침 불필요).")
    return report


def _say_rollback(report: Report, origin: Source, instance: runstate.Instance) -> None:
    """되돌리는 방법을 **명령 그대로** 보여준다.

    "갱신했더니 앱이 안 뜬다"에서 사람이 할 수 있는 일이 남아 있어야 한다.
    직전 커밋을 알고 있으니(설치 전에 읽어 뒀다) 못 박아서 되돌릴 수 있다.

    ⚠️ 가리키는 곳은 `--url` 로 바꾼 주소가 아니라 **설치본이 실제로 온 곳**
    (`origin`)이다 — 그 커밋은 거기에만 있다.
    """
    report.ok = False
    report.say("")
    report.say("직전 버전으로 되돌리는 방법:")
    report.say(f"  pip install --force-reinstall --no-deps \"{origin.pinned(origin.commit)}\"")
    report.say("  그다음 다시 띄운다:")
    report.say(f"  {instance.display}")

"""UI 버튼이 부르는 갱신 **실행** — 정본 CLI 를 분리된 프로세스로 띄운다.

    POST /api/update/run  ->  Launcher.launch()
                          ->  python -m gitwire_chat update --dir <대장>

⭐ **여기서 갱신하지 않는다.** 하는 일은 "이미 만들어 검증된 CLI 를 띄우고 즉시
돌아온다" 하나다. 이유는 `updater.py` 도크가 말하는 그것과 같다 — 서버가 자기
패키지를 갈아치우며 자기를 재시작하는 구조는 (a) Windows 에서 파일 락에 걸리고
(b) 실패했을 때 되돌릴 주체가 사라진다. 멈춤·설치·재기동·**실패 시 되돌리기와
안내**는 CLI 가 이미 한다. 그걸 다시 구현하지 않는다.

그래서 이 파일에 남는 책임은 딱 셋이다:

1. **띄우기** — 콘솔 창 없이, 터미널에서 떼어서, 출력을 로그 파일로. (그 방식은
   `updater.start_instance` 와 같다. 우리를 죽일 프로세스가 우리와 함께 죽으면
   갱신이 반쪽에서 멈춘다.)
2. **띄워도 되나 판정** — 이 인스턴스가 **대장에 적혀 있어야** 한다
   (`self_recorded`). 없으면 CLI 는 이 앱을 찾지 못해 멈추지도 다시 띄우지도
   못하고 파일만 갈아치운다 = 살아 있는 앱이 **옛 파이썬 코드로 새 정적 파일을
   서빙하는 섞인 상태**. 그건 조용한 고장이라 아예 거절하고 CLI 를 안내한다.
3. **동시 실행 방지** — 두 번 눌러도, 두 탭에서 눌러도, (같은 설치본의) 다른
   인스턴스에서 눌러도 한 번만 돌아야 한다. 아래 두 겹이다.

동시 실행을 어떻게 막나 — 두 겹, 그리고 **그 한계**
--------------------------------------------------
* **같은 서버 안**(두 번 누름·두 탭·두 스레드) = 이 객체가 자식 프로세스를
  들고 있으므로 ``Popen.poll()`` 이 **확정적**으로 답한다. 이것이 요구사항이
  말하는 그 경우이고, 여기서는 추측이 없다.
* **다른 프로세스**(같은 설치본의 다른 포트 인스턴스) = 대장 디렉토리에
  자물쇠 파일 한 장(`update-run.lock`)을 ``O_CREAT|O_EXCL`` 로 만든다. 원자적
  이라 둘이 동시에 만들 수 없다.

⚠️ **정직한 한계**: 그 자물쇠를 **풀어 줄 주체가 사라질 수 있다.** 갱신은 자물쇠를
만든 그 서버를 죽인다 — 그래서 정상 경로에서는 아무도 파일을 지우지 못한다.
pid 로 생존을 볼 수도 없다 (Windows 의 ``os.kill(pid, 0)`` 은 **프로세스를
종료시킨다** — `runstate.py` 도크). 그래서 남의 자물쇠는 **로그가 얼마나 조용한가**
로 판정한다: CLI 는 진행 상황을 계속 로그로 흘리므로, `IDLE_LIMIT` 동안 로그가
자라지 않으면 끝난(또는 죽은) 것으로 본다. 추측이라 두 방향으로 틀릴 수 있고,
어느 쪽으로 틀리는지 적어 둔다:

* 갱신이 **막 실패해서** 옛 서버가 되살아난 직후에는 자물쇠가 남아 있고 로그도
  방금 자랐다 — 그 뒤 `IDLE_LIMIT` 동안 버튼이 "이미 돌고 있다"로 거절한다
  (거짓 거절). 그래서 거절 응답에 **언제 시작했고 로그가 어디 있는지**를 함께
  싣는다 — 사람이 판단할 재료를 준다.
* 반대로 CLI 가 `IDLE_LIMIT` 넘게 조용한 구간(느린 ``pip`` 한 단계)이 있으면
  다른 프로세스가 이어받을 수 있다. 그래서 여유를 크게 잡았다(90초 — CLI 의
  가장 긴 침묵 구간인 재기동 대기 40초의 두 배).

`--force`·`--url` 같은 스위치는 여기서 노출하지 않는다. 화면은 "최신으로
올린다" 하나만 하고, 나머지는 CLI 가 정본이다.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from . import runstate, winspawn

log = logging.getLogger(__name__)

#: 갱신 프로세스의 출력을 모을 파일 (앱 상태 디렉토리 안 — 사람이 찾기 쉽게).
LOG_NAME = "update-run.log"

#: 실행마다 로그에 남기는 경계선. 지난 실행의 출력과 섞이지 않게
#: (`updater.RESTART_MARK` 과 같은 사상).
RUN_MARK = "=== gitwire-chat 갱신 (화면 버튼)"

#: 동시 실행을 막는 자물쇠 파일 이름 (대장 디렉토리 안 — 머신 단위로 한 곳).
LOCK_NAME = "update-run.lock"

#: 남의 자물쇠를 "끝난 것"으로 보는 로그 침묵 시간(초). 모듈 도크의 그 판정.
IDLE_LIMIT = 90.0

#: 그 침묵 판정과 무관하게 이만큼 지난 자물쇠는 버린다(초).
HARD_LIMIT = 1800.0


class Busy(Exception):
    """이미 갱신이 돌고 있(어 보인)다 — 무엇이 돌고 있는지 함께 들고 있다."""

    def __init__(self, run: "Run") -> None:
        super().__init__("이미 갱신이 돌고 있다 — 한 번에 하나만 돈다")
        self.run = run

    @property
    def hint(self) -> str:
        return (
            f"{self.run.age_text} 전에 시작했다. 진행 상황은 이 파일에 쌓인다:\n"
            f"  {self.run.log}\n"
            "끝나 있으면 잠시 뒤 다시 누르면 된다 (끝난 것을 알아보는 데 최대 "
            f"{IDLE_LIMIT:.0f}초 걸린다)."
        )


class Unmanaged(Exception):
    """이 인스턴스가 대장에 없다 — 화면에서 갱신하면 섞인 상태가 된다.

    ⚠️ **원인을 단정하지 않는다.** 한때 이 문구가 "서버가 뜰 때 자기를 대장에 적지
    못했다(읽기전용·권한 없음)"고 말했는데, 실사용에서 그 진단은 틀렸다 — 훨씬 흔한
    원인은 **돌고 있는 서버가 그 기능이 없던 옛 버전**인 것이다 (파일은 갱신됐어도
    프로세스는 뜰 때의 코드로 계속 돈다). 틀린 단정은 사람을 권한 조사로 보내고,
    정작 필요한 한 가지(앱을 다시 띄우기)를 못 하게 만든다.

    그래서 문구는 (1) **가장 흔한 원인부터** 말하고 (2) 대장의 **실제 상태**로 두
    경우를 가른다 — 적힌 것이 하나도 없는 것과, 적혀 있는데 이 pid 가 아닌 것은
    원인이 다르다 (`Launcher.unmanaged_hint`).
    """

    def __init__(self, hint: str) -> None:
        super().__init__(
            "돌고 있는 이 앱이 인스턴스 대장에 없다 — 화면에서는 갱신할 수 없다 "
            "(앱을 다시 띄우면 풀릴 가능성이 크다)"
        )
        self.hint = hint


class LaunchError(Exception):
    """갱신 프로세스를 띄우지 못했다."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True)
class Run:
    """돌고 있는(또는 방금 띄운) 갱신 한 건."""

    launcher_pid: int
    """이걸 띄운 **서버**의 pid. 자물쇠가 내 것인지 가르는 데 쓴다."""

    started_at: float
    log: str
    pid: int = 0
    """갱신 프로세스의 pid — **보고용**이다. 생존 판정에 쓰지 않는다
    (Windows 에서 pid 로 생존을 보는 안전한 방법이 없다 — 모듈 도크)."""

    command: tuple[str, ...] = ()

    @property
    def age(self) -> float:
        return max(0.0, time.time() - self.started_at)

    @property
    def age_text(self) -> str:
        seconds = int(self.age)
        return f"{seconds}초" if seconds < 120 else f"{seconds // 60}분"

    def to_json(self) -> dict:
        return {
            "launcher_pid": self.launcher_pid,
            "started_at": self.started_at,
            "log": self.log,
            "pid": self.pid,
            "command": list(self.command),
        }

    @classmethod
    def from_json(cls, data: dict) -> "Run | None":
        try:
            return cls(
                launcher_pid=int(data["launcher_pid"]),
                started_at=float(data["started_at"]),
                log=str(data.get("log") or ""),
                pid=int(data.get("pid") or 0),
                command=tuple(str(p) for p in (data.get("command") or [])),
            )
        except (KeyError, TypeError, ValueError):
            return None


class Launcher:
    """서버 하나가 들고 있는 갱신 실행기. `create_app` 이 하나 만들어 둔다."""

    def __init__(
        self,
        *,
        home: str | os.PathLike,
        directory: str | os.PathLike | None = None,
        python: str | None = None,
        extra_args: tuple[str, ...] = (),
    ) -> None:
        self._home = Path(home)
        self._directory = directory
        #: ⚠️ **콘솔 서브시스템 인터프리터**(``python.exe``)를 고른다 — 이 서버가
        #: ``pythonw.exe`` 로 떠 있어도(자동 시작 경로가 그렇다) 갱신 CLI 는
        #: ``python.exe`` 로 띄운다. ``pythonw`` 는 콘솔을 *아예* 갖지 않아서
        #: 그것이 부르는 ``git``·``pip`` 가 **각자 창을 띄운다.** 창 없는 콘솔을
        #: 갖고 손자에게 물려주는 쪽이 조용하다 — `winspawn` 도크.
        self._python, self._console_python = winspawn.hidden_console_python(python)
        #: 갱신 CLI 에 덧붙일 인자. 테스트가 ``--dry-run`` 을 넣어 **실제 프로세스
        #: 왕복**(띄움·로그·자물쇠 해제)을 pip 없이 검증한다.
        self._extra = tuple(extra_args)
        #: 재진입 가능해야 한다 — `launch()` 가 `current()` 를 안에서 부른다.
        self._guard = threading.RLock()
        self._child: subprocess.Popen | None = None

    # ------------------------------------------------------------- 좌표

    @property
    def run_dir(self) -> Path:
        return runstate.run_dir(self._directory)

    @property
    def log_path(self) -> Path:
        return self._home / LOG_NAME

    @property
    def lock_path(self) -> Path:
        return self.run_dir / LOCK_NAME

    def command(self) -> list[str]:
        """띄울 명령.

        ⚠️ 콘솔 스크립트(``gitwire-chat.exe``)가 아니라 ``-m`` 으로 부른다 —
        Windows 에서 pip 가 그 파일을 갈아치우려 할 때 실행 중이면 잠긴다
        (CLI 도 그래서 스스로 갈아탄다 — `_update_cli._reexec_via_module`).

        ``--dir`` 을 **해석된 값으로 명시**한다. 환경변수가 자식에게 그대로
        전해지긴 하지만, 대장이 어긋나면 갱신이 이 앱을 못 찾는다 — 그 한 가지가
        정확히 우리가 막으려는 고장이라 추측에 맡기지 않는다.
        """
        return [
            self._python, "-m", "gitwire_chat", "update",
            "--dir", str(self.run_dir),
            *self._extra,
        ]

    # ------------------------------------------------------- 띄워도 되나

    def self_recorded(self) -> runstate.Instance | None:
        """대장에 적힌 항목 중 **이 프로세스**인 것. 없으면 None.

        포트를 인자로 받지 않는다 — 앱 팩토리는 포트를 모른다. pid 로 찾으면
        포트를 몰라도 되고, "그 항목이 정말 나인가"까지 같은 비교로 끝난다.
        """
        me = os.getpid()
        for instance in runstate.load_all(directory=self._directory):
            if instance.pid == me:
                return instance
        return None

    def unmanaged_hint(self) -> str:
        """대장에 내가 없을 때 사람에게 할 말. **대장의 실제 상태로 갈린다.**

        두 경우는 원인이 다르므로 다른 말을 한다:

        * **적힌 것이 하나도 없다** — 옛 버전으로 돌고 있거나(대장에 적는 기능이
          없던 버전), 대장을 아예 쓸 수 없는 환경이다. 서버 로그에 "대장을 쓰지
          못했다" 경고가 있으면 후자다 (`runstate.record` 가 남긴다).
        * **적혀 있는데 이 pid 가 아니다** — 쓰기는 **되는** 환경이라는 뜻이므로
          권한은 거의 아니다. 그 항목은 다른 인스턴스이거나 먼저 죽은 프로세스의
          잔재이고, 이 앱은 자기를 적지 않는 옛 버전으로 떠 있을 가능성이 크다.

        어느 쪽이든 **먼저 할 일은 같다 — 앱을 다시 띄우는 것**이다. 그래서 그것을
        앞에 놓고, 권한 이야기는 "다시 띄워도 같으면" 뒤로 보낸다. 안내는 OS 를
        가리지 않는다 (이 앱은 Windows·macOS·리눅스에서 돈다).
        """
        others = runstate.load_all(directory=self._directory)
        me = os.getpid()
        common = (
            "가장 흔한 원인은 돌고 있는 서버가 이 기능이 없던 옛 버전인 것이다 — "
            "파일이 갱신돼도 프로세스는 뜰 때의 코드로 계속 돈다."
        )
        restart = (
            "앱을 껐다 다시 띄우면 지금 프로세스가 자기를 대장에 적는다 — "
            "터미널에서 띄웠으면 그 창에서 Ctrl+C 로 멈추고 다시 "
            "`python -m gitwire_chat`, 아이콘·자동 시작으로 떠 있으면 그 앱을 "
            "종료한 뒤 같은 방법으로 띄운다."
        )
        cli = (
            "그래도 같으면 터미널에서 갱신해라 — CLI 가 무엇이 걸리는지 말해 준다:"
            "  python -m gitwire_chat update"
        )
        if others:
            listed = ", ".join(f"포트 {i.port}(pid {i.pid})" for i in others[:4])
            first = (
                f"대장에는 {listed} 가 적혀 있는데 지금 이 프로세스(pid {me})는 없다. "
                f"{common} 대장에 쓰기는 되고 있으니 권한 문제는 아닐 것이다."
            )
            return "\n".join([first, restart, cli])
        first = (
            f"대장에 적힌 인스턴스가 하나도 없다 (그 폴더: {self.run_dir}). {common}"
        )
        permission = (
            "다시 띄워도 같은 말이 나오면 그때는 대장을 쓸 수 없는 환경일 수 있다 "
            "(읽기전용·권한 없음) — 서버 로그의 \"대장을 쓰지 못했다\" 경고가 그 증거다."
        )
        return "\n".join([first, restart, permission, cli])

    # ------------------------------------------------------------ 자물쇠

    def _read_lock(self) -> Run | None:
        try:
            raw = json.loads(self.lock_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return None
        return Run.from_json(raw) if isinstance(raw, dict) else None

    def _write_lock(self, run: Run) -> None:
        text = json.dumps(run.to_json(), ensure_ascii=False, indent=2) + "\n"
        self.lock_path.write_text(text, encoding="utf-8", newline="\n")

    def _drop_lock(self, run: Run) -> None:
        """내가 만든 **그** 자물쇠면 지운다. 남의 것은 건드리지 않는다."""
        seen = self._read_lock()
        if seen is None:
            return
        if (seen.launcher_pid, seen.started_at) != (run.launcher_pid, run.started_at):
            return
        try:
            self.lock_path.unlink()
        except OSError:
            pass

    def current(self) -> Run | None:
        """돌고 있(어 보이)는 갱신. 없으면 None.

        판정 근거가 **두 종류**이고 그 차이가 이 기능의 정직한 한계다 (모듈 도크):
        내가 띄운 것은 자식 프로세스에게 물어보므로 확정이고, 남이 띄운 것은
        로그의 침묵으로 추측한다.
        """
        with self._guard:
            run = self._read_lock()
            if run is None:
                return None
            child = self._child
            if child is not None and run.launcher_pid == os.getpid():
                return run if child.poll() is None else None
            if run.age > HARD_LIMIT:
                return None
            quiet_since = run.started_at
            try:
                quiet_since = max(quiet_since, Path(run.log).stat().st_mtime)
            except OSError:
                pass
            return run if (time.time() - quiet_since) <= IDLE_LIMIT else None

    # -------------------------------------------------------------- 실행

    def launch(self) -> Run:
        """갱신 CLI 를 띄우고 **즉시** 돌아온다. 기다리지 않는다."""
        with self._guard:
            live = self.current()
            if live is not None:
                raise Busy(live)
            if self.self_recorded() is None:
                raise Unmanaged(self.unmanaged_hint())
            argv = self.command()
            run = Run(
                launcher_pid=os.getpid(),
                started_at=time.time(),
                log=str(self.log_path),
                command=tuple(argv),
            )
            self._claim(run)
            child = self._spawn(argv, run)
            run = replace(run, pid=child.pid)
            self._child = child
            self._write_lock(run)
            threading.Thread(
                target=self._reap, args=(child, run), daemon=True, name="update-reap"
            ).start()
            log.warning(
                "갱신을 띄웠다 (pid %s) — 이 서버는 곧 멈춘다. 기록: %s",
                child.pid, run.log,
            )
            return run

    def _claim(self, run: Run) -> None:
        """자물쇠를 원자적으로 집는다. 이미 있으면 **죽은 것만** 이어받는다."""
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            # 위 `current()` 가 "죽은 자물쇠"로 판정한 그 파일이다 — 이어받는다.
            # (둘이 동시에 여기 올 수 있지만, 그건 둘 다 *끝난* 갱신을 이어받는
            #  경우다. 그 창은 자물쇠 없이 돌리는 것보다 훨씬 좁다.)
            self._write_lock(run)
            return
        except OSError as exc:
            raise LaunchError(
                f"동시 실행을 막는 자물쇠를 쓸 수 없다 — {exc}",
                hint=(
                    f"대장 디렉토리에 쓸 수 없다: {self.run_dir}\n"
                    "자물쇠 없이 갱신을 띄우지는 않는다 (두 번 돌면 pip 가 겹친다). "
                    "터미널에서: python -m gitwire_chat update"
                ),
            ) from None
        os.close(fd)
        self._write_lock(run)

    def _spawn(self, argv: list[str], run: Run) -> subprocess.Popen:
        """분리된 프로세스로 띄운다 — ⚠️ **콘솔 창을 만들지 않는다.**

        우리를 죽일 프로세스가 우리와 함께 죽으면 갱신이 반쪽에서 멈춘다. 그래서
        프로세스 그룹·세션을 떼고, 출력은 파일로 돌린다(버리면 "왜 안 됐나"가
        사라진다 — `updater.start_instance` 와 같은 이유).

        ⚠️ **창 억제는 `winspawn.detached_kwargs` 단일 원천을 쓴다.** 예전에는
        여기서 ``DETACHED_PROCESS`` 를 직접 줬는데, 그건 "창을 안 띄운다"가
        아니라 "콘솔을 아예 주지 않는다"는 뜻이어서 **갱신 CLI 가 부르는
        ``git``·``pip`` 가 각자 빈 창을 띄웠다.** 실사용에서 사용자가 그 창을
        닫았고 앱이 함께 죽었다. 지금은 창 없는 콘솔을 주고, 손자들이 그것을
        물려받는다.
        """
        handle = None
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(
                self.log_path, "a", encoding="utf-8", errors="replace", newline="\n"
            )
            handle.write(f"\n{RUN_MARK} {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            handle.write(f"{' '.join(argv)}\n")
            if not self._console_python:
                # 창 억제의 한 겹이 없다 — 숨기지 않는다. 갱신 자체는 진행한다.
                handle.write(
                    "⚠ python.exe 를 찾지 못해 pythonw.exe 로 띄운다 — 갱신이 부르는 "
                    "git·pip 이 빈 콘솔 창을 띄울 수 있다.\n"
                )
            handle.flush()
        except OSError as exc:
            log.warning("갱신 기록 파일을 열지 못했다 (%s) — 출력을 버리고 띄운다", exc)
            if handle is not None:
                handle.close()
            handle = None

        kwargs = winspawn.detached_kwargs()

        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env[runstate.ENV_DIR] = str(self.run_dir)
        parent = self._home.parent
        try:
            return subprocess.Popen(  # noqa: S603 — 명령은 우리가 만든 것이다
                argv,
                stdout=handle or subprocess.DEVNULL,
                stderr=subprocess.STDOUT if handle else subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                cwd=str(parent) if parent.is_dir() else None,
                env=env,
                **kwargs,
            )
        except OSError as exc:
            self._drop_lock(run)
            raise LaunchError(
                f"갱신 프로세스를 띄우지 못했다 — {exc}",
                hint="터미널에서 직접: " + " ".join(argv),
            ) from None
        finally:
            if handle is not None:
                handle.close()

    def _reap(self, child: subprocess.Popen, run: Run) -> None:
        """자식이 끝나면 자물쇠를 푼다.

        ⚠️ 정상 경로에서는 **이 스레드가 돌지 못한다** — 갱신이 우리를 먼저
        죽인다. 그래서 이건 "서버가 살아남은 경우"(바뀔 게 없었다·설치가
        실패했다·CLI 가 중단했다)만 정리한다. 살아남지 못한 경우의 자물쇠는
        로그 침묵으로 만료된다 (모듈 도크의 그 한계).
        """
        try:
            code = child.wait()
        except Exception:  # noqa: BLE001 — 회수 실패로 서버를 죽이지 않는다
            code = -1
        with self._guard:
            if self._child is child:
                self._child = None
            self._drop_lock(run)
        log.info("갱신 프로세스가 %s 로 끝났다 (기록: %s)", code, run.log)

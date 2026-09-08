"""콘솔 창 억제 — **플래그가 실제 호출에 걸려 있나.**

⚠️ **창을 실제로 세지 않는다.** 창을 세려면 창을 띄워야 하고, 그러면 이 테스트가
돌 때마다 그 머신 사용자의 화면에 빈 검은 창이 깜빡인다 — 실제로 그런 일이
일어났다(2026-09-08: 사용자가 그 창을 닫았고 앱이 함께 죽었다). 우리가 통제하는
것은 **생성 플래그와 인터프리터**이므로 그것을 단언한다. 플래그가 빠지면 이
파일이 깨진다.

무엇을 지키나
------------
* ⭐ **런처가 하나다.** 갱신 뒤 앱을 다시 띄우는 방식이 자동 시작과 **같은
  계산**에서 나온다 (`autostart.start_app`). 진짜 결함은 창이 아니라 "이 앱을
  띄우는 방법이 둘"이었다.
* 백그라운드로 오래 사는 자식(갱신 CLI · 다시 띄우는 앱) = ``CREATE_NO_WINDOW``
  **+ 새 프로세스 그룹**, 그리고 ``DETACHED_PROCESS`` **금지**.
* 잠깐 돌고 출력을 우리가 읽는 자식(git·pip·알림) = ``CREATE_NO_WINDOW``.
* macOS·리눅스 = ``start_new_session`` 만. ``creationflags`` 를 아예 넘기지
  않는다 (POSIX 에서 0 이 아니면 ``subprocess`` 가 거절한다).
"""

from __future__ import annotations

import ast
import io
import subprocess
import sys
from pathlib import Path

import pytest

from gitwire_chat import autostart, notify, updater, updaterun, winspawn

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)


# ------------------------------------------------------------- 생성 플래그


def test_오래_사는_자식은_창_없는_콘솔을_받는다(monkeypatch):
    """⭐ ``DETACHED_PROCESS`` 가 아니라 ``CREATE_NO_WINDOW`` 여야 한다.

    ``DETACHED_PROCESS`` 는 "창을 안 띄운다"가 아니라 "콘솔을 아예 주지
    않는다"는 뜻이고, 그러면 그 자식이 부르는 ``git``·``pip`` 이 **각자 창을
    띄운다**(실측). 게다가 MSDN 에 따르면 둘을 같이 주면 ``CREATE_NO_WINDOW``
    가 **무시된다** — 함께 쓸 수 없다.
    """
    monkeypatch.setattr(winspawn.os, "name", "nt")
    flags = winspawn.detached_kwargs()["creationflags"]
    assert flags & NO_WINDOW, "창 없는 콘솔 플래그가 빠졌다"
    assert flags & NEW_GROUP, "Ctrl+C 가 자식까지 전파된다"
    assert not flags & DETACHED, (
        "DETACHED_PROCESS 를 같이 주면 CREATE_NO_WINDOW 가 무시된다 — 손자가 창을 띄운다"
    )
    assert "start_new_session" not in winspawn.detached_kwargs()


def test_잠깐_도는_자식도_창을_띄우지_않는다(monkeypatch):
    monkeypatch.setattr(winspawn.os, "name", "nt")
    assert winspawn.quiet_kwargs() == {"creationflags": NO_WINDOW}


def test_POSIX_에서는_세션만_떼고_플래그를_넘기지_않는다(monkeypatch):
    """macOS·리눅스에 콘솔 창이라는 개념이 없다 — 동작을 바꾸지 않는다."""
    for name in ("posix", "java"):
        monkeypatch.setattr(winspawn.os, "name", name)
        assert winspawn.detached_kwargs() == {"start_new_session": True}
        assert winspawn.quiet_kwargs() == {}, (
            "POSIX 에 creationflags 를 넘기면 subprocess 가 거절한다"
        )


# --------------------------------------------------------- 인터프리터 선택


def _fake_install(tmp_path: Path, *names: str) -> Path:
    for name in names:
        (tmp_path / name).write_text("", encoding="utf-8")
    return tmp_path


def test_플래그를_줄_수_있으면_콘솔_있는_python_을_고른다(tmp_path):
    """⭐ ``pythonw`` 는 콘솔이 *아예* 없어서 손자가 창을 띄운다.

    창 없는 콘솔은 **물려진다** — 그래서 우리가 플래그를 줄 수 있는 자리에서는
    ``python.exe`` 가 정답이다.
    """
    _fake_install(tmp_path, "python.exe", "pythonw.exe")
    exe, sure = winspawn.hidden_console_python(str(tmp_path / "pythonw.exe"))
    assert Path(exe).name == "python.exe" and sure is True
    # 이미 콘솔 인터프리터면 그대로 둔다.
    exe, sure = winspawn.hidden_console_python(str(tmp_path / "python.exe"))
    assert Path(exe).name == "python.exe" and sure is True


def test_python_이_없으면_알린다_조용히_넘기지_않는다(tmp_path):
    _fake_install(tmp_path, "pythonw.exe")
    exe, sure = winspawn.hidden_console_python(str(tmp_path / "pythonw.exe"))
    assert Path(exe).name == "pythonw.exe"
    assert sure is False, "폴백을 성공으로 보고하면 창이 뜨는 것을 아무도 모른다"


def test_플래그를_줄_수_없으면_pythonw_를_고른다(tmp_path):
    """자동 시작(시작 폴더 ``.cmd``)은 ``creationflags`` 를 줄 방법이 없다."""
    _fake_install(tmp_path, "python.exe", "pythonw.exe")
    exe, sure = winspawn.console_free_python(str(tmp_path / "python.exe"))
    assert Path(exe).name == "pythonw.exe" and sure is True
    bare = tmp_path / "bare"
    bare.mkdir()
    _fake_install(bare, "python.exe")
    exe, sure = winspawn.console_free_python(str(bare / "python.exe"))
    assert Path(exe).name == "python.exe" and sure is False


def test_자동_시작은_같은_판정을_쓴다_중복_구현이_아니다(tmp_path):
    """단일 원천 — 한쪽만 고쳐져서 다른 쪽이 창을 띄우는 일이 없어야 한다."""
    _fake_install(tmp_path, "python.exe", "pythonw.exe")
    given = str(tmp_path / "python.exe")
    assert autostart._windows_python(given) == winspawn.console_free_python(given)


# --------------------------------------------- ⭐ 실제 호출에 걸려 있나


class _FakePopen:
    """``subprocess.Popen`` 자리에 끼워 **인자를 잡아 두는** 대역."""

    seen: dict = {}

    def __init__(self, argv, **kwargs) -> None:
        _FakePopen.seen = {"argv": list(argv), "kwargs": kwargs}
        self.pid = 4242
        #: pip 경로는 이 스트림을 줄 단위로 읽는다 (빈 출력 = 아무 말도 없었다).
        self.stdout = io.StringIO("")

    def poll(self):
        return None

    def wait(self):
        return 0


def test_갱신_CLI_를_띄우는_호출에_플래그가_걸려_있다(tmp_path, monkeypatch):
    """① 버튼이 갱신 CLI 를 띄우는 자리 (`updaterun.Launcher._spawn`)."""
    monkeypatch.setattr(winspawn.os, "name", "nt")
    monkeypatch.setattr(updaterun.subprocess, "Popen", _FakePopen)
    launcher = updaterun.Launcher(home=tmp_path / "chats", directory=tmp_path / "run")
    (tmp_path / "chats").mkdir(parents=True, exist_ok=True)
    launcher._spawn(launcher.command(), _run_stub(tmp_path))

    flags = _FakePopen.seen["kwargs"]["creationflags"]
    assert flags & NO_WINDOW and not flags & DETACHED
    # 로그는 계속 파일로 간다 — 창을 없앤 대신 출력을 잃으면 실패해도 아무것도 못 본다.
    assert _FakePopen.seen["kwargs"]["stdout"] is not subprocess.DEVNULL
    assert (tmp_path / "chats" / updaterun.LOG_NAME).exists()


def _run_stub(tmp_path: Path) -> updaterun.Run:
    return updaterun.Run(
        launcher_pid=1,
        started_at=0.0,
        log=str(tmp_path / "chats" / updaterun.LOG_NAME),
    )


def test_앱을_다시_띄우는_호출에도_플래그가_걸려_있다(tmp_path, monkeypatch):
    """② 갱신 CLI 가 앱을 다시 띄우는 자리 (`updater.start_instance`).

    하나만 고치면 창이 여전히 남는다 — 그래서 두 자리를 따로 단언한다.
    """
    monkeypatch.setattr(winspawn.os, "name", "nt")
    # ⚠️ 띄우는 것은 이제 단일 원천(autostart)이다 — 그쪽을 잡는다.
    monkeypatch.setattr(autostart.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(autostart, "supervisor_for", lambda port: None)
    home = tmp_path / "chats"
    home.mkdir(parents=True, exist_ok=True)
    instance = updater.runstate.Instance(
        port=8899,
        pid=4242,
        executable=sys.executable,
        args=("--port", "8899", "--home", str(home)),
        home=str(home),
    )
    report = updater.Report()
    assert updater.start_instance(instance, report, directory=tmp_path / "run")

    flags = _FakePopen.seen["kwargs"]["creationflags"]
    assert flags & NO_WINDOW and not flags & DETACHED
    assert _FakePopen.seen["argv"][1:4] == ["-m", "gitwire_chat", "--port"]
    assert (home / updater.RESTART_LOG).exists(), "재기동 로그가 파일로 가야 한다"


def test_갱신_재기동은_자동_시작과_같은_방식으로_띄운다(tmp_path, monkeypatch):
    """⭐ **런처가 하나여야 한다** — 이 파일의 가장 중요한 단언.

    진짜 결함은 "창이 뜬다"가 아니라 **이 앱을 띄우는 방법이 둘이었다**는
    것이었다: 로그인할 때는 자동 시작(콘솔 없는 ``pythonw``)이 띄우고, 갱신
    뒤에는 `updater` 가 자기만의 방식(``DETACHED_PROCESS`` + 대장에 적힌
    인터프리터)으로 띄웠다. 플래그를 하나 더 붙여도 런처가 둘인 상태는 남고
    다음에 또 어긋난다.

    그래서 여기서는 **같은 명령이 나오는가**를 본다. 한쪽 계산이 바뀌면 이
    테스트가 깨진다.
    """
    monkeypatch.setattr(winspawn.os, "name", "nt")
    monkeypatch.setattr(autostart.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(autostart, "supervisor_for", lambda port: None)
    venv = tmp_path / "venv"
    venv.mkdir()
    _fake_install(venv, "python.exe", "pythonw.exe")
    home = tmp_path / "chats"
    home.mkdir()
    args = ("--port", "8899", "--home", str(home))
    instance = updater.runstate.Instance(
        port=8899,
        pid=4242,
        executable=str(venv / "python.exe"),
        args=args,
        home=str(home),
    )
    report = updater.Report()
    assert updater.start_instance(instance, report, directory=tmp_path / "run")

    expected, console_free = autostart.background_command(
        args, python=str(venv / "python.exe")
    )
    assert _FakePopen.seen["argv"] == expected, (
        "갱신이 자동 시작과 다른 명령으로 띄운다 — 런처가 둘이다"
    )
    # 자동 시작 **등록 파일**에 박히는 명령과도 같은 인터프리터여야 한다.
    spec = autostart.build_spec(
        "windows",
        autostart.ServeOptions(port=8899, home=str(home)),
        python=str(venv / "python.exe"),
    )
    assert spec.command == expected, "등록 파일과 재기동이 서로 다른 인터프리터를 쓴다"
    assert console_free and Path(expected[0]).name == "pythonw.exe"


def test_갱신은_기동_로직을_직접_갖지_않는다(tmp_path, monkeypatch):
    """단일 원천을 **호출만** 하는가 — 자기 Popen 을 되살리면 깨진다."""
    calls: list[dict] = []

    def fake_start_app(args, **kwargs):
        calls.append({"args": list(args), **kwargs})
        return autostart.Started(ok=True, via="직접")

    monkeypatch.setattr(autostart, "start_app", fake_start_app)

    def boom(*a, **k):  # 직접 띄우려 하면 즉시 드러난다
        raise AssertionError("updater 가 앱을 직접 띄웠다 — 단일 원천을 지나지 않았다")

    monkeypatch.setattr(updater.subprocess, "Popen", boom)
    home = tmp_path / "chats"
    home.mkdir()
    instance = updater.runstate.Instance(
        port=8899, pid=4242, executable=sys.executable,
        args=("--port", "8899"), home=str(home),
    )
    assert updater.start_instance(instance, updater.Report(), directory=tmp_path / "run")
    assert len(calls) == 1
    assert calls[0]["args"] == ["--port", "8899"]
    assert calls[0]["port"] == 8899, "감독자 판정에 쓸 포트를 넘기지 않았다"
    assert str(calls[0]["log_path"]).endswith(updater.RESTART_LOG)


def test_감독자_판정도_한_곳에서만_한다(monkeypatch):
    """멈춤 쪽과 기동 쪽이 다른 답을 내면 감독자와 경합한다."""
    sentinel = object()
    monkeypatch.setattr(autostart, "supervisor_for", lambda port: sentinel)
    instance = updater.runstate.Instance(port=8899, pid=1, executable=sys.executable)
    assert updater._supervisor(instance) is sentinel


def test_git_과_pip_도_창을_띄우지_않는다(monkeypatch):
    """갱신 CLI 가 부르는 손자들 — 원인의 절반이 여기였다."""
    monkeypatch.setattr(winspawn.os, "name", "nt")
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["ls-remote"] = kwargs
        sha = "a" * 40
        return subprocess.CompletedProcess(argv, 0, f"{sha}\tHEAD\n", "")

    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    updater.remote_commit("https://example.invalid/x.git")
    assert seen["ls-remote"]["creationflags"] & NO_WINDOW
    assert seen["ls-remote"]["capture_output"] is True, "출력 캡처가 깨지면 판정이 죽는다"

    monkeypatch.setattr(updater.subprocess, "Popen", _FakePopen)
    report = updater.Report()
    updater.pip_install("gitwire-chat @ git+https://example.invalid/x.git", report)
    assert _FakePopen.seen["kwargs"]["creationflags"] & NO_WINDOW
    assert _FakePopen.seen["kwargs"]["stdout"] is subprocess.PIPE, (
        "pip 출력은 우리가 한 줄씩 받아 보고에 싣는다"
    )


def test_알림_백엔드도_같은_단일_원천을_쓴다(monkeypatch):
    monkeypatch.setattr(winspawn.os, "name", "nt")
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(notify.subprocess, "run", fake_run)
    assert notify._run(["cmd", "/c", "echo"]) is True
    assert seen["creationflags"] & NO_WINDOW


@pytest.mark.parametrize("module", [updaterun, updater, notify, autostart])
def test_창_억제를_직접_구현한_곳이_없다(module):
    """단일 원천 — 모듈마다 다시 쓰면 한 곳이 빠지고 **그 곳이** 창을 띄운다.

    ``ast`` 로 본다 — 도크·주석에서 플래그 이름을 *설명하는* 것은 막지 않고,
    ``creationflags=`` 를 **직접 넘기는 호출**만 막는다.
    """
    source = Path(module.__file__).read_text(encoding="utf-8")
    direct = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg == "creationflags"
    ]
    assert not direct, (
        f"{module.__name__} 이 창 억제 플래그를 직접 넘긴다 — winspawn 을 쓰라"
    )
    assert "winspawn" in source, f"{module.__name__} 이 단일 원천을 쓰지 않는다"

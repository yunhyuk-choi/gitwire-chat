"""갱신 — 대장(`runstate`) · 원천 판정 · 흐름 · **실제 프로세스 왕복**.

이 기능은 **사용자 환경을 바꾼다** (프로세스를 죽이고 패키지를 갈아치운다).
그래서 자동 시작 테스트와 같은 규율을 지킨다:

* 대장 디렉토리는 항상 ``tmp_path`` 로 주입한다. 혹시 빠뜨렸을 때를 위해
  autouse 픽스처가 환경변수로 한 번 더 막는다 — **머신의 실제 대장을 건드리지
  않는다.**
* pip 를 부르는 테스트는 없다. 그 한 걸음은 갈아끼우고, 대신 **멈춤·재기동은
  진짜 프로세스로** 왕복한다 (흉내를 내면 아무것도 증명하지 못하는 부분이다).
* 기본 포트(8770)에 사용자가 앱을 띄워 놓고 있을 수 있으므로, 그 포트를 보는
  경로는 전부 갈아끼우거나 임의 포트를 쓴다.
"""

from __future__ import annotations

import dataclasses
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from gitwire_chat import autostart, csrf, runstate, updater, updaterun

ROOT = Path(__file__).resolve().parents[1]

#: 실제로 앱을 띄워 보는 테스트에 주는 시간(초).
BOOT_TIMEOUT = 45.0


@pytest.fixture(autouse=True)
def _never_touch_real_registry(monkeypatch, tmp_path):
    """빠뜨린 테스트가 있어도 머신의 실제 인스턴스 대장으로 새지 않게 한다."""
    guard = tmp_path / "guard-run"
    guard.mkdir()
    monkeypatch.setenv(runstate.ENV_DIR, str(guard))
    monkeypatch.setenv(autostart.ENV_DIR, str(tmp_path / "guard-startup"))


def free_port() -> int:
    """OS 가 비어 있다고 알려 준 포트. 사용자 인스턴스(8770)와 부딪히지 않는다."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def sample(port: int = 8899, **kwargs) -> runstate.Instance:
    data = {
        "port": port,
        "pid": 4242,
        "executable": sys.executable,
        "args": ("--port", str(port), "--home", r"C:\some where\chats"),
        "home": r"C:\some where\chats",
        "author": "테스터",
        "version": "0.2.0",
        "started_at": 1.0,
    }
    data.update(kwargs)
    return runstate.Instance(**data)


# ================================================================= 대장


def test_대장에_적고_그대로_읽는다(tmp_path):
    instance = sample()
    path = runstate.record(instance, directory=tmp_path)
    assert path is not None and path.name == "8899.json"
    assert runstate.load(8899, directory=tmp_path) == instance


def test_대장_파일은_UTF8_이고_LF_다(tmp_path):
    path = runstate.record(sample(home="C:/한글 경로/chats"), directory=tmp_path)
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw
    assert "한글 경로" in json.loads(raw.decode("utf-8"))["home"]


def test_포트마다_다른_파일이라_인스턴스가_서로를_덮지_않는다(tmp_path):
    runstate.record(sample(8801), directory=tmp_path)
    runstate.record(sample(8802), directory=tmp_path)
    assert [i.port for i in runstate.load_all(directory=tmp_path)] == [8801, 8802]


def test_다시_띄우는_명령은_모듈로_부른다(tmp_path):
    """⚠️ 콘솔 스크립트(`gitwire-chat.exe`)로 부르면 Windows 에서 pip 가 잠긴다."""
    command = sample().command
    assert command[0] == sys.executable
    assert command[1:3] == ["-m", "gitwire_chat"]
    assert "--port" in command


def test_대장을_쓸_수_없어도_예외가_아니다_None_이다(tmp_path):
    """읽기전용·권한 없는 환경. 채팅은 멀쩡히 돌아야 한다."""
    blocker = tmp_path / "blocked"
    blocker.write_text("파일이라 디렉토리를 만들 수 없다", encoding="utf-8")
    assert runstate.record(sample(), directory=blocker / "run") is None


def test_망가진_대장_파일은_건너뛴다(tmp_path, caplog):
    runstate.record(sample(8801), directory=tmp_path)
    (tmp_path / "8802.json").write_text("{망가짐", encoding="utf-8")
    (tmp_path / "8803.json").write_text('{"port": "포트가 아니다"}', encoding="utf-8")
    with caplog.at_level("WARNING"):
        loaded = runstate.load_all(directory=tmp_path)
    assert [i.port for i in loaded] == [8801]
    assert "8802" in caplog.text and "8803" in caplog.text


def test_정상_종료는_대장에서_빠진다(tmp_path):
    runstate.record(sample(), directory=tmp_path)
    runstate.forget(8899, directory=tmp_path)
    assert runstate.load_all(directory=tmp_path) == []
    runstate.forget(8899, directory=tmp_path)          # 없어도 조용히 끝난다


def test_없는_포트를_물어보면_None(tmp_path):
    assert runstate.load(9999, directory=tmp_path) is None


def test_응답이_없으면_살아_있지_않다():
    assert runstate.probe(free_port(), timeout=0.5) is None
    assert not runstate.alive(sample(free_port()), timeout=0.5)


# ============================================================== 설치 원천


def direct_url(**kwargs) -> dict:
    data = {
        "url": "https://github.com/yunhyuk-choi/gitwire-chat.git",
        "vcs_info": {"vcs": "git", "commit_id": "a" * 40},
    }
    data.update(kwargs)
    return data


def test_git_설치본의_출처를_읽는다(monkeypatch):
    monkeypatch.setattr(updater, "_direct_url", lambda: direct_url())
    source = updater.installed_source()
    assert source.url.endswith("gitwire-chat.git")
    assert source.commit == "a" * 40
    assert source.ref == "HEAD"
    assert source.requirement == (
        "gitwire-chat @ git+https://github.com/yunhyuk-choi/gitwire-chat.git"
    )


def test_브랜치를_지정해_설치했으면_그_브랜치를_본다(monkeypatch):
    monkeypatch.setattr(
        updater,
        "_direct_url",
        lambda: direct_url(
            vcs_info={"vcs": "git", "commit_id": "b" * 40, "requested_revision": "main"}
        ),
    )
    source = updater.installed_source()
    assert source.ref == "main"
    assert source.requirement.endswith("gitwire-chat.git@main")


def test_되돌리기는_직전_커밋에_못_박는다(monkeypatch):
    monkeypatch.setattr(updater, "_direct_url", lambda: direct_url())
    source = updater.installed_source()
    assert source.pinned("c" * 40).endswith("gitwire-chat.git@" + "c" * 40)


def test_출처를_모르면_무엇을_하면_되는지_말한다(monkeypatch):
    """조용히 실패하지 않는다 — 힌트에 실제로 칠 명령이 들어 있다."""
    monkeypatch.setattr(updater, "_direct_url", lambda: None)
    with pytest.raises(updater.UpdateError) as caught:
        updater.installed_source()
    assert "pip install" in caught.value.hint


def test_편집가능_설치는_갱신_대상이_아니다(monkeypatch):
    monkeypatch.setattr(
        updater,
        "_direct_url",
        lambda: {"url": "file:///x", "dir_info": {"editable": True}},
    )
    with pytest.raises(updater.UpdateError) as caught:
        updater.installed_source()
    assert "git pull" in caught.value.hint


def test_git_이_아닌_설치본은_명확히_거절한다(monkeypatch):
    monkeypatch.setattr(updater, "_direct_url", lambda: {"url": "file:///tmp/x.whl"})
    with pytest.raises(updater.UpdateError):
        updater.installed_source()


# ============================================================== 원격 조회


def commit_in(repo: Path) -> str:
    """진짜 로컬 레포에 커밋 하나 — `ls-remote` 를 흉내 없이 재려고."""
    work = repo.parent / "work"
    subprocess.run(["git", "clone", str(repo), str(work)], check=True, capture_output=True)
    (work / "README.md").write_text("안녕\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "첫 커밋"], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=work, check=True, capture_output=True)
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def test_원격_커밋을_실제_git_으로_읽는다(bare_repo):
    """대역이 아니라 진짜 `git ls-remote` 다 (로컬 레포를 원격으로 삼는다)."""
    expected = commit_in(bare_repo)
    assert updater.remote_commit(str(bare_repo), "HEAD") == expected
    assert updater.remote_commit(str(bare_repo), "main") == expected


def test_없는_ref_는_명확히_실패한다(bare_repo):
    commit_in(bare_repo)
    with pytest.raises(updater.UpdateError) as caught:
        updater.remote_commit(str(bare_repo), "없는-브랜치")
    assert "없는-브랜치" in str(caught.value)


def test_읽을_수_없는_원격은_힌트에_git_의_말을_담는다(tmp_path):
    with pytest.raises(updater.UpdateError) as caught:
        updater.remote_commit(str(tmp_path / "없다.git"), "HEAD")
    assert caught.value.hint


@pytest.mark.parametrize(
    ("url", "expect"),
    [
        ("https://github.com/a/b.git", "https://github.com/a/b/compare/aaa...bbb"),
        ("https://gitlab.com/a/b.git", "https://gitlab.com/a/b/-/compare/aaa...bbb"),
        ("git@github.com:a/b.git", ""),
    ],
)
def test_무엇이_바뀌었나_링크(url, expect):
    assert updater.compare_link(url, "aaa", "bbb") == expect


# =========================================================== 감독자 판정


RENDER = {
    "windows": autostart.render_windows,
    "macos": autostart.render_macos,
    "linux": autostart.render_linux,
}


@pytest.mark.parametrize("platform", list(autostart.PLATFORMS))
def test_등록_내용에서_포트를_읽는다_세_포맷_전부(platform, tmp_path, monkeypatch):
    """세 포맷(cmd·plist·unit)에서 `--port` 값을 같은 방법으로 읽어낸다.

    갱신은 "돌고 있는 이 인스턴스가 감독자가 띄운 그것인가"를 이 값으로 가린다.
    포맷이 셋이라 파서도 셋이면 하나가 낡어도 모른다 — 그래서 한 방법으로 읽고,
    **세 포맷 전부**를 여기서 확인한다. (다른 OS 의 등록 파일은 이 머신에
    실제로 쓸 수 없으므로 렌더 결과를 그대로 먹인다.)
    """
    spec = autostart.build_spec(
        platform,
        autostart.ServeOptions(port=8123, home=str(tmp_path / "chats")),
        python=sys.executable,
        log_path=str(tmp_path / "log.txt"),
    )
    text = RENDER[platform](spec)
    assert "--port" in text
    backend = autostart.make_backend(
        autostart.host_platform(),
        autostart.ServeOptions(port=8123),
        directory=tmp_path / "reg",
        log_path=str(tmp_path / "log.txt"),
        python=sys.executable,
    )
    monkeypatch.setattr(backend, "read", lambda: text)
    assert backend.registered_port() == 8123


def test_등록되지_않았으면_포트도_없다(tmp_path):
    """실제 파일로 — 이 머신의 수단으로 등록 전·후를 본다."""
    backend = autostart.make_backend(
        autostart.host_platform(),
        autostart.ServeOptions(port=8123, home=str(tmp_path / "chats")),
        directory=tmp_path / "reg",
        log_path=str(tmp_path / "log.txt"),
        python=sys.executable,
    )
    assert backend.registered_port() is None
    assert backend.install().ok
    assert backend.registered_port() == 8123


def test_감독자가_되살리는_OS_가_어디인지_고정한다():
    """macOS·Linux 는 되살린다(경합) · Windows 시작 폴더는 아니다."""
    assert autostart.MacosBackend.supervises is True
    assert autostart.LinuxBackend.supervises is True
    assert autostart.WindowsBackend.supervises is False


def test_감독자_수단은_멈춤_요청을_받아_실제_명령을_부른다(monkeypatch, tmp_path):
    """macOS·Linux 경로는 이 머신에서 실행할 수 없으므로 명령 호출을 잡아 본다."""
    calls = []
    monkeypatch.setattr(autostart, "_run", lambda args: calls.append(args) or (True, ""))
    monkeypatch.setattr(autostart.shutil, "which", lambda name: f"/usr/bin/{name}")
    backend = autostart.make_backend(
        "linux",
        autostart.ServeOptions(port=8123),
        log_path=str(tmp_path / "log.txt"),
        python="/usr/bin/python3",
    )
    report = autostart.Report()
    assert backend.stop_service(report) is True
    assert backend.start_service(report) is True
    assert calls == [
        ["systemctl", "--user", "stop", autostart.SYSTEMD_UNIT],
        ["systemctl", "--user", "start", autostart.SYSTEMD_UNIT],
    ]


def test_윈도우_시작_폴더는_감독자가_아니라_아무것도_하지_않는다(tmp_path):
    backend = autostart.make_backend(
        "windows",
        autostart.ServeOptions(port=8123),
        directory=tmp_path / "reg",
        log_path=str(tmp_path / "log.txt"),
        python=sys.executable,
    )
    report = autostart.Report()
    assert backend.stop_service(report) is False
    assert backend.start_service(report) is False


# =============================================================== 흐름


@pytest.fixture
def no_network(monkeypatch):
    """원격 조회·설치·기본 포트 탐지를 갈아끼운다 (흐름만 본다)."""
    state = {
        "remote": "b" * 40,
        "installed": "a" * 40,
        "pip": [],
        "unmanaged": [],
        #: git 에서 오는 의존(gitwire)들. 기본은 없음 — 흐름만 보는 테스트는
        #: 그대로 돌고, 동반 갱신 테스트가 이 값을 채운다.
        "deps": [],
        #: pip 가 **실제로** 넣은 커밋 (name → commit). 설치 뒤 확인의 근거.
        "landed": {},
    }
    monkeypatch.setattr(
        updater,
        "_direct_url",
        lambda name=updater.DIST_NAME: direct_url(
            vcs_info={"vcs": "git", "commit_id": state["installed"]}
        ),
    )
    monkeypatch.setattr(updater, "remote_commit", lambda url, ref="HEAD": state["remote"])
    monkeypatch.setattr(updater, "companions", lambda **kw: list(state["deps"]))

    def fake_pip(requirement: str, report, **kw) -> bool:
        """pip 대역 — **무엇이 실제로 들어갔는지**까지 흉내 낸다.

        갱신은 설치 뒤 `direct_url.json` 을 다시 읽어 확인한다. 그 확인이
        의미를 가지려면 대역도 "설치되면 커밋이 바뀐다"를 지켜야 한다.
        """
        state["pip"].append(requirement)
        name = requirement.split(" @ ", 1)[0]
        for dep in state["deps"]:
            if dep.name == name:
                state["landed"][name] = dep.remote
        return True

    monkeypatch.setattr(updater, "pip_install", fake_pip)

    def fake_installed_dep(name: str):
        for dep in state["deps"]:
            if dep.name != name:
                continue
            was = dep.installed.commit if dep.installed else ""
            commit = state["landed"].get(name, was)
            if not commit:
                return None, "0.2.0"
            return (
                updater.Source(url=dep.declared.url, commit=commit, name=name),
                "0.2.0",
            )
        return None, ""

    monkeypatch.setattr(updater, "_installed_dep", fake_installed_dep)
    # ⚠️ 사용자가 8770 에 앱을 띄워 두고 있을 수 있다 — 그 포트를 보지 않게 한다.
    monkeypatch.setattr(updater, "_find_unmanaged", lambda known, ports: state["unmanaged"])
    return state


def test_바뀔_게_없으면_아무것도_하지_않는다(no_network, tmp_path):
    """⭐ 멀쩡한 앱을 재시작시키지 않는다."""
    no_network["remote"] = no_network["installed"]
    stopped = []
    report = updater.update(directory=tmp_path, stop_timeout=1.0)
    assert report.ok and not report.changed
    assert no_network["pip"] == [], "설치를 부르면 안 된다"
    assert stopped == []
    assert any("최신이다" in line for line in report.lines)


def test_최신이어도_force_면_다시_설치한다(no_network, tmp_path):
    no_network["remote"] = no_network["installed"]
    report = updater.update(directory=tmp_path, force=True, stop_timeout=1.0)
    assert report.ok and report.changed
    assert len(no_network["pip"]) == 1


def test_dry_run_은_계획만_보여주고_아무것도_안_한다(no_network, tmp_path):
    runstate.record(sample(8899), directory=tmp_path)     # 죽은 인스턴스 기록
    report = updater.update(directory=tmp_path, dry_run=True)
    assert report.ok and not report.changed
    assert no_network["pip"] == []
    text = "\n".join(report.lines)
    assert "[dry-run]" in text
    assert "pip install --force-reinstall --no-deps" in text


def test_대장에_있지만_죽은_인스턴스는_무시한다(no_network, tmp_path):
    runstate.record(sample(8899), directory=tmp_path)
    report = updater.update(directory=tmp_path)
    assert report.ok and report.changed
    text = "\n".join(report.lines)
    assert "응답이 없다" in text
    assert "다시 띄울 것이 없다" in text


def test_대장_밖_인스턴스가_있으면_중단하고_무엇을_할지_말한다(no_network, tmp_path):
    no_network["unmanaged"] = [(8770, updater.SAME_INSTALL)]
    report = updater.update(directory=tmp_path)
    assert not report.ok
    assert no_network["pip"] == [], "섞인 상태를 만들지 않는다"
    text = "\n".join(report.lines)
    assert "Ctrl+C" in text
    assert "--ignore-unmanaged" in text
    assert "같은 곳" in text


def test_설치_위치를_모르는_인스턴스는_모른다고_말한다(no_network, tmp_path):
    """⚠️ 모르는 것을 안다고 말하지 않는다.

    옛 버전은 자기 설치 위치를 알려주지 않는다. 그것을 "같은 설치본이다"라고
    단정하면(실제로는 다른 venv 일 수 있다) 보고가 거짓이 된다. 막는 것은
    같지만 **사유는 다르게** 말한다.
    """
    no_network["unmanaged"] = [(8770, updater.UNKNOWN_INSTALL)]
    report = updater.update(directory=tmp_path)
    assert not report.ok
    text = "\n".join(report.lines)
    assert "알 수 없어서" in text
    assert "같은 곳**이다" not in text


def test_되돌리기는_url_을_바꿔도_설치본이_온_곳을_가리킨다(
    no_network, tmp_path, monkeypatch
):
    """⚠️ `--url` 로 다른 곳을 설치하려다 실패했을 때, 되돌릴 커밋은 **원래 곳**에만
    있다. 바뀐 주소로 안내하면 있지도 않은 커밋을 가리키는 거짓 안내가 된다.
    """
    monkeypatch.setattr(
        updater, "pip_install", lambda req, report, **kw: report.fail("실패다") or False
    )
    monkeypatch.setattr(updater, "stop_instance", lambda inst, report, **kw: True)
    monkeypatch.setattr(updater, "start_instance", lambda inst, report, **kw: True)
    monkeypatch.setattr(runstate, "alive", lambda inst, **kw: True)
    monkeypatch.setattr(runstate, "wait_until_up", lambda port, **kw: {"pid": 1})
    runstate.record(sample(8899), directory=tmp_path)

    report = updater.update(directory=tmp_path, url="https://example.invalid/other.git")
    assert not report.ok
    text = "\n".join(report.lines)
    assert "gitwire-chat.git@" + "a" * 40 in text, "되돌리기가 원래 곳을 가리켜야 한다"
    assert "other.git@" + "a" * 40 not in text


def test_대장_밖_인스턴스를_무시하라고_하면_경고하고_진행한다(no_network, tmp_path):
    no_network["unmanaged"] = [(8770, updater.SAME_INSTALL)]
    report = updater.update(directory=tmp_path, ignore_unmanaged=True)
    assert report.ok and report.changed
    assert any("무시한다" in line for line in report.lines)


def test_url_을_바꾸면_조회와_설치가_같은_주소를_본다(no_network, tmp_path):
    other = "https://example.invalid/mine.git"
    report = updater.update(directory=tmp_path, url=other)
    assert report.ok
    assert no_network["pip"] == [f"gitwire-chat @ git+{other}"]


def test_설치가_실패하면_멈춘_것을_되살리고_되돌리는_명령을_보여준다(
    no_network, tmp_path, monkeypatch
):
    """⭐ "깨진 업데이트가 스스로를 못 고치는" 상태로 끝내지 않는다."""
    monkeypatch.setattr(
        updater,
        "pip_install",
        lambda req, report, **kw: report.fail("pip 이 1 로 끝났다 (테스트)") or False,
    )
    restarted = []
    monkeypatch.setattr(
        updater,
        "start_instance",
        lambda inst, report, **kw: restarted.append(inst.port) or True,
    )
    monkeypatch.setattr(updater, "stop_instance", lambda inst, report, **kw: True)
    monkeypatch.setattr(runstate, "alive", lambda inst, **kw: True)
    monkeypatch.setattr(runstate, "wait_until_up", lambda port, **kw: {"pid": 1})
    runstate.record(sample(8899), directory=tmp_path)

    report = updater.update(directory=tmp_path)
    assert not report.ok and not report.changed
    assert restarted == [8899], "멈춘 것을 되살려야 한다"
    text = "\n".join(report.lines)
    assert "되돌리거나 다시 시도하려면" in text
    assert "@" + "a" * 40 in text, "직전 커밋에 못 박은 명령이 있어야 한다"


def test_다시_뜨지_않으면_실패로_보고하고_로그와_되돌리기를_보여준다(
    no_network, tmp_path, monkeypatch
):
    """⭐ 조용히 "죽은 채로 종료"하지 않는다."""
    monkeypatch.setattr(updater, "stop_instance", lambda inst, report, **kw: True)
    monkeypatch.setattr(updater, "start_instance", lambda inst, report, **kw: True)
    monkeypatch.setattr(runstate, "alive", lambda inst, **kw: True)
    monkeypatch.setattr(runstate, "wait_until_up", lambda port, **kw: None)
    home = tmp_path / "chats"
    home.mkdir()
    (home / updater.RESTART_LOG).write_text(
        "Traceback (most recent call last):\n에러다\n", encoding="utf-8"
    )
    runstate.record(sample(8899, home=str(home)), directory=tmp_path)

    report = updater.update(directory=tmp_path)
    assert not report.ok
    assert report.changed, "설치 자체는 됐다는 사실도 남아야 한다"
    text = "\n".join(report.lines)
    assert "뜨지 않았다" in text
    assert "에러다" in text, "로그 꼬리가 보고에 있어야 한다"
    assert "되돌리는 방법" in text


def test_바뀐_것에_자원_도장이_들어간다(no_network, tmp_path):
    """캐시 무효화가 실제로 걸리는지 사람이 보고 알 수 있게."""
    report = updater.update(directory=tmp_path)
    assert any("자원 도장" in line for line in report.lines)


# ======================================================= 앱 안에서 알리기


@pytest.fixture
def client(tmp_path):
    from gitwire_chat.app import create_app
    from gitwire_chat.config import Settings

    app = create_app(
        Settings(home=tmp_path / "chats", notifications=False), start=False
    )
    return app.test_client()


def test_확인은_상태를_바꾸지_않고_결과만_준다(client, monkeypatch, bare_repo):
    """⭐ 앱 안에서는 **묻기만** 한다 — 여기서 갱신을 실행하지 않는다.

    인증 없는 루프백 앱에 "앱을 죽이고 갈아치우는" 엔드포인트를 두지 않는다는
    결정의 기계 검사다. 라우트 목록에 그런 것이 없어야 한다.
    """
    commit_in(bare_repo)
    monkeypatch.setattr(
        updater, "_direct_url", lambda: direct_url(url=str(bare_repo))
    )
    data = client.post("/api/update/check").get_json()
    assert data["behind"] is True
    assert data["installed"] == "a" * 40
    assert len(data["remote"]) == 40
    assert data["command"] == "python -m gitwire_chat update"


def test_최신이면_behind_가_거짓이다(client, monkeypatch, bare_repo):
    head = commit_in(bare_repo)
    monkeypatch.setattr(
        updater,
        "_direct_url",
        lambda: direct_url(url=str(bare_repo), vcs_info={"vcs": "git", "commit_id": head}),
    )
    data = client.post("/api/update/check").get_json()
    assert data["behind"] is False


def test_확인이_실패하면_사유와_힌트를_함께_준다(client, monkeypatch):
    """조용히 200 을 주지 않는다 — 화면이 그 힌트를 그대로 보여준다."""
    monkeypatch.setattr(updater, "_direct_url", lambda: None)
    response = client.post("/api/update/check")
    assert response.status_code == 400
    body = response.get_json()
    assert body["error"] and "pip install" in body["hint"]


def test_갱신_표면은_확인과_실행_둘뿐이고_실행은_POST_뿐이다(client):
    """⭐ 표면이 자라지 않았는지 센다.

    한때 이 자리에는 "앱을 갱신하는 엔드포인트는 **없다**"가 있었다. 그 판단을
    뒤집었지만(버튼으로 갱신까지 간다), 뒤집은 것은 *하나*뿐이다 — 실행 표면은
    `POST /api/update/run` 딱 하나이고, 그것도 우리 화면에서 온 요청만 받는다
    (`csrf.py`). `--force`·`--url` 처럼 위험을 늘리는 스위치는 HTTP 로 열지
    않았다: 그건 CLI 가 정본이다.
    """
    rules = sorted(str(rule) for rule in client.application.url_map.iter_rules())
    update_rules = [r for r in rules if "update" in r]
    assert update_rules == ["/api/update/check", "/api/update/run"], update_rules
    # 읽기 동사로는 부를 수 없다 — 링크·이미지·프리페치로 갱신이 시작되지 않게.
    assert client.get("/api/update/run").status_code == 405


# ======================================= ⭐ 화면에서 온 요청인가 (csrf.py)
#
# 여기 있는 것이 "버튼으로 갱신까지" 를 성립시킨 그 방어다. 예전 판단(실행
# 엔드포인트를 두지 않는다)의 근거는 "드라이브바이를 막을 수단이 없다"였는데,
# 그 뒷부분이 틀렸다는 것을 **거절되는지 세어서** 확인한다.


#: Flask 테스트 클라이언트가 쓰는 Host (`Origin` 비교 대상이 이것이다).
TEST_HOST = "localhost"


def form_post(origin: str = "http://evil.invalid", *, sec_fetch: bool = True) -> dict:
    """다른 페이지가 숨은 HTML 폼으로 보낸 POST 의 헤더 모양.

    ⭐ 여기 커스텀 헤더가 **없는 것**이 핵심이다 — 평범한 폼은 붙일 수 없다.
    `sec_fetch=False` 는 그 헤더를 안 보내는 오래된 브라우저다.
    """
    headers = {
        "Origin": origin,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if sec_fetch:
        headers["Sec-Fetch-Site"] = "cross-site"
        headers["Sec-Fetch-Mode"] = "navigate"
    return headers


def ui_post(host: str = "127.0.0.1:8770") -> dict:
    """우리 화면이 보내는 POST 의 헤더 모양 (브라우저가 붙이는 것까지)."""
    return {
        csrf.HEADER: csrf.HEADER_VALUE,
        "Origin": f"http://{host}",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
    }


@pytest.mark.parametrize(
    "headers,expect",
    [
        # 아무 헤더도 없는 최악의 경우 — **커스텀 헤더 요구**에서 죽는다.
        ({}, "요청 헤더가 없다"),
        ({"Content-Type": "application/x-www-form-urlencoded"}, "요청 헤더가 없다"),
        # 요즘 브라우저의 폼 전송 — `Sec-Fetch-Site` 에서 먼저 죽는다.
        (form_post(), "같은 출처에서 온 것이 아니라고"),
        # `Sec-Fetch-*` 를 안 보내는 브라우저의 폼 전송 — `Origin` 에서 죽는다.
        (form_post(sec_fetch=False), "다른 출처"),
        # 헤더를 붙였다고 우겨도 브라우저가 붙인 표식이 우리를 지킨다.
        ({csrf.HEADER: "update", "Sec-Fetch-Site": "cross-site"}, "같은 출처에서 온 것이 아니라고"),
        ({csrf.HEADER: "update", "Sec-Fetch-Site": "same-site"}, "같은 출처에서 온 것이 아니라고"),
        ({csrf.HEADER: "update", "Origin": "http://evil.invalid"}, "다른 출처"),
        # 샌드박스 iframe·data: 문서의 Origin
        ({csrf.HEADER: "update", "Origin": "null"}, "다른 출처"),
        # 이름만 맞고 값이 다른 헤더로는 열리지 않는다.
        ({csrf.HEADER: "아무거나"}, "요청 헤더가 없다"),
    ],
)
def test_우리_화면에서_오지_않은_요청은_사유와_함께_거절한다(headers, expect):
    reason = csrf.deny_reason(headers, "127.0.0.1:8770")
    assert reason, f"통과시켰다: {headers}"
    assert expect in reason, reason


def test_우리_화면_모양은_통과한다():
    assert csrf.deny_reason(ui_post(), "127.0.0.1:8770") == ""
    # localhost 로 열어도 된다 — 비교 대상은 Host 헤더 자신이다.
    assert csrf.deny_reason(ui_post("localhost:8770"), "localhost:8770") == ""


def test_Sec_Fetch_를_안_보내는_클라이언트도_통과한다():
    """⭐ **방어가 정상 사용을 막으면 실패다.**

    `Sec-Fetch-*` 는 비교적 최신 헤더다. 안 보내는 브라우저·`curl`·스크립트에서도
    우리 UI 는 동작해야 하므로 **없으면 통과**시킨다. 그 경우에도 커스텀 헤더
    요구는 그대로 성립한다 (평범한 폼은 못 붙인다).
    """
    assert csrf.deny_reason({csrf.HEADER: csrf.HEADER_VALUE}, "127.0.0.1:8770") == ""
    # Origin 도 없는 옛 클라이언트 (헤더 하나만 붙인 curl)
    assert csrf.deny_reason(
        {csrf.HEADER: csrf.HEADER_VALUE, "User-Agent": "curl/7.0"}, "127.0.0.1:8770"
    ) == ""


def test_화면과_서버가_같은_헤더를_쓴다():
    """두 곳이 어긋나면 "버튼이 403 만 받는다"는 사고가 된다.

    파이썬과 JS 가 같은 상수를 공유할 수는 없으니 문자열이 같은지를 **기계로**
    확인한다 — 테마 키를 템플릿↔모듈 사이에서 맞춰 보는 `test_theme.py` 와
    같은 종류의 계약 검사다.
    """
    js = (ROOT / "src" / "gitwire_chat" / "static" / "js" / "update.js").read_text(
        encoding="utf-8"
    )
    assert f"'{csrf.HEADER}'" in js, "update.js 가 다른 헤더 이름을 쓴다"
    assert f"'{csrf.HEADER_VALUE}'" in js, "update.js 가 다른 헤더 값을 쓴다"


# =================================== ⭐ 누르면 갱신이 시작된다 (엔드포인트)


class FakeLauncher:
    """`updaterun.Launcher` 의 **소비 표면만** 흉내 낸 기록기.

    엔드포인트 테스트에서 갱신을 정말로 띄우지 않는다. 실제 프로세스 왕복은
    아래 「실행기」 절이 따로 본다 — 두 관심사를 한 테스트에 섞으면 둘 다 흐려진다.
    """

    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self.launches = 0
        self.raise_with: BaseException | None = None

    def launch(self) -> updaterun.Run:
        if self.raise_with is not None:
            raise self.raise_with
        self.launches += 1
        return updaterun.Run(
            launcher_pid=os.getpid(),
            started_at=time.time(),
            log=str(self.home / updaterun.LOG_NAME),
            pid=987654,
            command=(sys.executable, "-m", "gitwire_chat", "update"),
        )


@pytest.fixture
def runner(tmp_path, monkeypatch):
    """`POST /api/update/run` 을 볼 준비 한 벌 (네트워크·실제 실행 없음)."""
    from gitwire_chat.app import create_app
    from gitwire_chat.config import Settings

    state = {"remote": "b" * 40, "installed": "a" * 40}
    monkeypatch.setattr(
        updater,
        "_direct_url",
        lambda: direct_url(vcs_info={"vcs": "git", "commit_id": state["installed"]}),
    )
    monkeypatch.setattr(
        updater, "remote_commit", lambda url, ref="HEAD": state["remote"]
    )
    app = create_app(
        Settings(home=tmp_path / "chats", notifications=False), start=False
    )
    launcher = FakeLauncher(tmp_path / "chats")
    app.extensions["gitwire_chat_update"] = launcher
    state["launcher"] = launcher
    state["client"] = app.test_client()
    return state


def test_새_것이_있으면_CLI_를_띄우고_즉시_202_로_답한다(runner):
    """⭐ 응답은 "**시작했다**"이고 "갱신됐다"가 아니다 — 기다리지 않는다."""
    response = runner["client"].post("/api/update/run", headers=ui_post(TEST_HOST))
    assert response.status_code == 202
    body = response.get_json()
    assert body["started"] is True
    assert runner["launcher"].launches == 1
    assert body["check"]["behind"] is True
    assert body["run"]["log"].endswith(updaterun.LOG_NAME)
    # 화면이 "새 서버가 떴다"를 가르는 기준 — 지금 서버의 pid.
    assert body["serving"]["pid"] == os.getpid()


def test_바뀔_게_없으면_아무것도_띄우지_않고_그렇게_말한다(runner):
    """⭐ 이 판정을 화면의 말을 믿고 건너뛰지 않는다 (서버가 확인한다)."""
    runner["remote"] = runner["installed"]
    response = runner["client"].post("/api/update/run", headers=ui_post(TEST_HOST))
    assert response.status_code == 200
    body = response.get_json()
    assert body["started"] is False and body["code"] == "current"
    assert body["check"]["behind"] is False
    assert runner["launcher"].launches == 0, "멀쩡한 앱을 재시작시키려 했다"


def test_거절된_요청은_갱신을_띄우지_않는다(runner):
    """403 이 "사유만 다르게 말하고 실행은 됐다"가 아니어야 한다."""
    for headers in ({}, form_post(), {csrf.HEADER: csrf.HEADER_VALUE, "Origin": "http://evil.invalid"},
    ):
        response = runner["client"].post("/api/update/run", headers=headers)
        assert response.status_code == 403, headers
        body = response.get_json()
        assert body["code"] == "forbidden"
        assert body["error"] and "python -m gitwire_chat update" in body["hint"]
    assert runner["launcher"].launches == 0


def test_이미_돌고_있으면_409_로_거절하고_어디를_보라고_말한다(runner):
    live = updaterun.Run(
        launcher_pid=os.getpid(),
        started_at=time.time() - 5,
        log=str(runner["launcher"].home / updaterun.LOG_NAME),
    )
    runner["launcher"].raise_with = updaterun.Busy(live)
    response = runner["client"].post("/api/update/run", headers=ui_post(TEST_HOST))
    assert response.status_code == 409
    body = response.get_json()
    assert body["code"] == "busy"
    assert updaterun.LOG_NAME in body["hint"], body["hint"]
    assert body["run"]["log"]


def test_대장에_없는_인스턴스는_거절하고_CLI_를_안내한다(runner):
    """⭐ 여기서 갱신하면 **옛 코드로 새 정적 파일을 서빙하는 섞인 상태**가 된다."""
    runner["launcher"].raise_with = updaterun.Unmanaged("힌트: python -m gitwire_chat update")
    response = runner["client"].post("/api/update/run", headers=ui_post(TEST_HOST))
    assert response.status_code == 409
    body = response.get_json()
    assert body["code"] == "unmanaged"
    assert "python -m gitwire_chat update" in body["hint"]


def test_띄우지_못하면_사유와_힌트를_준다(runner):
    runner["launcher"].raise_with = updaterun.LaunchError("띄우지 못했다", hint="직접: …")
    response = runner["client"].post("/api/update/run", headers=ui_post(TEST_HOST))
    assert response.status_code == 500
    assert response.get_json()["code"] == "launch"


def test_출처를_모르면_실행도_사유와_힌트를_준다(runner, monkeypatch):
    monkeypatch.setattr(updater, "_direct_url", lambda: None)
    response = runner["client"].post("/api/update/run", headers=ui_post(TEST_HOST))
    assert response.status_code == 400
    body = response.get_json()
    assert body["code"] == "source" and "pip install" in body["hint"]
    assert runner["launcher"].launches == 0


# ================================================ 실행기 (updaterun.py)


class SlowChild:
    """끝나는 시점을 **테스트가 쥔** 자식 프로세스 대역."""

    def __init__(self, pid: int = 123456) -> None:
        self.pid = pid
        self._done = threading.Event()

    def poll(self):
        return 0 if self._done.is_set() else None

    def wait(self):
        self._done.wait(timeout=10)
        return 0

    def finish(self) -> None:
        self._done.set()


class StubLauncher(updaterun.Launcher):
    """OS 실행 한 걸음만 갈아끼운 실행기 — 자물쇠·문지기는 **진짜**다."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.spawned: list[list[str]] = []
        self.children: list[SlowChild] = []

    def _spawn(self, argv, run):
        self.spawned.append(list(argv))
        child = SlowChild(pid=123456 + len(self.children))
        self.children.append(child)
        return child


def me_in_registry(directory: Path, port: int = 8899) -> runstate.Instance:
    """이 프로세스를 대장에 적는다 — 화면 갱신의 전제 조건."""
    instance = sample(port, pid=os.getpid())
    runstate.record(instance, directory=directory)
    return instance


def test_대장에_없으면_아예_띄우지_않는다(tmp_path):
    """⭐ 조용한 고장(섞인 상태)을 만들 수 있는 경우를 **거절**한다."""
    launcher = StubLauncher(home=tmp_path / "chats", directory=tmp_path / "run")
    with pytest.raises(updaterun.Unmanaged) as caught:
        launcher.launch()
    assert "python -m gitwire_chat update" in caught.value.hint
    assert launcher.spawned == []
    assert not launcher.lock_path.exists(), "거절했는데 자물쇠가 남았다"


def test_두_번_눌러도_두_탭에서_눌러도_한_번만_돈다(tmp_path):
    """⭐ 같은 서버 안에서는 추측하지 않는다 — 자식에게 물어본다."""
    run_dir = tmp_path / "run"
    me_in_registry(run_dir)
    launcher = StubLauncher(home=tmp_path / "chats", directory=run_dir)

    first = launcher.launch()
    assert launcher.spawned, "안 띄웠다"
    assert launcher.current() is not None
    with pytest.raises(updaterun.Busy) as caught:
        launcher.launch()                      # 두 번째 누름 / 다른 탭
    assert caught.value.run.started_at == first.started_at
    assert updaterun.LOG_NAME in caught.value.hint
    assert len(launcher.spawned) == 1, "두 번 띄웠다"

    # 끝나면 자물쇠가 풀리고 다시 띄울 수 있다.
    launcher.children[0].finish()
    for _ in range(100):
        if not launcher.lock_path.exists():
            break
        time.sleep(0.05)
    assert not launcher.lock_path.exists(), "자식이 끝났는데 자물쇠가 남았다"
    launcher.launch()
    assert len(launcher.spawned) == 2


def test_다른_서버가_방금_띄운_갱신이면_거절한다(tmp_path):
    """다른 포트의 인스턴스가 눌렀을 때 — 자물쇠 파일 한 장으로 갈린다."""
    run_dir = tmp_path / "run"
    me_in_registry(run_dir)
    launcher = StubLauncher(home=tmp_path / "chats", directory=run_dir)
    launcher.lock_path.parent.mkdir(parents=True, exist_ok=True)
    launcher._write_lock(
        updaterun.Run(
            launcher_pid=os.getpid() + 1,       # 남의 서버
            started_at=time.time(),
            log=str(tmp_path / "없는파일.log"),
        )
    )
    with pytest.raises(updaterun.Busy):
        launcher.launch()
    assert launcher.spawned == []


def test_로그가_조용해진_남의_자물쇠는_이어받는다(tmp_path):
    """⚠️ 자물쇠를 풀 주체가 사라질 수 있다 — 그 한계를 이렇게 메운다.

    갱신은 자물쇠를 만든 서버를 죽인다. 그래서 남의 자물쇠는 **로그가 얼마나
    조용한가**로 판정한다 (근거·한계는 `updaterun.py` 모듈 도크).
    """
    run_dir = tmp_path / "run"
    me_in_registry(run_dir)
    launcher = StubLauncher(home=tmp_path / "chats", directory=run_dir)
    quiet = tmp_path / "quiet.log"
    quiet.write_text("옛 갱신 기록\n", encoding="utf-8")
    old = time.time() - updaterun.IDLE_LIMIT - 60
    os.utime(quiet, (old, old))
    launcher.lock_path.parent.mkdir(parents=True, exist_ok=True)
    launcher._write_lock(
        updaterun.Run(launcher_pid=os.getpid() + 1, started_at=old, log=str(quiet))
    )
    assert launcher.current() is None
    launcher.launch()                          # 이어받는다
    assert len(launcher.spawned) == 1


def test_아주_오래된_자물쇠는_로그가_자라고_있어도_버린다(tmp_path):
    run_dir = tmp_path / "run"
    me_in_registry(run_dir)
    launcher = StubLauncher(home=tmp_path / "chats", directory=run_dir)
    fresh = tmp_path / "fresh.log"
    fresh.write_text("지금도 자란다\n", encoding="utf-8")
    launcher.lock_path.parent.mkdir(parents=True, exist_ok=True)
    launcher._write_lock(
        updaterun.Run(
            launcher_pid=os.getpid() + 1,
            started_at=time.time() - updaterun.HARD_LIMIT - 1,
            log=str(fresh),
        )
    )
    assert launcher.current() is None


def test_망가진_자물쇠는_없는_것으로_본다(tmp_path):
    run_dir = tmp_path / "run"
    me_in_registry(run_dir)
    launcher = StubLauncher(home=tmp_path / "chats", directory=run_dir)
    launcher.lock_path.parent.mkdir(parents=True, exist_ok=True)
    launcher.lock_path.write_text("{망가짐", encoding="utf-8")
    assert launcher.current() is None
    launcher.launch()
    assert len(launcher.spawned) == 1


def test_자물쇠_파일은_UTF8_이고_LF_다(tmp_path):
    run_dir = tmp_path / "run"
    me_in_registry(run_dir)
    launcher = StubLauncher(home=tmp_path / "chats", directory=run_dir)
    launcher.launch()
    raw = launcher.lock_path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw
    raw.decode("utf-8")


def test_띄우는_명령은_모듈로_부르고_대장을_명시한다(tmp_path):
    """⚠️ 콘솔 스크립트로 부르면 Windows 에서 pip 가 그 파일을 잠근다.

    그리고 ``--dir`` 을 해석된 값으로 박는다 — 대장이 어긋나면 갱신이 이 앱을
    못 찾아서 **정확히 우리가 막으려는 고장**(섞인 상태)이 된다.
    """
    run_dir = tmp_path / "run"
    launcher = updaterun.Launcher(home=tmp_path / "chats", directory=run_dir)
    argv = launcher.command()
    assert argv[0] == sys.executable
    assert argv[1:4] == ["-m", "gitwire_chat", "update"]
    assert argv[4:6] == ["--dir", str(run_dir)]
    assert "gitwire-chat" not in Path(argv[0]).name.lower()


def test_실제로_갱신_CLI_를_띄우고_기록을_남기고_자물쇠를_푼다(tmp_path, monkeypatch):
    """⭐ 흉내가 아니다 — **진짜 프로세스**를 띄운다.

    `--dry-run` 으로 띄우므로 아무것도 멈추지 않고 아무것도 설치하지 않는다
    (사용자 인스턴스도 안전하다 — dry-run 은 다른 포트를 *읽어만* 본다).
    보는 것은 이 파일이 책임지는 그 셋이다: **띄웠나 · 출력이 파일로 갔나 ·
    끝난 뒤 자물쇠가 풀렸나.**
    """
    run_dir = tmp_path / "run"
    me_in_registry(run_dir)
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    launcher = updaterun.Launcher(
        home=tmp_path / "chats", directory=run_dir, extra_args=("--dry-run",)
    )
    (tmp_path / "chats").mkdir(parents=True, exist_ok=True)
    run = launcher.launch()
    assert run.pid > 0
    assert launcher.lock_path.exists()

    deadline = time.monotonic() + BOOT_TIMEOUT
    while time.monotonic() < deadline and launcher.lock_path.exists():
        time.sleep(0.1)
    assert not launcher.lock_path.exists(), "자식이 끝났는데 자물쇠가 남았다"

    text = Path(run.log).read_text(encoding="utf-8", errors="replace")
    assert updaterun.RUN_MARK in text, text
    assert "--dry-run" in text, text
    # CLI 가 **무엇이든 말했다** — 출력을 버리지 않았다는 증거.
    lines = [line for line in text.strip().splitlines() if line.strip()]
    assert len(lines) >= 3, text


# =========================================================== 재기동 로그


def test_로그_꼬리는_이번_재기동_시도부터_보여준다(tmp_path):
    """⚠️ 로그를 이어 쓰므로 지난 재기동의 **정상** 출력이 꼬리를 채운다 (실측).

    그러면 "안 떴다"는 보고에 잘 뜬 로그가 붙어서 사람이 엉뚱한 줄을 읽는다.
    """
    log = tmp_path / "restart.log"
    log.write_text(
        "지난 실행 · 잘 떴다\n * Running on http://127.0.0.1:8791\n"
        f"\n{updater.RESTART_MARK} 2026-01-01 00:00:00\n"
        "gitwire-chat: error: unrecognized arguments: --없는옵션\n",
        encoding="utf-8",
    )
    tail = updater.log_tail(log)
    assert any("unrecognized arguments" in line for line in tail)
    assert not any("잘 떴다" in line for line in tail), "지난 실행이 섞였다"


def test_경계선이_없는_로그도_꼬리는_준다(tmp_path):
    """아무것도 안 주는 것보다 마지막 몇 줄이라도 주는 것이 낫다."""
    log = tmp_path / "restart.log"
    log.write_text("옛 형식 로그\n터졌다\n", encoding="utf-8")
    assert updater.log_tail(log) == ["옛 형식 로그", "터졌다"]


def test_없는_로그는_빈_목록이다(tmp_path):
    assert updater.log_tail(tmp_path / "없다.log") == []


# ================================================== ⭐ 진짜 프로세스 왕복


def spawn_app(port: int, home: Path, registry: Path, log: Path) -> subprocess.Popen:
    """실제 앱을 별도 프로세스로 띄운다 — 사용자 인스턴스(8770)가 아니다."""
    handle = open(log, "w", encoding="utf-8", errors="replace", newline="\n")
    env = dict(os.environ)
    env[runstate.ENV_DIR] = str(registry)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "gitwire_chat",
            "--port", str(port), "--home", str(home),
            "--author", "왕복테스트", "--no-notify",
        ],
        stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
    )
    proc._log_handle = handle          # 닫을 때까지 살려 둔다
    return proc


def test_서버가_스스로를_대장에_적고_그것으로_멈추고_다시_띄운다(tmp_path):
    """⭐ 흉내가 아니다 — 실제 프로세스를 띄우고, 멈추고, 다시 띄운다.

    이것이 `update` 의 ②·④ 걸음 그 자체다. 중간의 pip 만 빠져 있다(그 왕복은
    별도 venv 로 손으로 실증한다 — README 참조).

    같이 확인되는 것: 서버가 **자기 자신을** 정확히 적는가(포트·해석된 home·
    인자), 대장에 적힌 명령으로 다시 띄우면 **같은 앱**이 뜨는가.
    """
    port = free_port()
    registry = tmp_path / "registry"
    home = tmp_path / "chats"
    log = tmp_path / "app.log"
    proc = spawn_app(port, home, registry, log)
    try:
        seen = runstate.wait_until_up(port, timeout=BOOT_TIMEOUT)
        assert seen is not None, f"앱이 뜨지 않았다:\n{log.read_text(encoding='utf-8')}"

        instance = runstate.load(port, directory=registry)
        assert instance is not None, "서버가 자기를 대장에 적지 않았다"
        assert instance.port == port
        assert instance.pid == proc.pid == seen["pid"]
        assert Path(instance.home) == home.resolve()
        assert "--no-notify" in instance.args
        assert runstate.alive(instance)

        # 포트를 남이 물려받은 경우를 가르나 (pid 까지 맞춰 본다).
        impostor = dataclasses.replace(instance, pid=instance.pid + 100000)
        assert not runstate.alive(impostor)

        # --- 멈춘다 ---------------------------------------------------
        report = updater.Report()
        assert updater.stop_instance(instance, report, timeout=BOOT_TIMEOUT), report.lines
        assert runstate.probe(port, timeout=1.0) is None
        proc.wait(timeout=30)

        # --- 대장에 적힌 그대로 다시 띄운다 ----------------------------
        # ⚠️ `directory=` 를 반드시 준다. 안 주면 다시 뜬 앱이 **다른 대장**
        # (환경변수가 가리키는 guard-run)에 자기를 적어서, 아래 `finally` 가
        # 옛 pid 를 죽이고 새 프로세스를 **놓친다** — 실측된 누수였다(테스트를
        # 돌린 만큼 서버 프로세스가 머신에 쌓였다). 프로덕션 경로
        # (`updater.update`)는 언제나 이 인자를 넘긴다.
        report = updater.Report()
        assert updater.start_instance(instance, report, directory=registry), report.lines
        again = runstate.wait_until_up(port, timeout=BOOT_TIMEOUT)
        assert again is not None, "\n".join(report.lines)
        assert again["pid"] != instance.pid, "새 프로세스여야 한다"
        # 같은 앱인가 — 같은 상태 디렉토리를 본다.
        new_instance = runstate.load(port, directory=registry)
        assert new_instance is not None
        assert Path(new_instance.home) == home.resolve()
        assert new_instance.author == "왕복테스트"
    finally:
        # 이 테스트가 만든 프로세스를 **하나도 남기지 않는다.** 다시 띄운 것은
        # 대장에서(= 그 새 pid 로), 처음 것은 Popen 핸들로 각각 잡는다.
        candidate = runstate.load(port, directory=registry)
        if candidate is not None:
            try:
                os.kill(candidate.pid, signal.SIGTERM)
            except OSError:
                pass
        if proc.poll() is None:
            proc.kill()
        proc._log_handle.close()
        # 다시 띄운 프로세스가 완전히 사라질 때까지 (파일 잠금 방지)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and runstate.probe(port, timeout=0.5):
            time.sleep(0.3)
        # 조용히 새지 않는다 — 안 죽었으면 그 사실이 드러나야 한다.
        assert runstate.probe(port, timeout=1.0) is None, (
            f"테스트가 띄운 앱이 포트 {port} 에 살아 있다 — 프로세스가 샌다"
        )


# ============================== ⭐ git 에서 오는 의존 (gitwire) 동반 갱신


def companion(
    *,
    name: str = "gitwire",
    url: str = "https://github.com/yunhyuk-choi/gitwire.git",
    installed: str | None = "c" * 40,
    remote: str = "d" * 40,
    revision: str = "",
    installed_url: str | None = None,
) -> updater.Companion:
    """의존 하나 — 선언된 곳 · 설치된 커밋 · 원격 최신."""
    return updater.Companion(
        declared=updater.Source(url=url, revision=revision, name=name),
        installed=None if installed is None else updater.Source(
            url=installed_url or url, commit=installed, name=name
        ),
        remote=remote,
        version="0.2.0",
    )


def test_선언된_git_의존을_메타데이터에서_읽는다(monkeypatch):
    """⭐ URL 을 코드에 박지 않는다 — `pyproject.toml` → wheel METADATA 가 원천.

    박으면 레포를 옮길 때 고칠 곳이 둘이 되고, 한쪽만 고치면 조용히 옛 곳을
    본다. 설치본에는 `pyproject.toml` 이 없지만 METADATA 는 있다.
    """
    class FakeDist:
        requires = [
            "gitwire @ git+https://github.com/yunhyuk-choi/gitwire.git",
            "flask>=3",
            "pytest>=7; extra == \"dev\"",
            "other @ git+ssh://git@example.invalid/x.git@release",
            "wheel @ https://example.invalid/wheel.whl",   # git 이 아니다
        ]

    monkeypatch.setattr(
        "importlib.metadata.distribution", lambda name: FakeDist()
    )
    found = updater.declared_git_deps()
    assert [d.name for d in found] == ["gitwire", "other"]
    assert found[0].url == "https://github.com/yunhyuk-choi/gitwire.git"
    assert found[0].revision == "" and found[0].ref == "HEAD"
    # ⚠️ `@` 는 URL 안(user@host)에도 있다 — 마지막 `/` 뒤에서만 ref 를 가른다.
    assert found[1].url == "ssh://git@example.invalid/x.git"
    assert found[1].revision == "release"
    assert found[1].requirement == "other @ git+ssh://git@example.invalid/x.git@release"


def test_git_이_아닌_의존은_판정_불가라고_말한다():
    """조용히 "최신이다"로 넘기지 않는다 — 모르는 것은 모른다고 말한다."""
    dep = companion(installed=None)
    assert dep.behind is False
    assert "git 설치가 아니다" in dep.unknown
    blind = companion(remote="")
    assert blind.behind is False
    assert "원격을 읽지 못했다" in blind.unknown


def test_의존만_밀렸어도_갱신이_필요하다고_판정한다(no_network, tmp_path):
    """⭐ 정확히 사용자에게 일어난 그 상황 — 앱은 최신, 전송 계층은 밀림.

    지금까지는 앱 커밋만 보고 "최신이다"로 끝냈다. 그러면 그 라이브러리에서
    이미 고친 버그를 사용자가 계속 겪는다 (실측: 콘솔 창 깜빡임 수정).
    """
    no_network["remote"] = no_network["installed"]      # 앱은 최신
    no_network["deps"] = [companion()]                  # gitwire 는 밀렸다
    report = updater.update(directory=tmp_path, stop_timeout=1.0)
    assert report.ok and report.changed
    assert no_network["pip"] == [
        "gitwire @ git+https://github.com/yunhyuk-choi/gitwire.git"
    ], "gitwire 만 설치해야 한다 (앱은 최신이라 다시 설치하지 않는다)"
    text = "\n".join(report.lines)
    assert "최신이다 — 바뀔 것이 없다" not in text
    assert "라이브러리가 밀렸다" in text


def test_둘_다_최신이면_아무것도_하지_않는다(no_network, tmp_path):
    no_network["remote"] = no_network["installed"]
    no_network["deps"] = [companion(installed="d" * 40)]
    report = updater.update(directory=tmp_path, stop_timeout=1.0)
    assert report.ok and not report.changed
    assert no_network["pip"] == []
    text = "\n".join(report.lines)
    assert "최신이다 — 바뀔 것이 없다 (의존까지 봤다)" in text
    assert "[최신]" in text, "무엇을 보고 최신이라 했는지 드러나야 한다"


def test_앱만_밀렸으면_앱만_설치한다(no_network, tmp_path):
    no_network["deps"] = [companion(installed="d" * 40)]     # 의존은 최신
    report = updater.update(directory=tmp_path, stop_timeout=1.0)
    assert report.ok and report.changed
    assert no_network["pip"] == ["gitwire-chat @ git+https://github.com/yunhyuk-choi/gitwire-chat.git"]


def test_둘_다_밀렸으면_둘_다_설치하고_보고에_드러난다(no_network, tmp_path):
    no_network["deps"] = [companion()]
    report = updater.update(directory=tmp_path, stop_timeout=1.0)
    assert report.ok and report.changed
    assert no_network["pip"] == [
        "gitwire-chat @ git+https://github.com/yunhyuk-choi/gitwire-chat.git",
        "gitwire @ git+https://github.com/yunhyuk-choi/gitwire.git",
    ], "앱을 먼저, 그다음 의존"
    text = "\n".join(report.lines)
    assert "gitwire" in text and "cccccccccccc" in text, "의존의 before 가 안 보인다"


def test_의존_설치가_실패하면_섞인_상태라고_말하고_되돌리기를_준다(
    no_network, tmp_path, monkeypatch
):
    """⚠️ 한쪽만 되돌리면 섞인 상태가 남는다 — 두 명령을 함께 준다."""
    no_network["deps"] = [companion()]

    def flaky(requirement, report, **kw):
        no_network["pip"].append(requirement)
        if requirement.startswith("gitwire "):
            report.fail("pip 이 1 로 끝났다 (테스트)")
            return False
        return True

    monkeypatch.setattr(updater, "pip_install", flaky)
    monkeypatch.setattr(updater, "stop_instance", lambda inst, report, **kw: True)
    restarted: list[int] = []
    monkeypatch.setattr(
        updater,
        "start_instance",
        lambda inst, report, **kw: restarted.append(inst.port) or True,
    )
    monkeypatch.setattr(runstate, "alive", lambda inst, **kw: True)
    monkeypatch.setattr(runstate, "wait_until_up", lambda port, **kw: {"pid": 1})
    runstate.record(sample(8899), directory=tmp_path)

    report = updater.update(directory=tmp_path)
    assert not report.ok
    text = "\n".join(report.lines)
    assert "섞인 상태" in text
    assert "gitwire-chat @ git+https://github.com/yunhyuk-choi/gitwire-chat.git@" + "a" * 40 in text
    assert "갈아치우지 못했으므로 그대로다" in text
    assert restarted == [8899], "멈춘 것은 되살려야 한다"


def test_다시_뜨지_않으면_의존까지_되돌리는_명령을_준다(
    no_network, tmp_path, monkeypatch
):
    """갱신은 됐지만 앱이 안 뜬 경우 — 되돌릴 범위가 **두 패키지**다."""
    no_network["deps"] = [companion()]
    monkeypatch.setattr(updater, "stop_instance", lambda inst, report, **kw: True)
    monkeypatch.setattr(updater, "start_instance", lambda inst, report, **kw: True)
    monkeypatch.setattr(runstate, "alive", lambda inst, **kw: True)
    monkeypatch.setattr(runstate, "wait_until_up", lambda port, **kw: None)
    monkeypatch.setattr(
        updater, "_installed_dep", lambda name: (companion().installed, "0.2.0")
    )
    home = tmp_path / "chats"
    home.mkdir()
    runstate.record(sample(8899, home=str(home)), directory=tmp_path)

    report = updater.update(directory=tmp_path)
    assert not report.ok
    text = "\n".join(report.lines)
    assert "되돌리는 방법" in text
    assert "gitwire-chat @ git+https://github.com/yunhyuk-choi/gitwire-chat.git@" + "a" * 40 in text
    assert "gitwire @ git+https://github.com/yunhyuk-choi/gitwire.git@" + "c" * 40 in text


def test_설치_결과를_pip_기록으로_다시_읽어_확인한다(no_network, tmp_path, monkeypatch):
    """⭐ "설치했다"는 우리 주장이 아니라 `direct_url.json` 이 증거다."""
    no_network["deps"] = [companion()]
    monkeypatch.setattr(updater, "stop_instance", lambda inst, report, **kw: True)
    # pip 는 성공했다고 하지만 실제로 들어간 커밋은 옛 것이다 (조용한 실패).
    monkeypatch.setattr(
        updater, "_installed_dep", lambda name: (companion().installed, "0.2.0")
    )
    report = updater.update(directory=tmp_path, stop_timeout=1.0)
    assert not report.ok, "설치 결과가 원격과 다른데 성공으로 보고했다"
    assert any("원격과 다르다" in line for line in report.lines)


def test_확인_응답에_의존_상태가_실린다(no_network, monkeypatch):
    """화면(`update.js`)이 무엇이 왜 갱신되는지 말할 수 있어야 한다."""
    no_network["remote"] = no_network["installed"]
    no_network["deps"] = [companion()]
    found = updater.check()
    assert found.behind is True and found.self_behind is False
    data = found.to_json()
    assert data["behind"] is True and data["self_behind"] is False
    assert data["deps"][0]["name"] == "gitwire"
    assert data["deps"][0]["behind"] is True
    assert data["deps"][0]["installed"] == "c" * 40
    assert data["deps"][0]["remote"] == "d" * 40

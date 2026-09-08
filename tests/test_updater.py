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
import time
from pathlib import Path

import pytest

from gitwire_chat import autostart, runstate, updater

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
    state = {"remote": "b" * 40, "installed": "a" * 40, "pip": [], "unmanaged": []}
    monkeypatch.setattr(
        updater,
        "_direct_url",
        lambda: direct_url(vcs_info={"vcs": "git", "commit_id": state["installed"]}),
    )
    monkeypatch.setattr(updater, "remote_commit", lambda url, ref="HEAD": state["remote"])
    monkeypatch.setattr(
        updater,
        "pip_install",
        lambda req, report, **kw: state["pip"].append(req) or True,
    )
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


def test_앱을_갱신하는_엔드포인트는_없다(client):
    """⭐ 루프백·인증 없음 설계에서 원격 실행 표면을 만들지 않는다.

    브라우저로 아무 페이지나 열어 둔 상태에서 그 페이지가 폼 전송 하나로 우리
    앱을 재설치·재기동시킬 수 있게 되면, 막을 방법이 없다(인증이 없다).
    """
    rules = sorted(str(rule) for rule in client.application.url_map.iter_rules())
    update_rules = [r for r in rules if "update" in r]
    assert update_rules == ["/api/update/check"], update_rules


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
        report = updater.Report()
        assert updater.start_instance(instance, report), report.lines
        again = runstate.wait_until_up(port, timeout=BOOT_TIMEOUT)
        assert again is not None, "\n".join(report.lines)
        assert again["pid"] != instance.pid, "새 프로세스여야 한다"
        # 같은 앱인가 — 같은 상태 디렉토리를 본다.
        new_instance = runstate.load(port, directory=registry)
        assert new_instance is not None
        assert Path(new_instance.home) == home.resolve()
        assert new_instance.author == "왕복테스트"
    finally:
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

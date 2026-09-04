"""로그인 시 자동 시작 — 등록·해제·상태.

이 기능은 **사용자 환경을 바꾼다.** 그래서 테스트가 지켜야 할 것이 하나 더
있다: *실제 시작 폴더·LaunchAgents·systemd 디렉토리를 절대 건드리지 않는다.*
모든 테스트가 ``directory=tmp_path`` 주입점을 쓰고, 혹시라도 빠뜨렸을 때를
대비해 아래 autouse 픽스처가 환경변수로 한 번 더 막는다.
"""

from __future__ import annotations

import configparser
import plistlib
import sys
from pathlib import Path

import pytest

from gitwire_chat import autostart
from gitwire_chat.__main__ import main

HOST = autostart.host_platform()
OTHERS = [p for p in autostart.PLATFORMS if p != HOST]


@pytest.fixture(autouse=True)
def _never_touch_real_startup(monkeypatch, tmp_path):
    """빠뜨린 테스트가 있어도 진짜 시작 폴더로 새지 않게 한다."""
    guard = tmp_path / "guard-startup"
    guard.mkdir()
    monkeypatch.setenv(autostart.ENV_DIR, str(guard))


def _backend(tmp_path, platform=HOST, **kwargs):
    options = kwargs.pop("options", None) or autostart.ServeOptions(port=8899)
    return autostart.make_backend(
        platform,
        options,
        directory=kwargs.pop("directory", tmp_path / "reg"),
        log_path=kwargs.pop("log_path", str(tmp_path / "logs" / "autostart.log")),
        **kwargs,
    )


# ------------------------------------------------------------------ 렌더링


@pytest.mark.parametrize("platform", autostart.PLATFORMS)
def test_세_OS_모두_렌더되고_표식이_들어간다(platform, tmp_path):
    body = _backend(tmp_path, platform).render()
    assert autostart.MARKER_LINE in body
    assert "-m gitwire_chat" in body
    assert "8899" in body


@pytest.mark.parametrize("platform", autostart.PLATFORMS)
def test_인터프리터는_절대경로로_박힌다(platform, tmp_path):
    """로그인 셸의 PATH 를 믿지 않는다 — 그냥 ``python`` 이면 안 된다."""
    spec = _backend(tmp_path, platform).spec
    assert Path(spec.executable).is_absolute() or spec.executable.startswith("/")
    assert spec.executable not in ("python", "python3", "pythonw")
    if platform == HOST:
        # 지금 이 인터프리터(또는 그 옆의 콘솔 없는 짝)여야 한다.
        assert Path(spec.executable).parent == Path(sys.executable).parent


@pytest.mark.parametrize("platform", autostart.PLATFORMS)
def test_등록시점_옵션이_파일에_그대로_들어간다(platform, tmp_path):
    options = autostart.ServeOptions(
        port=9123,
        home=str(tmp_path / "상태"),
        author="앨리스",
        poll_interval=7.5,
        notifications=False,
    )
    body = _backend(tmp_path, platform, options=options).render()
    for token in ("--port", "9123", "--home", "--author", "--poll-interval", "7.5", "--no-notify"):
        assert token in body, token


@pytest.mark.parametrize("platform", autostart.PLATFORMS)
def test_로그_경로가_등록_파일_안에_있다(platform, tmp_path):
    backend = _backend(tmp_path, platform)
    assert backend.spec.log_path in backend.render()


#: 렌더링만 보는 테스트가 쓰는 합성 경로. pytest 의 tmp_path 는 **테스트 이름에서
#: 만들어지므로 한글이 섞인다** — "기본은 ASCII" 를 확인하려면 경로를 우리가 정해야 한다.
ASCII_LOG = "C:\\ProgramData\\gitwire-chat\\logs\\autostart.log"
KOREAN_LOG = "C:\\사용자\\로그\\autostart.log"


def test_windows_cmd_는_CRLF에_BOM이_없다(tmp_path):
    backend = _backend(tmp_path, "windows", log_path=ASCII_LOG)
    raw = backend.render().encode(backend.encoding)
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_windows_cmd_는_기본이_순수_ASCII다(tmp_path):
    """cmd.exe 는 배치 파일을 콘솔 OEM 코드페이지로 읽는다 — 한글을 넣지 않는다."""
    body = _backend(tmp_path, "windows", log_path=ASCII_LOG).render()
    assert body.isascii()
    assert "chcp" not in body


def test_경로에_비ASCII가_있으면_chcp로_UTF8을_선언한다(tmp_path):
    backend = _backend(tmp_path, "windows", log_path=KOREAN_LOG)
    body = backend.render()
    assert not body.isascii()
    lines = body.split("\r\n")
    assert lines[0] == "@echo off"
    assert lines[1] == "chcp 65001 > nul"   # 첫 줄이 ASCII 라 바이트 오프셋이 어긋나지 않는다


def test_plist는_진짜_plist로_파싱된다(tmp_path):
    body = _backend(tmp_path, "macos").render()
    data = plistlib.loads(body.encode("utf-8"))
    assert data["Label"] == autostart.LAUNCHD_LABEL
    assert data["RunAtLoad"] is True
    assert data["ProgramArguments"][1:3] == ["-m", "gitwire_chat"]
    assert data["StandardOutPath"] == data["StandardErrorPath"]


def test_plist는_XML_이스케이프를_한다(tmp_path):
    backend = _backend(tmp_path, "macos", options=autostart.ServeOptions(author="A & B <c>"))
    data = plistlib.loads(backend.render().encode("utf-8"))
    assert "A & B <c>" in data["ProgramArguments"]


def test_systemd_유닛은_진짜_유닛으로_파싱된다(tmp_path):
    body = _backend(tmp_path, "linux").render()
    parser = configparser.ConfigParser(strict=False)
    parser.optionxform = str
    parser.read_string(body)
    assert parser["Service"]["ExecStart"].startswith(("/", '"'))
    assert "-m gitwire_chat" in parser["Service"]["ExecStart"]
    assert parser["Install"]["WantedBy"] == "default.target"


def test_systemd_유닛은_공백있는_경로를_따옴표로_감싼다(tmp_path):
    backend = _backend(tmp_path, "linux", python="/opt/my apps/python3")
    exec_start = configparser.ConfigParser(strict=False)
    exec_start.optionxform = str
    exec_start.read_string(backend.render())
    assert exec_start["Service"]["ExecStart"].startswith('"/opt/my apps/python3"')


# --------------------------------------------------------------- 왕복·멱등


def test_설치_상태_해제_왕복(tmp_path):
    backend = _backend(tmp_path)
    assert backend.status().installed is False

    report = backend.install()
    assert report.ok and report.changed and report.installed
    assert Path(backend.path).is_file()

    status = backend.status()
    assert status.installed
    assert backend.path in "\n".join(status.lines)          # 경로를 보여준다
    assert backend.spec.log_path in "\n".join(status.lines)  # 로그 경로도

    removed = backend.uninstall()
    assert removed.ok and removed.changed
    assert not Path(backend.path).exists()
    assert backend.status().installed is False


def test_설치는_멱등이다(tmp_path):
    backend = _backend(tmp_path)
    first = backend.install()
    second = backend.install()
    assert first.changed is True
    assert second.changed is False          # 두 번째는 쓰지 않는다
    assert second.installed is True
    assert "이미" in "\n".join(second.lines)
    assert len(list(Path(backend.directory).iterdir()) ) == 1   # 파일은 여전히 하나


def test_옵션이_바뀌면_갱신한다(tmp_path):
    a = _backend(tmp_path, options=autostart.ServeOptions(port=8801))
    b = _backend(tmp_path, options=autostart.ServeOptions(port=8802))
    a.install()
    report = b.install()
    assert report.changed is True
    assert "8802" in Path(b.path).read_text(encoding="utf-8")
    assert len(list(Path(b.directory).iterdir())) == 1


def test_해제는_멱등이다(tmp_path):
    backend = _backend(tmp_path)
    backend.install()
    assert backend.uninstall().changed is True
    second = backend.uninstall()
    assert second.ok is True                # 없다고 실패하지 않는다
    assert second.changed is False
    assert "지울 것이 없다" in "\n".join(second.lines)


def test_상태는_등록된_내용이_지금_옵션과_다르면_말해준다(tmp_path):
    _backend(tmp_path, options=autostart.ServeOptions(port=8801)).install()
    other = _backend(tmp_path, options=autostart.ServeOptions(port=8802))
    assert "다르다" in "\n".join(other.status().lines)


# ------------------------------------------------------------------ 안전장치


def test_dry_run은_아무것도_쓰지_않고_내용을_보여준다(tmp_path):
    backend = _backend(tmp_path)
    report = backend.install(dry_run=True)
    assert report.ok and report.changed is False
    assert not Path(backend.path).exists()
    text = "\n".join(report.lines)
    assert backend.path in text
    assert autostart.MARKER_LINE in text     # 쓸 내용 전문이 그대로 보인다
    assert "dry-run" in text


def test_dry_run_해제도_지우지_않는다(tmp_path):
    backend = _backend(tmp_path)
    backend.install()
    report = backend.uninstall(dry_run=True)
    assert report.changed is False
    assert Path(backend.path).is_file()


def test_남의_파일은_force_없이_덮어쓰지_않는다(tmp_path):
    backend = _backend(tmp_path)
    path = Path(backend.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("남이 만든 것\n", encoding="utf-8")

    report = backend.install()
    assert report.ok is False
    assert path.read_text(encoding="utf-8") == "남이 만든 것\n"

    forced = backend.install(force=True)
    assert forced.ok and forced.changed
    assert autostart.MARKER in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("platform", OTHERS)
def test_다른_OS는_dry_run_미리보기만_되고_실제_등록은_거부한다(platform, tmp_path):
    backend = _backend(tmp_path, platform)
    assert backend.install(dry_run=True).ok is True
    report = backend.install()
    assert report.ok is False
    assert "미리보기" in "\n".join(report.lines)
    assert not Path(backend.path).exists()


def test_systemd가_없으면_명확히_실패하고_수동_방법을_안내한다(monkeypatch, tmp_path):
    """조용히 다른 수단으로 넘어가지 않는다 — 등록됐다고 믿게 하는 것이 최악이다."""
    monkeypatch.setattr(autostart.shutil, "which", lambda name: None)
    backend = autostart.make_backend("linux", autostart.ServeOptions(port=8899))
    assert backend.custom_dir is False       # 표준 위치를 대상으로 한 판정
    report = autostart.Report()
    assert backend.preflight(report) is False
    text = "\n".join(report.lines)
    assert "systemd" in text
    assert "시작 프로그램" in text            # 수동 방법 안내가 붙는다


@pytest.mark.parametrize("platform", autostart.PLATFORMS)
def test_토큰_값은_등록_파일에_새지_않는다(platform, tmp_path, monkeypatch):
    """이 앱은 토큰 **값**을 다루지 않는다. 등록 파일에도 당연히 없어야 한다."""
    monkeypatch.setenv("GITWIRE_TOKEN", "ghp_비밀값123")
    body = _backend(tmp_path, platform).render()
    assert "ghp_비밀값123" not in body
    assert "GITWIRE_TOKEN" not in body


def test_windows는_콘솔없는_인터프리터를_고른다(tmp_path):
    """pythonw.exe 가 있으면 그걸 쓰고, 없으면 폴백하되 그 사실을 알린다."""
    exe, console_free = autostart._windows_python(str(tmp_path / "python.exe"))
    assert console_free is False and exe.endswith("python.exe")

    (tmp_path / "pythonw.exe").write_bytes(b"")
    exe, console_free = autostart._windows_python(str(tmp_path / "python.exe"))
    assert console_free is True and exe.endswith("pythonw.exe")

    backend = _backend(tmp_path, "windows", python=str(tmp_path / "python.exe"))
    assert "pythonw.exe" in backend.render()


def test_pythonw가_없으면_폴백을_보고한다(tmp_path):
    backend = _backend(tmp_path, "windows", python=str(tmp_path / "없는" / "python.exe"))
    assert backend.spec.console_free is False
    assert "pythonw.exe 를 찾지 못했다" in "\n".join(backend.install(dry_run=True).lines)
    assert "/min" in backend.render()   # 최소한 창을 최소화해 띄운다


# ---------------------------------------------------------------------- CLI


def _cli(capsys, *args) -> tuple[int, str]:
    code = main(["autostart", *args])
    return code, capsys.readouterr().out


def test_CLI_왕복(capsys, tmp_path):
    reg = str(tmp_path / "reg")
    log = str(tmp_path / "logs" / "autostart.log")
    common = ["--dir", reg, "--log-file", log, "--port", "8899"]

    code, out = _cli(capsys, "status", *common)
    assert code == 0 and "등록되지 않음" in out

    code, out = _cli(capsys, "install", *common, "--dry-run")
    assert code == 0 and "dry-run" in out
    assert not list(Path(reg).iterdir()) if Path(reg).exists() else True

    code, out = _cli(capsys, "install", *common)
    assert code == 0 and "등록했다" in out

    code, out = _cli(capsys, "status", *common)
    assert code == 0 and "등록됨" in out and log in out

    code, out = _cli(capsys, "uninstall", "--dir", reg)
    assert code == 0 and "해제했다" in out


def test_CLI는_세_OS_미리보기를_한번에_보여준다(capsys, tmp_path):
    code, out = _cli(capsys, "install", "--os", "all", "--dry-run")
    assert code == 0
    for token in ("windows", "macos", "linux", "plist", "systemd", ".cmd"):
        assert token in out


def test_CLI_os_all_은_실제_등록에_쓸_수_없다(capsys, tmp_path):
    code, out = _cli(capsys, "install", "--os", "all", "--dir", str(tmp_path / "reg"))
    assert code == 2
    assert "dry-run" in out


def test_CLI는_환경변수로_등록_디렉토리를_받는다(capsys, tmp_path, monkeypatch):
    reg = tmp_path / "env-reg"
    monkeypatch.setenv(autostart.ENV_DIR, str(reg))
    code, out = _cli(capsys, "install", "--port", "8899", "--log-file", str(tmp_path / "a.log"))
    assert code == 0
    assert str(reg) in out
    assert list(reg.iterdir())


def test_서브커맨드가_아니면_서버_경로_그대로다():
    """``autostart`` 토큰이 없으면 기존 인자 파싱이 그대로 산다."""
    from gitwire_chat.__main__ import build_parser

    args = build_parser().parse_args(["--port", "9999", "--no-notify"])
    assert args.port == 9999 and args.no_notify is True

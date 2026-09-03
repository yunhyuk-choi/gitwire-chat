"""설정·방 목록 저장소, 그리고 ⚠️ `chats/` 무시가 **실제로** 되는지."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gitwire_chat import config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_home_우선순위_명시값_환경변수_소스체크아웃(tmp_path, monkeypatch):
    assert config.resolve_home(tmp_path / "여기") == (tmp_path / "여기").resolve()

    monkeypatch.setenv("GITWIRE_CHAT_HOME", str(tmp_path / "환경"))
    assert config.resolve_home() == (tmp_path / "환경").resolve()

    monkeypatch.delenv("GITWIRE_CHAT_HOME")
    # 지금은 소스 체크아웃으로 돌고 있으므로 <레포>/chats 여야 한다.
    assert config.resolve_home() == PROJECT_ROOT / "chats"


def test_패키지_설치_형태에서는_OS_데이터_디렉토리로_떨어진다(monkeypatch):
    monkeypatch.setattr(config, "_source_checkout_root", lambda: None)
    home = config.resolve_home()
    assert "gitwire-chat" in str(home)
    assert home.is_absolute()


def test_rooms_json_왕복(tmp_path):
    store = config.RoomStore(tmp_path / "rooms.json")
    assert store.load() == []
    rooms = [
        config.Room(id="a1", repo_url="https://example.invalid/x.git", name="우리 방"),
        config.Room(id="b2", repo_url="https://example.invalid/y.git", token_env="T"),
    ]
    store.save(rooms)
    again = store.load()
    assert [r.id for r in again] == ["a1", "b2"]
    assert again[0].name == "우리 방"
    text = (tmp_path / "rooms.json").read_bytes()
    assert not text.startswith(b"\xef\xbb\xbf")   # BOM 없음
    assert b"\r\n" not in text                     # LF


def test_깨진_rooms_json_은_빈_목록으로_떨어진다(tmp_path):
    path = tmp_path / "rooms.json"
    path.write_text("{ 이건 JSON 이 아니다", encoding="utf-8")
    assert config.RoomStore(path).load() == []


def test_토큰_값은_저장되지_않는다(tmp_path):
    store = config.RoomStore(tmp_path / "rooms.json")
    store.save([config.Room(id="a", repo_url="u", token_env="MY_TOKEN")])
    saved = (tmp_path / "rooms.json").read_text(encoding="utf-8")
    assert "MY_TOKEN" in saved       # 이름은 남고
    assert "token\":" not in saved   # 값 자리는 아예 없다


# ------------------------------------------------------------------------
# ⚠️ 이 설계의 전제: `chats/` 가 gitignore 된다.
# 무시되지 않으면 클론이 embedded git repository 로 gitlink 스테이징된다.
# 주장하지 말고 git 에게 직접 물어본다.
# ------------------------------------------------------------------------


def _git(*args, cwd=PROJECT_ROOT):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def test_gitignore_에_인라인_주석이_붙어_있지_않다():
    """`#` 는 줄 첫 칸에서만 주석이다. `chats/  # 설명` 은 패턴을 무효화한다."""
    for line in (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        assert "#" not in line, f"패턴 뒤 인라인 주석 발견: {line!r}"


@pytest.mark.skipif(
    not (PROJECT_ROOT / ".git").exists(), reason="git 레포가 아니다"
)
def test_git_check_ignore_로_chats_무시를_실증한다():
    for candidate in (
        "chats/",
        "chats/rooms.json",
        "chats/channels/room-abc123/clone/records/x.json",
    ):
        result = _git("check-ignore", "-v", candidate)
        assert result.returncode == 0, f"{candidate} 가 무시되지 않는다"
        assert ".gitignore" in result.stdout
        assert "chats/" in result.stdout


@pytest.mark.skipif(
    not (PROJECT_ROOT / ".git").exists(), reason="git 레포가 아니다"
)
def test_추적_중인_파일에_chats_가_없다():
    result = _git("ls-files", "chats")
    assert result.stdout.strip() == ""


def test_소스_전체가_UTF8_BOM없음_LF():
    """POLICY-ENCODING — 생성 파일은 UTF-8(BOM 없음)·LF."""
    skip_parts = {".git", "__pycache__", "chats", ".pytest_cache"}
    problems = []
    for path in sorted(PROJECT_ROOT.rglob("*")):
        if not path.is_file() or skip_parts & set(path.parts):
            continue
        if path.suffix in {".png", ".ico"} or ".egg-info" in str(path):
            continue
        raw = path.read_bytes()
        rel = path.relative_to(PROJECT_ROOT)
        if raw.startswith(b"\xef\xbb\xbf"):
            problems.append((str(rel), "BOM"))
        if b"\r\n" in raw:
            problems.append((str(rel), "CRLF"))
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            problems.append((str(rel), "UTF-8 아님"))
    assert problems == []

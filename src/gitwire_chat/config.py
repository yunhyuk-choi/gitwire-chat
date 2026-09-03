"""설정 — 로컬 상태 위치(`chats/`)와 방 목록 저장소.

클론 위치 결정
--------------
방 클론은 이 앱의 로컬 상태 디렉토리(`chats/`) 안에 들어간다. gitwire 는
``home=`` 인자로 채널 디렉토리 루트를 바꿀 수 있으므로 그 값을 여기서 정한다::

    <chat_home>/channels/<slug>-<hash12>/clone   ← 방 하나의 클론
    <chat_home>/channels/<slug>-<hash12>/cursors ← 소비자별 커서
    <chat_home>/rooms.json                       ← 방 목록(이 앱 소유)

⚠️ **`chats/` 가 gitignore 되어 있다는 것이 이 배치의 전제다.** 무시되지 않으면
부모 레포가 클론을 `embedded git repository` 로 경고하고 gitlink 로 스테이징한다.
무시되기만 하면 부모 `git status` 에 보이지 않고 `git clean -fdx` 도
`Would skip repository` 로 건너뛴다(실측 확인).

우선순위
--------
1. ``GITWIRE_CHAT_HOME`` 환경변수 (또는 ``--home`` 인자)
2. 소스 체크아웃으로 돌고 있으면 ``<레포 루트>/chats``
3. 그 외(패키지로 설치된 형태 — 앱 디렉토리가 없다)는 OS 데이터 디렉토리

3번이 필요한 이유: ``pip install gitwire-chat`` 로 설치하면 코드가
site-packages 에 있고 그 옆에 상태를 쓰는 것은 잘못이다.
"""

from __future__ import annotations

import getpass
import json
import os
import secrets
import socket
import sys
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path

#: 방 목록 파일 이름
ROOMS_FILE = "rooms.json"

#: 이 설치(= 이 home 디렉토리)의 전송 수준 식별자를 담는 파일
INSTANCE_FILE = "instance.txt"

#: 기본 폴 주기(초). gitwire 기본값 30초보다 짧게 잡되, 호스트 rate limit 을
#: 생각해 무작정 줄이지 않는다. 지연 = 폴 주기라는 한계는 README 에 명시한다.
DEFAULT_POLL_INTERVAL = 15.0


def _os_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / "gitwire-chat"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "gitwire-chat"
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "gitwire-chat"


def _source_checkout_root() -> Path | None:
    """소스 체크아웃으로 돌고 있으면 그 루트(= pyproject.toml 이 있는 곳)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src").is_dir():
            return parent
    return None


def resolve_home(explicit: str | os.PathLike | None = None) -> Path:
    """로컬 상태 디렉토리(`chats/`)를 결정한다."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("GITWIRE_CHAT_HOME")
    if env:
        return Path(env).expanduser().resolve()
    root = _source_checkout_root()
    if root is not None:
        return root / "chats"
    return _os_data_dir()


def instance_id(home: Path) -> str:
    """이 설치의 **전송 수준 식별자**(gitwire ``sender``)를 만들거나 읽는다.

    ⚠️ gitwire 의 기본 ``sender`` 는 ``<유저>.<호스트>`` 다. 실제 배치에서는
    참가자마다 머신이 달라 문제가 없지만, **같은 머신에서 두 인스턴스를 띄우면
    (테스트, 계정 두 개, 한 사람이 두 프로필) 두 프로세스의 sender 가 같아진다.**
    그러면 "이건 내가 보낸 것인가" 판정이 무너져 남의 메시지를 자기 에코로 오인한다.

    그래서 home 마다 한 번 난수를 만들어 붙인다. 파일에 남기므로 재시작해도
    같은 값이고, 사람에게 보여 줄 이름이 아니라 전송 식별자다.
    """
    path = Path(home) / INSTANCE_FILE
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "anon"
    try:
        host = socket.gethostname().split(".")[0]
    except Exception:  # noqa: BLE001
        host = "local"
    value = f"{user}.{host}.{secrets.token_hex(3)}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n", encoding="utf-8", newline="\n")
    except OSError:
        pass
    return value


@dataclass(frozen=True)
class Room:
    """등록된 방 하나. 토큰 **값**은 절대 담지 않는다 — 환경변수 이름만."""

    id: str
    repo_url: str
    name: str = ""
    author: str = ""
    token_env: str = ""
    poll_interval: float = DEFAULT_POLL_INTERVAL

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "repo_url": self.repo_url,
            "name": self.name,
            "author": self.author,
            "token_env": self.token_env,
            "poll_interval": self.poll_interval,
        }

    @classmethod
    def from_json(cls, data: dict) -> "Room":
        return cls(
            id=str(data.get("id") or ""),
            repo_url=str(data.get("repo_url") or ""),
            name=str(data.get("name") or ""),
            author=str(data.get("author") or ""),
            token_env=str(data.get("token_env") or ""),
            poll_interval=float(data.get("poll_interval") or DEFAULT_POLL_INTERVAL),
        )


@dataclass
class Settings:
    """앱 설정 한 벌."""

    home: Path
    author: str = ""
    """기본 표시 이름. 방이 따로 정하지 않으면 이걸 쓴다."""

    poll_interval: float = DEFAULT_POLL_INTERVAL
    recent_limit: int = 50
    """타임라인 최초 렌더 건수. '이전 불러오기'로 더 가져온다."""

    page_limit: int = 50
    notifications: bool = True
    extra: dict = field(default_factory=dict)

    @property
    def rooms_path(self) -> Path:
        return self.home / ROOMS_FILE


def default_author() -> str:
    """표시 이름 기본값 — 사람이 알아볼 만한 것으로."""
    for var in ("GITWIRE_CHAT_AUTHOR", "USER", "USERNAME", "LOGNAME"):
        value = os.environ.get(var)
        if value:
            return value.strip()[:64]
    return "익명"


def load_settings(home: str | os.PathLike | None = None, **overrides) -> Settings:
    resolved = resolve_home(home)
    settings = Settings(home=resolved, author=default_author())
    for key, value in overrides.items():
        if value is not None and hasattr(settings, key):
            setattr(settings, key, value)
    return settings


class RoomStore:
    """`rooms.json` 읽기/쓰기. 원자적 교체로 중간 상태를 남기지 않는다."""

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)

    def load(self) -> list[Room]:
        if not self.path.is_file():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return []
        items = raw.get("rooms") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return []
        out = []
        for item in items:
            if isinstance(item, dict):
                room = Room.from_json(item)
                if room.id and room.repo_url:
                    out.append(room)
        return out

    def save(self, rooms: list[Room]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(
            {"version": 1, "rooms": [r.to_json() for r in rooms]},
            ensure_ascii=False,
            indent=2,
        )
        # 원자적 교체 — 쓰다 죽어도 방 목록이 반쪽으로 남지 않는다.
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text + "\n")
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def with_defaults(room: Room, settings: Settings) -> Room:
    """방에 비어 있는 값을 전역 설정으로 채운다."""
    return replace(
        room,
        author=room.author or settings.author,
        poll_interval=room.poll_interval or settings.poll_interval,
    )

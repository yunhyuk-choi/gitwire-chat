"""gitwire-chat — 중앙 서버 없이 git 레포를 메시지 저장소로 쓰는 비동기 채팅.

전송 계층은 전부 `gitwire` 에 위탁한다. 이 패키지는 그 위에 **대화**를 얹는다:
메시지 스키마, 타임라인, SSE 로 밀어 넣는 웹 UI, OS 알림.

    python -m gitwire_chat            # http://127.0.0.1:8770
"""

from .app import create_app
from .config import Room, RoomStore, Settings, load_settings, resolve_home
from .events import EventBus
from .notify import Notifier
from .rooms import RoomError, RoomManager, room_id_for
from .schema import Message, build_payload, parse_record

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "create_app",
    "Settings",
    "Room",
    "RoomStore",
    "load_settings",
    "resolve_home",
    "RoomManager",
    "RoomError",
    "room_id_for",
    "EventBus",
    "Notifier",
    "Message",
    "build_payload",
    "parse_record",
]

"""방 관리 — gitwire 채널 위에 얹은 "대화방".

방 하나 = gitwire 채널 하나 = git 레포 하나. 이 모듈이 하는 일은 얇다:

* 방 등록/목록 (`chats/rooms.json`)
* 채널 열기 (빈 레포면 gitwire 가 알아서 초기화한다)
* 타임라인 조회 (최근 N + 이전 불러오기) · 검색
* 메시지 전송 (+ **로컬 에코**)
* 상시 구독 → 새 메시지를 이벤트 버스로, 그리고 조건이 맞으면 OS 알림으로

전송 계층은 **전부 gitwire 에 위탁한다.** 여기에 git 명령이 단 한 줄도 없다.

주입 가능성
----------
``opener`` 인자로 채널 생성 함수를 갈아끼울 수 있다(기본값
``gitwire.open_channel``). 덕분에 대부분의 테스트가 네트워크·git 없이 돈다.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import gitwire

from . import schema
from .config import Room, RoomStore, Settings, instance_id, with_defaults
from .events import EventBus
from .notify import Notifier

log = logging.getLogger(__name__)

#: 방마다 기억하는 "이미 본 메시지 ID" 상한. 중복 수신 방어용이라 최근 것만 있으면 된다.
SEEN_LIMIT = 4096


class RoomError(Exception):
    """방 등록·조회 실패 (사용자에게 그대로 보여줘도 되는 메시지)."""


def room_id_for(repo_url: str) -> str:
    """레포 URL → 방 ID. gitwire 의 정규화·해시 규약을 그대로 쓴다.

    같은 레포를 https/ssh, .git 유무로 다르게 적어도 같은 방이 된다.
    """
    return gitwire.layout.channel_key(repo_url)


def _display_name(repo_url: str) -> str:
    norm = gitwire.normalize_repo_url(repo_url)
    return norm.rstrip("/").rsplit("/", 1)[-1] or norm


def _ts_from_record_id(record_id: str) -> datetime:
    """레코드 ID 앞머리의 고정폭 타임스탬프를 되읽는다 (로컬 에코용)."""
    try:
        from gitwire.records import parse_ts

        stamp = record_id.rsplit("/", 1)[-1].split("-", 1)[0]
        return parse_ts(stamp)
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc)


class _Seen:
    """최근 본 메시지 ID 집합 (bounded). 재전달·로컬 에코 중복을 막는다."""

    def __init__(self, limit: int = SEEN_LIMIT) -> None:
        self._ids: "OrderedDict[str, None]" = OrderedDict()
        self._limit = limit
        self._lock = threading.Lock()

    def add(self, key: str) -> bool:
        """처음 보는 것이면 True. 이미 본 것이면 False."""
        with self._lock:
            if key in self._ids:
                return False
            self._ids[key] = None
            while len(self._ids) > self._limit:
                self._ids.popitem(last=False)
            return True

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._ids


class RoomManager:
    """등록된 방 전체를 열고, 폴링하고, 이벤트를 뿌린다."""

    def __init__(
        self,
        settings: Settings,
        *,
        bus: EventBus | None = None,
        notifier: Notifier | None = None,
        store: RoomStore | None = None,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self.bus = bus or EventBus()
        self.notifier = notifier or Notifier(enabled=settings.notifications)
        self.store = store or RoomStore(settings.rooms_path)
        self._opener = opener or gitwire.open_channel

        self._lock = threading.RLock()
        self._rooms: "OrderedDict[str, Room]" = OrderedDict()
        self._channels: dict[str, Any] = {}
        self._subs: dict[str, Any] = {}
        self._seen: dict[str, _Seen] = {}
        self._started = False
        self.instance = instance_id(settings.home)
        """이 설치의 전송 수준 식별자 (gitwire sender). 표시 이름이 아니다."""

        for room in self.store.load():
            self._rooms[room.id] = with_defaults(room, settings)

    # --------------------------------------------------------------- 조회

    @property
    def home(self) -> Path:
        return self.settings.home

    def rooms(self) -> list[Room]:
        with self._lock:
            return list(self._rooms.values())

    def get(self, room_id: str) -> Room:
        with self._lock:
            room = self._rooms.get(room_id)
        if room is None:
            raise RoomError(f"등록되지 않은 방이다: {room_id}")
        return room

    # ---------------------------------------------------------- 채널 열기

    def _credential(self, room: Room):
        """토큰은 **환경변수 이름**만 저장하고 값은 프로세스 안에서만 읽는다."""
        var = room.token_env or "GITWIRE_TOKEN"
        try:
            return gitwire.TokenCredential.from_env(var)
        except Exception:  # noqa: BLE001 — 값이 없으면 자격증명 없이 (공개·로컬 레포)
            return None

    def channel(self, room_id: str):
        """방의 gitwire 채널. 없으면 연다 (빈 레포면 gitwire 가 초기화)."""
        with self._lock:
            channel = self._channels.get(room_id)
            if channel is not None:
                return channel
            room = self.get(room_id)
        kwargs: dict[str, Any] = {
            "home": self.home,
            "consumer": "chat",
            "sender": self.instance,
            "name": room.name or None,
            "poll_interval": room.poll_interval or self.settings.poll_interval,
        }
        credential = self._credential(room)
        if credential is not None:
            kwargs["credential"] = credential
        kwargs.update(self.settings.extra.get("channel_kwargs", {}))
        try:
            channel = self._opener(room.repo_url, **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise RoomError(f"방을 열 수 없다: {exc}") from exc
        with self._lock:
            existing = self._channels.get(room_id)
            if existing is not None:
                return existing
            self._channels[room_id] = channel
            self._seen.setdefault(room_id, _Seen())
        return channel

    # --------------------------------------------------------------- 등록

    def register(
        self,
        repo_url: str,
        *,
        name: str = "",
        author: str = "",
        token_env: str = "",
        poll_interval: float | None = None,
    ) -> Room:
        """레포 주소를 방으로 등록한다. 빈 레포면 gitwire 가 규약을 심는다."""
        repo_url = (repo_url or "").strip()
        if not repo_url:
            raise RoomError("레포 주소가 비어 있다")
        rid = room_id_for(repo_url)
        with self._lock:
            if rid in self._rooms:
                return self._rooms[rid]

        room = with_defaults(
            Room(
                id=rid,
                repo_url=repo_url,
                name=(name or "").strip() or _display_name(repo_url),
                author=(author or "").strip(),
                token_env=(token_env or "").strip(),
                poll_interval=poll_interval or self.settings.poll_interval,
            ),
            self.settings,
        )
        with self._lock:
            self._rooms[rid] = room
        try:
            self.channel(rid)  # 여기서 클론·초기화가 일어난다 (실패하면 등록 취소)
        except RoomError:
            with self._lock:
                self._rooms.pop(rid, None)
            raise
        self._persist()
        if self._started:
            self._start_room(rid)
        self.bus.publish(None, "rooms", {"rooms": [r.to_json() for r in self.rooms()]})
        return room

    def unregister(self, room_id: str) -> None:
        """방 목록에서 뺀다. 클론은 지우지 않는다 (기록은 파일이 곧 원본이다)."""
        with self._lock:
            self._rooms.pop(room_id, None)
            sub = self._subs.pop(room_id, None)
            channel = self._channels.pop(room_id, None)
            self._seen.pop(room_id, None)
        if sub is not None:
            try:
                sub.stop()
            except Exception:  # noqa: BLE001
                log.debug("구독 정리 실패", exc_info=True)
        if channel is not None:
            try:
                channel.close()
            except Exception:  # noqa: BLE001
                log.debug("채널 정리 실패", exc_info=True)
        self._persist()
        self.bus.publish(None, "rooms", {"rooms": [r.to_json() for r in self.rooms()]})

    def _persist(self) -> None:
        try:
            self.store.save(self.rooms())
        except OSError as exc:
            log.warning("방 목록 저장 실패: %s", exc)

    # ----------------------------------------------------------- 타임라인

    def _messages(self, room_id: str, limit: int | None = None) -> list[schema.Message]:
        channel = self.channel(room_id)
        try:
            records = channel.history(limit)
        except Exception as exc:  # noqa: BLE001
            raise RoomError(f"기록을 읽을 수 없다: {exc}") from exc
        return [schema.parse_record(r) for r in records]

    def timeline(self, room_id: str, limit: int | None = None) -> list[schema.Message]:
        """최근 N건 (시간순). v1 은 가상 스크롤 없이 이만큼만 그린다."""
        return self._messages(room_id, limit or self.settings.recent_limit)

    def older(
        self, room_id: str, before_id: str, limit: int | None = None
    ) -> list[schema.Message]:
        """`before_id` 보다 앞선 메시지 최대 N건 ('이전 불러오기').

        ⚠️ gitwire 의 ``history()`` 는 "최근 N건"만 지원하고 커서 기준 역방향
        페이징이 없다. 그래서 지금은 전체를 읽어 잘라낸다 — 대화가 아주 길면
        비싸다. (기반 API 피드백으로 남겼다.)
        """
        limit = limit or self.settings.page_limit
        everything = self._messages(room_id, None)
        cut = len(everything)
        for index, message in enumerate(everything):
            if message.id == before_id:
                cut = index
                break
        start = max(0, cut - limit)
        return everything[start:cut]

    def search(
        self, room_id: str, query: str, limit: int = 50
    ) -> list[schema.Message]:
        """서버에서 **레코드를 뒤진다** — DOM 에 없는 과거 메시지도 찾는다."""
        query = (query or "").strip()
        if not query:
            return []
        needle = query.casefold()
        hits = [
            m
            for m in self._messages(room_id, None)
            if needle in m.text.casefold() or needle in m.author.casefold()
        ]
        return hits[-limit:]

    # ------------------------------------------------------------- 보내기

    def send(
        self,
        room_id: str,
        text: str,
        *,
        author: str = "",
        reply_to: str | None = None,
    ) -> schema.Message:
        """메시지 발행 + **로컬 에코**.

        로컬 에코가 필요한 이유: 구독은 폴 주기(기본 15초)마다 돈다. 에코가
        없으면 *내가 방금 보낸 말*이 화면에 뜨는 데 최대 폴 주기가 걸린다.
        에코와 나중의 구독 전달은 **같은 레코드 ID** 라 중복 제거로 합쳐진다.
        """
        room = self.get(room_id)
        payload = schema.build_payload(
            author or room.author or self.settings.author, text, reply_to
        )
        channel = self.channel(room_id)
        try:
            record_id = channel.append(payload, flush=True)
        except Exception as exc:  # noqa: BLE001
            raise RoomError(f"메시지를 보낼 수 없다: {exc}") from exc

        record = gitwire.Record(
            id=record_id,
            sender=getattr(channel, "sender", "local"),
            timestamp=_ts_from_record_id(record_id),
            payload=payload,
        )
        message = schema.parse_record(record)
        self._deliver(room_id, message, own=True)
        return message

    # -------------------------------------------------------------- 구독

    def _deliver(self, room_id: str, message: schema.Message, *, own: bool) -> bool:
        """새 메시지 1건을 브라우저(SSE)와 OS 알림으로 흘린다.

        **멱등하다.** gitwire 는 크래시 시점에 따라 재전달할 수 있고, 로컬
        에코와 구독 전달은 어차피 겹친다. 봉투 ID 로 한 번만 통과시킨다.
        """
        seen = self._seen.setdefault(room_id, _Seen())
        if not seen.add(message.id):
            return False
        self.bus.publish(room_id, "message", message.to_json())
        if not own and self.bus.viewers(room_id) == 0:
            try:
                room = self.get(room_id)
                self.notifier.notify_message(
                    room.name or room_id, message.author, message.text
                )
            except Exception:  # noqa: BLE001 — 알림 실패가 수신을 막으면 안 된다
                log.debug("알림 실패", exc_info=True)
        return True

    def _own_sender(self, room_id: str) -> str:
        channel = self._channels.get(room_id)
        return getattr(channel, "sender", "") if channel else ""

    def on_record(self, room_id: str, record: Any) -> bool:
        """구독 콜백 (테스트에서 직접 부를 수 있게 공개해 둔다)."""
        message = schema.parse_record(record)
        own = bool(message.sender) and message.sender == self._own_sender(room_id)
        return self._deliver(room_id, message, own=own)

    def _start_room(self, room_id: str) -> None:
        with self._lock:
            if room_id in self._subs:
                return
        try:
            channel = self.channel(room_id)
        except RoomError as exc:
            log.warning("방 %s 를 열 수 없어 구독을 건너뛴다: %s", room_id, exc)
            return
        # 이미 화면에 있는 과거 메시지로 알림 폭탄이 터지지 않게, 지금까지의
        # 레코드는 '처리됨'으로 표시하고 **이후 것만** 구독으로 받는다.
        try:
            channel.skip_to_now()
        except Exception:  # noqa: BLE001
            log.debug("백로그 건너뛰기 실패 — 그대로 진행한다", exc_info=True)

        def callback(record, _rid=room_id):
            try:
                self.on_record(_rid, record)
            except Exception:  # noqa: BLE001
                log.exception("레코드 처리 실패")

        def on_error(_record, exc, _rid=room_id):
            log.warning("방 %s 폴링 오류: %s", _rid, exc)
            self.bus.publish(_rid, "trouble", {"room": _rid, "detail": str(exc)})

        try:
            sub = channel.subscribe(callback, on_error=on_error)
        except Exception as exc:  # noqa: BLE001
            log.warning("방 %s 구독 실패: %s", room_id, exc)
            return
        with self._lock:
            self._subs[room_id] = sub

    def start(self) -> None:
        """등록된 모든 방을 구독한다. 한 방이 실패해도 나머지는 돈다."""
        self._started = True
        for room in self.rooms():
            self._start_room(room.id)

    def stop(self) -> None:
        self._started = False
        with self._lock:
            subs = list(self._subs.values())
            self._subs.clear()
            channels = list(self._channels.values())
            self._channels.clear()
        for sub in subs:
            try:
                sub.stop()
            except Exception:  # noqa: BLE001
                log.debug("구독 정리 실패", exc_info=True)
        for channel in channels:
            try:
                channel.close()
            except Exception:  # noqa: BLE001
                log.debug("채널 정리 실패", exc_info=True)
        self.notifier.close()
        self.bus.close_all()

    def poll_now(self, room_id: str) -> int:
        """폴 주기를 기다리지 않고 즉시 한 번 당겨온다 (수동 새로고침)."""
        channel = self.channel(room_id)
        delivered = 0

        def callback(record):
            nonlocal delivered
            if self.on_record(room_id, record):
                delivered += 1

        try:
            channel.poll_once(callback)
        except Exception as exc:  # noqa: BLE001
            raise RoomError(f"새로고침 실패: {exc}") from exc
        return delivered

    def info(self, room_id: str) -> dict:
        channel = self.channel(room_id)
        try:
            return channel.info()
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}


def messages_json(messages: Iterable[schema.Message]) -> list[dict]:
    return [m.to_json() for m in messages]

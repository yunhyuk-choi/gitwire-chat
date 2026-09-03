"""SSE 팬아웃 버스.

브라우저 탭 하나 = 구독자 하나. 구독자마다 자기 큐를 갖고, 발행은 모든 큐에
넣기만 한다(느린 구독자가 다른 구독자를 막지 않는다).

큐가 가득 차면 **가장 오래된 것을 버린다.** 채팅에서 최신이 밀리는 것보다
아주 오래된 이벤트를 잃는 편이 낫고, 어차피 브라우저는 재연결 시 타임라인을
다시 받아온다.

"보고 있는 사람"도 여기서 센다 — OS 알림을 띄울지 판정하는 근거다.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterator

#: 구독자 1인당 큐 상한
QUEUE_MAXSIZE = 512

#: keepalive 주기(초). 프록시·브라우저가 유휴 연결을 끊지 않게 한다.
KEEPALIVE = 15.0


@dataclass
class Event:
    name: str
    data: Any

    def encode(self) -> str:
        """SSE 와이어 포맷. 데이터의 개행은 줄마다 `data:` 를 붙여야 한다."""
        body = json.dumps(self.data, ensure_ascii=False)
        lines = "".join(f"data: {line}\n" for line in body.split("\n"))
        return f"event: {self.name}\n{lines}\n"


class Subscriber:
    def __init__(self, room_id: str | None, client: str = "") -> None:
        self.room_id = room_id
        self.client = client
        """브라우저 탭 식별자. 가시성 보고(POST)와 이 SSE 연결을 잇는 끈이다."""
        self.queue: "queue.Queue[Event | None]" = queue.Queue(maxsize=QUEUE_MAXSIZE)
        self.visible = True
        """이 탭이 지금 방을 실제로 보고 있나 (탭 숨김·다른 방 이동 시 False)."""

    def put(self, event: Event) -> None:
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.queue.put_nowait(event)
            except queue.Full:
                pass

    def close(self) -> None:
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass


class EventBus:
    def __init__(self, keepalive: float = KEEPALIVE) -> None:
        self._subs: set[Subscriber] = set()
        self._lock = threading.Lock()
        self.keepalive = keepalive

    # ------------------------------------------------------------ 구독 관리

    def subscribe(self, room_id: str | None = None, client: str = "") -> Subscriber:
        sub = Subscriber(room_id, client)
        with self._lock:
            self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        with self._lock:
            self._subs.discard(sub)
        sub.close()

    def close_all(self) -> None:
        with self._lock:
            subs = list(self._subs)
            self._subs.clear()
        for sub in subs:
            sub.close()

    # -------------------------------------------------------------- 발행

    def publish(self, room_id: str | None, name: str, data: Any) -> int:
        """해당 방(또는 전체)을 듣는 구독자에게 이벤트를 밀어 넣는다."""
        event = Event(name, data)
        with self._lock:
            targets = [
                s for s in self._subs if room_id is None or s.room_id in (None, room_id)
            ]
        for sub in targets:
            sub.put(event)
        return len(targets)

    # ------------------------------------------------------------ 시청 판정

    def viewers(self, room_id: str) -> int:
        """지금 이 방을 **실제로 보고 있는** 탭 수 (OS 알림 판정용)."""
        with self._lock:
            return sum(1 for s in self._subs if s.room_id == room_id and s.visible)

    def subscriber_count(self, room_id: str | None = None) -> int:
        with self._lock:
            if room_id is None:
                return len(self._subs)
            return sum(1 for s in self._subs if s.room_id == room_id)

    def set_visible(
        self, room_id: str, visible: bool, client: str = ""
    ) -> int:
        """탭 하나(또는 client 미지정 시 그 방의 전체)의 가시성을 바꾼다."""
        changed = 0
        with self._lock:
            for sub in self._subs:
                if sub.room_id != room_id:
                    continue
                if client and sub.client != client:
                    continue
                sub.visible = visible
                changed += 1
        return changed


def stream(sub: Subscriber, keepalive: float = KEEPALIVE) -> Iterator[str]:
    """구독자 큐 → SSE 텍스트 스트림 (제너레이터)."""
    yield "retry: 3000\n\n"
    yield Event("hello", {"room": sub.room_id, "ts": time.time()}).encode()
    while True:
        try:
            item = sub.queue.get(timeout=keepalive)
        except queue.Empty:
            yield ": keepalive\n\n"
            continue
        if item is None:
            break
        yield item.encode()

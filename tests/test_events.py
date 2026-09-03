"""SSE 팬아웃 버스."""

from __future__ import annotations

from gitwire_chat.events import Event, EventBus, stream


def test_SSE_와이어_포맷():
    text = Event("message", {"text": "안녕"}).encode()
    assert text.startswith("event: message\n")
    assert text.endswith("\n\n")
    assert '"안녕"' in text  # ensure_ascii 없이 그대로


def test_개행이_든_데이터도_SSE_규격을_지킨다():
    text = Event("message", {"text": "한 줄\n두 줄"}).encode()
    body_lines = [l for l in text.splitlines() if l.startswith("data: ")]
    # JSON 안의 개행은 \n 으로 이스케이프되므로 data 줄은 하나다.
    assert len(body_lines) == 1
    for line in text.splitlines():
        assert line == "" or line.split(":", 1)[0] in {"event", "data", "id", "retry", ""}


def test_방별로만_전달된다():
    bus = EventBus()
    a = bus.subscribe("room-a")
    b = bus.subscribe("room-b")
    everyone = bus.subscribe(None)

    assert bus.publish("room-a", "message", {"x": 1}) == 2  # a + 전체구독
    assert a.queue.qsize() == 1
    assert b.queue.qsize() == 0
    assert everyone.queue.qsize() == 1


def test_느린_구독자가_다른_구독자를_막지_않는다():
    bus = EventBus()
    slow = bus.subscribe("r")
    from gitwire_chat import events

    for i in range(events.QUEUE_MAXSIZE + 20):
        bus.publish("r", "message", {"i": i})
    # 넘치면 가장 오래된 것을 버린다 — 큐가 무한히 자라지 않는다.
    assert slow.queue.qsize() <= events.QUEUE_MAXSIZE


def test_시청자_수와_가시성_보고():
    bus = EventBus()
    bus.subscribe("r", client="tab1")
    bus.subscribe("r", client="tab2")
    assert bus.viewers("r") == 2

    bus.set_visible("r", False, "tab1")
    assert bus.viewers("r") == 1          # tab1 은 숨겨졌다
    bus.set_visible("r", False, "tab2")
    assert bus.viewers("r") == 0          # 아무도 안 본다 → OS 알림 대상


def test_구독_해제하면_시청자에서_빠진다():
    bus = EventBus()
    sub = bus.subscribe("r", client="tab")
    assert bus.viewers("r") == 1
    bus.unsubscribe(sub)
    assert bus.viewers("r") == 0
    assert bus.subscriber_count("r") == 0


def test_스트림은_hello_로_시작하고_close_로_끝난다():
    bus = EventBus(keepalive=0.05)
    sub = bus.subscribe("r")
    gen = stream(sub, 0.05)
    assert next(gen).startswith("retry:")
    assert "event: hello" in next(gen)

    bus.publish("r", "message", {"text": "안녕"})
    chunk = next(gen)
    assert "event: message" in chunk and "안녕" in chunk

    sub.close()
    remaining = list(gen)
    assert all("event: message" not in c for c in remaining)

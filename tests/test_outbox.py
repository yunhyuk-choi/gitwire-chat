"""아웃박스 — "아직 상대에게 못 갔다"를 누가 알고 누가 말하나.

전송 응답이 원격 push 를 기다리지 않게 되면서 생긴 것은 속도만이 아니다.
**"보냈다"가 두 사건으로 갈렸다** — 앱이 받았나(=디스크), 상대에게 갔나(=push).
여기서 보는 것은 그 둘째다:

* 응답이 밀어내기를 **기다리지 않는다** (느린 push 로 실증)
* 실패해도 **조용하지 않다** — 상태가 이벤트로 나가고 방 목록에도 실린다
* 실패해도 **로컬에는 남는다** — 밀어내지 못한 레코드가 채널에 그대로 있다
* 밀어내기는 **겹치지 않는다** (방당 워커 하나 = 순서)
* 종료·기동이 잔여분을 **밀어낸다**

⚠️ 실제 git·네트워크로 "정말 원격 파일이 되나"를 보는 것은 `test_two_instances.py`
가 한다. 여기서 그걸 흉내 내면 아무것도 증명하지 못한다.
"""

from __future__ import annotations

import threading
import time

import pytest

from gitwire_chat.app import create_app
from gitwire_chat.outbox import SENDING, STUCK, SYNCED, Outbox
from gitwire_chat.rooms import RoomManager

REPO = "https://example.invalid/room.git"


@pytest.fixture
def client(manager):
    app = create_app(manager.settings, manager, start=False)
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


# ------------------------------------------------------------------ 단위


def test_add_하면_밀어내고_상태가_synced_로_돌아온다():
    calls = []
    seen = []
    box = Outbox(lambda: calls.append(1), on_state=seen.append, name="t")
    box.add()
    assert box.wait_idle(5.0)
    box.close()
    assert calls, "밀어내기가 아예 불리지 않았다"
    assert box.state.state == SYNCED
    assert box.state.pending == 0
    # 지나간 상태에 '나가는 중' 이 있었다 (그 사이를 서버가 알고는 있었다).
    assert SENDING in [s.state for s in seen]


def test_실패하면_stuck_으로_드러나고_사유가_실린다():
    """조용한 실패 금지 — 예외를 삼키고 synced 로 돌아오면 그게 최악이다."""
    seen = []
    boom = RuntimeError("fatal: Authentication failed")
    box = Outbox(
        lambda: (_ for _ in ()).throw(boom),
        on_state=seen.append,
        describe=lambda exc: "인증에 실패했다",
        retry_base=30.0,          # 이 테스트에서 재시도가 끼어들지 않게
        name="t",
    )
    box.add()
    end = time.monotonic() + 5.0
    while time.monotonic() < end and box.state.state != STUCK:
        time.sleep(0.02)
    state = box.state
    assert state.state == STUCK
    assert state.pending == 1, "못 나간 건수가 사라졌다"
    assert state.detail == "인증에 실패했다"
    assert [s.state for s in seen][-1] == STUCK
    box.close(timeout=1.0)


def test_실패한_뒤에도_계속_다시_민다():
    fails = {"n": 2}

    def flush():
        if fails["n"] > 0:
            fails["n"] -= 1
            raise RuntimeError("네트워크")

    box = Outbox(flush, retry_base=0.05, retry_max=0.05, name="t")
    box.add()
    assert box.wait_idle(10.0), "재시도가 끝내 성공하지 못했다"
    assert box.state.state == SYNCED
    assert fails["n"] == 0
    box.close()


def test_밀어내기가_겹치지_않는다():
    """방당 워커 하나 — 두 push 가 동시에 돌면 순서도 상태도 무너진다."""
    overlap = []
    live = {"n": 0}
    lock = threading.Lock()

    def flush():
        with lock:
            live["n"] += 1
            overlap.append(live["n"])
        time.sleep(0.02)
        with lock:
            live["n"] -= 1

    box = Outbox(flush, name="t")
    for _ in range(20):
        box.add()
    assert box.wait_idle(10.0)
    box.close()
    assert max(overlap) == 1, f"밀어내기가 겹쳤다: {overlap}"


def test_코얼레싱_연달아_보내도_밀어내기는_묶인다():
    """20건이 20번 나가면 원격을 20번 왕복한다 — 묶여야 한다."""
    calls = []

    def flush():
        calls.append(1)
        time.sleep(0.03)

    box = Outbox(flush, name="t")
    for _ in range(20):
        box.add()
    assert box.wait_idle(10.0)
    box.close()
    assert len(calls) < 20, f"한 건마다 한 번씩 밀었다 ({len(calls)}회)"


def test_종료가_남은_것을_밀어낸다():
    """정상 종료라면 다음 기동까지 미룰 이유가 없다."""
    calls = []
    started = threading.Event()

    box = Outbox(lambda: calls.append(1), name="t")
    # 워커가 손대기 전에 접는다 — close 가 스스로 밀어야 한다.
    box._closing.set()          # noqa: SLF001 — 경계 조건을 만드는 것이 요점이다
    with box._lock:             # noqa: SLF001
        box._pending = 3        # noqa: SLF001
    started.set()
    box.close()
    assert calls, "종료가 남은 것을 버렸다"


def test_close_가_밀어내기에_실패해도_종료를_막지_않는다():
    box = Outbox(lambda: (_ for _ in ()).throw(OSError("디스크 없음")), name="t")
    with box._lock:             # noqa: SLF001
        box._pending = 1        # noqa: SLF001
    box._closing.set()          # noqa: SLF001
    box.close()                 # 던지지 않는다 (레코드는 디스크에 남는다)


# ------------------------------------------------------- RoomManager 통합


def test_전송_응답이_밀어내기를_기다리지_않는다(manager, fake_opener):
    """⭐ 이 변경의 전부 — push 가 느려도 응답은 빠르다."""
    room = manager.register(REPO)
    channel = manager.channel(room.id)
    channel.flush_delay = 2.0            # 느린 원격

    t0 = time.perf_counter()
    message = manager.send(room.id, "빨리 돌아와야 한다")
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.5, f"응답이 push 를 기다렸다 ({elapsed*1000:.0f} ms)"
    assert message.text == "빨리 돌아와야 한다"
    # 응답 시점에 레코드는 **이미** 채널에 있다 (내구성은 여기서 끝난다).
    assert channel.records[-1].id == message.id
    # 그리고 밀어내기는 뒤따라 일어난다.
    assert manager.outbox(room.id).wait_idle(20.0)
    assert channel.unpushed() == []


def test_보내기가_flush_True_를_쓰지_않는다(manager, fake_opener):
    """`flush=True` 가 다시 들어오면 응답이 다시 push 를 기다린다 — 회귀 방지."""
    room = manager.register(REPO)
    channel = manager.channel(room.id)
    seen = []
    original = channel.append

    def watched(payload, *, sender=None, flush=False):
        seen.append(flush)
        return original(payload, sender=sender, flush=flush)

    channel.append = watched
    manager.send(room.id, "안녕")
    assert seen == [False]


def test_밀어내기_실패는_이벤트로_드러나고_로컬에는_남는다(manager, fake_opener):
    room = manager.register(REPO)
    channel = manager.channel(room.id)
    channel.flush_error = RuntimeError("fatal: could not resolve host github.com")
    sub = manager.bus.subscribe(room.id, "tab1")

    manager.send(room.id, "못 나갈 말")

    box = manager.outbox(room.id)
    end = time.monotonic() + 5.0
    while time.monotonic() < end and box.state.state != STUCK:
        time.sleep(0.02)

    state = box.state
    assert state.state == STUCK, "밀어내기가 실패했는데 조용하다"
    assert state.pending == 1
    assert "네트워크" in state.detail, state.detail   # rooms.classify 를 그대로 쓴다

    # (1) 방 단위 이벤트로 나갔다 — 브라우저가 그걸 그린다.
    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait())
    outbox_events = [e for e in events if e.name == "outbox"]
    assert outbox_events, "아웃박스 상태가 SSE 로 나가지 않았다"
    assert outbox_events[-1].data["state"] == STUCK
    assert outbox_events[-1].data["room"] == room.id

    # (2) 방 목록에도 실린다 — 방을 막 열어도 바로 보이라고.
    assert manager.rooms_payload()[0]["outbox"]["state"] == STUCK

    # (3) ⭐ 그리고 **로컬에는 그대로 있다.** 못 간 것이지 잃은 것이 아니다.
    assert [r.payload["text"] for r in channel.unpushed()] == ["못 나갈 말"]


def test_다시_보내기가_회복시킨다(manager, fake_opener):
    room = manager.register(REPO)
    channel = manager.channel(room.id)
    channel.flush_error = RuntimeError("fatal: could not resolve host")
    manager.send(room.id, "다시 보낼 말")

    box = manager.outbox(room.id)
    end = time.monotonic() + 5.0
    while time.monotonic() < end and box.state.state != STUCK:
        time.sleep(0.02)
    assert box.state.state == STUCK

    channel.flush_error = None            # 네트워크가 돌아왔다
    manager.flush_outbox(room.id)
    assert box.wait_idle(10.0)
    assert manager.outbox_state(room.id).state == SYNCED
    assert channel.unpushed() == []


def test_방이_붙으면_지난_실행의_잔여분을_밀어낸다(settings, fake_opener):
    """⭐ 유실 방지의 본체 — 강제 종료로 남은 레코드는 다음 기동이 민다.

    (대역은 '밀었다'까지만 말한다. 진짜 git 으로 커밋되지 않은 파일이 살아
    돌아오는지는 `test_two_instances.py` 가 실제 레포에서 본다.)
    """
    mgr = RoomManager(settings, opener=fake_opener)
    try:
        room = mgr.register(REPO)
        mgr.wait_for_connect()
        channel = mgr.channel(room.id)
        assert mgr.outbox(room.id).wait_idle(10.0)
        assert channel.flushes >= 1, "붙었는데 잔여분을 밀지 않았다"
    finally:
        mgr.stop()


def test_stop_이_남은_것을_밀어낸다(settings, fake_opener):
    mgr = RoomManager(settings, opener=fake_opener)
    room = mgr.register(REPO)
    mgr.wait_for_connect()
    channel = mgr.channel(room.id)
    channel.flush_error = RuntimeError("fatal: could not resolve host")
    mgr.send(room.id, "종료 전에 보낸 말")
    time.sleep(0.2)
    channel.flush_error = None
    mgr.stop()
    assert channel.unpushed() == [], "종료가 남은 말을 버렸다"


def test_아직_안_붙은_방의_아웃박스는_비어_있다(settings, fake_opener):
    """상태를 물어보다가 방을 여는 부작용이 나면 안 된다 (조회는 조회다)."""
    mgr = RoomManager(settings, opener=fake_opener)
    try:
        state = mgr.outbox_state("없는방")
        assert state.state == SYNCED and state.pending == 0
    finally:
        mgr.stop()


# ------------------------------------------------------------------ HTTP


def test_다시_보내기_엔드포인트(client, manager):
    room = manager.register(REPO)
    channel = manager.channel(room.id)
    channel.flush_error = RuntimeError("fatal: could not resolve host")
    manager.send(room.id, "못 나갈 말")

    box = manager.outbox(room.id)
    end = time.monotonic() + 5.0
    while time.monotonic() < end and box.state.state != STUCK:
        time.sleep(0.02)

    res = client.post(f"/api/rooms/{room.id}/outbox")
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json()["outbox"]["state"] in (STUCK, SENDING, SYNCED)

    channel.flush_error = None
    client.post(f"/api/rooms/{room.id}/outbox")
    assert box.wait_idle(10.0)
    assert channel.unpushed() == []

    info = client.get(f"/api/rooms/{room.id}/info").get_json()
    assert info["outbox"]["state"] == SYNCED


def test_없는_방의_다시_보내기는_404(client):
    assert client.post("/api/rooms/없는방/outbox").status_code == 404

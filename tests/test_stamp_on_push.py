"""전송의 새 계약 — **봉투는 원격에 push 될 때 생긴다.** 그 결과를 고정한다.

기반(gitwire)이 레코드의 시각·ID 를 *push 되는 순간*에 정한다 (`Channel.append`
도크: 작성 시각으로 굳히면 오프라인에서 쓴 말이 며칠 뒤 과거 날짜 ID 로 도착해
**아무도 안 봤는데 "전원 읽음"** 이 된다). 소비자 쪽에서 그 변경이 만드는 성질이
넷이고, 여기서 전부 못 박는다:

1. `send()` 는 **봉투 없는 티켓**을 준다 — HTTP 응답은 202 "받아 뒀다"다.
2. 읽음 커서는 **보내기로는 오르지 않는다** (발행 트리거가 없다 — 전송당
   커밋·push 가 하나다). 그런데도 *내 뱃지가 내 말로 늘지 않는다* · *남의
   화면에서 내가 안 읽은 사람으로 세어지지 않는다* 가 그대로 성립한다 —
   근거가 커서에서 **작성자 판정**으로 옮겨졌기 때문이다.
3. 못 나간 말은 **"보냈다"로 표시된 적이 없다** — SSE 로 흐르지도, 타임라인에
   보이지도 않고, `stuck` 으로 드러난다. (*보내는 중* 에서 사라지는 것은 괜찮고,
   *보냈다* 에서 사라지는 것은 안 된다.)
4. 커서에는 임시 ID(`~pending/…`)가 **절대** 들어가지 않는다 — 그 값이 한 번
   들어가면 사전식 최대값이라 카운트가 영구히 0 이 된다 (실측된 사고).

대역 채널을 쓴다 (`conftest.FakeChannel`). 진짜 git 왕복으로 같은 것을 보는 것은
`test_two_instances.py` 다.
"""

from __future__ import annotations

import time

import gitwire
import pytest

from gitwire_chat import reads as reads_mod
from gitwire_chat.app import create_app
from gitwire_chat.outbox import STUCK, SYNCED
from gitwire_chat.rooms import RoomManager

REPO = "https://example.com/team/room.git"


def _channel(fake_opener):
    return next(iter(fake_opener.channels.values()))


def _drain(sub, name: str) -> list[dict]:
    out = []
    while not sub.queue.empty():
        event = sub.queue.get_nowait()
        if event.name == name:
            out.append(event.data)
    return out


def _wait_stuck(box, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end and box.state.state != STUCK:
        time.sleep(0.02)
    assert box.state.state == STUCK, box.state


# ------------------------------------------------- 1. 응답에 봉투가 없다


def test_전송_응답은_받아_뒀다까지만_말한다(manager, fake_opener):
    """`send()` 는 티켓을 주고, HTTP 는 202 다 — 지어낸 봉투가 없다."""
    room = manager.register(REPO)
    ticket = RoomManager.send(manager, room.id, "안녕")      # 진짜 send

    assert isinstance(ticket, gitwire.PendingRecord)
    assert ticket.pushed is False
    with pytest.raises(gitwire.NotPushed):
        ticket.id

    app = create_app(manager.settings, manager, start=False)
    app.config.update(TESTING=True)
    with app.test_client() as client:
        res = client.post(f"/api/rooms/{room.id}/messages", json={"text": "또"})
    assert res.status_code == 202
    assert res.get_json() == {"queued": True}


# --------------------------- 2. 커서는 **보내기로는 아예 오르지 않는다**


def test_보내기는_커서를_올리지_않고_뱃지도_늘리지_않는다(manager, fake_opener):
    """⭐ 못 나간 동안에도, 나간 뒤에도 커서는 그대로다 — 그런데 뱃지는 0 이다.

    예전에는 push 가 끝난 뒤 그 ID 까지 커서를 올렸다(`_after_push`). 그 전진은
    카운트 공식에도(작성자는 분모에서 빠진다) 아카이브 합의에도(`archived` 만
    본다)영향이 없었고, 값은 **전송당 커밋·push 하나**로 치렀다. 뱃지가 내
    레코드를 세지 않게 된 지금은 올릴 이유가 남지 않는다.
    """
    room = manager.register(REPO)
    channel = _channel(fake_opener)
    manager.timeline(room.id)                          # 커서 파일 하나가 생긴다
    channel.flush_error = RuntimeError("fatal: could not resolve host github.com")

    RoomManager.send(manager, room.id, "못 나갈 내 말")
    _wait_stuck(manager.outbox(room.id))

    view = manager.read_view(room.id)
    assert view.cursor == "", "나가지도 않은 말로 커서가 움직였다"
    assert "~" not in view.cursor

    channel.flush_error = None                         # 네트워크가 돌아왔다
    writes = channel.state_writes
    manager.flush_outbox(room.id)
    assert manager.outbox(room.id).wait_idle(10.0)

    landed = channel.records[-1]
    assert gitwire.is_record_id(landed.id)
    view = manager.read_view(room.id)
    assert view.cursor == "", "나간 뒤 보내기 경로가 커서를 밀었다"
    mine = [p for p in view.participants if p.key == view.me][0]
    assert mine.cursor == "", "보내기가 커서를 발행했다"
    # ⭐ 두 번째 push 가 생기지 않는다 — 참가자 상태 파일을 아예 안 만졌다.
    assert channel.state_writes == writes, "보내기가 참가자 상태 파일을 다시 썼다"
    # 그런데도 내 말은 내 뱃지에 뜨지 않는다 (판정은 커서가 아니라 작성자다).
    assert view.unread == 0, "내 말이 내 뱃지를 늘렸다"
    assert view.first_unread is None


def test_남의_화면에서_내가_안_읽은_사람으로_세어지지_않는다(manager, fake_opener):
    """카운트 공식의 `p ≠ A` — **작성자 판정**이 그것을 성립시킨다 (커서가 아니다).

    남의 화면이 세는 값은 발행된 커서 집합 + 각자의 `senders` 목록이다
    (`static/js/reads.js` 의 `countUnread`). 그 계산을 그대로 재현해 "나는 내
    메시지를 안 읽은 사람으로 세어지지 않는다"를 확인한다 — 내 발행 커서가
    **빈 문자열인 채로도** 성립하는 것이 이 변경의 요점이다.
    """
    room = manager.register(REPO)
    channel = _channel(fake_opener)
    manager.timeline(room.id)
    channel.inject_state("bob@example.com", reads_mod.build_value("", ["bob.host"]))

    manager.send(room.id, "내 말")                     # 나갈 때까지 기다린다
    landed = channel.records[-1]

    published = {
        key: reads_mod.parse_state(state)
        for key, state in channel.read_states().items()
    }
    me = manager.person
    assert published[me] is not None
    assert published[me].cursor == "", "보내기가 커서를 발행했다"
    # ⭐ 남의 화면이 세는 것과 **같은 계산**: 작성자(`p ≠ A`)를 먼저 빼고,
    #    그 다음에 커서를 본다. 짝짓기 근거는 봉투의 sender ∈ 그 사람의 senders.
    unread_by = {
        key: bool(
            state
            and landed.sender not in state.senders        # ① 작성자는 안 센다
            and (not state.cursor or state.cursor < landed.id)   # ② 커서
        )
        for key, state in published.items()
    }
    assert unread_by[me] is False, "내 커서가 안 올랐다고 내가 세어졌다"
    assert unread_by["bob@example.com"] is True        # 밥은 아직 안 읽었다
    # ①이 판정의 전부라는 것 — 내 파일에 내 설치본이 적혀 있기 때문이다.
    assert landed.sender in published[me].senders


# ------------------------------- 3. 못 나간 말은 "보냈다"로 표시된 적이 없다


def test_못_나간_말은_보냈다고_표시되지_않는다(manager, fake_opener):
    """⭐ *보내는 중* 에서 사라지는 것은 괜찮고, *보냈다* 에서 사라지면 안 된다.

    "보냈다"의 화면 표현은 **봉투가 달린 메시지**다 (SSE `message` 이벤트 ·
    타임라인의 한 줄). 나가지 못한 동안에는 그 둘 어디에도 나타나지 않아야 한다 —
    그래야 프로세스가 죽어 대기열이 사라져도 사용자가 본 것과 어긋나지 않는다.
    """
    room = manager.register(REPO)
    channel = _channel(fake_opener)
    channel.flush_error = RuntimeError("fatal: could not resolve host github.com")
    sub = manager.bus.subscribe(room.id, "tab1")

    RoomManager.send(manager, room.id, "못 나갈 말")
    _wait_stuck(manager.outbox(room.id))

    assert _drain(sub, "message") == [], "안 나간 말이 봉투처럼 흘렀다"
    assert manager.timeline(room.id).messages == []
    # 조용히 사라지지 않는다 — 방 단위 상태로 드러난다.
    assert manager.outbox_state(room.id).state == STUCK
    assert manager.outbox_state(room.id).pending == 1

    channel.flush_error = None
    manager.flush_outbox(room.id)
    assert manager.outbox(room.id).wait_idle(10.0)

    # 나간 **그때** 처음으로 봉투가 흐른다.
    got = _drain(sub, "message")
    assert [m["text"] for m in got] == ["못 나갈 말"]
    assert gitwire.is_record_id(got[0]["id"])
    assert got[0]["mine"] is True
    assert [m.text for m in manager.timeline(room.id).messages] == ["못 나갈 말"]
    assert manager.outbox_state(room.id).state == SYNCED


def test_대기열은_다음_기동이_밀어내지_않는다(settings, fake_opener):
    """⭐ 의도된 성질 — 죽으면 안 나간 것은 사라진다.

    기반 쪽 증명은 `gitwire/tests/test_stamp_on_push.py` 가 실제 클론으로 한다
    (메모리 대기열이므로 물려줄 것이 없다). 여기서 보는 것은 **앱이 그 사실에
    기대어 아무 복구 시도도 하지 않는다**는 것이다 — 예전에는 붙자마자 한 번
    밀어(`_drain_outbox`) 지난 실행의 잔여분을 내보냈다.
    """
    mgr = RoomManager(settings, opener=fake_opener)
    try:
        room = mgr.register(REPO)
        mgr.wait_for_connect()
        channel = mgr.channel(room.id)
        assert mgr.outbox(room.id).wait_idle(10.0)
        assert channel.flushes == 0, "붙기만 했는데 밀었다"
        assert not hasattr(mgr, "_drain_outbox"), "죽은 복구 경로가 남아 있다"
    finally:
        mgr.stop()


# ----------------------------------------- 4. 임시 ID 는 커서가 되지 않는다


def test_보내는_중에도_커서로_임시_ID_가_나가지_않는다(manager, fake_opener):
    """세 가드가 살아 있는지 — 쓰기 거부 · 저장값 위생 · 형식 판정."""
    room = manager.register(REPO)
    channel = _channel(fake_opener)
    manager.timeline(room.id)
    channel.flush_error = RuntimeError("fatal: could not resolve host")
    RoomManager.send(manager, room.id, "보내는 중인 말")
    _wait_stuck(manager.outbox(room.id))

    # ① 쓰기 — 화면이 임시 ID 를 올려도 거부한다 (조용히 저장하지 않는다)
    with pytest.raises(reads_mod.InvalidCursor):
        manager.mark_read(room.id, "~pending/000001")
    # ② 저장값 — 이미 오염된 값은 '커서 없음'으로 읽는다
    assert reads_mod.sane_cursor("~pending/000001") == ""
    # ③ 형식 — 판정의 주인은 기반이다
    assert gitwire.is_record_id("~pending/000001") is False

    view = manager.read_view(room.id)
    assert view.cursor == ""
    assert all("~" not in (p.cursor or "") for p in view.participants)

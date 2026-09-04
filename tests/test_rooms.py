"""방 관리 — 등록, 타임라인, 전송·로컬 에코, 중복 방어, 알림 판정.

여기서는 gitwire 채널을 대역으로 갈아끼운다 (네트워크·git 없음).
"""

from __future__ import annotations

import pytest

import gitwire

from gitwire_chat import schema
from gitwire_chat.rooms import RoomError, RoomNotReady, room_id_for

from conftest import FakeChannel

REPO = "https://example.invalid/team/room.git"


def test_같은_레포는_표기가_달라도_같은_방이다():
    assert room_id_for(REPO) == room_id_for("https://example.invalid/team/room")
    assert room_id_for(REPO) == room_id_for("HTTPS://EXAMPLE.INVALID/team/room.git")
    assert room_id_for(REPO) != room_id_for("https://example.invalid/team/other.git")


def test_방_등록은_채널을_열고_목록에_남는다(manager, fake_opener):
    room = manager.register(REPO, name="우리 방")
    assert room.id == room_id_for(REPO)
    assert [r.id for r in manager.rooms()] == [room.id]
    # 클론은 chats/ 아래로 간다 (gitwire home 주입).
    channel = fake_opener.channels[list(fake_opener.channels)[0]]
    assert channel.kwargs["home"] == manager.home
    assert channel.kwargs["consumer"] == "chat"


def test_등록은_디스크에_남아_재시작을_견딘다(manager, settings, fake_opener):
    manager.register(REPO, name="우리 방")
    from gitwire_chat.rooms import RoomManager

    reborn = RoomManager(settings, opener=fake_opener)
    assert [r.name for r in reborn.rooms()] == ["우리 방"]


def test_빈_주소는_거부된다(manager):
    with pytest.raises(RoomError):
        manager.register("   ")


def test_클론에_실패해도_방은_사라지지_않고_사유가_남는다(settings):
    """예전에는 등록이 취소되며 방이 통째로 사라져 *왜* 안 됐는지 알 수 없었다."""
    from gitwire_chat.rooms import FAILED, READY, RoomManager

    attempts = []

    def flaky(url, **kw):
        attempts.append(url)
        if len(attempts) == 1:
            raise gitwire.AuthError("authentication failed")
        return FakeChannel(url, **kw)

    mgr = RoomManager(settings, opener=flaky)
    try:
        room = mgr.register(REPO)          # 즉시 돌아온다
        mgr.wait_for_connect()

        assert [r.id for r in mgr.rooms()] == [room.id], "방이 사라졌다"
        status = mgr.status(room.id)
        assert status.state == FAILED
        assert status.code == "auth"
        assert "토큰" in status.hint and "GITWIRE_TOKEN" in status.hint
        payload = mgr.rooms_payload()[0]
        assert payload["status"]["state"] == FAILED

        # 읽기·쓰기는 "아직 아니다"로 갈린다 (일반 오류가 아니다)
        with pytest.raises(RoomNotReady):
            mgr.timeline(room.id)
        with pytest.raises(RoomNotReady):
            mgr.send(room.id, "안녕")

        # 재시도하면 붙는다
        mgr.reconnect(room.id)
        mgr.wait_for_connect()
        assert mgr.status(room.id).state == READY
        assert mgr.timeline(room.id).messages == []
    finally:
        mgr.stop()


def test_등록은_클론을_기다리지_않고_즉시_돌아온다(settings):
    """⭐ G-1 의 요점 — 느린 클론이 HTTP 요청을 붙잡지 않는다."""
    import threading
    import time
    from gitwire_chat.rooms import CONNECTING, READY, RoomManager

    release = threading.Event()

    def slow(url, **kw):
        release.wait(10.0)                 # 느린 네트워크·큰 레포를 흉내낸다
        return FakeChannel(url, **kw)

    mgr = RoomManager(settings, opener=slow)
    try:
        t0 = time.monotonic()
        room = mgr.register(REPO)
        elapsed = time.monotonic() - t0

        assert elapsed < 1.0, f"등록이 클론을 기다렸다 ({elapsed:.1f}s)"
        assert mgr.status(room.id).state == CONNECTING
        assert mgr.rooms_payload()[0]["status"]["state"] == CONNECTING
        with pytest.raises(RoomNotReady):  # 아직 못 읽는다 — 그리고 그게 보인다
            mgr.timeline(room.id)

        release.set()
        mgr.wait_for_connect()
        assert mgr.status(room.id).state == READY
    finally:
        release.set()
        mgr.stop()


def test_보내면_레코드가_남고_로컬_에코가_즉시_흐른다(manager, fake_opener):
    room = manager.register(REPO)
    sub = manager.bus.subscribe(room.id, client="tab")

    message = manager.send(room.id, "안녕", author="최윤혁")

    assert message.author == "최윤혁" and message.text == "안녕"
    assert sub.queue.qsize() == 1              # 폴 주기를 기다리지 않는다
    channel = list(fake_opener.channels.values())[0]
    assert channel.records[-1].payload["text"] == "안녕"
    assert channel.records[-1].payload["kind"] == "msg"


def test_같은_메시지가_두_번_와도_한_번만_흐른다(manager, fake_opener):
    """로컬 에코 + 구독 재전달 = 중복. 봉투 ID 로 멱등하게 막는다."""
    room = manager.register(REPO)
    sub = manager.bus.subscribe(room.id)
    channel = list(fake_opener.channels.values())[0]

    message = manager.send(room.id, "안녕")
    record = channel.records[-1]
    assert manager.on_record(room.id, record) is False   # 같은 ID → 무시
    assert manager.on_record(room.id, record) is False
    assert sub.queue.qsize() == 1
    assert message.id == record.id


def test_남이_보낸_것은_보는_사람이_없을_때만_알림(manager, fake_opener):
    room = manager.register(REPO)
    channel = list(fake_opener.channels.values())[0]
    notifier = manager.notifier

    # (1) 아무도 안 보고 있다 → 알림
    channel.inject(schema.build_payload("영희", "밥 먹자"))
    manager.poll_now(room.id)
    assert notifier.sent and "영희" in notifier.sent[-1][1]

    # (2) 탭이 보고 있다 → 알림 없음
    notifier.sent.clear()
    manager.bus.subscribe(room.id, client="tab")
    channel.inject(schema.build_payload("영희", "왔어?"))
    manager.poll_now(room.id)
    assert notifier.sent == []


def test_내가_보낸_것은_알림하지_않는다(manager, fake_opener):
    room = manager.register(REPO)
    manager.send(room.id, "혼잣말")
    assert manager.notifier.sent == []


def test_타임라인은_최근_N건만(manager, fake_opener):
    room = manager.register(REPO)
    for i in range(12):
        manager.send(room.id, f"메시지 {i}")
    recent = manager.timeline(room.id)          # settings.recent_limit == 5
    assert [m.text for m in recent] == [f"메시지 {i}" for i in range(7, 12)]
    assert recent.has_more is True              # 위에 더 있다 (기반이 알려준 값)


def test_이전_불러오기는_그_앞을_준다(manager):
    room = manager.register(REPO)
    for i in range(12):
        manager.send(room.id, f"메시지 {i}")
    recent = manager.timeline(room.id)
    older = manager.older(room.id, recent.oldest)   # page_limit == 3
    assert [m.text for m in older] == ["메시지 4", "메시지 5", "메시지 6"]
    assert older.has_more is True

    # 계속 거슬러 올라가다 처음에 닿으면 빈 쪽 + has_more=False.
    cursor = older.oldest
    seen = []
    for _ in range(10):
        page = manager.older(room.id, cursor)
        if not page.messages:
            break
        seen = list(page.messages) + seen
        cursor = page.oldest
        if not page.has_more:
            break
    assert [m.text for m in seen] == [f"메시지 {i}" for i in range(0, 4)]

    # 맨 위에서는 **더 없다고 말한다** — 무한 스크롤의 종료 조건이다.
    assert manager.older(room.id, cursor).has_more is False


def test_페이징은_기반의_keyset_커서를_그대로_쓴다(manager, fake_opener):
    """소비자가 자기 방식으로 자르지 않는다 — before= 를 기반에 넘긴다."""
    room = manager.register(REPO)
    for i in range(9):
        manager.send(room.id, f"메시지 {i}")
    channel = fake_opener.channels[gitwire.normalize_repo_url(REPO)]

    calls = []
    original = channel.history_page

    def spy(*, before=None, limit=50, fresh=True):
        calls.append((before, limit, fresh))
        return original(before=before, limit=limit, fresh=fresh)

    channel.history_page = spy
    first = manager.timeline(room.id)
    second = manager.older(room.id, first.oldest)

    # recent_limit=5, page_limit=3. fresh=False = 읽기는 원격을 보지 않는다.
    assert calls == [(None, 5, False), (first.oldest, 3, False)]
    assert [m.text for m in second] == ["메시지 1", "메시지 2", "메시지 3"]
    # 전량 읽기(history(None))로 흘러가지 않았다.
    assert all(c[1] is not None for c in calls)


def test_검색은_DOM_에_없는_과거까지_뒤진다(manager):
    room = manager.register(REPO)
    for i in range(30):
        manager.send(room.id, f"메시지 {i}", author="최윤혁")
    manager.send(room.id, "점심 뭐 먹지", author="영희")

    hits = manager.search(room.id, "점심")
    assert [m.text for m in hits] == ["점심 뭐 먹지"]

    # 최근 5건 밖(맨 앞)에 있는 것도 찾는다.
    assert [m.text for m in manager.search(room.id, "메시지 0")] == ["메시지 0"]
    # 작성자로도 찾는다.
    assert len(manager.search(room.id, "영희")) == 1
    assert manager.search(room.id, "  ") == []


def test_등록_해제는_구독과_채널을_정리한다(manager, fake_opener):
    room = manager.register(REPO)
    manager.start()
    channel = list(fake_opener.channels.values())[0]
    assert channel.subscribers

    manager.unregister(room.id)
    assert manager.rooms() == []
    assert channel.subscribers == []
    assert channel.closed is True


def test_구독_시작은_백로그를_건너뛴다(manager, fake_opener):
    """이미 화면에 있는 과거 대화로 알림 폭탄이 터지면 안 된다."""
    room = manager.register(REPO)
    channel = list(fake_opener.channels.values())[0]
    for i in range(5):
        channel.inject(schema.build_payload("영희", f"과거 {i}"))

    manager.notifier.sent.clear()
    manager.start()
    assert channel.skipped_to == 5
    assert manager.notifier.sent == []

    channel.inject(schema.build_payload("영희", "새 메시지"))
    assert manager.notifier.sent and "새 메시지" in manager.notifier.sent[-1][1]


def test_한_방이_실패해도_나머지는_돈다(settings, fake_opener):
    from gitwire_chat.rooms import RoomManager

    good = "https://example.invalid/a.git"
    bad = "https://example.invalid/b.git"

    def opener(url, **kw):
        if "b.git" in url:
            raise OSError("이 방은 못 연다")
        return fake_opener(url, **kw)

    mgr = RoomManager(settings, opener=opener)
    mgr.register(good)
    # 목록 파일에 직접 손상된 방을 끼워 넣은 상황을 흉내 낸다.
    from gitwire_chat.config import Room

    mgr._rooms["broken"] = Room(id="broken", repo_url=bad)
    mgr.start()          # 예외가 밖으로 나가지 않는다
    assert len(mgr.rooms()) == 2
    mgr.stop()


def test_내용이_있는_레포는_사유가_구분된다(settings):
    """gitwire 가 막아 준 것을 사용자 말로 옮긴다 (기반의 안전장치와 짝)."""
    from gitwire_chat.rooms import FAILED, RoomManager

    def busy_repo(url, **kw):
        raise gitwire.ChannelInitError(
            "이 레포에는 이미 내용이 있다 (src/app.py). 채널 규약은 **빈 레포에만** 심는다"
        )

    mgr = RoomManager(settings, opener=busy_repo)
    try:
        room = mgr.register(REPO)
        mgr.wait_for_connect()
        status = mgr.status(room.id)
        assert status.state == FAILED and status.code == "notempty"
        assert "빈 레포" in status.hint
        assert [r.id for r in mgr.rooms()] == [room.id]   # 방은 남는다
    finally:
        mgr.stop()

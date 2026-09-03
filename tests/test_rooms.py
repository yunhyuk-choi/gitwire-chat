"""방 관리 — 등록, 타임라인, 전송·로컬 에코, 중복 방어, 알림 판정.

여기서는 gitwire 채널을 대역으로 갈아끼운다 (네트워크·git 없음).
"""

from __future__ import annotations

import pytest

from gitwire_chat import schema
from gitwire_chat.rooms import RoomError, room_id_for

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


def test_채널을_못_열면_등록이_취소된다(settings):
    from gitwire_chat.rooms import RoomManager

    def broken(url, **kw):
        raise OSError("클론 실패")

    mgr = RoomManager(settings, opener=broken)
    with pytest.raises(RoomError):
        mgr.register(REPO)
    assert mgr.rooms() == []


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


def test_이전_불러오기는_그_앞을_준다(manager):
    room = manager.register(REPO)
    for i in range(12):
        manager.send(room.id, f"메시지 {i}")
    recent = manager.timeline(room.id)
    older = manager.older(room.id, recent[0].id)   # page_limit == 3
    assert [m.text for m in older] == ["메시지 4", "메시지 5", "메시지 6"]

    # 계속 거슬러 올라가다 처음에 닿으면 빈 목록.
    cursor = older[0].id
    seen = []
    for _ in range(10):
        page = manager.older(room.id, cursor)
        if not page:
            break
        seen = page + seen
        cursor = page[0].id
    assert [m.text for m in seen] == [f"메시지 {i}" for i in range(0, 4)]


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

"""⭐ 읽기 경로에서 **원격 왕복을 뗐다** — 그 대신 무엇이 신선도를 맡는가.

실측이 이 파일의 존재 이유다 (이 머신 · GitHub private repo):

    GET /api/rooms/<id>/messages?limit=50   1.4 ~ 2.9 초
      └ git ls-remote origin refs/heads/main   1.3 초   ← 바닥
        └ 실제 네트워크 왕복은 100ms 대. 나머지는 자격증명 헬퍼 프로세스 +
          HTTPS 왕복 3회 + git 프로세스 기동.

레코드는 이미 로컬 클론에 있고, 신선도는 **폴러**가 이미 맡고 있다. 그래서
조회는 `fresh=False` 로 로컬만 읽는다. 여기서 못 박는 것:

* **C-1** 최신 쪽·위로 페이징·검색 — 셋 다 원격을 보지 않는다.
* **C-2** 다만 "최신 쪽을 본다"는 신선도가 의미 있는 유일한 순간이므로,
  **화면을 막지 않고** 백그라운드로 한 번 당긴다. 위로 페이징은 당기지 않는다
  (과거는 이미 받은 것이다).
* **C-3** 그 당기기는 방당 1개다 — 방을 빠르게 오가도 스레드를 쌓지 않는다.
* **C-4** 자격증명 캐시는 **옵트인**이고, 켰을 때만 기반에 내려간다.

"폴러가 새 메시지를 계속 가져오는가"는 대역으로 증명할 수 없다 —
`test_two_instances.py` 의 「구독만으로」 케이스가 실제 git 으로 확인한다.
"""

from __future__ import annotations

import threading
import time

import gitwire
import pytest

from gitwire_chat.app import create_app
from gitwire_chat.config import Settings, load_settings

REPO = "https://example.invalid/team/room.git"


def _channel(fake_opener, repo=REPO):
    return fake_opener.channels[gitwire.normalize_repo_url(repo)]


def _settle(manager, room_id: str, timeout: float = 5.0) -> None:
    """진행 중인 '지금 당기기' 스레드를 기다린다 (테스트 결정성)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        thread = manager._refreshers.get(room_id)
        if thread is None or not thread.is_alive():
            return
        thread.join(timeout=0.2)


# ------------------------------------------------- C-1. 원격을 보지 않는다


def test_타임라인_조회는_원격을_보지_않는다(manager, fake_opener):
    room = manager.register(REPO)
    for i in range(9):
        manager.send(room.id, f"메시지 {i}")
    channel = _channel(fake_opener)

    channel.read_fresh.clear()
    first = manager.timeline(room.id)
    manager.older(room.id, first.oldest)
    manager.search(room.id, "메시지")

    assert channel.read_fresh, "읽기가 아예 일어나지 않았다 (테스트가 헛돌았다)"
    assert channel.read_fresh == [False] * len(channel.read_fresh), (
        "읽기 경로 어딘가가 아직 원격을 본다"
    )


def test_HTTP_조회도_원격을_보지_않는다(manager, fake_opener):
    room = manager.register(REPO)
    for i in range(9):
        manager.send(room.id, f"메시지 {i}")
    app = create_app(manager.settings, manager, start=False)
    app.config.update(TESTING=True)
    client = app.test_client()

    channel = _channel(fake_opener)
    channel.read_fresh.clear()
    latest = client.get(f"/api/rooms/{room.id}/messages?limit=5").get_json()
    assert len(latest["messages"]) == 5 and latest["has_more"] is True
    older = client.get(
        f"/api/rooms/{room.id}/messages?before={latest['messages'][0]['id']}&limit=3"
    ).get_json()
    assert [m["text"] for m in older["messages"]] == [
        "메시지 1", "메시지 2", "메시지 3"
    ]
    assert channel.read_fresh == [False, False]

    # 계약은 그대로다 — 두 쪽이 겹치지도, 빠뜨리지도 않는다.
    ids = [m["id"] for m in older["messages"]] + [m["id"] for m in latest["messages"]]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)


# ----------------------------------- C-2/C-3. 신선도는 막지 않고 뒤따라온다


def test_최신_쪽을_열면_백그라운드로_한_번_당긴다(manager, fake_opener):
    """방을 여는 순간만 당긴다. **응답을 기다리게 하지 않는다.**"""
    room = manager.register(REPO)
    manager.start()
    channel = _channel(fake_opener)

    before = channel.polls
    manager.timeline(room.id)
    _settle(manager, room.id)
    assert channel.polls == before + 1, "최신 쪽을 열었는데 당기지 않았다"

    # 위로 거슬러 올라가기는 원격과 무관하다 — 당기지 않는다.
    older_before = channel.polls
    manager.older(room.id, "records/29991231/zzz.json")
    _settle(manager, room.id)
    assert channel.polls == older_before, "과거 페이징이 원격을 당겼다"


def test_당기기는_방당_하나다(manager, fake_opener):
    """방을 빠르게 오가도 ls-remote 스레드를 쌓지 않는다.

    실제 폴은 1.3초짜리 원격 왕복이므로, 겹치는 동안 또 부르는 것이 문제다.
    그래서 대역을 **일부러 멈춰 세워** 겹치는 상황을 만든다.
    """
    room = manager.register(REPO)
    manager.start()
    channel = _channel(fake_opener)

    entered = threading.Event()
    release = threading.Event()
    original = channel.poll_once

    def slow(callback, **kwargs):
        entered.set()
        release.wait(5.0)
        return original(callback, **kwargs)

    channel.poll_once = slow
    first = manager.refresh_async(room.id)
    assert entered.wait(5.0), "당기기가 시작되지 않았다"

    threads = {manager.refresh_async(room.id) for _ in range(20)}
    assert threads == {first}, "당기는 중에 스레드가 또 생겼다"

    release.set()
    _settle(manager, room.id)
    assert channel.polls == 1, "폴이 겹쳐서 나갔다"


def test_당기기_실패는_조회를_깨지_않는다(manager, fake_opener):
    """폴이 터져도 화면에는 로컬 기록이 그대로 나온다."""
    room = manager.register(REPO)
    manager.send(room.id, "이미 받아 둔 말")
    manager.start()
    channel = _channel(fake_opener)

    def boom(*args, **kwargs):
        raise RuntimeError("원격이 죽었다")

    channel.poll_once = boom
    page = manager.timeline(room.id)
    _settle(manager, room.id)
    assert [m.text for m in page.messages] == ["이미 받아 둔 말"]


# --------------------------------------------- C-4. 자격증명 캐시는 옵트인


def test_자격증명_캐시는_기본으로_꺼져_있다(settings, fake_opener):
    from gitwire_chat.rooms import RoomManager

    assert settings.credential_cache == 0.0
    mgr = RoomManager(settings, opener=fake_opener)
    try:
        mgr.register(REPO)
        mgr.wait_for_connect(timeout=10.0)
        assert "credential_helpers" not in _channel(fake_opener).kwargs
    finally:
        mgr.stop()


def test_켜면_기반에_로컬_helper_사슬로_내려간다(settings, fake_opener):
    from dataclasses import replace

    from gitwire_chat.rooms import RoomManager

    mgr = RoomManager(replace(settings, credential_cache=900.0), opener=fake_opener)
    try:
        mgr.register(REPO)
        mgr.wait_for_connect(timeout=10.0)
        helpers = _channel(fake_opener).kwargs["credential_helpers"]
        assert helpers == ["cache --timeout=900"]
    finally:
        mgr.stop()


@pytest.mark.parametrize(
    "raw, expected",
    [("", 0.0), ("900", 900.0), ("0", 0.0), ("이상한값", 0.0), ("-5", 0.0)],
)
def test_환경변수로만_켠다(monkeypatch, tmp_path, raw, expected):
    monkeypatch.setenv("GITWIRE_CHAT_HOME", str(tmp_path))
    if raw:
        monkeypatch.setenv("GITWIRE_CHAT_CREDENTIAL_CACHE", raw)
    else:
        monkeypatch.delenv("GITWIRE_CHAT_CREDENTIAL_CACHE", raising=False)
    assert load_settings().credential_cache == expected


def test_Settings_기본값(tmp_path):
    assert Settings(home=tmp_path).credential_cache == 0.0

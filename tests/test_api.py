"""HTTP 표면 — 셸 HTML 은 한 번, 그 뒤로는 JSON 과 SSE 뿐."""

from __future__ import annotations

import json
import threading

import pytest

from gitwire_chat.app import create_app

REPO = "https://example.invalid/team/room.git"


@pytest.fixture
def client(manager):
    app = create_app(manager.settings, manager, start=False)
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


def test_셸_HTML_은_메시지를_담지_않는다(client, manager):
    room = manager.register(REPO)
    manager.send(room.id, "서버가 렌더한 메시지가 아니다")

    html = client.get("/").get_data(as_text=True)
    assert "gitwire-chat" in html
    assert "app.js" in html
    # ⭐ 서버는 메시지를 HTML 로 굽지 않는다. 그래서 리렌더할 것 자체가 없다.
    assert "서버가 렌더한 메시지가 아니다" not in html
    assert 'id="messages"' in html


def test_방_등록과_목록(client):
    res = client.post("/api/rooms", json={"repo_url": REPO, "name": "우리 방"})
    assert res.status_code == 201
    room_id = res.get_json()["room"]["id"]

    rooms = client.get("/api/rooms").get_json()["rooms"]
    assert [r["id"] for r in rooms] == [room_id]
    assert rooms[0]["name"] == "우리 방"


def test_빈_주소는_400(client):
    res = client.post("/api/rooms", json={"repo_url": ""})
    assert res.status_code == 400
    assert "error" in res.get_json()


def test_보내기와_타임라인(client, manager):
    room = manager.register(REPO)
    res = client.post(
        f"/api/rooms/{room.id}/messages", json={"text": "안녕", "author": "최윤혁"}
    )
    assert res.status_code == 201
    message = res.get_json()["message"]
    assert message["author"] == "최윤혁" and message["text"] == "안녕"
    assert message["id"].startswith("records/")

    data = client.get(f"/api/rooms/{room.id}/messages").get_json()
    assert [m["text"] for m in data["messages"]] == ["안녕"]


def test_빈_본문은_400(client, manager):
    room = manager.register(REPO)
    res = client.post(f"/api/rooms/{room.id}/messages", json={"text": "   "})
    assert res.status_code == 400


def test_이전_불러오기_페이징(client, manager):
    room = manager.register(REPO)
    for i in range(12):
        manager.send(room.id, f"메시지 {i}")

    first = client.get(f"/api/rooms/{room.id}/messages").get_json()
    assert len(first["messages"]) == 5 and first["maybe_more"] is True

    oldest = first["messages"][0]["id"]
    page = client.get(
        f"/api/rooms/{room.id}/messages?before={oldest}"
    ).get_json()
    assert [m["text"] for m in page["messages"]] == ["메시지 4", "메시지 5", "메시지 6"]


def test_검색(client, manager):
    room = manager.register(REPO)
    for i in range(20):
        manager.send(room.id, f"메시지 {i}")
    manager.send(room.id, "점심 뭐 먹지")

    data = client.get(f"/api/rooms/{room.id}/search?q=점심").get_json()
    assert [m["text"] for m in data["messages"]] == ["점심 뭐 먹지"]


def test_가시성_보고(client, manager):
    room = manager.register(REPO)
    manager.bus.subscribe(room.id, client="tab1")
    assert manager.bus.viewers(room.id) == 1

    client.post(
        f"/api/rooms/{room.id}/visibility", json={"visible": False, "client": "tab1"}
    )
    assert manager.bus.viewers(room.id) == 0


def test_없는_방은_400(client):
    assert client.get("/api/rooms/없는방/messages").status_code == 400
    assert client.post("/api/rooms/없는방/messages", json={"text": "x"}).status_code == 400


def test_SSE_는_새_메시지만_흘린다(client, manager):
    room = manager.register(REPO)
    manager.send(room.id, "이건 스트림 열기 전 메시지")

    res = client.get(f"/api/rooms/{room.id}/stream?client=tab1", buffered=False)
    assert res.headers["Content-Type"].startswith("text/event-stream")
    assert res.headers["Cache-Control"].startswith("no-cache")
    assert res.headers["X-Accel-Buffering"] == "no"

    chunks = res.response
    assert next(chunks).decode("utf-8").startswith("retry:")
    assert "event: hello" in next(chunks).decode("utf-8")

    def later():
        manager.send(room.id, "스트림으로 오는 메시지")

    threading.Timer(0.05, later).start()

    payload = None
    for _ in range(50):
        chunk = next(chunks).decode("utf-8")
        if chunk.startswith("event: message"):
            payload = json.loads(chunk.split("data: ", 1)[1])
            break
    assert payload is not None
    assert payload["text"] == "스트림으로 오는 메시지"
    # ⭐ 과거 메시지는 스트림으로 오지 않는다 — 스트림은 '증분'만 나른다.
    assert payload["text"] != "이건 스트림 열기 전 메시지"
    res.close()

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
    """보내기 응답은 **"받아 뒀다"(202)** 까지만 말한다 — 봉투가 없다.

    ⭐ 레코드의 ID·시각은 원격에 push 되는 순간에 정해진다(`rooms.send` 도크).
    그래서 이 응답에 봉투를 실을 수가 없고, 실으려면 지어내야 한다. 화면은
    낙관적 항목으로 그리고, 진짜 봉투는 push 뒤 SSE 로 온다.
    """
    room = manager.register(REPO)
    res = client.post(
        f"/api/rooms/{room.id}/messages", json={"text": "안녕", "author": "최윤혁"}
    )
    assert res.status_code == 202
    assert res.get_json() == {"queued": True}
    assert "message" not in res.get_json(), "없는 봉투를 지어냈다"

    # 나간 뒤에는 조회 경로에 그대로 보인다 (기다리는 것은 테스트용 매니저다).
    assert manager.outbox(room.id).wait_idle(10.0)
    message = client.get(
        f"/api/rooms/{room.id}/messages"
    ).get_json()["messages"][-1]
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
    assert len(first["messages"]) == 5 and first["has_more"] is True

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


# ------------------------------------------------ G. 연결 상태 · 레포 만들기

from conftest import FakeChannel  # noqa: E402


def test_방_등록은_즉시_돌아오고_상태가_함께_온다(client, manager):
    """클론은 백그라운드다 — 응답에 '받는 중' 이 실려 화면이 설명할 수 있다."""
    res = client.post("/api/rooms", json={"repo_url": REPO, "name": "우리 방"})
    assert res.status_code == 201
    room = res.get_json()["room"]
    # 응답에 연결 상태가 함께 온다 — 화면이 곧바로 '받는 중' 을 그릴 수 있다.
    assert room["status"]["state"] in ("connecting", "ready")

    manager.wait_for_connect()
    listed = client.get("/api/rooms").get_json()["rooms"]
    assert listed[0]["status"]["state"] == "ready"


def test_아직_안_붙은_방은_409_와_상태를_돌려준다(settings):
    """오류(400)가 아니라 **상태**(409)다 — 화면이 '받는 중' 을 그릴 수 있게."""
    import threading

    from gitwire_chat.app import create_app
    from gitwire_chat.events import EventBus
    from gitwire_chat.rooms import RoomManager

    release = threading.Event()

    def slow(url, **kw):
        release.wait(10.0)
        return FakeChannel(url, **kw)

    mgr = RoomManager(settings, bus=EventBus(keepalive=0.05), opener=slow)
    slow_app = create_app(settings, mgr, start=False)
    slow_client = slow_app.test_client()
    try:
        created = slow_client.post("/api/rooms", json={"repo_url": REPO})
        assert created.status_code == 201
        room_id = created.get_json()["room"]["id"]

        got = slow_client.get(f"/api/rooms/{room_id}/messages")
        assert got.status_code == 409
        body = got.get_json()
        assert body["status"]["state"] == "connecting"
        assert body["error"]

        sent = slow_client.post(f"/api/rooms/{room_id}/messages", json={"text": "안녕"})
        assert sent.status_code == 409
    finally:
        release.set()
        mgr.stop()


def test_실패한_방은_재시도할_수_있다(settings):
    import gitwire

    from gitwire_chat.app import create_app
    from gitwire_chat.events import EventBus
    from gitwire_chat.rooms import RoomManager

    attempts = []

    def flaky(url, **kw):
        attempts.append(url)
        if len(attempts) == 1:
            raise gitwire.AuthError("authentication failed")
        return FakeChannel(url, **kw)

    mgr = RoomManager(settings, bus=EventBus(keepalive=0.05), opener=flaky)
    flaky_app = create_app(settings, mgr, start=False)
    flaky_client = flaky_app.test_client()
    try:
        room_id = flaky_client.post("/api/rooms", json={"repo_url": REPO}).get_json()[
            "room"
        ]["id"]
        mgr.wait_for_connect()
        listed = flaky_client.get("/api/rooms").get_json()["rooms"]
        assert listed[0]["status"]["state"] == "failed"
        assert listed[0]["status"]["code"] == "auth"
        assert "GITWIRE_TOKEN" in listed[0]["status"]["hint"]

        retried = flaky_client.post(f"/api/rooms/{room_id}/retry")
        assert retried.status_code == 200
        mgr.wait_for_connect()
        assert flaky_client.get("/api/rooms").get_json()["rooms"][0]["status"][
            "state"
        ] == "ready"
    finally:
        mgr.stop()


def test_레포_만들기_계획은_무엇이_만들어지는지_알려준다(client, monkeypatch):
    """토큰이 없으면 링크 모드 — 프리필된 주소와 유도된 clone_url 을 준다."""
    monkeypatch.delenv("GITWIRE_TOKEN", raising=False)
    res = client.post(
        "/api/repos/plan",
        json={"host": "github.com", "name": "우리 방", "owner": "yunhyuk-choi"},
    )
    assert res.status_code == 200
    plan = res.get_json()
    assert plan["mode"] == "link"
    assert plan["private"] is True
    assert plan["name"] == "chat-room"          # 한국어 이름 → 기본값(사용자가 고친다)
    assert "visibility=private" in plan["link"]
    assert plan["clone_url"] == "https://github.com/yunhyuk-choi/chat-room.git"


def test_모르는_호스트는_거들지_않는다(client):
    plan = client.post(
        "/api/repos/plan", json={"host": "git.example.internal", "name": "room"}
    ).get_json()
    assert plan["mode"] == "manual"
    assert plan["link"] == "" and plan["clone_url"] == ""


def test_토큰이_있으면_앱_안에서_만들_계획이_된다(client, monkeypatch):
    monkeypatch.setenv("GITWIRE_TOKEN", "ghp_secret_value")
    monkeypatch.setattr(
        "gitwire_chat.forges.github_login", lambda token: "yunhyuk-choi"
    )
    plan = client.post(
        "/api/repos/plan", json={"host": "github.com", "name": "our-room"}
    ).get_json()
    assert plan["mode"] == "api"
    assert plan["owner"] == "yunhyuk-choi"       # 누구 계정에 만들지 미리 보인다
    assert "ghp_secret" not in str(plan)         # ⚠️ 토큰은 응답에 없다


def test_레포_생성은_토큰이_없으면_사유를_준다(client, monkeypatch):
    monkeypatch.delenv("GITWIRE_TOKEN", raising=False)
    res = client.post("/api/repos", json={"host": "github.com", "name": "our-room"})
    assert res.status_code == 400
    body = res.get_json()
    assert body["code"] == "token" and "GITWIRE_TOKEN" in body["error"]


def test_레포_생성_실패는_사유와_힌트를_그대로_전달한다(client, monkeypatch):
    from gitwire_chat import forges

    monkeypatch.setenv("GITWIRE_TOKEN", "ghp_secret_value")

    def boom(*args, **kwargs):
        raise forges.ForgeError(
            "만들 수 없는 이름이다 (name already exists)", code="name",
            hint="다른 이름을 넣어라.",
        )

    monkeypatch.setattr("gitwire_chat.forges.create_github_repo", boom)
    res = client.post("/api/repos", json={"host": "github.com", "name": "our-room"})
    assert res.status_code == 400
    body = res.get_json()
    assert body["code"] == "name" and body["hint"]
    assert "ghp_secret" not in str(body)


def test_레포_생성_성공은_주소를_돌려준다(client, monkeypatch):
    monkeypatch.setenv("GITWIRE_TOKEN", "ghp_secret_value")
    monkeypatch.setattr(
        "gitwire_chat.forges.create_github_repo",
        lambda *a, **k: {
            "full_name": "me/our-room", "private": True,
            "clone_url": "https://github.com/me/our-room.git", "html_url": "",
        },
    )
    res = client.post("/api/repos", json={"host": "github.com", "name": "our-room"})
    assert res.status_code == 201
    assert res.get_json()["repo"]["clone_url"] == "https://github.com/me/our-room.git"


# ---------------------------------------- G-3. 자격증명 탐색 · 발급 거들기

"""⭐ 자격증명은 **선택**이고, 그 상태를 화면이 알아야 한다.

기반(gitwire)은 자격증명을 기동 시 한 번 읽어 들고 쓴다. 못 찾으면 git 이
대화형으로 묻는데 이 앱은 창 없이 돌아 물어볼 곳이 없다 — 조용히 멈춘다.
그래서 서버가 "어디 있나"를 답한다.

⚠️ 이 묶음이 가장 중요하게 보는 것: **응답에 값이 없다.**
"""

SECRET_TOKEN = "ghp_ThisIsASecretTokenValue1234567890"


def _no_git(monkeypatch, reply=None):
    """git 을 부르지 않게 한다 (기본: helper 가 아무것도 못 준 상태)."""
    from gitwire_chat import tokens

    empty = (1, "", "fatal: could not read Username: terminal prompts disabled")
    calls = []

    def runner(args, stdin=""):
        calls.append((list(args), stdin))
        return reply or empty

    monkeypatch.setattr(tokens, "_run_git", runner)
    return calls


def test_토큰_상태는_출처만_말한다(client, monkeypatch):
    monkeypatch.setenv("GITWIRE_TOKEN", SECRET_TOKEN)
    _no_git(monkeypatch)
    body = client.get("/api/token").get_json()
    assert body["found"] is True and body["source"] == "env"
    assert "환경변수 GITWIRE_TOKEN" in body["label"]
    # ⚠️ 값은 응답 어디에도 없다.
    assert SECRET_TOKEN not in json.dumps(body, ensure_ascii=False)


def test_토큰이_없으면_왜_못_찾았는지까지_말한다(client, monkeypatch):
    monkeypatch.delenv("GITWIRE_TOKEN", raising=False)
    _no_git(monkeypatch)
    body = client.get("/api/token").get_json()
    assert body["found"] is False and body["source"] == "none"
    # 조용한 실패 금지 — git 이 준 사유가 남는다
    assert "terminal prompts disabled" in body["detail"]


def test_토큰_상태는_한_번만_git_을_부른다(client, monkeypatch):
    """`git credential fill` 은 실측 435ms — 요청마다 부르면 화면이 느려진다."""
    monkeypatch.delenv("GITWIRE_TOKEN", raising=False)
    calls = _no_git(monkeypatch)
    for _ in range(3):
        client.get("/api/token")
    assert len(calls) == 1, calls
    # 화면이 명시로 다시 물으면 그때는 본다 (만료·취소된 토큰을 바꾼 뒤)
    client.get("/api/token?fresh=1")
    assert len(calls) == 2, calls


def test_토큰이_없어도_방_등록과_전송은_그대로_된다(client, manager, monkeypatch):
    """⭐ 토큰은 **선택이다.** 발급을 건너뛴 사람의 앱이 멈추지 않는다."""
    monkeypatch.delenv("GITWIRE_TOKEN", raising=False)
    _no_git(monkeypatch)
    assert client.get("/api/token").get_json()["found"] is False

    res = client.post("/api/rooms", json={"repo_url": REPO, "name": "토큰 없는 방"})
    assert res.status_code == 201
    room_id = res.get_json()["room"]["id"]
    sent = client.post(f"/api/rooms/{room_id}/messages", json={"text": "토큰 없이"})
    assert sent.status_code == 202
    manager.outbox(room_id).wait_idle(5.0)
    got = client.get(f"/api/rooms/{room_id}/messages").get_json()["messages"]
    assert [m["text"] for m in got] == ["토큰 없이"]


# ------------------------------------------------------- H. '내 것' 판정

"""⭐ `mine` — 봉투(`sender`)와 이 설치본의 식별자를 비교한 결과다.

이 묶음이 지키는 것은 하나다: **판정이 경로에 따라 달라지지 않는다.**
예전에는 '내 것'이 전송 직후의 화면 특례로만 존재해서, 같은 메시지를 다시
읽어오면 통째로 남의 것이 됐다.
"""


def _mine_by_text(payload) -> dict:
    return {m["text"]: m["mine"] for m in payload}


def test_내_메시지는_다시_읽어도_내_것이다(client, manager):
    """⭐ 회귀: 보내고 → **다시 읽었을 때** (= 새로고침) 판정이 살아 있나."""
    room = manager.register(REPO)

    # ⚠️ 전송 응답에는 봉투가 없으므로 '내 것' 판정도 없다 (그때는 판정할 봉투가
    # 아직 없다 — `test_보내기와_타임라인`). 판정이 실리는 곳은 **조회와 SSE** 이고
    # 그 둘이 같은 값을 준다 (`test_SSE_도_같은_판정을_싣는다`).
    manager.send(room.id, "내가 쓴 말")

    again = client.get(f"/api/rooms/{room.id}/messages").get_json()["messages"]
    assert _mine_by_text(again) == {"내가 쓴 말": True}   # 다시 읽어도 내 것


def test_남의_메시지는_내_것이_아니다(client, manager):
    room = manager.register(REPO)
    channel = manager.channel(room.id)
    manager.send(room.id, "내 말")
    channel.inject({"kind": "msg", "v": 1, "author": "밥", "text": "남의 말"},
                   sender="other.host.999")

    got = client.get(f"/api/rooms/{room.id}/messages").get_json()["messages"]
    assert _mine_by_text(got) == {"내 말": True, "남의 말": False}


def test_모든_조회_경로가_같은_판정을_준다(client, manager):
    """최초 로드 · 위로 페이징 · 검색 — 어느 문으로 들어와도 같은 값."""
    room = manager.register(REPO)
    channel = manager.channel(room.id)
    for i in range(6):
        manager.send(room.id, f"내 말 {i}")
        channel.inject({"kind": "msg", "v": 1, "author": "밥", "text": f"남의 말 {i}"},
                       sender="other.host.999")

    def expected(texts):
        return {t: t.startswith("내 말") for t in texts}

    recent = client.get(f"/api/rooms/{room.id}/messages").get_json()["messages"]
    assert _mine_by_text(recent) == expected([m["text"] for m in recent])
    assert set(_mine_by_text(recent).values()) == {True, False}   # 둘 다 섞여 있다

    older = client.get(
        f"/api/rooms/{room.id}/messages?before={recent[0]['id']}"
    ).get_json()["messages"]
    assert older, "위로 페이징이 비었다 — 이 테스트가 아무것도 보지 못한다"
    assert _mine_by_text(older) == expected([m["text"] for m in older])

    for query, mine in (("내 말", True), ("남의 말", False)):
        hits = client.get(
            f"/api/rooms/{room.id}/search?q={query}"
        ).get_json()["messages"]
        assert hits and all(m["mine"] is mine for m in hits), query


def test_SSE_도_같은_판정을_싣는다(client, manager):
    room = manager.register(REPO)
    channel = manager.channel(room.id)
    res = client.get(f"/api/rooms/{room.id}/stream?client=tab1", buffered=False)
    chunks = res.response
    next(chunks)
    next(chunks)

    def later():
        manager.send(room.id, "내가 스트림으로")
        # 구독 콜백을 그대로 탄다 — 폴러가 남의 레코드를 물어 온 그 경로다.
        manager.on_record(room.id, channel.inject(
            {"kind": "msg", "v": 1, "author": "밥", "text": "남이 스트림으로"},
            sender="other.host.999",
        ))

    threading.Timer(0.05, later).start()

    seen = {}
    for _ in range(80):
        chunk = next(chunks).decode("utf-8")
        if chunk.startswith("event: message"):
            body = json.loads(chunk.split("data: ", 1)[1])
            seen[body["text"]] = body["mine"]
            if len(seen) == 2:
                break
    res.close()
    assert seen == {"내가 스트림으로": True, "남이 스트림으로": False}

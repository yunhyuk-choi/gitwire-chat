"""Flask 앱 — HTML 을 한 번 서빙하고, 이후 새 메시지는 SSE 로 민다.

경로 설계의 핵심은 "**HTML 은 딱 한 번**" 이다. 서버가 메시지를 렌더한 HTML
조각을 밀어 넣거나, 새 메시지마다 페이지를 다시 그리는 일은 없다. 서버는
JSON 만 밀고, 브라우저 JS 가 노드를 만들어 `appendChild` 한다.

    GET  /                                 셸 HTML (1회)
    GET  /api/rooms                        방 목록
    POST /api/rooms                        방 등록 (레포 주소 → 방)
    DEL  /api/rooms/<id>                   방 목록에서 제거
    GET  /api/rooms/<id>/messages          최근 N건 / before=<메시지ID> 로 그 앞
                                           (응답의 has_more 가 무한 스크롤의 종료 조건)
    POST /api/rooms/<id>/messages          보내기
    GET  /api/rooms/<id>/search?q=          서버측 레코드 검색
    POST /api/rooms/<id>/refresh           폴 주기를 기다리지 않고 즉시 당기기
    POST /api/rooms/<id>/visibility        이 탭이 방을 보고 있나 (알림 판정)
    GET  /api/rooms/<id>/stream?client=    ⭐ SSE — 새 메시지만 흘러온다
"""

from __future__ import annotations

import logging

from flask import Flask, Response, jsonify, render_template, request

from . import events
from .config import Settings, load_settings
from .rooms import RoomError, RoomManager, messages_json

log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    manager: RoomManager | None = None,
    *,
    start: bool = True,
) -> Flask:
    """앱 팩토리. `manager` 를 주입하면 gitwire 없이도 테스트할 수 있다."""
    settings = settings or (manager.settings if manager else load_settings())
    manager = manager or RoomManager(settings)
    settings.home.mkdir(parents=True, exist_ok=True)

    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    app.json.ensure_ascii = False
    app.extensions["gitwire_chat"] = manager

    if start:
        manager.start()

    # ------------------------------------------------------------------ 셸

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            default_author=settings.author,
            recent_limit=settings.recent_limit,
        )

    @app.get("/api/settings")
    def get_settings():
        return jsonify(
            {
                "author": settings.author,
                "recent_limit": settings.recent_limit,
                "page_limit": settings.page_limit,
                "poll_interval": settings.poll_interval,
                "home": str(settings.home),
                "notifications": settings.notifications,
            }
        )

    # ----------------------------------------------------------------- 방

    @app.get("/api/rooms")
    def list_rooms():
        return jsonify({"rooms": [r.to_json() for r in manager.rooms()]})

    @app.post("/api/rooms")
    def add_room():
        data = request.get_json(silent=True) or request.form or {}
        try:
            room = manager.register(
                str(data.get("repo_url") or ""),
                name=str(data.get("name") or ""),
                author=str(data.get("author") or ""),
                token_env=str(data.get("token_env") or ""),
            )
        except RoomError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"room": room.to_json()}), 201

    @app.delete("/api/rooms/<room_id>")
    def remove_room(room_id: str):
        manager.unregister(room_id)
        return jsonify({"ok": True})

    @app.get("/api/rooms/<room_id>/info")
    def room_info(room_id: str):
        try:
            return jsonify(manager.info(room_id))
        except RoomError as exc:
            return jsonify({"error": str(exc)}), 404

    # ------------------------------------------------------------- 메시지

    @app.get("/api/rooms/<room_id>/messages")
    def get_messages(room_id: str):
        before = request.args.get("before")
        try:
            limit = int(request.args.get("limit") or 0) or None
        except ValueError:
            limit = None
        try:
            page = manager.page(room_id, before=before or None, limit=limit)
        except RoomError as exc:
            return jsonify({"error": str(exc)}), 400
        # `has_more` 는 기반(gitwire)이 직접 판정한 값이다 — 쪽 크기로 추측하지
        # 않는다. 추측이 틀리면 브라우저가 맨 위에서 헛요청을 한 번 더 보낸다.
        return jsonify(
            {"messages": messages_json(page.messages), "has_more": page.has_more}
        )

    @app.post("/api/rooms/<room_id>/messages")
    def post_message(room_id: str):
        data = request.get_json(silent=True) or request.form or {}
        try:
            message = manager.send(
                room_id,
                str(data.get("text") or ""),
                author=str(data.get("author") or ""),
                reply_to=(str(data.get("reply_to")) if data.get("reply_to") else None),
            )
        except RoomError as exc:
            return jsonify({"error": str(exc)}), 400
        except ValueError as exc:  # schema.InvalidMessage
            return jsonify({"error": str(exc)}), 400
        return jsonify({"message": message.to_json()}), 201

    @app.get("/api/rooms/<room_id>/search")
    def search(room_id: str):
        query = request.args.get("q") or ""
        try:
            items = manager.search(room_id, query)
        except RoomError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"messages": messages_json(items), "query": query})

    @app.post("/api/rooms/<room_id>/refresh")
    def refresh(room_id: str):
        try:
            delivered = manager.poll_now(room_id)
        except RoomError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"delivered": delivered})

    @app.post("/api/rooms/<room_id>/visibility")
    def visibility(room_id: str):
        data = request.get_json(silent=True) or request.form or {}
        visible = bool(data.get("visible"))
        changed = manager.bus.set_visible(
            room_id, visible, str(data.get("client") or "")
        )
        return jsonify({"ok": True, "changed": changed})

    # ------------------------------------------------------------ SSE

    @app.get("/api/rooms/<room_id>/stream")
    def stream(room_id: str):
        client = request.args.get("client") or ""
        sub = manager.bus.subscribe(room_id, client)

        def generate():
            try:
                yield from events.stream(sub, manager.bus.keepalive)
            finally:
                manager.bus.unsubscribe(sub)

        response = Response(generate(), mimetype="text/event-stream")
        response.headers["Cache-Control"] = "no-cache, no-transform"
        response.headers["Connection"] = "keep-alive"
        # nginx 등 역프록시가 SSE 를 버퍼링하지 않게.
        response.headers["X-Accel-Buffering"] = "no"
        return response

    return app

"""Flask 앱 — HTML 을 한 번 서빙하고, 이후 새 메시지는 SSE 로 민다.

경로 설계의 핵심은 "**HTML 은 딱 한 번**" 이다. 서버가 메시지를 렌더한 HTML
조각을 밀어 넣거나, 새 메시지마다 페이지를 다시 그리는 일은 없다. 서버는
JSON 만 밀고, 브라우저 JS 가 노드를 만들어 `appendChild` 한다.

    GET  /                                 셸 HTML (1회, **캐시 안 함**)
    GET  /assets/<도장>/<파일>             ⭐ 도장 박힌 정적 자원 (영구 캐시)
    GET  /api/version                      설치본 버전 · 자원 도장 · 이 프로세스
    POST /api/update/check                 새 버전이 있나 (⭐ 사용자가 누를 때만)
    POST /api/update/run                   ⭐ 갱신을 **시작**시킨다 — 정본 CLI 를
                                           분리된 프로세스로 띄우고 즉시 반환.
                                           우리 화면에서 온 요청만 받는다 (`csrf.py`)
    GET  /api/rooms                        방 목록 (+ 연결 상태)
    POST /api/rooms                        방 등록 → **즉시 반환**, 클론은 백그라운드
    POST /api/rooms/<id>/retry             실패한 방 다시 연결
    DEL  /api/rooms/<id>                   방 목록에서 제거
    POST /api/repos/plan                   레포 만들기 계획(무엇이 만들어지는지)
    POST /api/repos                        레포 생성 (토큰이 있을 때만, 명시적 확인)
    GET  /api/rooms/<id>/messages          최근 N건 / before=<메시지ID> 로 그 앞
                                           (응답의 has_more 가 무한 스크롤의 종료 조건)
    POST /api/rooms/<id>/messages          보내기 (**원격 push 를 기다리지 않는다**)
    POST /api/rooms/<id>/outbox            아직 못 나간 것을 지금 다시 밀기
    GET  /api/rooms/<id>/reads             ⭐ 읽음 스냅샷 (내 안 읽은 개수 +
                                           참가자별 커서). **카운트는 담지 않는다** —
                                           브라우저가 커서에서 파생시킨다
    POST /api/rooms/<id>/reads             "여기까지 읽었다" (커서 전진, 낙관적)
    GET  /api/rooms/<id>/search?q=          서버측 레코드 검색
    POST /api/rooms/<id>/refresh           폴 주기를 기다리지 않고 즉시 당기기
    POST /api/rooms/<id>/visibility        이 탭이 방을 보고 있나 (알림 판정)
    GET  /api/rooms/<id>/stream?client=    ⭐ SSE — 새 메시지만 흘러온다
"""

from __future__ import annotations

import logging
import os
import sys

from flask import (
    Flask,
    Response,
    jsonify,
    make_response,
    render_template,
    request,
    send_from_directory,
)

from . import assets, csrf, events, forges, updaterun
from .config import Settings, load_settings
from .rooms import RoomError, RoomManager, RoomNotReady

log = logging.getLogger(__name__)

#: 배포 이름 — `importlib.metadata` 조회 키이자 pip 인자에 쓰는 이름.
DIST_NAME = "gitwire-chat"


def installed_version() -> str:
    """설치본 버전 문자열. 소스 체크아웃처럼 메타데이터가 없으면 빈 문자열.

    ⚠️ 이 값은 **캐시 무효화에 쓸 수 없다** — 이 프로젝트는 같은 버전 문자열로
    여러 번 배포한다. 캐시는 `assets.AssetStamper`(내용 해시)가 깬다. 여기 있는
    것은 사람이 읽는 표시·보고용이다.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version(DIST_NAME)
    except Exception:  # noqa: BLE001 — PackageNotFoundError 포함
        return ""


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

    # 정적 자원 도장. 템플릿은 `asset('app.js')` 로만 URL 을 만든다 —
    # `url_for('static', ...)` 는 도장이 없어서 캐시를 깰 수 없다.
    stamper = assets.AssetStamper(app.static_folder)
    app.extensions["gitwire_chat_assets"] = stamper
    app.jinja_env.globals["asset"] = stamper.url

    # 갱신 실행기. 서버 하나가 하나를 들고 있어야 "지금 내가 띄운 갱신이 아직
    # 도나"를 확정적으로 알 수 있다 (`updaterun.py` 동시 실행 방지).
    app.extensions["gitwire_chat_update"] = updaterun.Launcher(home=settings.home)

    if start:
        manager.start()

    # ------------------------------------------------------------------ 셸

    @app.get("/")
    def index():
        # ⚠️ **셸은 캐시하지 않는다.** 도장 박힌 자원 URL 이 이 HTML 안에 들어
        # 있으므로, 셸이 캐시되면 도장을 바꿔도 브라우저가 옛 URL 을 계속 쓴다 —
        # 캐시 무효화 전체가 무력화된다. 첫 페인트 전에 도는 인라인 테마 조각도
        # 여기 있어서 같이 신선해진다. 크기는 6KB 남짓이고 로컬 서버라 비용이 없다.
        response = make_response(
            render_template(
                "index.html",
                default_author=settings.author,
                recent_limit=settings.recent_limit,
            )
        )
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    # ------------------------------------------------------- 정적 자원

    @app.get(f"{assets.ASSETS_PREFIX}/<stamp>/<path:filename>")
    def asset(stamp: str, filename: str):
        """도장 박힌 정적 자원.

        도장이 맞으면 **1년 + immutable** 로 준다. 내용이 바뀌면 도장이 바뀌어
        URL 자체가 달라지므로, 오래 캐시하는 것이 이 방식의 요점이다.
        """
        current = stamper.stamp
        response = send_from_directory(app.static_folder, filename)
        if stamp == current:
            response.headers["Cache-Control"] = (
                f"public, max-age={assets.ASSET_MAX_AGE}, immutable"
            )
            return response
        # 옛 도장으로 들어온 요청 — 갱신 직후까지 열려 있던 탭이 뒤늦게 부르는
        # 경우다. 우리는 옛 파일을 보관하지 않으니 **지금 파일**밖에 줄 것이 없다.
        # 그걸 immutable 로 못 박으면 틀린 내용이 옛 URL 에 영구히 붙는다.
        # 조용히 넘기지 않는다 — 서버 로그에 남기고 지금 도장을 헤더로 알린다.
        log.warning(
            "옛 자원 도장 %s 로 %s 요청 — 지금 도장은 %s. "
            "그 탭은 새로고침해야 맞는 조합이 된다.",
            stamp, filename, current,
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Asset-Stamp"] = current
        response.headers["X-Asset-Stamp-Mismatch"] = "1"
        return response

    @app.get("/api/version")
    def version():
        """설치본 식별 정보 — 버전 · 자원 도장 · 이 프로세스.

        ``pid`` 를 싣는 이유: 갱신 도구가 "이 포트에 응답하는 것이 정말 내가
        기록해 둔 그 프로세스인가"를 확인해야 한다. ``prefix``(파이썬 설치 접두사)
        는 "이 인스턴스가 **지금 갈아치울 그 설치본**에서 왔나"를 가른다 — 다른
        venv 에서 도는 앱은 이 갱신과 무관하므로 막을 이유가 없다. 루프백 전용
        앱이라 프로세스 번호·경로는 비밀이 아니다.
        """
        return jsonify(
            {
                "name": DIST_NAME,
                "version": installed_version(),
                "asset_stamp": stamper.stamp,
                "pid": os.getpid(),
                "prefix": sys.prefix,
            }
        )

    @app.post("/api/update/check")
    def update_check():
        """새 버전이 있나 — ⭐ **사용자가 누를 때만** 원격을 본다.

        주기 폴링을 두지 않는 근거는 `static/js/update.js` 의 도크에 있다 (요지:
        확인은 실제 네트워크 왕복이라, 로컬 앱이 사용자가 모르는 사이에 외부로
        나가는 동작을 만들지 않는다).

        이 호출이 하는 일은 ``git ls-remote`` 한 번이다. 상태를 바꾸지 않는다.
        """
        from . import updater

        try:
            return jsonify(updater.check().to_json())
        except updater.UpdateError as exc:
            return jsonify({"error": str(exc), "hint": exc.hint}), 400

    @app.post("/api/update/run")
    def update_run():
        """⭐ 누르면 갱신이 **끝까지** 진행된다. 단, 여기서 갱신하지는 않는다.

        하는 일이 셋이고 순서가 그대로 방어의 순서다:

        1. **우리 화면에서 온 요청인가** (`csrf.py`). 이 앱은 루프백 전용 ·
           인증 없음이라, "앱을 죽이고 갈아치우는" 엔드포인트를 드라이브바이로
           부를 수 없게 만드는 것이 이 문의 전부다 — 커스텀 요청 헤더 +
           ``Sec-Fetch-Site`` + ``Origin``. 근거·한계는 그 모듈 도크에 있다.
        2. **바뀔 게 있나** (`updater.check` = ``git ls-remote`` 한 번).
           없으면 **아무것도 띄우지 않고** 그렇게 답한다. 이 판정을 화면의 말을
           믿고 건너뛰지 않는다 — 멀쩡한 앱을 재시작시키지 않는 것은 서버가
           지켜야 하는 성질이다.
        3. **정본 CLI 를 분리된 프로세스로 띄우고 즉시 반환** (`updaterun.py`).
           멈춤·설치·재기동·실패 시 되돌리기와 안내는 그 CLI 가 이미 한다.
           서버가 자기 패키지를 갈아치우며 자기를 재시작하는 구조로 만들지
           않는다 (Windows 파일 락 · 실패하면 되돌릴 주체가 사라진다).

        ⚠️ 성공 응답은 "**시작했다**"(202)이고 "갱신됐다"가 아니다. 그 뒤 이
        서버는 곧 죽는다 — 화면이 그 구간을 어떻게 버티는지는
        `static/js/update.js` 가 소유한다. 응답에 이 프로세스의 ``pid`` 를 실어
        주는 이유가 그것이다: 화면은 **pid 가 달라진 것**으로 새 서버를 가른다.
        """
        from . import updater

        reason = csrf.deny_reason(request.headers, request.host)
        if reason:
            # 조용히 거절하지 않는다 — 서버 로그에 남긴다. 정상 UI 가 갑자기
            # 막히는 일이 생기면 그 이유가 여기 찍혀 있어야 한다.
            log.warning("갱신 실행 요청을 거절했다 — %s", reason)
            return jsonify(
                {"error": reason, "code": "forbidden", "hint": csrf.HINT}
            ), 403

        try:
            found = updater.check()
        except updater.UpdateError as exc:
            return jsonify(
                {"error": str(exc), "hint": exc.hint, "code": "source"}
            ), 400
        if not found.behind:
            return jsonify({
                "started": False,
                "code": "current",
                "check": found.to_json(),
            })

        launcher = app.extensions["gitwire_chat_update"]
        try:
            run = launcher.launch()
        except updaterun.Busy as exc:
            return jsonify({
                "error": str(exc), "code": "busy",
                "hint": exc.hint, "run": exc.run.to_json(),
            }), 409
        except updaterun.Unmanaged as exc:
            return jsonify(
                {"error": str(exc), "code": "unmanaged", "hint": exc.hint}
            ), 409
        except updaterun.LaunchError as exc:
            return jsonify(
                {"error": str(exc), "code": "launch", "hint": exc.hint}
            ), 500
        return jsonify({
            "started": True,
            "run": run.to_json(),
            "check": found.to_json(),
            # 화면이 "새 서버가 떴다"를 가르는 기준. 버전·도장이 아니라 pid 다 —
            # 정적 파일이 안 바뀌면 도장은 그대로이고, 버전 문자열은 같은 값으로
            # 여러 번 배포한다 (`installed_version` 도크).
            "serving": {
                "pid": os.getpid(),
                "version": installed_version(),
                "asset_stamp": stamper.stamp,
            },
        }), 202

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
        return jsonify({"rooms": manager.rooms_payload()})

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
        # 클론은 백그라운드에서 돈다 — 여기서 기다리지 않는다.
        return jsonify(
            {"room": {**room.to_json(), "status": manager.status(room.id).to_json()}}
        ), 201

    @app.post("/api/rooms/<room_id>/retry")
    def retry_room(room_id: str):
        try:
            status = manager.reconnect(room_id)
        except RoomError as exc:
            return jsonify({"error": str(exc)}), 404
        return jsonify({"status": status.to_json()})

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
        except RoomNotReady as exc:
            # 아직 받는 중이거나 실패한 방 — 오류가 아니라 **상태**다.
            return jsonify(
                {"error": str(exc), "status": manager.status(room_id).to_json()}
            ), 409
        except RoomError as exc:
            return jsonify({"error": str(exc)}), 400
        # `has_more` 는 기반(gitwire)이 직접 판정한 값이다 — 쪽 크기로 추측하지
        # 않는다. 추측이 틀리면 브라우저가 맨 위에서 헛요청을 한 번 더 보낸다.
        return jsonify(
            {
                "messages": manager.messages_json(room_id, page.messages),
                "has_more": page.has_more,
            }
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
        except RoomNotReady as exc:
            return jsonify(
                {"error": str(exc), "status": manager.status(room_id).to_json()}
            ), 409
        except RoomError as exc:
            return jsonify({"error": str(exc)}), 400
        except ValueError as exc:  # schema.InvalidMessage
            return jsonify({"error": str(exc)}), 400
        return jsonify({"message": manager.message_json(room_id, message)}), 201

    @app.get("/api/rooms/<room_id>/search")
    def search(room_id: str):
        query = request.args.get("q") or ""
        try:
            items = manager.search(room_id, query)
        except RoomError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(
            {"messages": manager.messages_json(room_id, items), "query": query}
        )

    @app.post("/api/rooms/<room_id>/outbox")
    def flush_outbox(room_id: str):
        """사용자가 누른 '다시 보내기'.

        전송 응답이 push 를 기다리지 않게 되면서 "아직 상대에게 못 갔다"가 방
        단위 상태가 됐다 (`rooms.outbox`). 그 상태에서 사람이 취할 수 있는 유일한
        행동이 이것이라, 버튼도 엔드포인트도 하나면 된다.
        """
        try:
            state = manager.flush_outbox(room_id)
        except RoomNotReady as exc:
            return jsonify(
                {"error": str(exc), "status": manager.status(room_id).to_json()}
            ), 409
        except RoomError as exc:
            return jsonify({"error": str(exc)}), 404
        return jsonify({"outbox": state.to_json()})

    @app.get("/api/rooms/<room_id>/reads")
    def get_reads(room_id: str):
        """⭐ 읽음 스냅샷 — **커서만** 싣는다 (카운트는 파생값이다).

        메시지마다의 "안 읽은 수"를 서버가 계산해 실어 보내지 않는 이유: 그 값은
        *다른 사람의 커서가 움직이면* 한꺼번에 바뀐다. 커서 지도(사람 수만큼)를
        주면 브라우저가 화면에 있는 메시지에 대해서만 매번 계산하고, 커서가
        움직였을 때 노드를 다시 만들지 않고 숫자만 덧입힌다
        (`static/js/reads.js` · `message-node.js`).
        """
        try:
            return jsonify(manager.read_view(room_id).to_json())
        except RoomNotReady as exc:
            return jsonify(
                {"error": str(exc), "status": manager.status(room_id).to_json()}
            ), 409
        except RoomError as exc:
            return jsonify({"error": str(exc)}), 404

    @app.post("/api/rooms/<room_id>/reads")
    def mark_reads(room_id: str):
        """"여기까지 읽었다". 커서는 **단조 증가** — 뒤로 보내려 해도 안 간다.

        ⚠️ 이 응답은 push 를 기다리지 않는다. 내 로컬 커서는 즉시 움직이고(뱃지가
        바로 줄어든다), 남에게 알리는 발행은 아웃박스가 뒤에서 민다 —
        메시지 전송이 읽음 발행 때문에 늦어지지 않는다 (`rooms.mark_read`).
        """
        data = request.get_json(silent=True) or request.form or {}
        cursor = str(data.get("cursor") or "")
        try:
            view = manager.mark_read(room_id, cursor)
        except RoomNotReady as exc:
            return jsonify(
                {"error": str(exc), "status": manager.status(room_id).to_json()}
            ), 409
        except RoomError as exc:
            return jsonify({"error": str(exc)}), 404
        return jsonify(view.to_json())

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

    # ------------------------------------------------- 레포 만들기 (G-2)

    def _token_for(env_name: str) -> str:
        """토큰 **값**은 여기서만 읽고 응답·로그 어디에도 싣지 않는다."""
        return os.environ.get((env_name or "GITWIRE_TOKEN").strip(), "")

    @app.post("/api/repos/plan")
    def plan_repo():
        """무엇이 만들어지는지 **누르기 전에** 보여주기 위한 계획.

        레포 생성은 계정을 바꾸는 외부 동작이라 조용히 하지 않는다.
        """
        data = request.get_json(silent=True) or request.form or {}
        host = str(data.get("host") or "github.com")
        forge = forges.detect(host)
        name = forges.repo_slug(str(data.get("name") or ""))
        owner = str(data.get("owner") or "").strip()
        token_env = str(data.get("token_env") or "").strip()
        token = _token_for(token_env) if forge.can_api else ""

        detail, mode = "", "manual"
        if forge.can_api and token:
            try:
                owner = owner or forges.github_login(token)
                mode = "api"
            except forges.ForgeError as exc:
                detail = f"{exc} — {exc.hint}".strip(" —")
                mode = "link" if forge.can_prefill else "manual"
        elif forge.can_prefill:
            mode = "link"
        return jsonify({
            "forge": {"kind": forge.kind, "host": forge.host, "label": forge.label},
            "mode": mode,          # api = 앱 안에서 생성 / link = 링크로 / manual = 직접
            "owner": owner,
            "name": name,
            "private": True,
            "link": forges.new_repo_link(
                forge.kind, name, owner=owner,
                description=str(data.get("description") or ""),
            ),
            "clone_url": forges.clone_url(forge.kind, owner, name),
            "token_env": token_env or "GITWIRE_TOKEN",
            "detail": detail,
        })

    @app.post("/api/repos")
    def create_repo():
        """⚠️ 실제로 레포를 만든다. 사용자가 계획을 보고 명시적으로 누른 뒤에만."""
        data = request.get_json(silent=True) or request.form or {}
        forge = forges.detect(str(data.get("host") or "github.com"))
        if not forge.can_api:
            return jsonify({
                "error": f"{forge.label} 은 앱 안에서 만들 수 없다 — 링크로 만들어라",
                "code": "unsupported",
            }), 400
        token_env = str(data.get("token_env") or "").strip() or "GITWIRE_TOKEN"
        token = _token_for(token_env)
        if not token:
            return jsonify({
                "error": f"환경변수 {token_env} 에 토큰이 없다",
                "code": "token",
                "hint": f"토큰을 넣고 앱을 다시 띄우면 앱 안에서 만들 수 있다. "
                        f"지금은 링크로 만들어도 된다.",
            }), 400
        try:
            created = forges.create_github_repo(
                token,
                str(data.get("name") or ""),
                owner=str(data.get("owner") or "").strip(),
                description=str(data.get("description") or ""),
                private=True,
            )
        except forges.ForgeError as exc:
            return jsonify({"error": str(exc), "code": exc.code, "hint": exc.hint}), 400
        return jsonify({"repo": created}), 201

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

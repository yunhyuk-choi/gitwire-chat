"""위험한 POST 하나를 지키는 문 — **이 요청이 우리 화면에서 왔나.**

왜 이 파일이 생겼나
------------------
`POST /api/update/run` 은 앱을 멈추고 패키지를 갈아치우고 다시 띄운다. 이 앱은
루프백 전용 · **인증 없음**이 설계다. 그래서 예전에는 그런 엔드포인트를 아예
두지 않았다 — "브라우저로 아무 페이지나 열어 둔 상태에서 그 페이지가 폼 전송
하나로 우리 앱을 재설치시킬 수 있고, 인증이 없으니 막을 수단이 없다"는 판단이었다.

**앞부분은 맞았고 뒷부분이 틀렸다.** 로그인 없이도 "우리 화면에서 온 요청"만
받는 것은 표준 수단으로 된다. 세 겹이고, 각각 **다른 것**을 막는다:

1. **커스텀 요청 헤더를 요구한다** (``X-Gitwire-Chat: update``).
   평범한 HTML ``<form>`` 은 요청 헤더를 붙일 수 없다 — 드라이브바이의 주된
   형태(숨은 폼 자동 전송)가 여기서 죽는다. 스크립트로 붙이면 그 요청은 더 이상
   *단순 요청*이 아니게 되어 브라우저가 먼저 preflight(``OPTIONS``)를 보내는데,
   우리는 CORS 를 켜지 않으므로(``Access-Control-Allow-*`` 를 응답하지 않는다)
   **본 요청이 나가지도 못한다.**
2. **``Sec-Fetch-Site`` 를 본다.** 브라우저가 붙이는 값이고 페이지 JS 가
   덮어쓸 수 없다(금지된 헤더 이름). ``same-origin`` 이 아니면 거절한다.
   ⚠️ **없으면 통과시킨다** — 이 헤더를 안 보내는 오래된 브라우저·``curl``·
   스크립트에서도 우리 UI 는 동작해야 한다. *방어가 정상 사용을 막으면 실패다.*
   그 경우에도 1번은 그대로 성립한다.
3. **``Origin`` 이 있으면 우리 호스트와 같아야 한다.** 브라우저는 same-origin
   POST 에도 ``Origin`` 을 붙인다. 비교 대상은 ``Host`` 헤더다 —
   ``127.0.0.1:8770`` 로 열었든 ``localhost:8770`` 으로 열었든 자기 자신과 같으면
   된다(우리가 바인드하는 주소는 루프백 하나뿐이다). 값이 ``null`` 인 경우
   (샌드박스 iframe·``data:`` 문서)는 netloc 이 비어 거절된다.

무엇을 **안** 하나 — 페이지에 심는 1회용 토큰
--------------------------------------------
두지 않았다. 이 문이 막는 상대는 *다른 오리진의 페이지*이고 그건 위 세 겹으로
막힌다. 같은 기기에서 도는 프로그램은 ``GET /``(인증 없음)로 토큰을 그냥 읽어
갈 수 있어서 토큰이 늘려 주는 것이 없고, 서버가 재기동되면 옛 토큰이 죽어서
**정상 경로만** 깨진다. 즉 비용은 실재하고 이득은 없다.

이 엔드포인트가 늘린 위험은 `README` 「보안 모델」에 그대로 적어 뒀다 — 이 앱을
밖으로 노출하면 안 되는 이유가 한 칸 더 무거워졌다.
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: 우리 화면이 붙이는 커스텀 요청 헤더. **이름과 값을 둘 다** 고정한다 —
#: 우연히 붙는 헤더로 문이 열리지 않게. 값이 화면(`static/js/update.js`)의
#: 것과 같은지는 `tests/test_updater.py` 가 기계로 확인한다.
HEADER = "X-Gitwire-Chat"
HEADER_VALUE = "update"

#: `Sec-Fetch-Site` 가 이 값일 때만 통과 (없으면 통과 — 모듈 도크 2번).
SAME_ORIGIN = "same-origin"

#: 거절 응답에 함께 싣는 안내. 사람이 다음에 무엇을 하면 되는지 남긴다.
HINT = (
    "이 요청은 앱 화면(http://127.0.0.1:<포트>/)에서만 보낼 수 있다. "
    "터미널에서 갱신하려면: python -m gitwire_chat update"
)


def deny_reason(headers, host: str) -> str:
    """거절 사유. 통과면 **빈 문자열**.

    ``headers`` 는 ``.get(name, default)`` 만 있으면 된다 (Flask 의 헤더 객체와
    평범한 dict 가 둘 다 들어온다 — 그래서 이 함수는 Flask 를 모른다).
    """
    site = str(headers.get("Sec-Fetch-Site", "") or "").strip()
    if site and site != SAME_ORIGIN:
        return (
            f"브라우저가 이 요청을 같은 출처에서 온 것이 아니라고 표시했다 "
            f"(Sec-Fetch-Site: {site})."
        )

    origin = str(headers.get("Origin", "") or "").strip()
    if origin:
        netloc = urlsplit(origin).netloc.lower()
        if not netloc or netloc != str(host or "").strip().lower():
            return f"다른 출처에서 온 요청이다 (Origin: {origin})."

    if str(headers.get(HEADER, "") or "").strip() != HEADER_VALUE:
        return (
            f"우리 화면이 붙이는 요청 헤더가 없다 ({HEADER}: {HEADER_VALUE}). "
            "평범한 HTML 폼은 이 헤더를 붙일 수 없다 — 그것이 이 문의 요점이다."
        )
    return ""

"""⭐ **진짜 브라우저**로 페이지를 열어 보는 연기 감지기.

이 파일이 존재하는 이유는 한 문장으로 말할 수 있다 —
**stub DOM 테스트 22/22 가 전부 통과하는 동안 앱은 완전히 죽어 있었다.**

stub 하네스가 못 잡는 구간이 정확히 셋이다:

1. **Node 안에서 돈다.** `node --check` 도 stub DOM 도 `process` 같은 Node 전역이
   실제로 있는 곳에서 실행된다. 브라우저에만 없는 것은 원리상 안 잡힌다.
2. **스크립트 로딩 순서를 건너뛴다.** stub 은 `window.TanStackVirtual` 을 직접
   세팅하고 `boot()` 을 부른다. 실제 실패 지점(모듈이 실려서 전역이 생기는가,
   그 순간이 boot 보다 앞인가)이 통째로 빠진다.
3. **템플릿을 안 읽는다.** `index.html` 의 script 태그가 잘못돼도 모른다.

그래서 여기서는 아무것도 흉내 내지 않는다. 실제 Flask 앱을 임의 포트로 띄우고,
설치된 크로미움 계열 브라우저를 헤드리스로 붙여 **콘솔과 서버 로그를 본다.**

판정 기준 (둘 다 이번 사고를 정면으로 겨냥한다):

* 콘솔에 `Uncaught` 가 **하나라도 있으면 실패.**
* 브라우저가 `/api/rooms` 를 **실제로 불렀나** — 이것이 `wire()` 까지 갔다는
  증거다. "페이지가 200 이다"는 이번에 아무것도 증명하지 못했다(200 이었다).

⚠️ 브라우저가 없는 환경(리눅스 CI·맥 등)에서는 **SKIP** 한다. 스위트가 깨지면 안
된다. 대신 SKIP 은 보여야 한다 — `pyproject.toml` 의 `addopts` 에 `-ra` 가 있어
매 실행 요약에 사유가 뜬다.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from werkzeug.serving import make_server

import gitwire_chat
from gitwire_chat.app import create_app
from gitwire_chat.config import Settings

#: 브라우저 기동에 주는 시간(초). 헤드리스 첫 실행은 프로필을 만드느라 느리다.
BROWSER_TIMEOUT = 120
#: 페이지에 주는 가상 시간(ms). fetch 한 번 왕복에 넉넉하다.
VIRTUAL_TIME_BUDGET = 8000


def find_browser() -> str | None:
    """크로미움 계열 브라우저를 찾는다. 없으면 None (→ SKIP).

    OS 중립: PATH 를 먼저 보고, 그다음 각 OS 의 관례적 설치 위치를 본다.
    `GITWIRE_CHAT_BROWSER` 로 직접 지정할 수도 있다.
    """
    override = os.environ.get("GITWIRE_CHAT_BROWSER")
    if override:
        return override if Path(override).exists() else shutil.which(override)

    for name in (
        "msedge", "microsoft-edge", "microsoft-edge-stable",
        "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
        "chrome",
    ):
        found = shutil.which(name)
        if found:
            return found

    candidates = [
        # Windows
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        # macOS
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        # Linux
        "/usr/bin/microsoft-edge", "/usr/bin/google-chrome",
        "/usr/bin/chromium", "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return None


BROWSER = find_browser()
needs_browser = pytest.mark.skipif(
    BROWSER is None,
    reason="크로미움 계열 브라우저가 없다 — 브라우저 연기 테스트를 건너뛴다 "
           "(GITWIRE_CHAT_BROWSER 로 경로를 지정할 수 있다)",
)


class RecordingServer:
    """앱을 임의 포트로 띄우고 **실제로 들어온 요청 경로를 기록**한다.

    포트를 0 으로 열어 OS 가 골라 주게 한다 — 사용자가 쓰고 있는 인스턴스(8770)와
    부딪히지 않는 것이 요점이다.
    """

    def __init__(self, app) -> None:
        self.paths: list[str] = []
        self._app = app
        self._server = make_server("127.0.0.1", 0, self._wsgi, threaded=True)
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _wsgi(self, environ, start_response):
        self.paths.append(environ.get("PATH_INFO", ""))
        return self._app(environ, start_response)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def __enter__(self) -> "RecordingServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._thread.join(timeout=10)
        self._server.server_close()


#: ⭐ **테스트 전용** 두 경로. 앱에는 없다 — 여기서 붙인다.
#:
#: 왜 필요한가: 헤드리스 브라우저에 "이 테마로 열어라"를 시킬 방법이 없다. 선택은
#: `localStorage` 에 있고 그건 **같은 출처의 페이지에서만** 쓸 수 있다. 그래서 같은
#: 서버에 씨앗 페이지를 하나 달고, 그 페이지가 값을 심은 뒤 앱으로 넘긴다.
#: (앱에 `?theme=` 같은 뒷문을 뚫지 않는다 — 뒷문은 남고 테스트는 끝난다.)
SEED_PAGE = """<!doctype html><html lang="ko"><meta charset="utf-8"><body><script>
try {
  localStorage.setItem('gitwire-chat.theme', %(name)s);
  localStorage.setItem('gitwire-chat.layout', %(layout)s);
} catch (e) {}
location.replace('/');
</script></body></html>"""

#: 팔레트가 **실제로 계산되는 색**을 재는 페이지. 진짜 `style.css` 를 그대로 물고
#: 화면에 있는 것과 같은 클래스 조합을 세워, `getComputedStyle` 값을 DOM 에 적어
#: 놓는다(`--dump-dom` 으로 회수한다).
#:
#: "적용했다"는 클레임이 아니라 **브라우저가 계산한 값**을 근거로 남기는 것이
#: 목적이다. 앱 페이지 쪽 증거(속성이 찍혔나 · 예외 0 · `/api/rooms` 호출)는 아래
#: 다른 테스트가 따로 본다 — 둘이 합쳐져야 증명이 닫힌다.
PROBE_PAGE = """<!doctype html><html lang="ko"%(attr)s><head><meta charset="utf-8">
<link rel="stylesheet" href="/static/style.css"></head><body>
<div class="app"><aside class="sidebar"><ul class="rooms">
<li class="room active"><button class="room-btn"><span class="room-name">방</span>
<span class="room-url" id="p-activeurl">주소</span></button></li></ul></aside>
<main class="chat"><div class="timeline" id="p-timeline"><div class="messages">
<article class="msg" id="p-theirs"><div class="msg-head">
<span class="author" id="p-theirs-author">남</span></div>
<div class="body">남의 말</div></article>
<article class="msg mine" id="p-mine"><div class="msg-head">
<span class="author" id="p-mine-author">나</span></div>
<div class="body">내 말</div></article>
<article class="msg mine failed" id="p-failed"><div class="msg-state">
<span class="state-text" id="p-failed-text">보내지 못했다</span></div></article>
</div></div>
<div class="outbox" id="p-outbox"><span>못 나갔다</span></div>
<p class="status error" id="p-error">오류</p>
</main></div>
<pre id="out"></pre>
<script>
function cs(id) { return getComputedStyle(document.getElementById(id)); }
document.getElementById('out').textContent = JSON.stringify({
  token_bg: getComputedStyle(document.documentElement).getPropertyValue('--bg').trim(),
  body_bg: getComputedStyle(document.body).backgroundColor,
  panel_bg: cs('p-theirs').backgroundColor,
  ink: cs('p-theirs').color,
  theirs_author: cs('p-theirs-author').color,
  mine_bg: cs('p-mine').backgroundColor,
  mine_border: cs('p-mine').borderTopColor,
  mine_author: cs('p-mine-author').color,
  danger: cs('p-error').color,
  failed_text: cs('p-failed-text').color,
  outbox_bg: cs('p-outbox').backgroundColor,
  outbox_ink: cs('p-outbox').color,
  active_url: cs('p-activeurl').color,
  scheme: getComputedStyle(document.documentElement).colorScheme
});
</script></body></html>"""


#: `log` 배치가 **실제로 어떻게 계산되는지** 재는 두 페이지.
#:
#: 앱 페이지로는 이걸 잴 수 없다 — 연기 테스트의 앱에는 방이 0개라(네트워크·git 을
#: 타지 않으려고) 메시지 줄이 아예 그려지지 않는다. 그래서 `message-node.js` 가
#: 만드는 것과 **같은 구조**를 손으로 세우고 진짜 `style.css` 를 물린다.
#: 구조가 어긋나면 stub DOM 테스트가 먼저 깨진다(3조각·클래스 이름을 거기서 고정).
#:
#: ⚠️ 측정은 **iframe 안에서** 한다. 이 환경의 헤드리스 Edge 는 `--window-size`
#: 를 줘도 뷰포트가 492px 아래로 내려가지 않는데, 좁은 폭 동작은 미디어 쿼리라
#: **뷰포트 폭**에서만 재진다. iframe 은 자기 폭이 곧 뷰포트다.
PROBE_LOG_ROWS = """<!doctype html><html lang="ko" data-chat-layout="log"%(theme)s>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<link rel="stylesheet" href="/static/style.css"></head><body>
<div class="app"><main class="chat"><div class="timeline"><div class="messages">
<article class="msg" id="r0" data-row="even" data-sender="1" data-index="0">
<time class="ts"><span class="hm">14:03</span><span class="sec" id="r0s">:22</span></time>
<span class="author" id="r0a">앨리스</span>
<div class="line"><div class="body">짝수 줄</div>
<div class="msg-actions"><button type="button" class="link">답장</button></div>
<div class="msg-state" hidden></div></div></article>
<article class="msg" id="r1" data-row="odd" data-sender="3" data-index="1">
<time class="ts"><span class="hm">14:03</span><span class="sec">:23</span></time>
<span class="author" id="r1a">밥</span>
<div class="line"><div class="body">홀수 줄 — 아주 긴 URL 도 줄을 깨지 않아야 한다
https://example.invalid/아주/긴/경로/가/이어지는/주소</div></div></article>
<article class="msg mine" id="r2" data-row="even" data-sender="2" data-index="2">
<time class="ts"><span class="hm">14:03</span><span class="sec">:24</span></time>
<span class="author" id="r2a">나</span>
<div class="line"><div class="body">내 줄</div></div></article>
</div></div></main></div></body></html>"""

#: 위 페이지를 정해진 폭의 iframe 에 넣고, 그 안의 계산된 값을 회수한다.
PROBE_LOG_FRAME = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
</head><body style="margin:0">
<iframe id="f" src="/__test__/probelog/%(theme)s/0"
        style="width:%(width)dpx;height:640px;border:0"></iframe>
<pre id="out"></pre>
<script>
var frame = document.getElementById('f');
frame.addEventListener('load', function () {
  var win = frame.contentWindow;
  var doc = frame.contentDocument;
  function cs(id) { return win.getComputedStyle(doc.getElementById(id)); }
  document.getElementById('out').textContent = JSON.stringify({
    viewport: win.innerWidth,
    display: cs('r0').display,
    columns: cs('r0').gridTemplateColumns.trim().split(/\s+/).length,
    numeric: win.getComputedStyle(doc.querySelector('.ts')).fontVariantNumeric,
    sec_display: cs('r0s').display,
    even_bg: cs('r0').backgroundColor,
    odd_bg: cs('r1').backgroundColor,
    mine_bg: cs('r2').backgroundColor,
    mine_rail: cs('r2').borderLeftColor,
    other_rail: cs('r0').borderLeftColor,
    sender1: cs('r0a').color,
    sender2: cs('r2a').color,
    sender3: cs('r1a').color,
    row_width: Math.round(doc.getElementById('r0').getBoundingClientRect().width),
    /* 접혔나 — 2열에서는 본문이 시각·발신자 **아래 줄**로 내려간다. */
    author_top: Math.round(doc.getElementById('r0a').getBoundingClientRect().top),
    time_top: Math.round(doc.querySelector('.ts').getBoundingClientRect().top),
    body_top: Math.round(doc.querySelector('#r0 .line').getBoundingClientRect().top),
    /* 가로 스크롤이 생기지 않나 (320px 규율) */
    overflow: doc.documentElement.scrollWidth > win.innerWidth + 1
  });
});
</script></body></html>"""


def attach_test_routes(app) -> None:
    """씨앗·측정 페이지를 붙인다. **테스트 안에서만** 존재한다."""
    import json as _json

    from flask import Response

    def seed(name: str, layout: str = "bubbles"):
        body = SEED_PAGE % {
            "name": _json.dumps(name), "layout": _json.dumps(layout)
        }
        return Response(body, mimetype="text/html")

    def probelog(name: str, width: int):
        theme = "" if name == "default" else f' data-chat-theme="{name}"'
        if width <= 0:
            return Response(PROBE_LOG_ROWS % {"theme": theme}, mimetype="text/html")
        body = PROBE_LOG_FRAME % {"theme": name, "width": width}
        return Response(body, mimetype="text/html")

    def probe(name: str):
        attr = "" if name == "default" else f' data-chat-theme="{name}"'
        return Response(PROBE_PAGE % {"attr": attr}, mimetype="text/html")

    app.add_url_rule("/__test__/seed/<name>", "test_seed", seed)
    app.add_url_rule("/__test__/seed/<name>/<layout>", "test_seed2", seed)
    app.add_url_rule("/__test__/probe/<name>", "test_probe", probe)
    app.add_url_rule("/__test__/probelog/<name>/<int:width>", "test_probelog", probelog)


@pytest.fixture
def served(tmp_path):
    """빈 상태의 앱을 임의 포트로 띄운다 (방 0개 = 네트워크·git 을 안 탄다)."""
    settings = Settings(
        home=tmp_path / "chats",
        author="브라우저테스트",
        poll_interval=0.5,
        notifications=False,
    )
    app = create_app(settings)
    attach_test_routes(app)
    try:
        with RecordingServer(app) as server:
            yield server
    finally:
        app.extensions["gitwire_chat"].stop()


def open_headless(
    url: str, profile: Path, window: str | None = None
) -> tuple[str, str]:
    """헤드리스로 페이지를 열고 (DOM, 콘솔) 을 돌려준다.

    `window` 로 창 폭을 준다 — 좁은 폭 동작은 CSS 미디어 쿼리라 **실제 폭**에서만
    재진다 (JS 가 폭을 재서 분기하지 않는다).
    """
    profile.mkdir(parents=True, exist_ok=True)
    argv = [
        BROWSER,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--enable-logging=stderr",
        "--log-level=0",
        f"--virtual-time-budget={VIRTUAL_TIME_BUDGET}",
        "--dump-dom",
    ]
    if window:
        argv.append(f"--window-size={window}")
    argv.append(url)
    proc = subprocess.run(
        argv, capture_output=True, timeout=BROWSER_TIMEOUT,
        # 브라우저 로그는 OS 로케일을 타므로 디코드를 관대하게 한다.
        text=True, encoding="utf-8", errors="replace",
    )
    return proc.stdout, proc.stderr


def uncaught_lines(console: str) -> list[str]:
    """콘솔에서 `Uncaught` 를 골라낸다.

    브라우저에 딸린 확장(chrome-extension://)의 잡음은 우리 페이지가 아니므로
    뺀다 — 그 외에는 무엇이든 실패다.
    """
    return [
        line for line in console.splitlines()
        if "Uncaught" in line and "chrome-extension://" not in line
    ]


@needs_browser
def test_실제_브라우저에서_페이지가_예외_없이_뜬다(served, tmp_path):
    dom, console = open_headless(served.url, tmp_path / "profile")

    bad = uncaught_lines(console)
    assert not bad, (
        "브라우저 콘솔에 처리되지 않은 예외가 있다 — 이 상태면 그 시점 이후의\n"
        "스크립트가 통째로 죽는다(버튼이 하나도 안 붙는다):\n  "
        + "\n  ".join(bad)
    )
    # 셸이 실제로 그려졌나 (템플릿이 렌더됐고 우리 정적 파일을 받았나).
    assert 'id="composer"' in dom
    # ⚠️ 자원 URL 에는 **내용 도장**이 박힌다 (`/assets/<도장>/app.js`).
    # 도장 값은 파일이 바뀌면 달라지므로 여기서 값을 고정하지 않는다.
    assert re.search(r"/assets/[0-9a-f]{12}/app\.js", dom), dom[:2000]


@needs_browser
def test_브라우저가_실제로_서버를_부른다_배선까지_갔다는_증거(served, tmp_path):
    """⭐ 이번 결함의 최소 판정 기준.

    `+` 를 눌러도 아무 일이 없던 이유는 `boot()` 이 `wire()` 전에 예외로 끊겨
    이벤트 핸들러가 하나도 안 붙었기 때문이다. 그때 브라우저는 `/api/rooms` 를
    **한 번도 부르지 않았다.** 페이지 응답은 200 이었다 — 그러니 상태코드가
    아니라 **이 호출**을 본다.
    """
    dom, console = open_headless(served.url, tmp_path / "profile")

    assert "/api/rooms" in served.paths, (
        "브라우저가 /api/rooms 를 부르지 않았다 = boot() 이 wire() 에 도달하지 못했다.\n"
        f"실제로 들어온 요청: {served.paths}\n"
        f"콘솔:\n{console[-2000:]}"
    )
    # 배선까지 갔으면 방 목록 응답을 그렸고, 방이 0개니 안내가 보여야 한다.
    assert 'id="rooms-empty"' in dom


@needs_browser
def test_정상_설치에서는_가상_스크롤이_결함으로_뜨지_않는다(served, tmp_path):
    """가상 스크롤은 이 앱이 대화를 그리는 방식 그 자체다.

    한때 여기에 '격하(degrade)' 가 있었다 — 엔진을 못 쓰면 전부 그리기로 계속
    가는 폴백이다. 걷어냈다: 성능 때문에 붙인 기능이 **조용히 빠진 채로** 앱이
    돌면, 다음에 벤더를 갱신하다 또 깨져도 아무도 모른다.

    지금은 못 쓰면 **결함으로 드러난다.** 그 지문이 정상 설치에서 보이면
    벤더 번들이 또 깨진 것이다. 초기화 단위 중 하나라도 못 서면 그것도 뜬다.
    """
    dom, _ = open_headless(served.url, tmp_path / "profile")
    assert "메시지를 그릴 수 없다" not in dom, (
        "가상 스크롤 결함 표시가 떴다 — 벤더 번들이 브라우저에서 못 돌고 있다"
    )
    assert "메시지 영역이 동작하지 않는다" not in dom
    assert "초기화 실패" not in dom, "초기화 단위 중 하나가 서지 못했다"


@needs_browser
def test_진입점이_ES_모듈로_실린다(served, tmp_path):
    """진입점이 `type="module"` 이라야 import 로 의존이 명시된다.

    예전에는 인라인 모듈이 엔진을 `window` 에 담아 두고 classic 스크립트가
    그것을 집어갔다 — "실렸나 / 그때 실렸나"가 실제 사고 지점이었다.
    """
    dom, _ = open_headless(served.url, tmp_path / "profile")
    assert 'type="module"' in dom
    assert re.search(r"/assets/[0-9a-f]{12}/app\.js", dom), dom[:2000]


# ------------------------------------------------------------- 색 테마

#: 고를 수 있는 팔레트 전부. `기본` 을 포함한다 — 첫 방문에 검은 화면이 되지
#: 않는다는 것도 브라우저에서 확인해야 하는 사실이다.
THEME_IDS = ["default", "log", "ide", "tty", "tui"]


def rgb(hex_or_rgba: str) -> str:
    """CSS 값을 브라우저가 `getComputedStyle` 로 되돌려 주는 표기로 바꾼다."""
    value = hex_or_rgba.strip()
    if value.startswith("#"):
        h = value.lstrip("#")
        return "rgb(%d, %d, %d)" % tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    return value.replace("rgba(", "rgba(").replace(",", ", ").replace("  ", " ")


def palette(name: str) -> dict[str, str]:
    """`style.css` 의 팔레트 정의 (단일 원천 — 여기서 값을 베끼지 않는다)."""
    from test_theme import palettes

    key = "기본-라이트" if name == "default" else name
    return palettes()[key]["__decls__"]


@needs_browser
@pytest.mark.parametrize("theme", THEME_IDS)
def test_각_팔레트가_실제_브라우저에서_예외_없이_뜬다(theme, served, tmp_path):
    """⭐ "테스트는 통과하는데 앱은 죽어 있다"를 팔레트마다 되풀이해 막는다.

    씨앗 페이지가 선택을 심고 앱으로 넘긴다. 그 뒤 보는 것은 이 프로젝트가
    실제로 당한 사고의 판정 기준 그대로다 — 콘솔 `Uncaught` 0, `/api/rooms`
    호출됨(=배선까지 갔다), 결함 표시 없음. 여기에 "고른 팔레트가 루트에 찍혔나"
    하나가 더 붙는다.
    """
    dom, console = open_headless(
        f"{served.url}__test__/seed/{theme}", tmp_path / "profile"
    )

    bad = uncaught_lines(console)
    assert not bad, f"[{theme}] 콘솔에 예외가 있다:\n  " + "\n  ".join(bad)

    # 앱까지 넘어갔나 (씨앗 페이지에 머물러 있으면 아래 전부 무의미하다).
    assert 'id="composer"' in dom, f"[{theme}] 앱으로 넘어가지 않았다"
    assert "/api/rooms" in served.paths, (
        f"[{theme}] /api/rooms 를 부르지 않았다 = boot() 이 배선까지 못 갔다.\n"
        f"들어온 요청: {served.paths}\n콘솔:\n{console[-1500:]}"
    )
    assert 'id="rooms-empty"' in dom

    # 고른 팔레트가 루트에 찍혔나. `기본` 은 **찍히지 않아야** 한다(시스템 추종).
    # ⚠️ 문자열 포함으로 보면 첫 페인트 조각의 **소스**가 걸린다 — 여는 `<html>`
    # 태그만 본다 (실제로 이 오탐에 한 번 걸렸다).
    import re as re_mod

    root_tag = re_mod.search(r"<html[^>]*>", dom)
    assert root_tag, "루트 태그를 찾지 못했다"
    if theme == "default":
        assert "data-chat-theme" not in root_tag.group(0), (
            f"고르지 않았는데 테마가 찍혔다: {root_tag.group(0)}"
        )
    else:
        assert f'data-chat-theme="{theme}"' in root_tag.group(0), (
            f"[{theme}] 루트에 팔레트가 찍히지 않았다 — 색이 바뀌지 않는다: "
            f"{root_tag.group(0)}"
        )
    # 고르는 UI 도 실제로 그려져 있나 (select 가 없으면 바꿀 방법이 없다).
    assert 'id="theme-select"' in dom
    assert f'<option value="{theme}"' in dom

    # 초기화 실패·가상 스크롤 결함이 팔레트 때문에 생기지 않았나.
    assert "초기화 실패" not in dom, f"[{theme}] 초기화 단위 하나가 못 섰다"
    assert "메시지를 그릴 수 없다" not in dom


@needs_browser
@pytest.mark.parametrize("theme", THEME_IDS)
def test_각_팔레트의_계산된_색이_정의와_같다(theme, served, tmp_path):
    """⭐ "적용했다"가 아니라 **브라우저가 계산한 값**으로 확인한다.

    토큰을 하나 빼먹으면 그 자리만 다른 팔레트 색이 나온다. 그건 `style.css` 를
    읽어서는 못 잡는다 — 실제로 상속·특이도를 거쳐 나온 값을 봐야 잡힌다.
    """
    import html as html_mod
    import json
    import re as re_mod

    dom, console = open_headless(
        f"{served.url}__test__/probe/{theme}", tmp_path / "profile"
    )
    assert not uncaught_lines(console), console[-1500:]

    found = re_mod.search(r'<pre id="out">(.*?)</pre>', dom, re_mod.S)
    assert found, f"[{theme}] 측정값을 회수하지 못했다:\n{dom[-1500:]}"
    got = json.loads(html_mod.unescape(found.group(1)))
    print(f"  [{theme}] 브라우저가 계산한 색: {json.dumps(got, ensure_ascii=False)}")

    tokens = palette(theme)
    if theme == "default":
        # 헤드리스는 보통 라이트로 뜨지만 환경에 따라 다크일 수 있다. `기본` 이
        # 지켜야 하는 것은 **시스템을 따른다**는 사실이므로 둘 다 통과시킨다.
        dark = palette_dark()
        assert got["body_bg"] in (rgb(tokens["--bg"]), rgb(dark["--bg"])), got["body_bg"]
        assert got["scheme"].strip() in ("light dark", "normal"), got["scheme"]
        return

    assert got["scheme"].strip() == "dark", f"[{theme}] color-scheme 이 dark 가 아니다"
    expected = {
        "token_bg": tokens["--bg"],
        "body_bg": rgb(tokens["--bg"]),
        "panel_bg": rgb(tokens["--panel"]),
        "ink": rgb(tokens["--ink"]),
        "theirs_author": rgb(tokens["--theirs-ink"]),
        "mine_bg": rgb(tokens["--mine"]),
        "mine_border": rgb(tokens["--mine-line"]),
        "mine_author": rgb(tokens["--mine-ink"]),
        "danger": rgb(tokens["--danger"]),
        "failed_text": rgb(tokens["--danger"]),
        "outbox_bg": rgb(tokens["--warn-bg"]),
        "outbox_ink": rgb(tokens["--warn-ink"]),
        "active_url": rgb(tokens["--accent-muted"]),
    }
    wrong = {k: (got[k], v) for k, v in expected.items() if got[k] != v}
    assert not wrong, (
        f"[{theme}] 계산된 색이 팔레트 정의와 다르다 (실제, 기대):\n  "
        + "\n  ".join(f"{k}: {a} ≠ {b}" for k, (a, b) in wrong.items())
    )


#: 배치는 두 가지고, 색과 **직교한다** — 조합이 성립하는지도 하나 본다.
LAYOUT_CASES = [("default", "bubbles"), ("default", "log"), ("tty", "log")]


@needs_browser
@pytest.mark.parametrize("theme,layout", LAYOUT_CASES)
def test_각_배치가_실제_브라우저에서_예외_없이_뜬다(theme, layout, served, tmp_path):
    """배치는 **구조**를 갈아끼운다 — 색보다 깨질 여지가 크다.

    그래서 팔레트와 같은 규율로 본다: 콘솔 `Uncaught` 0 · `/api/rooms` 호출 ·
    루트 표식 · 결함 표시 없음. 그리고 두 축이 **함께** 찍히는지도 확인한다.
    """
    dom, console = open_headless(
        f"{served.url}__test__/seed/{theme}/{layout}", tmp_path / "profile"
    )
    bad = uncaught_lines(console)
    assert not bad, f"[{theme}/{layout}] 콘솔 예외:\n  " + "\n  ".join(bad)
    assert 'id="composer"' in dom, f"[{theme}/{layout}] 앱으로 넘어가지 않았다"
    assert "/api/rooms" in served.paths
    assert "초기화 실패" not in dom, f"[{theme}/{layout}] 초기화 단위가 못 섰다"
    assert "메시지를 그릴 수 없다" not in dom

    import re as re_mod

    root = re_mod.search(r"<html[^>]*>", dom).group(0)
    if layout == "bubbles":
        assert "data-chat-layout" not in root, f"기본 배치인데 표식이 찍혔다: {root}"
    else:
        assert f'data-chat-layout="{layout}"' in root, root
    if theme == "default":
        assert "data-chat-theme" not in root, root
    else:
        assert f'data-chat-theme="{theme}"' in root, root
    # 배치를 고르는 칸은 어느 배치에서도 살아 있어야 한다 (갇히지 않는다).
    assert 'id="layout-select"' in dom
    assert f'<option value="{layout}"' in dom


@needs_browser
@pytest.mark.parametrize("width,expect_cols", [(900, 3), (320, 2)])
def test_log_배치가_실제로_격자로_계산된다(width, expect_cols, served, tmp_path):
    """⭐ 폭에 따른 접힘·줄무늬·발신자 색을 **브라우저가 계산한 값**으로 본다.

    넓은 폭은 시각·발신자·본문 3열, 320px 는 발신자를 본문 위로 접어 2열이고
    초는 숨는다. "적용했다"가 아니라 계산된 값이 근거다.
    """
    import html as html_mod
    import json
    import re as re_mod

    dom, console = open_headless(
        f"{served.url}__test__/probelog/tty/{width}", tmp_path / "profile"
    )
    assert not uncaught_lines(console), console[-1500:]
    found = re_mod.search(r'<pre id="out">(.*?)</pre>', dom, re_mod.S)
    assert found and found.group(1).strip(), dom[-1500:]
    got = json.loads(html_mod.unescape(found.group(1)))
    print(f"  [log · {width}px] {json.dumps(got, ensure_ascii=False)}")

    tokens = palette("tty")
    assert got["viewport"] == width, f"뷰포트가 {width} 가 아니다 ({got['viewport']})"
    assert got["display"] == "grid", "줄이 격자로 놓이지 않았다"
    assert got["columns"] == expect_cols, (
        f"{width}px 에서 {expect_cols}열이어야 한다 (실제 {got['columns']})"
    )
    assert got["numeric"] == "tabular-nums", "시각의 자릿수가 맞춰지지 않는다"
    assert got["overflow"] is False, "가로 스크롤이 생겼다"

    if expect_cols == 2:
        # 좁은 폭: 본문이 **아래 줄**로 접히고(시각·발신자가 머리줄), 초는 숨는다.
        assert got["sec_display"] == "none", "좁은데 초가 자리를 먹는다"
        assert got["body_top"] - got["time_top"] > 8, (
            f"본문이 접히지 않았다 (body {got['body_top']} / ts {got['time_top']})"
        )
        assert abs(got["author_top"] - got["time_top"]) <= 4, "발신자가 머리줄에 없다"
    else:
        assert got["sec_display"] == "inline", "넓은데 초가 안 보인다"
        # 3열: 시각·발신자·본문이 **한 줄**이다.
        # (기준선 정렬 때문에 1~2px 차이는 난다 — 같은 줄인지가 요점이다.)
        assert abs(got["author_top"] - got["time_top"]) <= 4, "3열인데 같은 줄이 아니다"
        assert abs(got["body_top"] - got["time_top"]) <= 4, "3열인데 본문이 아래로 갔다"

    # 줄무늬 — 홀수 줄만 줄무늬 색, 짝수는 투명, 내 줄은 내 바닥이 이긴다.
    assert got["odd_bg"] == rgb(tokens["--stripe"]), got["odd_bg"]
    assert got["even_bg"] == "rgba(0, 0, 0, 0)", got["even_bg"]
    assert got["mine_bg"] == rgb(tokens["--mine"]), got["mine_bg"]
    # 내 것은 **왼쪽 레일**로 갈린다 (오른쪽으로 옮기지 않는다).
    assert got["mine_rail"] == rgb(tokens["--mine-ink"]), got["mine_rail"]
    assert got["other_rail"] == "rgba(0, 0, 0, 0)", got["other_rail"]
    # 발신자마다 다른 색이 실제로 계산된다.
    assert got["sender1"] == rgb(tokens["--sender-1"])
    assert got["sender2"] == rgb(tokens["--sender-2"])
    assert got["sender3"] == rgb(tokens["--sender-3"])
    assert len({got["sender1"], got["sender2"], got["sender3"]}) == 3
    # 줄은 폭을 다 쓴다 (말풍선처럼 잘리지 않는다).
    assert got["row_width"] >= width - 24, got


def palette_dark() -> dict[str, str]:
    from test_theme import palettes

    return palettes()["기본-다크"]["__decls__"]


def test_브라우저가_없으면_건너뛴다는_사실이_드러난다():
    """SKIP 이 조용히 지나가지 않게 하는 자기 점검.

    브라우저가 없을 때 이 스위트가 **깨지지 않고 건너뛰는지**, 그리고 그 사유가
    사람이 읽을 수 있는지 확인한다. (`-ra` 로 매 실행 요약에 뜬다.)
    """
    assert needs_browser.kwargs["reason"]
    if BROWSER is None:
        print("브라우저 없음 → 브라우저 연기 테스트 SKIP", file=sys.stderr)
    else:
        assert Path(BROWSER).exists() or shutil.which(BROWSER)


# ------------------------------------------- ⭐ 캐시 무효화 (같은 버전 재배포)


@pytest.fixture
def served_copy(tmp_path):
    """정적 트리의 **사본**을 물고 뜬 앱.

    캐시 무효화를 브라우저에서 재려면 테스트가 정적 파일을 실제로 고쳐야 한다.
    패키지 안의 원본을 고치면 실패·중단 시 레포가 더러워진 채로 남으므로,
    사본으로 옮겨 놓고 그것을 고친다.

    ⚠️ 앱에 뒷문을 뚫지 않는다 — Flask 의 `static_folder` 와 우리 스탬퍼의
    `root` 는 원래 공개 속성이고, 테스트가 그 둘을 같은 곳으로 돌려놓을 뿐이다.
    """
    pkg_static = Path(gitwire_chat.__file__).parent / "static"
    copy = tmp_path / "static"
    shutil.copytree(pkg_static, copy)

    settings = Settings(
        home=tmp_path / "chats", author="캐시테스트", poll_interval=0.5,
        notifications=False,
    )
    app = create_app(settings)
    app.static_folder = str(copy)
    app.extensions["gitwire_chat_assets"].root = copy
    try:
        with RecordingServer(app) as server:
            server.static_root = copy
            yield server
    finally:
        app.extensions["gitwire_chat"].stop()


def stamp_of(dom: str) -> str:
    """DOM 에 박힌 자원 도장. 셸이 실제로 뱉은 URL 에서 읽는다."""
    found = re.search(r"/assets/([0-9a-f]{12})/app\.js", dom)
    assert found, f"셸에 도장 박힌 진입점 URL 이 없다:\n{dom[:2000]}"
    return found.group(1)


@needs_browser
def test_같은_버전으로_재배포해도_브라우저가_새_파일을_받는다(served_copy, tmp_path):
    """⭐ **지금의 실패 조건을 그대로 흉내 낸다.**

    이 프로젝트는 `pyproject.toml` 의 버전을 `0.2.0` 에 두고 git 커밋만 배포한다.
    즉 `importlib.metadata.version()` 은 갱신 후에도 **같은 값**이다. 그 값을
    캐시 키로 썼다면 브라우저는 옛 JS 를 계속 썼을 것이다 — 사용자가 매번
    강력 새로고침을 기억해야 하는 절차가 바로 그것이다.

    여기서는 브라우저 프로필을 **같은 것으로 재사용**해 디스크 캐시를 살려 둔
    채 세 번 연다:

      1회  옛 파일 → 도장 A
      2회  파일을 고친 뒤(버전 문자열은 그대로) → 도장 B ≠ A, 새 코드가 **실행**된다
      3회  아무것도 안 고침 → 도장 그대로 + 진입점을 **다시 안 받는다**(캐시 명중)

    3회가 반대 방향을 닫는다: 도장이 그대로면 브라우저는 네트워크를 타지 않는다.
    그게 없으면 "매번 다 새로 받는다"로도 1·2회를 통과할 수 있다.
    """
    from gitwire_chat.app import installed_version

    profile = tmp_path / "profile"          # ⭐ 세 번 모두 같은 프로필 = 캐시 유지
    entry = served_copy.static_root / "app.js"
    original = entry.read_bytes()
    version_before = installed_version()

    # --- 1회: 옛 파일 -------------------------------------------------
    dom1, console1 = open_headless(served_copy.url, profile)
    assert not uncaught_lines(console1), console1[-2000:]
    stamp1 = stamp_of(dom1)
    assert f"/assets/{stamp1}/app.js" in served_copy.paths, served_copy.paths
    assert 'data-probe="v2"' not in dom1

    # --- "배포": 버전 문자열은 그대로, 파일 내용만 바뀐다 ----------------
    entry.write_bytes(
        original
        + b"\ndocument.documentElement.setAttribute('data-probe', 'v2');\n"
    )
    assert installed_version() == version_before, (
        "이 테스트의 전제가 깨졌다 — 버전 문자열이 바뀌면 흉내가 아니다"
    )

    # --- 2회: 새 파일이 실제로 내려오나 -------------------------------
    served_copy.paths.clear()
    dom2, console2 = open_headless(served_copy.url, profile)
    assert not uncaught_lines(console2), console2[-2000:]
    stamp2 = stamp_of(dom2)
    assert stamp2 != stamp1, "파일을 고쳤는데 도장이 그대로다 = 캐시가 안 깨진다"
    assert f"/assets/{stamp2}/app.js" in served_copy.paths, served_copy.paths
    # ⭐ 클레임이 아니라 **실행된 결과**를 본다 — 새 코드가 루트에 찍은 표식.
    assert 'data-probe="v2"' in dom2, (
        "새 진입점이 내려오지 않았다(또는 안 돌았다) = 캐시가 옛 파일을 먹였다"
    )

    # --- 3회: 안 고치면 그대로 + 캐시 명중 ----------------------------
    served_copy.paths.clear()
    dom3, console3 = open_headless(served_copy.url, profile)
    assert not uncaught_lines(console3), console3[-2000:]
    assert stamp_of(dom3) == stamp2, "안 고쳤는데 도장이 바뀌었다"
    assert 'data-probe="v2"' in dom3
    assert f"/assets/{stamp2}/app.js" not in served_copy.paths, (
        "도장이 그대로인데 진입점을 또 받아 갔다 = immutable 캐시가 안 먹는다.\n"
        f"들어온 요청: {served_copy.paths}"
    )
    # 셸은 매번 새로 받는다 — 그 안에 새 도장이 들어 있어야 하니까.
    assert "/" in served_copy.paths

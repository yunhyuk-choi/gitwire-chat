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

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from werkzeug.serving import make_server

import gitwire_chat
from gitwire_chat import reads as reads_mod
from gitwire_chat.app import create_app
from gitwire_chat.config import Settings
from gitwire_chat.events import EventBus
from gitwire_chat.rooms import RoomManager

from conftest import RecordingNotifier

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
        #: 메서드·쿼리·응답 코드까지 남긴 기록. 드라이브바이 방어를 재려면
        #: "왔나"가 아니라 **무슨 메서드로 와서 몇 번으로 끝났나**가 필요하다.
        self.calls: list[dict] = []
        self._app = app
        self._server = make_server("127.0.0.1", 0, self._wsgi, threaded=True)
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _wsgi(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        method = environ.get("REQUEST_METHOD", "")
        query = environ.get("QUERY_STRING", "")
        self.paths.append(path)

        def record(status, headers, exc_info=None):
            self.calls.append({
                "method": method, "path": path, "query": query,
                "status": int(str(status).split()[0]),
            })
            return start_response(status, headers, exc_info)

        return self._app(environ, record)

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


#: `ide` 배치(연속 발화 묶기)를 **브라우저가 계산한 값**으로 재는 페이지.
#:
#: 앱 페이지로는 잴 수 없다 — 연기 테스트의 앱에는 방이 0개라(네트워크·git 을
#: 타지 않으려고) 메시지 줄이 그려지지 않는다. 그래서 `message-node.js` 가 만드는
#: 것과 **같은 구조**를 손으로 세우고 진짜 `style.css` 를 물린다 (구조가 어긋나면
#: stub DOM 테스트가 먼저 깨진다 — 조각·클래스 이름을 거기서 고정한다).
#:
#: 묶인 줄은 머리 조각이 `hidden` 이다 — 타임라인이 그렇게 만든다(지우지 않는다).
#: 떠 있는 머리(`.sticky-head`)도 함께 세워 **자리를 차지하지 않는지**(height 0)와
#: sticky 로 계산되는지를 본다.
PROBE_IDE_ROWS = """<!doctype html><html lang="ko" data-chat-layout="ide"%(theme)s>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<link rel="stylesheet" href="/static/style.css"></head><body>
<div class="app"><main class="chat"><div class="timeline" id="tl">
<div class="sticky-head" id="sh" data-sender="1"><div class="sticky-row" id="sr">
<span class="author" id="sha">bc.lee</span><time class="ts">14:12</time></div></div>
<div class="messages">
<article class="msg" id="g0" data-sender="1" data-index="0">
<div class="msg-head" id="g0h"><span class="author" id="g0a">bc.lee</span>
<time class="ts">14:12</time></div>
<div class="line" id="g0l"><div class="body">묶음의 첫 줄</div>
<div class="msg-actions"><button type="button" class="link">답장</button></div>
<div class="msg-state" hidden></div></div></article>
<article class="msg" id="g1" data-sender="1" data-index="1">
<div class="msg-head" id="g1h" hidden><span class="author">bc.lee</span>
<time class="ts">14:12</time></div>
<div class="line" id="g1l"><div class="body">묶인 줄 — 아주 긴 URL 도 줄을 깨지 않아야 한다
https://example.invalid/아주/긴/경로/가/이어지는/주소</div></div></article>
<article class="msg mine" id="g2" data-sender="2" data-index="2">
<div class="msg-head" id="g2h"><span class="author" id="g2a">나</span>
<time class="ts">14:16</time></div>
<div class="line" id="g2l"><div class="body">내 줄</div></div></article>
<article class="msg" id="g3" data-sender="1" data-index="3">
<div class="msg-head" id="g3h" hidden><span class="author">bc.lee</span>
<time class="ts">14:12</time></div>
<div class="line" id="g3l"><div class="body">묶음의 첫 줄</div>
<div class="msg-actions"><button type="button" class="link">답장</button></div>
<div class="msg-state" hidden></div></div></article>
<div id="filler" style="height:1200px"></div>
</div></div></main></div></body></html>"""

#: 위 페이지를 정해진 폭의 iframe 에 넣고 계산된 값을 회수한다 (`log` 과 같은 이유 —
#: 이 환경의 헤드리스 Edge 는 뷰포트를 492px 아래로 못 내린다. iframe 은 자기 폭이
#: 곧 뷰포트다).
PROBE_IDE_FRAME = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
</head><body style="margin:0">
<iframe id="f" src="/__test__/probeide/%(theme)s/0"
        style="width:%(width)dpx;height:640px;border:0"></iframe>
<pre id="out"></pre>
<script>
var frame = document.getElementById('f');
frame.addEventListener('load', function () {
  var win = frame.contentWindow;
  var doc = frame.contentDocument;
  function cs(id) { return win.getComputedStyle(doc.getElementById(id)); }
  function box(id) { return doc.getElementById(id).getBoundingClientRect(); }
  document.getElementById('out').textContent = JSON.stringify({
    viewport: win.innerWidth,
    /* 머리가 묶인 줄에서 **자리를 차지하지 않나** */
    head_display: cs('g0h').display,
    grouped_head_display: cs('g1h').display,
    grouped_head_height: Math.round(box('g1h').height),
    /* 묶인 줄이 첫 줄보다 낮다 = 세로 밀도를 벌었다 */
    first_height: Math.round(box('g0').height),
    grouped_height: Math.round(box('g1').height),
    /* 첫 줄과 **같은 본문**으로 묶인 줄 — 밀도 이득은 이 둘을 비교해야 보인다. */
    grouped_same_text_height: Math.round(box('g3').height),
    /* 내것은 **왼쪽 레일 색만** 바뀐다 (위치를 옮기지 않는다) */
    mine_rail: cs('g2l').borderLeftColor,
    other_rail: cs('g0l').borderLeftColor,
    mine_bg: cs('g2').backgroundColor,
    mine_left: Math.round(box('g2').left),
    other_left: Math.round(box('g0').left),
    /* 발신자 색 (묶기와 무관하게 log 과 같은 슬롯 규칙) */
    sender1: cs('g0a').color,
    mine_author: cs('g2a').color,
    /* 떠 있는 머리 — sticky 이고 **흐름을 차지하지 않는다**(높이 0) */
    sticky_position: cs('sh').position,
    sticky_height: Math.round(box('sh').height),
    sticky_row_position: cs('sr').position,
    sticky_row_visible: Math.round(box('sr').height) > 0,
    sticky_author: cs('sha').color,
    row_width: Math.round(box('g0').width),
    /* ⭐ 스크롤한 뒤에도 떠 있는 머리가 **창 위에 남아 있나.** 묶음 중간에서
       창이 시작해도 누가 말했는지 화면에 있다는 것이 곧 이 값이다. */
    scrolled: (function () {
      var tl = doc.getElementById('tl');
      tl.scrollTop = 300;
      return Math.round(tl.scrollTop);
    })(),
    sticky_offset_from_top: (function () {
      var tl = doc.getElementById('tl').getBoundingClientRect();
      return Math.round(box('sr').top - tl.top);
    })(),
    sticky_inside_view: (function () {
      var tl = doc.getElementById('tl').getBoundingClientRect();
      var r = box('sr');
      return r.top >= tl.top - 1 && r.bottom <= tl.bottom + 1 && r.height > 0;
    })(),
    /* 그 자리에서 첫 줄(진짜 머리)은 이미 화면 위로 밀려 올라갔다 — 전제다. */
    first_head_above_view: (function () {
      var tl = doc.getElementById('tl').getBoundingClientRect();
      return box('g0h').bottom < tl.top;
    })(),
    /* 가로 스크롤이 생기지 않나 (320px 규율) */
    overflow: doc.documentElement.scrollWidth > win.innerWidth + 1
  });
});
</script></body></html>"""


#: ⭐ **읽음 표시가 실제로 보이는지** 재는 페이지 (배치 3종 × 팔레트 × 좁은 폭).
#:
#: 앱 페이지로는 잴 수 없다 — 연기 테스트의 앱에는 방이 0개라(네트워크·git 을 타지
#: 않으려고) 메시지 줄이 그려지지 않는다. 그래서 `message-node.js` 가 만드는 것과
#: **같은 구조**를 손으로 세우고 진짜 `style.css` 를 물린다 (구조가 어긋나면 stub
#: DOM 테스트가 먼저 깨진다 — 조각·클래스 이름을 거기서 고정한다).
#:
#: 여기서 보려는 것은 클레임이 아니라 **브라우저가 계산한 값**이다:
#:   · 카운트 뱃지가 바닥과 다른 색으로 칠해지나 (팔레트마다)
#:   · 그 뱃지가 **자리를 차지하지 않나** (절대 위치 — 줄 높이가 그대로인가)
#:   · 320px 에서 가로 스크롤이 생기지 않나
#:   · 구분선이 보이나 (높이 > 0)
#:   · 방 목록 뱃지가 보이나
PROBE_READS_ROWS = """<!doctype html><html lang="ko"%(layout)s%(theme)s>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<link rel="stylesheet" href="/static/style.css"></head><body>
<div class="app">
<aside class="sidebar"><ul class="rooms">
<li class="room"><button class="room-btn" id="rb"><span class="room-name">방</span>
<span class="room-url">주소</span><span class="room-unread" id="badge">3</span></button></li>
<li class="room active"><button class="room-btn" id="rb2"><span class="room-name">보는 방</span>
<span class="room-url">주소</span><span class="room-unread" id="badge2">7</span></button></li>
</ul></aside>
<main class="chat"><div class="timeline"><div class="messages">
<article class="msg" id="m0" data-row="even" data-sender="1" data-index="0">
<time class="ts"><span class="hm">14:03</span><span class="sec">:22</span></time>
<span class="author">앨리스</span>
<div class="msg-head"><span class="author">앨리스</span><time class="ts">14:03</time></div>
<div class="line"><div class="body">읽음 숫자가 붙는 줄 — 본문이 길어도 숫자와 겹치지
않아야 한다 (오른쪽 여백을 미리 비워 둔다)</div></div>
<span class="msg-reads" id="r0">2</span></article>
<article class="msg" id="m1" data-row="odd" data-sender="3" data-index="1">
<div class="new-mark" id="nm"><span class="new-mark-label">여기부터 새 메시지</span></div>
<time class="ts"><span class="hm">14:04</span><span class="sec">:01</span></time>
<span class="author">밥</span>
<div class="msg-head"><span class="author">밥</span><time class="ts">14:04</time></div>
<div class="line"><div class="body">구분선이 붙은 줄</div></div>
<span class="msg-reads" id="r1" hidden></span></article>
<article class="msg mine" id="m2" data-row="even" data-sender="2" data-index="2">
<time class="ts"><span class="hm">14:05</span><span class="sec">:44</span></time>
<span class="author">나</span>
<div class="msg-head"><span class="author">나</span><time class="ts">14:05</time></div>
<div class="line"><div class="body">내 말 — 여기에도 숫자가 붙는다</div></div>
<span class="msg-reads" id="r2">99+</span></article>
</div></div></main></div></body></html>"""

#: 위 페이지를 정해진 폭의 iframe 에 넣고 계산된 값을 회수한다 (`log`·`ide` 와 같은
#: 이유 — 이 환경의 헤드리스 Edge 는 뷰포트를 492px 아래로 못 내린다).
PROBE_READS_FRAME = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
</head><body style="margin:0">
<iframe id="f" src="/__test__/probereads/%(layout)s/%(theme)s/0"
        style="width:%(width)dpx;height:640px;border:0"></iframe>
<pre id="out"></pre>
<script>
var frame = document.getElementById('f');
frame.addEventListener('load', function () {
  var win = frame.contentWindow;
  var doc = frame.contentDocument;
  function cs(id) { return win.getComputedStyle(doc.getElementById(id)); }
  function box(id) { return doc.getElementById(id).getBoundingClientRect(); }
  document.getElementById('out').textContent = JSON.stringify({
    viewport: win.innerWidth,
    /* 카운트 뱃지 — 보이나 · 색이 바닥과 다른가 · **자리를 차지하지 않나** */
    reads_display: cs('r0').display,
    reads_position: cs('r0').position,
    reads_color: cs('r0').color,
    mine_reads_color: cs('r2').color,
    row_bg: cs('m0').backgroundColor,
    reads_visible: box('r0').width > 0 && box('r0').height > 0,
    reads_hidden_when_zero: cs('r1').display === 'none',
    /* 절대 위치라 줄 높이에 영향이 없다 — 숫자가 있는 줄과 없는 줄의 높이가 같다
       (본문이 다르면 비교가 안 되므로 여기서는 **숫자 칸의 흐름 점유**만 본다). */
    reads_in_flow: (function () {
      var line = doc.querySelector('#m0 .line').getBoundingClientRect();
      var pill = box('r0');
      return pill.top >= line.bottom;      /* 흐름을 차지하면 줄 아래로 밀린다 */
    })(),
    /* 숫자가 줄 **안**에 있나 (밖으로 삐져나가면 가로 스크롤이 된다) */
    reads_inside_row: (function () {
      var row = box('m0');
      var pill = box('r0');
      return pill.right <= row.right + 1 && pill.left >= row.left;
    })(),
    /* 본문과 겹치지 않나 — 오른쪽 여백을 미리 비워 뒀는지의 실측 */
    body_right: Math.round(doc.querySelector('#m0 .body').getBoundingClientRect().right),
    reads_left: Math.round(box('r0').left),
    /* 구분선 — 보이나 */
    mark_height: Math.round(box('nm').height),
    mark_color: cs('nm').color,
    /* 방 목록 뱃지 — 보이나 · 선택된 방에서도 대비가 남나 */
    badge_bg: cs('badge').backgroundColor,
    badge_color: cs('badge').color,
    badge_visible: box('badge').width > 0 && box('badge').height > 0,
    badge_inside: box('badge').right <= box('rb').right + 1,
    active_badge_bg: cs('badge2').backgroundColor,
    active_badge_color: cs('badge2').color,
    /* 가로 스크롤이 생기지 않나 (320px 규율) */
    overflow: doc.documentElement.scrollWidth > win.innerWidth + 1
  });
});
</script></body></html>"""


#: ⭐ **실제 앱**의 읽음 카운트를 회수하는 프로브.
#:
#: 왜 `--dump-dom` 이 아닌가: 방이 있는 앱은 **SSE 스트림을 열어 둔다.** 그러면
#: 문서 로드가 끝나지 않아 `--dump-dom` 이 영원히 돌아오지 않는다(실측 — 120초
#: 타임아웃). 다른 브라우저 테스트가 이 문제를 안 겪는 이유는 방을 0개로 두어
#: 스트림이 없기 때문이고, 그래서 그 테스트들로는 읽음 카운트를 원리상 못 잰다.
#:
#: 그래서 방향을 뒤집는다 — 페이지가 앱을 iframe 에 띄우고 스스로 화면을 들여다본
#: 뒤 **결과를 서버로 POST 한다.** 판정 근거는 브라우저가 실제로 그린 DOM 이고,
#: 서버는 그것을 받아 적기만 한다.
PROBE_APP_READS = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
</head><body style="margin:0"><script>
try {
  localStorage.setItem('gitwire-chat.theme', 'default');
  localStorage.setItem('gitwire-chat.layout', %(layout)s);
} catch (e) {}
var frame = document.createElement('iframe');
frame.style.cssText = 'width:1024px;height:720px;border:0';
frame.src = '/';
document.body.appendChild(frame);
var tries = 0;
function snapshot() {
  var doc = frame.contentDocument;
  if (!doc) { return { ready: false }; }
  var slots = doc.querySelectorAll('.msg-reads');
  var shown = [];
  for (var i = 0; i < slots.length; i++) {
    if (!slots[i].hidden) { shown.push(slots[i].textContent); }
  }
  return {
    ready: true,
    layout: doc.documentElement.getAttribute('data-chat-layout'),
    messages: doc.querySelectorAll('.msg').length,
    slots: slots.length,
    badges: shown,
    errors: (frame.contentWindow.__probeErrors || []).slice(0, 5)
  };
}
function look() {
  tries += 1;
  var out;
  try { out = snapshot(); } catch (e) { out = { ready: false, error: String(e) }; }
  out.tries = tries;
  /* 숫자가 뜨면 바로, 아니면 시간을 다 써 보고 **그 사실 그대로** 보고한다
     (조용히 성공으로 넘어가지 않는다 — 빈 결과도 결과다). */
  if ((out.badges && out.badges.length) || tries >= 40) {
    fetch('/__test__/report', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(out)
    });
    return;
  }
  setTimeout(look, 250);
}
setTimeout(look, 400);
</script></body></html>"""


def attach_test_routes(app) -> None:
    """씨앗·측정 페이지를 붙인다. **테스트 안에서만** 존재한다."""
    import json as _json

    from flask import Response, request

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

    def probeide(name: str, width: int):
        theme = "" if name == "default" else f' data-chat-theme="{name}"'
        if width <= 0:
            return Response(PROBE_IDE_ROWS % {"theme": theme}, mimetype="text/html")
        body = PROBE_IDE_FRAME % {"theme": name, "width": width}
        return Response(body, mimetype="text/html")

    def probe(name: str):
        attr = "" if name == "default" else f' data-chat-theme="{name}"'
        return Response(PROBE_PAGE % {"attr": attr}, mimetype="text/html")

    def probereads(layout: str, name: str, width: int):
        if width <= 0:
            theme = "" if name == "default" else f' data-chat-theme="{name}"'
            box = "" if layout == "bubbles" else f' data-chat-layout="{layout}"'
            return Response(
                PROBE_READS_ROWS % {"layout": box, "theme": theme},
                mimetype="text/html",
            )
        body = PROBE_READS_FRAME % {
            "layout": layout, "theme": name, "width": width
        }
        return Response(body, mimetype="text/html")

    reports: list = []
    app.test_reports = reports          # 테스트가 여기서 결과를 읽는다

    def appreads(layout: str):
        body = PROBE_APP_READS % {"layout": _json.dumps(layout)}
        return Response(body, mimetype="text/html")

    def report():
        reports.append(request.get_json(silent=True) or {})
        return Response('{"ok":true}', mimetype="application/json")

    app.add_url_rule("/__test__/appreads/<layout>", "test_appreads", appreads)
    app.add_url_rule("/__test__/report", "test_report", report, methods=["POST"])
    app.add_url_rule("/__test__/seed/<name>", "test_seed", seed)
    app.add_url_rule("/__test__/seed/<name>/<layout>", "test_seed2", seed)
    app.add_url_rule("/__test__/probe/<name>", "test_probe", probe)
    app.add_url_rule("/__test__/probelog/<name>/<int:width>", "test_probelog", probelog)
    app.add_url_rule("/__test__/probeide/<name>/<int:width>", "test_probeide", probeide)
    app.add_url_rule(
        "/__test__/probereads/<layout>/<name>/<int:width>",
        "test_probereads", probereads,
    )


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


@pytest.fixture
def served_with_room(tmp_path, bare_repo):
    """⭐ **방이 있는** 앱을 띄운다 — 진짜 git, 진짜 레코드, 진짜 커서 파일.

    다른 브라우저 테스트는 방을 0개로 두어 git 을 안 탄다. 그런데 읽음 카운트는
    *방 안의 메시지*에 붙는 숫자라 그 상태에서는 원리상 잴 수 없고, 손으로 세운
    마크업(`PROBE_READS_ROWS`)으로는 **CSS 만** 증명된다 — 실사용에서 무너진 것은
    CSS 가 아니라 배선이었다(커서가 오염돼 카운트가 언제나 0). 그래서 여기서는
    실제 앱이 실제 방을 그리게 한다.

    참가자를 하나 더 심어 두는 이유: 카운트는 "**남이** 안 읽은 수"라 나 혼자면
    정의상 0 이다 (분모가 없다).
    """
    settings = Settings(
        home=tmp_path / "chats",
        author="브라우저테스트",
        poll_interval=0.5,
        notifications=False,
    )
    manager = RoomManager(
        settings, bus=EventBus(keepalive=0.2), notifier=RecordingNotifier()
    )
    app = create_app(settings, manager, start=False)
    attach_test_routes(app)
    room = manager.register(str(bare_repo))
    manager.wait_for_connect()
    assert manager.status(room.id).state == "ready", manager.status(room.id).detail
    manager.timeline(room.id)                     # = 방을 열었다 (내 커서 파일)
    manager.send(room.id, "읽음 숫자가 붙어야 하는 말")
    manager.send(room.id, "두 번째 말")
    # ⭐ 남 하나 — 그 사람의 발행 커서를 **실제 방에 저장돼 있던 오염 값**으로
    #    둔다. 이것이 실사용에서 화면이 죽은 상태 그 자체다: `~`(0x7E) 가
    #    `records/`(0x72) 보다 사전식 뒤라 `cursor(p) < M` 이 거짓이 되고, 그 사람이
    #    "다 읽은 사람"으로 세어져 카운트가 **0** 이 된다(= 숫자가 아예 안 뜬다).
    #    새 코드는 이 값을 커서 없음으로 떨궈 안 읽은 것으로 세므로 카운트 = 1 이다.
    channel = manager.reads(room.id).channel
    channel.write_state(
        "bob@example.com",
        reads_mod.build_value("~pending/000001", ["bob.host"]),
        identity="bob@example.com",
    )
    manager.start()
    try:
        with RecordingServer(app) as server:
            yield server, app
    finally:
        manager.stop()


def run_until_report(url: str, profile: Path, app, *, seconds: float = 40.0) -> dict:
    """브라우저를 띄우고 프로브가 보내오는 **한 건**을 회수한다.

    ⚠️ 이 경로는 `--dump-dom` 을 쓸 수 없다 (위 `PROBE_APP_READS` 도크 — 방이 있는
    앱은 SSE 를 열어 둬서 로드가 끝나지 않는다). 그래서 브라우저를 살려 둔 채
    **서버에 도착한 보고**를 기다리고, 받으면 브라우저를 끝낸다. 못 받으면 그
    사실이 곧 실패다 — 여기서 임의로 성공을 만들지 않는다.
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
        "--window-size=1200,900",
        url,
    ]
    # ⚠️ `--virtual-time-budget` 을 주지 않는다 — 프로브가 실시간 타이머로
    # 화면을 다시 보는데, 가상 시간은 그 폴링을 앞질러 태워 버린다.
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    reports = app.test_reports
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not reports:
            time.sleep(0.25)
    finally:
        proc.kill()
        try:
            _, console = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:      # pragma: no cover — 방어
            console = ""
    if not reports:
        # 조용히 넘어가지 않는다 — 콘솔을 그대로 실어 실패시킨다.
        raise AssertionError(
            "브라우저가 화면 보고를 보내지 못했다 (앱이 뜨지 않았거나 프로브가 죽었다)."
            + chr(10) + "브라우저 콘솔:" + chr(10) + console[-4000:]
        )
    bad = uncaught_lines(console)
    assert not bad, chr(10).join(bad)
    return reports[0]


@needs_browser
@pytest.mark.parametrize("layout", ["bubbles", "log", "ide"])
def test_실제_앱에서_읽음_카운트가_세_배치_모두에_보인다(
    served_with_room, tmp_path, layout
):
    """⭐ 실사용 신고를 정면으로 겨냥한다 — "안 읽음 카운트가 UI 에 안 보인다".

    ⚠️ 이 테스트가 없어서 놓쳤다. 손으로 세운 마크업 프로브(`PROBE_READS_ROWS`)는
    **CSS 만** 봤고, stub DOM 테스트는 커서를 실제 봉투 ID 로 직접 넘겨 줬다. 실제로
    무너진 지점은 앱이 스스로 커서를 전진시키는 그 사이(낙관적 임시 ID 채택)였고,
    그 결과 카운트가 언제나 0 이라 세 배치 모두에서 아무 숫자도 뜨지 않았다
    (`ide` 만의 문제가 아니었다 — 신고가 `ide` 에서 먼저 올라온 것일 뿐이다).
    """
    server, app = served_with_room
    got = run_until_report(
        server.url + f"__test__/appreads/{layout}",
        tmp_path / f"profile-{layout}",
        app,
    )
    assert got.get("ready"), got
    # `bubbles` 는 기본값이라 속성을 찍지 않는다 (다른 프로브도 같은 규약).
    assert got.get("layout") == (None if layout == "bubbles" else layout), got
    assert got.get("messages", 0) >= 2, f"메시지가 그려지지 않았다: {got}"
    assert got.get("slots", 0) >= 2, f"읽음 자리가 없다 ({layout}): {got}"
    # ⭐ 화면에 **실제로 보이는** 숫자. 남 한 명이 아직 안 읽었으므로 전부 1 이다.
    #    (그 사람의 발행 커서는 오염 값이다 — 복구 경로가 진짜 브라우저에서 도는지가
    #     여기서 판정된다. 떨구지 않으면 이 목록이 비고, 그것이 실사용 신고다.)
    assert got.get("badges"), (
        f"{layout} 배치에서 읽음 카운트가 화면에 하나도 없다 — 실사용 신고와 같은 상태다:"
        f" {got}"
    )
    assert set(got["badges"]) == {"1"}, got


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


#: 배치는 색과 **직교한다** — 조합이 성립하는지도 함께 본다.
LAYOUT_CASES = [
    ("default", "bubbles"), ("default", "log"), ("tty", "log"),
    ("default", "ide"), ("tty", "ide"),
]


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


@needs_browser
@pytest.mark.parametrize("theme", THEME_IDS)
def test_ide_배치가_팔레트_다섯_종에서_모두_예외_없이_뜬다(theme, served, tmp_path):
    """⭐ 배치와 색은 **직교한다** — 그 조합이 실제로 성립하는지 다섯 번 본다.

    `ide` 는 렌더 로직을 건드린 배치다(연속 발화 묶기·떠 있는 머리). 그래서
    팔레트마다 앱을 열어 이 프로젝트가 실제로 당한 사고의 기준으로 본다:
    콘솔 `Uncaught` 0 · `/api/rooms` 호출됨(=배선까지 갔다) · 결함 표시 없음.
    """
    dom, console = open_headless(
        f"{served.url}__test__/seed/{theme}/ide", tmp_path / "profile"
    )
    bad = uncaught_lines(console)
    assert not bad, f"[{theme}/ide] 콘솔 예외:\n  " + "\n  ".join(bad)
    assert 'id="composer"' in dom, f"[{theme}/ide] 앱으로 넘어가지 않았다"
    assert "/api/rooms" in served.paths, f"[{theme}/ide] 서버를 부르지 않았다"
    assert "초기화 실패" not in dom, f"[{theme}/ide] 초기화 단위가 못 섰다"
    assert "메시지를 그릴 수 없다" not in dom

    import re as re_mod

    root = re_mod.search(r"<html[^>]*>", dom).group(0)
    assert 'data-chat-layout="ide"' in root, root
    if theme == "default":
        assert "data-chat-theme" not in root, root
    else:
        assert f'data-chat-theme="{theme}"' in root, root
    # 떠 있는 머리는 **자리에 있고 숨어 있다** (묶음 중간에서만 뜬다).
    assert 'id="sticky-head"' in dom, "떠 있는 머리 자리가 없다"
    # 두 축을 고르는 칸은 어느 조합에서도 살아 있다 (갇히지 않는다).
    assert 'id="layout-select"' in dom
    assert '<option value="ide"' in dom


@needs_browser
@pytest.mark.parametrize("width", [900, 320])
def test_ide_배치의_묶기가_실제로_계산된다(width, served, tmp_path):
    """⭐ 묶인 줄에서 머리가 **자리를 차지하지 않는지**, 떠 있는 머리가 **흐름을
    차지하지 않는지**를 브라우저가 계산한 값으로 본다.

    이 둘이 이 배치의 두 위험이다. 머리가 자리를 차지하면 묶어도 세로 밀도가
    벌리지 않고, 떠 있는 머리가 흐름을 차지하면 가상화가 계산한 세로 좌표와 실제
    픽셀이 어긋난다 — 머리 잘림을 막으려고 붙인 장치가 스크롤을 망가뜨린다.

    320px 도 같은 규율로 본다. 묶기는 좁은 폭에서 **오히려 이득이 크다**
    (반복되는 이름이 세로를 덜 먹는다) — 접을 것이 없어 `log` 처럼 2열로 갈 필요도
    없다. 여기서 보는 것은 가로 스크롤이 생기지 않고 줄이 폭을 다 쓴다는 사실이다.
    """
    import html as html_mod
    import json
    import re as re_mod

    dom, console = open_headless(
        f"{served.url}__test__/probeide/tty/{width}", tmp_path / "profile"
    )
    assert not uncaught_lines(console), console[-1500:]
    found = re_mod.search(r'<pre id="out">(.*?)</pre>', dom, re_mod.S)
    assert found and found.group(1).strip(), dom[-1500:]
    got = json.loads(html_mod.unescape(found.group(1)))
    print(f"  [ide · {width}px] {json.dumps(got, ensure_ascii=False)}")

    tokens = palette("tty")
    assert got["viewport"] == width, f"뷰포트가 {width} 가 아니다 ({got['viewport']})"

    # (1) 묶인 줄의 머리는 **없는 것과 같다** (지운 것이 아니라 숨긴 것이다).
    assert got["head_display"] != "none", "묶음 첫 줄의 머리가 안 보인다"
    assert got["grouped_head_display"] == "none", "묶였는데 머리가 자리를 먹는다"
    assert got["grouped_head_height"] == 0, got["grouped_head_height"]
    assert got["grouped_same_text_height"] < got["first_height"], (
        f"묶여도 높이가 같다 (첫 줄 {got['first_height']} / "
        f"같은 본문의 묶인 줄 {got['grouped_same_text_height']})"
    )

    # (2) 내것은 **왼쪽 레일 색만** 바뀐다 — 위치도 바닥도 그대로다.
    assert got["mine_rail"] == rgb(tokens["--mine-ink"]), got["mine_rail"]
    assert got["other_rail"] == rgb(tokens["--line"]), got["other_rail"]
    assert got["mine_bg"] == "rgba(0, 0, 0, 0)", got["mine_bg"]
    assert got["mine_left"] == got["other_left"], "내 줄이 오른쪽으로 옮겨졌다"
    assert got["mine_author"] == rgb(tokens["--mine-ink"]), got["mine_author"]
    assert got["sender1"] == rgb(tokens["--sender-1"]), got["sender1"]

    # (3) 떠 있는 머리 — sticky 이고 **흐름을 차지하지 않는다**(높이 0).
    assert got["sticky_position"] == "sticky", got["sticky_position"]
    assert got["sticky_height"] == 0, (
        f"떠 있는 머리가 흐름을 차지한다 ({got['sticky_height']}px)"
    )
    assert got["sticky_row_position"] == "absolute", got["sticky_row_position"]
    assert got["sticky_row_visible"] is True, "떠 있는 머리가 보이지 않는다"
    # ⭐ 스크롤해서 진짜 머리가 화면 위로 밀려 나간 상태에서도 **창 위에 남는다.**
    assert got["scrolled"] > 0, "스크롤이 생기지 않아 이 판정이 무의미하다 (전제 실패)"
    assert got["first_head_above_view"] is True, (
        "진짜 머리가 아직 화면 안에 있다 — 잘림 상황이 재현되지 않았다 (전제 실패)"
    )
    # (`.timeline` 자신의 위쪽 여백만큼은 내려온다 — sticky 는 스크롤포트의
    #  패딩 박스 위에 붙는다. 요점은 **함께 밀려 올라가지 않는다**는 것이다.)
    assert 0 <= got["sticky_offset_from_top"] <= 8, (
        f"떠 있는 머리가 함께 밀려 올라갔다 (창 위에서 {got['sticky_offset_from_top']}px)"
    )
    assert got["sticky_inside_view"] is True, "떠 있는 머리가 창 밖으로 나갔다"
    assert got["sticky_author"] == rgb(tokens["--sender-1"]), got["sticky_author"]

    # (4) 좁은 폭에서도 가로 스크롤이 없고 줄은 폭을 다 쓴다.
    assert got["overflow"] is False, "가로 스크롤이 생겼다"
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


# ------------------------- ⭐ 드라이브바이 방어 (진짜 브라우저로 두들긴다)
#
# 이 절이 존재하는 이유는 한 문장이다 — **예전 판단을 뒤집었기 때문이다.**
# 한때 "누르면 앱을 죽이고 갈아치우는 엔드포인트는 아무 웹페이지의 폼 전송
# 하나로 트리거되고 막을 수단이 없다"고 보고 그 엔드포인트를 두지 않았다.
# 뒤집은 근거(커스텀 헤더 + `Sec-Fetch-Site` + `Origin`)는 논리일 뿐이라,
# **실제로 막히는지 브라우저로 두들겨서** 남긴다.
#
# `tests/test_updater.py` 의 헤더 단위 테스트와 서로를 대체하지 않는다:
# 거기서는 *우리 문이 무엇을 거절하나*를 보고, 여기서는 *브라우저가 실제로
# 무엇을 보내나*를 본다. 후자가 없으면 "폼은 헤더를 못 붙인다" 같은 문장이
# 검증되지 않은 가정으로 남는다.

#: 공격자 페이지. **다른 오리진**에서 서빙되고 네 가지로 두들긴다.
ATTACK_PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>드라이브바이</title></head><body>
<!-- (a) 숨은 폼 자동 전송. 응답을 읽을 필요도 없다 — 예전 판단이 두려워한 바로
     그 모양이다. 폼은 **커스텀 헤더를 붙일 수 없다.** -->
<form id="f" method="POST" action="%(target)s/api/update/run?probe=a-form" target="sink"></form>
<iframe name="sink" style="display:none"></iframe>
<pre id="out"></pre>
<script>
var target = '%(target)s';
var log = {};
function done(name, text) {
  log[name] = text;
  document.getElementById('out').textContent = JSON.stringify(log);
}

try { document.getElementById('f').submit(); done('a_form', 'sent'); }
catch (e) { done('a_form', 'throw: ' + e); }

/* (b) 커스텀 헤더를 붙인 fetch — 그러면 단순 요청이 아니게 되어 브라우저가
   preflight 를 먼저 보낸다. 우리는 CORS 를 켜지 않으므로 본 요청이 안 나간다. */
fetch(target + '/api/update/run?probe=b-header', {
  method: 'POST', mode: 'cors', headers: { 'X-Gitwire-Chat': 'update' }
}).then(function (r) { done('b_header', 'reached: ' + r.status); },
        function (e) { done('b_header', 'blocked: ' + e.name); });

/* (c) 헤더 없는 단순 요청 — 나가긴 한다. 서버가 거절해야 한다. */
fetch(target + '/api/update/run?probe=c-simple', { method: 'POST', mode: 'cors' })
  .then(function (r) { done('c_simple', 'reached: ' + r.status); },
        function (e) { done('c_simple', 'blocked: ' + e.name); });

/* (d) 응답을 아예 안 읽는 fire-and-forget. 응답을 못 읽어도 **효과**만 나면
   공격은 성공이다 — 그래서 이 모양을 따로 본다. */
fetch(target + '/api/update/run?probe=d-nocors', { method: 'POST', mode: 'no-cors' })
  .then(function () { done('d_nocors', 'sent'); },
        function (e) { done('d_nocors', 'blocked: ' + e.name); });
</script></body></html>"""

#: 우리 앱과 **같은 오리진**에서 도는 페이지. 정상 경로가 통과하는지 본다.
#: (앱의 실제 화면은 확인 → 확인받기 → 실행이라 브라우저 한 번으로 몰기 어렵다.
#:  여기서는 `update.js` 가 보내는 그 요청 모양만 그대로 흉내 낸다.)
SAME_ORIGIN_PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>정상 경로</title></head><body><pre id="out"></pre>
<script>
var log = {};
function done(name, text) {
  log[name] = text;
  document.getElementById('out').textContent = JSON.stringify(log);
}
/* (e) 우리 화면이 실제로 보내는 그대로 — 통과해야 한다. */
fetch('/api/update/run?probe=e-ui', {
  method: 'POST', headers: { 'X-Gitwire-Chat': 'update' }
}).then(function (r) { done('e_ui', 'reached: ' + r.status); },
        function (e) { done('e_ui', 'blocked: ' + e.name); });
/* (f) 같은 출처인데 **헤더만** 뺐다 — 그 헤더가 실제로 문을 지키나. */
fetch('/api/update/run?probe=f-noheader', { method: 'POST' })
  .then(function (r) { done('f_noheader', 'reached: ' + r.status); },
        function (e) { done('f_noheader', 'blocked: ' + e.name); });
</script></body></html>"""


class RecordingLauncher:
    """`updaterun.Launcher` 자리에 끼우는 기록기 — **아무것도 띄우지 않는다.**

    이 테스트에서 진짜 갱신이 돌면 브라우저 연기 테스트가 사용자 환경을 바꾼다.
    그리고 "몇 번 띄웠나" 가 이 절의 가장 강한 증거다: 거절이 사유만 다르게
    말하고 실행은 됐다면 여기 숫자가 올라간다.
    """

    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self.launches = 0

    def launch(self):
        from gitwire_chat import updaterun

        self.launches += 1
        return updaterun.Run(
            launcher_pid=os.getpid(),
            started_at=0.0,
            log=str(self.home / updaterun.LOG_NAME),
            pid=999999,
        )


class PageServer:
    """HTML 한 장만 주는 서버. **다른 오리진**을 만드는 데 쓴다."""

    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")
        self._server = make_server("127.0.0.1", 0, self._wsgi, threaded=True)
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _wsgi(self, environ, start_response):
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [self._body]

    @property
    def url(self) -> str:
        # ⚠️ 앱은 `127.0.0.1` 로 열고 이 페이지는 `localhost` 로 연다 —
        # 호스트가 다르므로 브라우저에게 **cross-site** 다 (포트만 다르면
        # same-site 로 잡혀서 `Sec-Fetch-Site` 값이 약해진다).
        return f"http://localhost:{self.port}/"

    def __enter__(self) -> "PageServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._thread.join(timeout=10)
        self._server.server_close()


@pytest.fixture
def guarded(tmp_path, monkeypatch):
    """갱신 실행 엔드포인트를 브라우저로 두들겨 볼 준비 한 벌.

    갱신은 **띄우지 않고**(실행기를 기록기로 갈아끼운다) 원격도 **보지 않는다**
    ("새 것이 있다"를 고정한다). 즉 이 테스트가 보는 것은 오직 **문**이다.
    """
    from gitwire_chat import updater

    monkeypatch.setattr(
        updater,
        "_direct_url",
        lambda: {
            "url": "https://example.invalid/gitwire-chat.git",
            "vcs_info": {"vcs": "git", "commit_id": "a" * 40},
        },
    )
    monkeypatch.setattr(updater, "remote_commit", lambda url, ref="HEAD": "b" * 40)

    settings = Settings(
        home=tmp_path / "chats", author="문지기테스트", poll_interval=0.5,
        notifications=False,
    )
    app = create_app(settings)
    launcher = RecordingLauncher(tmp_path / "chats")
    app.extensions["gitwire_chat_update"] = launcher

    def page(name: str):
        from flask import Response

        return Response(SAME_ORIGIN_PAGE, mimetype="text/html")

    app.add_url_rule("/__test__/samesite/<name>", "test_samesite", page)
    try:
        with RecordingServer(app) as server:
            server.launcher = launcher
            yield server
    finally:
        app.extensions["gitwire_chat"].stop()


def update_posts(calls, probe: str) -> list[dict]:
    return [
        c for c in calls
        if c["method"] == "POST" and c["path"] == "/api/update/run" and probe in c["query"]
    ]


def update_options(calls, probe: str) -> list[dict]:
    return [
        c for c in calls
        if c["method"] == "OPTIONS" and c["path"] == "/api/update/run" and probe in c["query"]
    ]


@needs_browser
def test_다른_페이지는_갱신을_시작시킬_수_없고_우리_화면은_통과한다(guarded, tmp_path):
    """⭐ 예전 판단을 뒤집은 근거를 **실증**한다.

    두 번 연다:

      ① 다른 오리진(`localhost:B`)의 공격 페이지 → 네 가지로 두들긴다.
         숨은 폼 · 커스텀 헤더 fetch · 헤더 없는 단순 요청 · no-cors.
      ② 같은 오리진(`127.0.0.1:A`)의 페이지 → 우리 화면이 보내는 모양 하나와
         **헤더만 뺀** 모양 하나.

    판정 기준 셋:
      · 다른 오리진에서 온 POST 는 하나도 통과하지 못한다 (전부 403).
      · 커스텀 헤더를 붙인 크로스 오리진 요청은 **POST 자체가 도달하지 못한다**
        (preflight 에서 죽는다) — 커스텀 헤더 요구가 실제로 문이라는 증거.
      · 갱신은 **딱 한 번** 시작된다 — 같은 오리진 + 헤더가 있는 그 하나.
    """
    target = f"http://127.0.0.1:{guarded.port}"

    # --- ① 다른 오리진에서 두들긴다 -----------------------------------
    with PageServer(ATTACK_PAGE % {"target": target}) as attacker:
        dom, console = open_headless(attacker.url, tmp_path / "profile-evil")
    attack_calls = list(guarded.calls)
    table = "\n".join(
        f"  {c['method']:>7} {c['path']}?{c['query']} → {c['status']}"
        for c in attack_calls if c["path"] == "/api/update/run"
    )
    print("공격 페이지가 만든 요청:\n" + (table or "  (없음)"))
    print("공격 페이지가 본 결과: " + (re.search(r"<pre id=\"out\">(.*?)</pre>", dom, re.S).group(1) if "id=\"out\"" in dom else "?"))

    reached = [c for c in attack_calls if c["path"] == "/api/update/run" and c["status"] != 403]
    assert not [c for c in reached if c["method"] == "POST"], (
        "다른 오리진의 POST 가 문을 통과했다:\n" + table
    )
    assert guarded.launcher.launches == 0, "다른 페이지가 갱신을 시작시켰다"
    assert not uncaught_lines(console), console[-2000:]

    # ⭐ 커스텀 헤더를 붙이려 하면 본 요청이 **나가지도 못한다.**
    assert not update_posts(attack_calls, "b-header"), (
        "커스텀 헤더를 붙인 크로스 오리진 POST 가 서버까지 왔다 = preflight 가 안 막았다:\n"
        + table
    )
    assert update_options(attack_calls, "b-header"), (
        "preflight(OPTIONS)도 안 왔다 — 이 브라우저가 무엇을 했는지 확인해야 한다:\n"
        + table
    )

    # 숨은 폼 전송은 서버까지 오고, 거기서 403 으로 죽는다.
    form_posts = update_posts(attack_calls, "a-form")
    assert form_posts, "폼 전송이 서버에 도달하지 않았다 (브라우저 동작 확인 필요):\n" + table
    assert all(c["status"] == 403 for c in form_posts), table

    # --- ② 같은 오리진 = 우리 화면 -------------------------------------
    guarded.calls.clear()
    dom2, console2 = open_headless(
        f"{guarded.url}__test__/samesite/x", tmp_path / "profile-ui"
    )
    ui_calls = list(guarded.calls)
    ui_table = "\n".join(
        f"  {c['method']:>7} {c['path']}?{c['query']} → {c['status']}"
        for c in ui_calls if c["path"] == "/api/update/run"
    )
    print("같은 오리진 페이지가 만든 요청:\n" + (ui_table or "  (없음)"))
    assert not uncaught_lines(console2), console2[-2000:]

    ok = update_posts(ui_calls, "e-ui")
    assert ok, "우리 화면 모양의 요청이 서버에 도달하지 않았다:\n" + ui_table
    assert all(c["status"] == 202 for c in ok), (
        "⚠️ 방어가 정상 사용을 막았다 — 그것도 실패다:\n" + ui_table
    )

    # 같은 출처인데 헤더만 빼면 막힌다 = 그 헤더가 실제로 문이다.
    bare = update_posts(ui_calls, "f-noheader")
    assert bare and all(c["status"] == 403 for c in bare), ui_table

    assert guarded.launcher.launches == 1, (
        f"갱신이 {guarded.launcher.launches} 번 시작됐다 (정상 경로 하나만이어야 한다)"
    )


# ------------------------------------------------------------- 읽음 표시

#: 배치 × 팔레트 조합. 색과 배치는 직교하므로 둘을 섞어 본다.
READS_CASES = [
    ("bubbles", "default"), ("bubbles", "tty"),
    ("log", "default"), ("log", "log"), ("log", "tui"),
    ("ide", "default"), ("ide", "ide"), ("ide", "tty"),
]


def _reads_probe(served, tmp_path, layout: str, theme: str, width: int) -> dict:
    import html as html_mod
    import json
    import re as re_mod

    dom, console = open_headless(
        f"{served.url}__test__/probereads/{layout}/{theme}/{width}",
        tmp_path / f"profile-{layout}-{theme}-{width}",
        window="1200,900",
    )
    assert not uncaught_lines(console), console[-1500:]
    found = re_mod.search(r'<pre id="out">(.*?)</pre>', dom, re_mod.S)
    assert found, f"[{layout}/{theme}/{width}] 측정값을 회수하지 못했다:\n{dom[-1500:]}"
    return json.loads(html_mod.unescape(found.group(1)))


@needs_browser
@pytest.mark.parametrize("layout,theme", READS_CASES)
def test_읽음_카운트가_세_배치_모든_팔레트에서_보인다(layout, theme, served, tmp_path):
    """⭐ "붙였다"가 아니라 **브라우저가 계산한 값**으로 확인한다.

    카운트는 팔레트 토큰(`--accent` · `--mine-ink`)으로 칠하므로, 어느 팔레트에서
    바닥과 같은 색이 되면 숫자가 **보이지 않는다.** 그건 CSS 를 읽어서는 못 잡는다.
    """
    got = _reads_probe(served, tmp_path, layout, theme, 900)
    print(f"  [{layout}/{theme}] {json.dumps(got, ensure_ascii=False)}")

    assert got["reads_visible"], "카운트 뱃지가 그려지지 않았다"
    assert got["reads_display"] != "none"
    # ⭐ **자리를 차지하지 않는다** — 절대 위치라 카운트가 바뀌어도 높이가 그대로다.
    assert got["reads_position"] == "absolute", got["reads_position"]
    assert got["reads_in_flow"] is False, "카운트가 흐름을 차지한다 (줄이 밀린다)"
    # 색이 바닥과 다르다 (보인다).
    assert got["reads_color"] != got["row_bg"], (got["reads_color"], got["row_bg"])
    assert got["mine_reads_color"] != got["row_bg"]
    # 줄 안에 있고, 본문과 겹치지 않는다 (오른쪽 여백을 미리 비워 뒀다).
    assert got["reads_inside_row"], "카운트가 줄 밖으로 나갔다"
    assert got["body_right"] <= got["reads_left"] + 1, (
        f"본문이 카운트 자리까지 뻗었다 (본문 right={got['body_right']} · "
        f"카운트 left={got['reads_left']})"
    )
    # 0 이면 아예 그리지 않는다.
    assert got["reads_hidden_when_zero"], "카운트 0 인데 자리가 남아 있다"
    # 구분선도 보인다.
    assert got["mark_height"] > 0, "'여기부터 새 메시지' 구분선이 보이지 않는다"
    assert got["mark_color"] != got["row_bg"]
    # 방 목록 뱃지 — 선택된 방에서도 대비가 남는다 (바닥이 강조색으로 바뀐다).
    assert got["badge_visible"], "방 목록 뱃지가 그려지지 않았다"
    assert got["badge_bg"] != got["badge_color"]
    assert got["active_badge_bg"] != got["active_badge_color"]
    assert got["badge_inside"], "뱃지가 방 버튼 밖으로 나갔다"


@needs_browser
@pytest.mark.parametrize("layout", ["bubbles", "log", "ide"])
def test_320px_에서도_읽음_표시가_가로_스크롤을_만들지_않는다(layout, served, tmp_path):
    """320px 규율 — 좁은 폭은 미디어 쿼리라 **실제 뷰포트**에서만 재진다.

    iframe 을 쓰는 이유: 이 환경의 헤드리스 Edge 는 `--window-size` 를 줘도
    뷰포트가 492px 아래로 내려가지 않는다 (`log`·`ide` 측정과 같은 함정).
    """
    got = _reads_probe(served, tmp_path, layout, "default", 320)
    print(f"  [{layout}/320px] {json.dumps(got, ensure_ascii=False)}")
    assert got["viewport"] == 320, got["viewport"]
    assert got["overflow"] is False, "320px 에서 가로 스크롤이 생겼다"
    assert got["reads_visible"], "320px 에서 카운트가 사라졌다"
    assert got["reads_inside_row"], "320px 에서 카운트가 줄 밖으로 나갔다"
    assert got["body_right"] <= got["reads_left"] + 1, "320px 에서 본문과 겹친다"
    assert got["badge_visible"], "320px 에서 방 목록 뱃지가 사라졌다"
    assert got["mark_height"] > 0, "320px 에서 구분선이 사라졌다"

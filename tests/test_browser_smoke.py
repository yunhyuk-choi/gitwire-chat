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
    try:
        with RecordingServer(app) as server:
            yield server
    finally:
        app.extensions["gitwire_chat"].stop()


def open_headless(url: str, profile: Path) -> tuple[str, str]:
    """헤드리스로 페이지를 열고 (DOM, 콘솔) 을 돌려준다."""
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
        url,
    ]
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
    assert "/static/app.js" in dom


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
def test_정상_설치에서는_가상_스크롤이_격하되지_않는다(served, tmp_path):
    """격하는 안전망이지 평상시 상태가 아니다.

    벤더 파일이 브라우저에서 못 돌면 앱은 (죽는 대신) 격하되어 계속 도는데,
    그러면 이 사실이 상태줄과 `.messages` 의 `plain` 클래스로 드러난다.
    정상 설치에서 그것이 보이면 벤더 번들이 또 깨진 것이다.
    """
    dom, _ = open_headless(served.url, tmp_path / "profile")
    # ⚠️ "가상 스크롤" 이라는 말 자체는 index.html 주석에도 있다. 격하의 지문은
    #    상태줄에 남는 이 문구와 `.messages` 에 붙는 `plain` 클래스다.
    assert "가상 스크롤 없이(전부 그리기) 계속한다" not in dom, (
        "가상 스크롤 격하 안내가 떴다 — 벤더 번들이 브라우저에서 못 돌고 있다"
    )
    assert "plain" not in _messages_class(dom), "타임라인이 격하 배치로 그려졌다"


def _messages_class(dom: str) -> str:
    """`<div ... id="messages" ...>` 의 class 속성만 뽑아 본다."""
    for tag in re.findall(r"<div[^>]*id=\"messages\"[^>]*>", dom):
        found = re.search(r'class="([^"]*)"', tag)
        return found.group(1) if found else ""
    return ""


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

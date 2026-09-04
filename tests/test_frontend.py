"""프런트엔드 검증 — 빠르고 세밀한 층: `node --check` + stub DOM.

⭐ "전체 리렌더가 없다"의 증명은 `tests/js/render.test.mjs` 가 한다. 여기서는
그것을 pytest 안으로 끌어와 같은 한 번의 `pytest -q` 로 돌게 만든다.

⚠️ **이 층만으로는 부족하다.** stub DOM 은 Node 안에서 돌고(브라우저에만 없는
전역을 못 잡는다), `window.TanStackVirtual` 을 직접 세팅한 뒤 `boot()` 을 부르므로
**스크립트 로딩 순서**라는 실제 실패 지점을 건너뛴다. 실제로 이 스위트가 22/22 로
통과하는 동안 앱은 브라우저에서 완전히 죽어 있었다. 그 위에 얹은 연기 감지기가
`tests/test_browser_smoke.py`(진짜 브라우저)이고, 벤더 파일의 Node 전용 전역을
원문에서 훑는 것이 `tests/test_vendor_assets.py` 다. 셋은 서로를 대체하지 않는다.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "src" / "gitwire_chat" / "static" / "app.js"
RENDER_TEST = ROOT / "tests" / "js" / "render.test.mjs"

node = shutil.which("node")
needs_node = pytest.mark.skipif(node is None, reason="node 가 없다")


@needs_node
def test_app_js_문법이_유효하다():
    result = subprocess.run(
        [node, "--check", str(APP_JS)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stderr


@needs_node
def test_stub_DOM_으로_구동해_리렌더가_없음을_확인한다(capsys):
    result = subprocess.run(
        [node, str(RENDER_TEST)],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    print(result.stdout)
    print(result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL" not in result.stdout


def test_HTML_문자열_주입_경로가_소스에_없다():
    """innerHTML·insertAdjacentHTML·document.write 가 아예 없어야 한다.

    노드가 아니라 HTML 문자열로 화면을 갱신하는 순간 그 영역은 통째로
    다시 그려진다 — 우리가 피하려는 바로 그것이다.
    """
    text = APP_JS.read_text(encoding="utf-8")
    for banned in ("insertAdjacentHTML", "document.write", "outerHTML ="):
        assert banned not in text, f"{banned} 사용 금지"
    # 문자열 'innerHTML' 은 카운터 이름·주석으로만 등장할 수 있다 (대입은 금지).
    assert ".innerHTML =" not in text
    assert ".innerHTML=" not in text


def test_정적_파일이_UTF8_이고_BOM_이_없다():
    for path in [
        APP_JS,
        ROOT / "src" / "gitwire_chat" / "static" / "style.css",
        ROOT / "src" / "gitwire_chat" / "templates" / "index.html",
    ]:
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{path.name} 에 BOM 이 있다"
        assert b"\r\n" not in raw, f"{path.name} 에 CRLF 가 있다"
        raw.decode("utf-8")


def test_반응형_규칙이_들어_있다():
    css = (ROOT / "src" / "gitwire_chat" / "static" / "style.css").read_text(
        encoding="utf-8"
    )
    assert "@media (max-width: 719px)" in css   # 좁은 창: 한 화면씩
    assert "@media (min-width: 720px)" in css   # 넓은 창: 2단
    assert "overflow-wrap" in css               # 긴 URL 이 레이아웃을 깨지 않게
    assert "viewport" not in css


def test_HTML_에_뷰포트_메타가_있다():
    html = (ROOT / "src" / "gitwire_chat" / "templates" / "index.html").read_text(
        encoding="utf-8"
    )
    assert 'name="viewport"' in html
    assert 'lang="ko"' in html

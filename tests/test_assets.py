"""정적 자원 캐시 무효화 — 도장이 **정말 내용에 반응하나**.

이 파일이 겨냥하는 사고는 하나다: **"업데이트했는데 화면은 그대로."**
갱신은 파이썬 패키지만 갈아치우므로, URL 이 그대로면 브라우저는 옛 JS·CSS 를
계속 쓴다. 그래서 여기서 보는 것은 "도장이 붙었나"가 아니라

* 파일을 **바꾸면** URL 이 바뀌나,
* 안 바꾸면 **그대로인가** (재설치로 mtime 만 달라진 경우 포함),
* **같은 버전 문자열**로 재배포해도 바뀌나 ← 이게 지금의 실패 조건이다,
* 모듈 그래프·벤더까지 **한꺼번에** 갈리나,
* 셸 HTML 이 캐시되어 그 전부를 무력화하지 않나

이다. 실제 브라우저가 새 파일을 정말 받아 가는지는
`tests/test_browser_smoke.py` 의 「같은 버전 재배포」 테스트가 따로 본다 —
둘이 합쳐져야 증명이 닫힌다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from gitwire_chat import assets
from gitwire_chat.app import create_app, installed_version
from gitwire_chat.config import Settings

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "gitwire_chat"
TEMPLATE = PKG / "templates" / "index.html"

#: 도장 없는 정적 URL 을 만드는 템플릿 호출. 이 문자열이 템플릿에 있으면
#: 그 자원만 옛 캐시에서 나온다.
UNSTAMPED_CALL = "url_for('static'"


@pytest.fixture
def tree(tmp_path) -> Path:
    """작은 정적 트리 — 실물과 같은 모양(진입점 · 모듈 · 벤더 · CSS)."""
    root = tmp_path / "static"
    (root / "js").mkdir(parents=True)
    (root / "vendor" / "engine").mkdir(parents=True)
    (root / "app.js").write_text("import './js/boot.js';\n", encoding="utf-8")
    (root / "js" / "boot.js").write_text(
        "export function boot() {}\n", encoding="utf-8"
    )
    (root / "vendor" / "engine" / "index.js").write_text(
        "export var v = 1;\n", encoding="utf-8"
    )
    (root / "style.css").write_text("body { color: red }\n", encoding="utf-8")
    return root


@pytest.fixture
def client(tmp_path):
    app = create_app(
        Settings(home=tmp_path / "chats", notifications=False), start=False
    )
    return app, app.test_client()


# ------------------------------------------------------------------ 도장 자체


def test_도장은_트리_내용에서_나온다(tree):
    stamp = assets.compute_stamp(tree)
    assert re.fullmatch(r"[0-9a-f]{12}", stamp), stamp


def test_같은_내용이면_같은_도장이다(tree):
    assert assets.compute_stamp(tree) == assets.compute_stamp(tree)


def test_파일을_바꾸면_도장이_바뀐다(tree):
    before = assets.compute_stamp(tree)
    (tree / "js" / "boot.js").write_text(
        "export function boot() { return 1; }\n", encoding="utf-8"
    )
    assert assets.compute_stamp(tree) != before


def test_벤더_파일을_바꿔도_도장이_바뀐다(tree):
    """벤더는 우리가 안 고치는 파일이라 빠뜨리기 쉽다.

    하나라도 옛 파일이 새 파일들 사이에 섞이면 "그대로" 보다 더 이상한 증상이 난다.
    """
    before = assets.compute_stamp(tree)
    (tree / "vendor" / "engine" / "index.js").write_text(
        "export var v = 2;\n", encoding="utf-8"
    )
    assert assets.compute_stamp(tree) != before


def test_CSS_를_바꿔도_도장이_바뀐다(tree):
    before = assets.compute_stamp(tree)
    (tree / "style.css").write_text("body { color: blue }\n", encoding="utf-8")
    assert assets.compute_stamp(tree) != before


def test_파일이_늘거나_이름이_바뀌면_도장이_바뀐다(tree):
    before = assets.compute_stamp(tree)
    (tree / "js" / "extra.js").write_text("export var x = 1;\n", encoding="utf-8")
    added = assets.compute_stamp(tree)
    assert added != before
    (tree / "js" / "extra.js").rename(tree / "js" / "renamed.js")
    assert assets.compute_stamp(tree) != added


def test_내용을_안_바꾸면_mtime_이_달라도_도장이_그대로다(tree):
    """⚠️ 이 성질이 mtime·크기 기반 도장을 기각한 이유다.

    `pip install --force-reinstall` 은 내용이 같은 파일도 새로 쓴다 — mtime 이
    전부 달라진다. 그걸 도장으로 쓰면 아무것도 안 바뀌었는데 URL 이 바뀐다.
    """
    before = assets.compute_stamp(tree)
    for path in assets.iter_asset_files(tree):
        path.write_bytes(path.read_bytes())         # 같은 내용 · 새 mtime
    for path in assets.iter_asset_files(tree):
        os.utime(path, (1, 1))
    assert assets.compute_stamp(tree) == before


def test_문서와_파이썬_캐시는_도장에_안_들어간다(tree):
    before = assets.compute_stamp(tree)
    (tree / "vendor" / "engine" / "VENDORING.md").write_text("설명\n", encoding="utf-8")
    (tree / "js" / "__pycache__").mkdir()
    (tree / "js" / "__pycache__" / "x.pyc").write_bytes(b"\x00")
    assert assets.compute_stamp(tree) == before


# ------------------------------------------------- 값싼 재계산 (AssetStamper)


def test_스탬퍼는_지문이_그대로면_다시_읽지_않는다(tree, monkeypatch):
    stamper = assets.AssetStamper(tree)
    first = stamper.stamp

    calls = []

    def spy(root):
        calls.append(root)
        return first

    monkeypatch.setattr(assets, "compute_stamp", spy)
    assert stamper.stamp == first
    assert calls == [], "지문이 그대로인데 트리를 다시 읽었다"


def test_스탬퍼는_파일이_바뀌면_서버를_안_내려도_새_도장을_준다(tree):
    """소스 체크아웃으로 개발할 때의 성질. 재기동 없이 다음 새로고침에 반영된다."""
    stamper = assets.AssetStamper(tree)
    before = stamper.stamp
    (tree / "app.js").write_text(
        "import './js/boot.js';\n// 고쳤다\n", encoding="utf-8"
    )
    assert stamper.stamp != before


def test_스탬퍼_URL_은_도장을_경로에_박는다(tree):
    stamper = assets.AssetStamper(tree)
    assert stamper.url("app.js") == f"/assets/{stamper.stamp}/app.js"


# --------------------------------------------------- ⭐ 같은 버전 재배포 시나리오


def test_같은_버전_문자열로_재배포해도_도장은_바뀐다(tree):
    """⭐ 이 프로젝트의 **실제** 배포 방식이 정확히 이것이다.

    `pyproject.toml` 의 버전은 `0.2.0` 에 머문 채 git 커밋만 나간다. 그래서
    `importlib.metadata.version()` 을 캐시 키로 쓰면 열 번을 배포해도 값이
    안 바뀌고, 브라우저는 계속 옛 파일을 쓴다. 내용 해시는 그 상황에서도 바뀐다.
    """
    version_before = installed_version()
    stamp_before = assets.compute_stamp(tree)
    # "배포" — 버전 문자열은 그대로, 파일 내용만 바뀐다.
    (tree / "js" / "boot.js").write_text(
        "export function boot() { /* v2 */ }\n", encoding="utf-8"
    )
    assert installed_version() == version_before, "버전 문자열은 그대로여야 한다"
    assert assets.compute_stamp(tree) != stamp_before


# ------------------------------------------------------------------ 라우팅·헤더


def test_셸은_캐시하지_않는다(client):
    """셸이 캐시되면 그 안의 새 자원 URL 이 브라우저에 도달하지 못한다."""
    _, c = client
    assert "no-store" in c.get("/").headers.get("Cache-Control", "")


def test_셸이_뱉는_자원_URL_에_모두_도장이_박혀_있다(client):
    app, c = client
    stamp = app.extensions["gitwire_chat_assets"].stamp
    body = c.get("/").get_data(as_text=True)
    urls = [
        u
        for u in re.findall(r'(?:href|src)="([^"]+)"', body)
        if not u.startswith(("http", "#", "mailto"))
    ]
    assert urls, "셸이 자원을 하나도 안 부른다 — 셀렉터가 낡았다"
    for url in urls:
        assert url.startswith(f"/assets/{stamp}/"), url


def test_템플릿이_도장_없는_static_URL_을_쓰지_않는다():
    """⚠️ `url_for` 정적 호출 한 줄이 캐시 무효화를 조용히 뚫는다.

    그 URL 은 파일이 바뀌어도 그대로여서, 그 자원만 옛 캐시에서 나온다.
    """
    # 주석은 먼저 걷어낸다 — 도크가 "이걸 쓰지 마라"를 설명하려고 그 호출
    # 문자열을 그대로 적어 두기 때문이다. 검사 대상은 **실제 마크업**이다.
    markup = re.sub(r"<!--.*?-->", "", TEMPLATE.read_text(encoding="utf-8"), flags=re.S)
    hits = [line.strip() for line in markup.splitlines() if UNSTAMPED_CALL in line]
    assert not hits, "도장 없는 정적 URL:\n  " + "\n  ".join(hits)


def test_도장이_맞으면_영구_캐시로_준다(client):
    app, c = client
    stamp = app.extensions["gitwire_chat_assets"].stamp
    response = c.get(f"/assets/{stamp}/app.js")
    assert response.status_code == 200
    cache = response.headers["Cache-Control"]
    assert "immutable" in cache and "max-age=31536000" in cache


def test_모듈_그래프와_벤더도_같은_접두사에서_나온다(client):
    """상대 import 가 같은 접두사 안에서 해석된다 — 그래서 그래프 전체가 갈린다."""
    app, c = client
    stamp = app.extensions["gitwire_chat_assets"].stamp
    for path in ("js/boot.js", "js/theme.js", "vendor/tanstack-virtual-core/index.js"):
        response = c.get(f"/assets/{stamp}/{path}")
        assert response.status_code == 200, path
        assert "immutable" in response.headers["Cache-Control"], path


def test_진입점의_import_가_전부_상대경로다():
    """절대 경로(`/static/...`) import 가 하나라도 있으면 도장이 전파되지 않는다."""
    pattern = re.compile(r"""^\s*import[^'"]*['"]([^'"]+)['"]""", re.M)
    offenders = []
    files = [PKG / "static" / "app.js", *sorted((PKG / "static" / "js").glob("*.js"))]
    for path in files:
        for spec in pattern.findall(path.read_text(encoding="utf-8")):
            if not spec.startswith(("./", "../")):
                offenders.append(f"{path.name}: {spec}")
    assert not offenders, "도장이 전파되지 않는 import:\n  " + "\n  ".join(offenders)


def test_틀린_도장은_영구_캐시로_주지_않고_경고를_남긴다(client, caplog):
    """옛 도장 URL 에 지금 내용을 immutable 로 붙이면 그게 영구히 굳는다.

    그리고 조용히 넘기지 않는다 — 서버 로그에 남는다.
    """
    _, c = client
    with caplog.at_level("WARNING"):
        response = c.get("/assets/000000000000/app.js")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Asset-Stamp-Mismatch"] == "1"
    assert "옛 자원 도장" in caplog.text


def test_경로_탈출은_막힌다(client):
    app, c = client
    stamp = app.extensions["gitwire_chat_assets"].stamp
    assert c.get(f"/assets/{stamp}/../config.py").status_code == 404


def test_버전_엔드포인트가_도장을_알려준다(client):
    app, c = client
    data = c.get("/api/version").get_json()
    assert data["asset_stamp"] == app.extensions["gitwire_chat_assets"].stamp
    assert data["name"] == "gitwire-chat"
    assert isinstance(data["pid"], int) and data["pid"] > 0

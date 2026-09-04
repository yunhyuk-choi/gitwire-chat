"""브라우저로 가는 JS 에 **Node 전용 전역이 없다**를 기계적으로 못 박는다.

왜 이 테스트가 있나 — 실제로 당한 사고이기 때문이다. 벤더링한
`@tanstack/virtual-core` 배포본이 `NODE_ENV` 환경변수를 참조했다. 상류는 번들러가
빌드 시점에 리터럴로 치환해 준다는 전제로 그렇게 낸다. 이 앱은 빌드 단계가 없어
파일이 브라우저에 그대로 가는데, 브라우저에는 그 전역이 없다. 결과는 런타임
`ReferenceError` 였고 — 그것도 모듈 평가가 아니라 생성자 안에서 터져서 —
앱 전체가 조용히 죽었다. 테스트는 22/22 로 전부 통과하고 있었다.

`node --check` 도 stub DOM 테스트도 이것을 못 잡는다. **둘 다 Node 안에서 돌기
때문이다** — 거기엔 그 전역이 실제로 있다. 그래서 실행이 아니라 **원문을 훑는다.**

⚠️ 이 검사는 주석을 벗겨내지 않는다 (벗기면 문자열·정규식 리터럴에서 오탐·누락이
생겨 검사에 구멍이 난다). 대신 **벤더 디렉토리의 주석에도 금지 토큰을 문자 그대로
적지 않는다**는 규칙을 둔다 — 경위 설명은 `VENDORING.md` 가 맡는다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "gitwire_chat" / "static"
VENDOR = STATIC / "vendor" / "tanstack-virtual-core"

#: 브라우저가 받는 스크립트 전부 (우리 것 + 벤더링한 것).
BROWSER_JS = sorted(STATIC.rglob("*.js"))

#: 브라우저에 없는 Node 전용 전역·모듈 시스템.
#:
#: `global` 은 일부러 뺐다 — `app.js` 가 IIFE 파라미터 이름으로 쓰고 있어서
#: (`}(typeof globalThis !== 'undefined' ? globalThis : this))`) 그건 지역
#: 이름이지 Node 전역이 아니다. 진짜 Node 전역을 쓰면 아래 것들에 먼저 걸린다.
NODE_ONLY = [
    (r"\bprocess\b", "process (환경변수·프로세스 정보 — 브라우저에 없다)"),
    (r"\brequire\s*\(", "require() (CommonJS — 브라우저는 import 를 쓴다)"),
    (r"\bmodule\.exports\b", "module.exports (CommonJS)"),
    (r"\bexports\s*\.", "exports.* (CommonJS)"),
    (r"\b__dirname\b", "__dirname"),
    (r"\b__filename\b", "__filename"),
    (r"\bBuffer\b", "Buffer"),
]


def test_브라우저용_JS_파일이_실제로_있다():
    """빈 목록을 훑고 '통과'라고 말하는 일이 없게 한다."""
    names = {p.name for p in BROWSER_JS}
    assert "app.js" in names
    assert {"index.js", "utils.js", "lazy-measurements.js"} <= names


@pytest.mark.parametrize("path", BROWSER_JS, ids=lambda p: p.name)
def test_브라우저용_JS_에_Node_전용_전역이_없다(path: Path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for pattern, label in NODE_ONLY:
        for m in re.finditer(pattern, text):
            line = text.count("\n", 0, m.start()) + 1
            hits.append(f"{path.name}:{line} — {label}")
    assert not hits, (
        "브라우저에 없는 Node 전용 전역이 남아 있다. 이대로 배포하면 화면이 죽는다:\n  "
        + "\n  ".join(hits)
        + f"\n\n벤더 파일이라면 {VENDOR.name}/VENDORING.md 의 갱신 절차를 따라라."
    )


def test_벤더링_출처와_라이선스_표기가_남아_있다():
    """상류 코드를 손봤으면 무엇을·왜 바꿨는지가 레포에 남아 있어야 한다."""
    license_text = (VENDOR / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in license_text

    note = (VENDOR / "VENDORING.md").read_text(encoding="utf-8")
    assert "@tanstack/virtual-core 3.17.8" in note
    assert "MIT" in note

    for name in ("index.js", "utils.js", "lazy-measurements.js"):
        head = (VENDOR / name).read_text(encoding="utf-8")[:400]
        assert "@tanstack/virtual-core 3.17.8" in head, f"{name} 에 출처 표기가 없다"


def test_벤더_파일이_UTF8_이고_BOM_없이_LF_다():
    for path in sorted(VENDOR.iterdir()):
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{path.name} 에 BOM 이 있다"
        assert b"\r\n" not in raw, f"{path.name} 에 CRLF 가 있다"
        raw.decode("utf-8")

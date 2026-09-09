"""색 테마 검증 — **토큰화가 실제로 끝났는지**를 기계로 본다.

이 파일이 있는 이유는 하나다. 색을 팔레트로 갈아끼우는 구조에서 가장 흔한 사고는
"거의 다 됐는데 한 군데가 안 따라간다"이고, 그 한 군데는 눈에 **가장 잘 띄는**
버그가 된다 (어두운 화면에 흰 띠 하나, 안 보이는 placeholder 하나).

그래서 사람 눈이 아니라 다음을 센다:

1. `style.css` 의 팔레트 구역 **밖에는 색 리터럴이 0개**다. JS·템플릿에도 없다.
2. 팔레트마다 **같은 토큰 집합**을 전부 정의한다 (하나 빼먹으면 그 자리만
   기본 팔레트 값이 새어 나온다 — 정확히 위의 그 사고다).
3. 팔레트 블록은 **색만** 정의한다. 폰트·여백이 섞여 들어오면 높이가 바뀌고,
   그 순간 가상 스크롤의 측정값이 낡아 스크롤이 튄다 (이번 범위는 색뿐이다).
4. 각 팔레트의 본문·보조 글자·오류·초점 링 **대비**가 기준을 넘는다.
5. 테마 목록과 저장 계약이 CSS·템플릿·모듈 **세 곳에서 같다**.

⚠️ 전환 동작(노드 재생성 0 · 스크롤 보존 · 영속)은 여기가 아니라
`tests/js/render.test.mjs` 가 stub DOM 에서 수치로 증명하고, 실제 브라우저에서
각 팔레트가 뜨는지는 `tests/test_browser_smoke.py` 가 본다. 셋은 서로를
대체하지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "gitwire_chat"
STATIC = PKG / "static"
CSS = STATIC / "style.css"
INDEX = PKG / "templates" / "index.html"
THEME_JS = STATIC / "js" / "theme.js"
OUR_JS = [STATIC / "app.js"] + sorted((STATIC / "js").glob("*.js"))

#: 색 리터럴로 보는 것. `transparent`·`currentColor`·`inherit` 는 값이 없으므로
#: 팔레트를 배신하지 않는다 — 세지 않는다.
COLOR_LITERAL = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|\brgba?\s*\(|\bhsla?\s*\(|"
    # 이름 있는 색. ⚠️ 앞뒤에 `-`·글자가 붙으면 색이 아니다 — `white-space` 는
    # 색이 아니라 속성 이름이다. 이 구분이 없으면 오탐으로 테스트가 무력해진다.
    r"(?<![-\w])(?:white|black|red|green|blue|yellow|orange|purple|pink|brown|"
    r"gray|grey|silver|navy|teal|olive|maroon|lime|aqua|fuchsia|crimson|tomato|"
    r"gold|beige|ivory|khaki|salmon|coral|orchid|plum|azure)(?![-\w])"
)

#: 색이 아닌 토큰 (팔레트를 갈아도 같아야 하는 것들).
NON_COLOR_TOKENS = {"--radius", "--gap"}


def strip_comments(text: str) -> str:
    """주석을 걷어낸다 — 주석 안의 `#messages` 같은 글자를 색으로 오인하지 않게."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def css_text() -> str:
    return CSS.read_text(encoding="utf-8")


def palette_region() -> tuple[str, str]:
    """(팔레트 구역, 그 밖의 전부) 로 가른다."""
    css = css_text()
    begin, end = "/* PALETTE:BEGIN */", "/* PALETTE:END */"
    assert begin in css and end in css, "팔레트 구역 표식이 없다"
    head, rest = css.split(begin, 1)
    body, tail = rest.split(end, 1)
    return body, head + tail


def palettes() -> dict[str, dict[str, str]]:
    """팔레트 이름 → {토큰: 값}."""
    body, _ = palette_region()
    out: dict[str, dict[str, str]] = {}
    for selector, block in re.findall(r"(:root[^{]*)\{([^}]*)\}", strip_comments(body)):
        selector = selector.strip()
        named = re.search(r'data-chat-theme="([a-z]+)"', selector)
        if named:
            key = named.group(1)
        elif ":not([data-chat-theme])" in selector:
            key = "기본-다크"
        else:
            key = "기본-라이트"
        # ⚠️ 토큰 이름에 **숫자**가 들어간다(`--sender-1`). `[a-z-]+` 로 두면
        # 그 토큰들이 조용히 안 잡혀, 팔레트가 빠뜨려도 아무 테스트가 안 깨진다
        # (실제로 한 번 그랬다).
        decls = dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", block))
        # 토큰(`--*`)이 아닌 일반 선언. 반드시 **글자로 시작**해야 한다 —
        # `[a-z-]+` 로 두면 `--bg` 의 첫 `-` 부터 잡혀 토큰까지 섞여 들어온다.
        extras = re.findall(r"(?<![-\w])([a-z][a-z-]*)\s*:\s*([^;]+);", block)
        out[key] = {"__decls__": decls, "__extras__": extras}  # type: ignore[dict-item]
    return out


# --------------------------------------------------------------- 1. 잔존 색

def test_팔레트_밖에는_색_리터럴이_하나도_없다():
    """토큰화의 **완료 조건**이다. 하나라도 남으면 그 부분만 테마를 안 따라간다."""
    _, outside = palette_region()
    hits = []
    for i, line in enumerate(strip_comments(outside).splitlines(), 1):
        if COLOR_LITERAL.search(line):
            hits.append(f"style.css(팔레트 밖):{i} — {line.strip()}")
    assert not hits, "팔레트 밖에 색이 하드코딩돼 있다:\n  " + "\n  ".join(hits)


def test_스크립트와_템플릿에도_색_리터럴이_없다():
    """색을 JS 로 칠하면 CSS 팔레트가 그 자리를 통제할 수 없다."""
    hits = []
    for path in OUR_JS + [INDEX]:
        text = strip_comments(path.read_text(encoding="utf-8"))
        for i, line in enumerate(text.splitlines(), 1):
            if COLOR_LITERAL.search(line):
                hits.append(f"{path.name}:{i} — {line.strip()}")
    assert not hits, "스크립트·템플릿에 색이 박혀 있다:\n  " + "\n  ".join(hits)


def test_색은_전부_역할_이름의_토큰으로_참조된다():
    """`--green-500` 같은 **색 이름** 금지 — 팔레트를 갈면 그 이름이 거짓말이 된다."""
    body, _ = palette_region()
    banned = re.findall(
        r"--(?:red|green|blue|yellow|orange|purple|gray|grey|white|black)[a-z0-9-]*",
        strip_comments(body),
    )
    assert not banned, f"색 이름 토큰이 있다: {banned}"


# ----------------------------------------------------- 2·3. 팔레트 구조

def test_팔레트마다_같은_토큰_집합을_전부_정의한다():
    """하나 빼먹으면 그 자리만 다른 팔레트 값이 새어 나온다 — 가장 흔한 사고다."""
    table = palettes()
    base = set(table["기본-라이트"]["__decls__"]) - NON_COLOR_TOKENS
    assert len(base) >= 15, f"기본 팔레트의 토큰이 너무 적다: {sorted(base)}"
    problems = []
    for name, data in table.items():
        if name == "기본-라이트":
            continue
        got = set(data["__decls__"])
        missing = base - got
        extra = got - base - NON_COLOR_TOKENS
        if missing:
            problems.append(f"{name}: 빠진 토큰 {sorted(missing)}")
        if extra:
            problems.append(f"{name}: 정의되지 않은 토큰 {sorted(extra)}")
    assert not problems, "\n  ".join(problems)


def test_팔레트_블록은_색만_정의한다():
    """폰트·여백이 팔레트에 섞이면 **높이가 바뀐다.**

    높이가 바뀌면 가상 스크롤의 측정값이 낡아 테마를 고르는 순간 읽던 자리가
    튄다. 이번 범위는 색뿐이고, 그 경계를 여기서 못 박는다.
    """
    allowed = {"color-scheme"}
    problems = []
    for name, data in palettes().items():
        for prop, value in data["__extras__"]:
            if prop in allowed:
                continue
            problems.append(f"{name}: {prop}: {value}")
    assert not problems, (
        "팔레트 블록에 색이 아닌 선언이 있다 (높이가 바뀌면 스크롤이 튄다):\n  "
        + "\n  ".join(problems)
    )


def test_초점_링과_placeholder_가_토큰을_쓴다():
    """어두운 팔레트에서 **사라지기 쉬운 두 가지**다.

    · 초점 링: 브라우저 기본 링은 배경에 따라 안 보일 수 있다. 키보드로만 쓰는
      사람에게는 이것이 커서다 — 팔레트마다 강조색으로 그린다.
    · placeholder: UA 가 자기 색을 쓴다. 우리 팔레트를 따라오지 않는다.
    """
    css = strip_comments(css_text())
    focus = re.search(r":focus-visible\s*\{([^}]*)\}", css)
    assert focus, ":focus-visible 규칙이 없다 (초점이 안 보이는 팔레트가 생긴다)"
    assert "var(--focus)" in focus.group(1), f"초점 링이 토큰을 안 쓴다: {focus.group(1)}"

    ph = re.search(r"::placeholder\s*\{([^}]*)\}", css)
    assert ph, "::placeholder 규칙이 없다 (UA 기본색이 팔레트를 배신한다)"
    assert "var(--muted)" in ph.group(1), f"placeholder 가 토큰을 안 쓴다: {ph.group(1)}"


def test_터미널_팔레트는_다크로_선언된다():
    """`color-scheme: dark` 가 없으면 스크롤바·select 목록이 흰색으로 뜬다.

    (우리 CSS 가 칠하지 못하는 브라우저 UI 부분이다 — 토큰으로는 못 고친다.)
    """
    table = palettes()
    for name in ("log", "ide", "tty", "tui"):
        extras = dict(table[name]["__extras__"])
        assert extras.get("color-scheme", "").strip() == "dark", f"{name}: color-scheme 없음"
    base = dict(table["기본-라이트"]["__extras__"])
    assert base.get("color-scheme", "").strip() == "light dark", "기본은 시스템을 따라야 한다"


# ------------------------------------------------------------- 4. 대비

def _rgb(value: str) -> tuple[float, float, float, float]:
    value = value.strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", value)
    if m:
        h = m.group(1)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
    m = re.fullmatch(r"rgba?\(([^)]+)\)", value)
    if m:
        parts = [p.strip() for p in m.group(1).replace("/", ",").split(",")]
        nums = [float(p) for p in parts]
        alpha = nums[3] if len(nums) > 3 else 1.0
        return (nums[0], nums[1], nums[2], alpha)
    raise AssertionError(f"해석할 수 없는 색: {value!r}")


def _over(fg: str, bg: str) -> tuple[float, float, float]:
    """반투명 전경을 배경 위에 합성한다 (rgba 토큰의 실제 표시색)."""
    r, g, b, a = _rgb(fg)
    br, bg_, bb, _ = _rgb(bg)
    return (a * r + (1 - a) * br, a * g + (1 - a) * bg_, a * b + (1 - a) * bb)


def _lum(rgb: tuple[float, float, float]) -> float:
    def chan(v: float) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (chan(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: str, bg: str) -> float:
    a, b = _lum(_over(fg, bg)), _lum(_over(bg, bg))
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


#: (글자 토큰, 바닥 토큰, 최소 대비, 무엇인가)
#:
#: 기준: 본문은 AAA(7.0), 11~13px 보조·오류 글자는 AA(4.5). 내 말풍선 바닥
#: (`--mine`)에 앉는 보조 글자만 4.0 이다 — 터미널 팔레트는 전부 4.5 를 넘지만
#: `기본`의 연두색 말풍선이 예전부터 4.03 이고, `기본`은 "지금 모습 그대로"가
#: 요구사항이라 그 값을 **고정**한다(회귀 방지). 초점 링은 글자가 아니라
#: UI 요소라 3.0 이다 (WCAG 1.4.11).
TEXT_PAIRS = [
    ("--ink", "--bg", 7.0, "본문"),
    ("--ink", "--panel", 7.0, "본문(패널)"),
    ("--ink", "--mine", 7.0, "본문(내 말풍선)"),
    ("--muted", "--bg", 4.5, "보조 글자"),
    ("--muted", "--panel", 4.5, "보조 글자(패널)"),
    ("--muted", "--mine", 4.0, "보조 글자(내 말풍선)"),
    ("--danger", "--bg", 4.5, "오류"),
    ("--danger", "--panel", 4.5, "오류(패널)"),
    ("--danger", "--mine", 4.0, "오류(내 말풍선)"),
    ("--theirs-ink", "--panel", 4.5, "남의 이름"),
    ("--mine-ink", "--mine", 4.5, "내 이름"),
    ("--warn-ink", "--warn-bg", 4.5, "못 나간 말 띠"),
    ("--focus", "--bg", 3.0, "초점 링"),
    ("--focus", "--panel", 3.0, "초점 링(패널)"),
]

#: 줄 기반 배치(`log`)가 더한 바닥과 글자색. 줄은 `--panel` 이 아니라
#: `--bg` · `--stripe`(줄무늬) · `--mine` 위에 앉는다 — 그 세 바닥 전부를 본다.
#: 발신자 색 6종은 **어느 바닥에서도** 읽혀야 한다 (색상만 돌리고 명도는 맞췄다).
TEXT_PAIRS += [
    ("--ink", "--stripe", 7.0, "본문(줄무늬)"),
    ("--muted", "--stripe", 4.5, "보조 글자(줄무늬)"),
    ("--danger", "--stripe", 4.5, "오류(줄무늬)"),
]
for _slot in range(1, 7):
    for _surface in ("--bg", "--panel", "--stripe", "--mine"):
        TEXT_PAIRS.append((f"--sender-{_slot}", _surface, 4.5, f"발신자 {_slot}"))

#: 떠 있는 발신자 머리(`ide` 의 묶기)는 `--bg` 바닥 위에 앉는다 — 이름이 그 위에서
#: 읽혀야 "누가 말했는지 화면에 있다"가 성립한다. 발신자 색 6종은 위에서 이미
#: `--bg` 를 보고, 남은 두 이름색을 여기서 본다.
TEXT_PAIRS += [
    ("--theirs-ink", "--bg", 4.5, "남의 이름(떠 있는 머리)"),
    ("--mine-ink", "--bg", 4.5, "내 이름(떠 있는 머리)"),
]

#: 강조 블록(선택된 방·주 버튼) 위의 글자.
#: 터미널 팔레트는 AA(4.5)를 지킨다. `기본` 은 예전부터 흰 글자를 파란 블록에
#: 얹어 라이트 3.5~4.6 / 다크 2.6~3.2 인데, `기본` 은 지금 모습을 유지해야 하므로
#: **개선하지 않고 현재 값을 고정**한다 (미해결로 보고했다 — 고치려면 강조색
#: 자체를 어둡게 해야 하고 그건 `기본`의 모습을 바꾸는 일이다).
ACCENT_PAIRS = [("--accent-ink", "--accent"), ("--accent-muted", "--accent")]
ACCENT_MIN = {"기본-라이트": 3.4, "기본-다크": 2.5}


@pytest.mark.parametrize("name", sorted(palettes()))
def test_팔레트의_대비가_기준을_넘는다(name: str, capsys):
    tokens = palettes()[name]["__decls__"]
    lines = []
    bad = []
    for fg, bg, floor, what in TEXT_PAIRS:
        got = contrast(tokens[fg], tokens[bg])
        lines.append(f"    {what:20s} {fg} on {bg}: {got:5.2f} (≥{floor})")
        if got < floor:
            bad.append(f"{name} {what}: {fg}({tokens[fg]}) on {bg}({tokens[bg]}) = {got:.2f} < {floor}")
    floor = ACCENT_MIN.get(name, 4.5)
    for fg, bg in ACCENT_PAIRS:
        got = contrast(tokens[fg], tokens[bg])
        lines.append(f"    {'강조 위 글자':20s} {fg} on {bg}: {got:5.2f} (≥{floor})")
        if got < floor:
            bad.append(f"{name} 강조: {fg} on {bg} = {got:.2f} < {floor}")
    print(f"  [{name}]")
    print("\n".join(lines))
    assert not bad, "대비 미달:\n  " + "\n  ".join(bad)


def test_배치_규칙은_대화_영역에만_걸린다():
    """⭐ 망가진 배치가 **되돌릴 UI 까지 먹으면** 안 된다.

    배치 규칙이 사이드바·고르는 칸·상태줄에 손을 대면, 배치가 깨질 때 그것을
    되돌릴 방법이 함께 사라진다 (저장값이 남아 새로고침해도 같은 상태로 뜬다).
    그래서 `[data-chat-layout=…]` 규칙은 **대화 영역 안에서만** 산다.

    ⚠️ **입력창(`.composer`)은 이 목록에서 빠졌다** (`tty` 배치가 그것을 프롬프트
    모양으로 바꾼다 — `윤혁 ▸`). 이 테스트가 지키려는 것은 "대화 영역 밖을 건드리지
    말라"가 아니라 **되돌릴 길이 살아 있으라**이고, 되돌리는 길은 사이드바의 고르는
    칸(+ 초기화 실패 시 자동 폴백)이다. 입력창은 그 길 위에 없다 — 입력창이
    이상해져도 배치는 되돌릴 수 있다. 반대로 상태줄(`.status`)은 **실패를 말하는
    자리**라 목록에 남는다.
    """
    css = strip_comments(css_text())
    off_limits = (
        ".sidebar", ".theme-bar", "#theme-select", "#layout-select", ".rooms",
        ".room-btn", ".room-name", ".status", ".brand", ".icon-btn",
        ".add-room", ".search-bar",
    )
    # ⚠️ 떠 있는 머리(`.sticky-head`)는 이 목록에 없다 — 그 규칙에는
    # `data-chat-layout` 이 붙지 않기 때문이다(배치와 무관하게 같은 자리이고,
    # 띄울지 말지는 타임라인이 `hidden` 으로 정한다).
    allowed = (
        ".msg", ".timeline", ".messages", ".older-sentinel", ".jump", ".composer",
    )
    problems = []
    for line in css.splitlines():
        if "data-chat-layout" not in line:
            continue
        selector = line.split("{")[0]
        for token in off_limits:
            if token in selector:
                problems.append(f"{token} 를 건드린다: {selector.strip()}")
        if not any(token in selector for token in allowed):
            problems.append(f"대화 영역 밖이다: {selector.strip()}")
    assert not problems, "배치 규칙이 경계를 넘었다:\n  " + "\n  ".join(problems)


# ------------------------------------------------- 5. 세 곳의 계약 일치

def theme_ids_from_js() -> list[str]:
    text = THEME_JS.read_text(encoding="utf-8")
    block = text.split("export var THEMES", 1)[1].split("];", 1)[0]
    return re.findall(r"id:\s*'([a-z]+)'", block)


def test_테마_목록이_CSS_템플릿_모듈_세_곳에서_같다():
    """어긋나면 **고를 수는 있는데 색이 안 바뀌는** 항목이 생긴다."""
    js_ids = theme_ids_from_js()
    # ⚠️ 템플릿에는 배치 고르는 칸도 있다 — **색 칸 안**만 본다 (안 좁히면 두
    # 축의 option 이 섞여 이 테스트가 무엇도 확인하지 않게 된다).
    html = INDEX.read_text(encoding="utf-8")
    bar = html.split('id="theme-select"', 1)[1].split("</select>", 1)[0]
    html_ids = re.findall(r'<option value="([a-z]+)"', bar)
    css_ids = re.findall(r'data-chat-theme="([a-z]+)"\]', css_text())

    assert js_ids == html_ids, f"모듈 {js_ids} ≠ 템플릿 {html_ids}"
    # `기본` 은 팔레트 블록이 없다 — 속성을 **지워서** 시스템 추종으로 돌아간다.
    assert sorted(set(css_ids)) == sorted(i for i in js_ids if i != "default"), (
        f"CSS {sorted(set(css_ids))} ≠ 모듈 {js_ids}"
    )
    assert js_ids[0] == "default", "첫 방문 기본값은 `기본` 이어야 한다"


def test_배치_목록이_템플릿_모듈_구조표_세_곳에서_같다():
    """어긋나면 **고를 수는 있는데 구조가 없는** 배치가 생긴다.

    색은 CSS 가 값을 갖고 있어 세 곳(CSS·템플릿·모듈)을 봤고, 배치는 구조를
    `message-node.js` 의 표가 갖고 있어 그 표까지 본다.
    """
    js = THEME_JS.read_text(encoding="utf-8")
    block = js.split("export var LAYOUTS", 1)[1].split("];", 1)[0]
    mod_ids = re.findall(r"id:\s*'([a-z-]+)'", block)

    html = INDEX.read_text(encoding="utf-8")
    bar = html.split('id="layout-select"', 1)[1].split("</select>", 1)[0]
    html_ids = re.findall(r'<option value="([a-z-]+)"', bar)

    node_js = (STATIC / "js" / "message-node.js").read_text(encoding="utf-8")
    table = re.search(r"STRUCTURES\s*=\s*\{([^}]*)\}", node_js)
    assert table, "message-node.js 에 구조 표가 없다"
    struct_ids = re.findall(r"([a-z-]+)\s*:", table.group(1))

    assert mod_ids == html_ids, f"모듈 {mod_ids} ≠ 템플릿 {html_ids}"
    assert sorted(mod_ids) == sorted(struct_ids), f"모듈 {mod_ids} ≠ 구조표 {struct_ids}"
    assert mod_ids[0] == "bubbles", "기본 배치는 지금까지의 모습(말풍선)이어야 한다"


def test_첫_페인트_조각과_모듈이_같은_저장_계약을_쓴다():
    """어긋나면 **새로고침할 때마다 테마가 풀린다.**

    템플릿의 인라인 조각은 흰 화면 번쩍임만 막는다. 그 조각과 모듈이 다른 키를
    보면, 심었다가 모듈이 다른 값으로 되돌리는 깜빡임이 생긴다.
    """
    js = THEME_JS.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    #: 두 축이 각자 키·속성을 갖는다 (색과 배치는 직교한다 — 합치지 않는다).
    for name in ("STORAGE_KEY", "ROOT_ATTR", "LAYOUT_KEY", "LAYOUT_ATTR"):
        found = re.search(name + r" = '([^']+)'", js)
        assert found, f"theme.js 에서 {name} 을 찾지 못했다"
        assert f"'{found.group(1)}'" in html, (
            f"템플릿이 다른 {name} 을 쓴다 ({found.group(1)})"
        )
    # 첫 페인트 조각은 **기본값을 찍지 않는다** (속성이 남으면 기본 동작이 깨진다:
    # 색은 시스템 추종, 배치는 지금까지의 모습).
    assert "!== 'default'" in html
    assert "!== 'bubbles'" in html


def test_테마_모듈이_UTF8_이고_LF_이다():
    for path in (THEME_JS, CSS, INDEX, Path(__file__)):
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{path.name} 에 BOM 이 있다"
        assert b"\r\n" not in raw, f"{path.name} 에 CRLF 가 있다"
        raw.decode("utf-8")

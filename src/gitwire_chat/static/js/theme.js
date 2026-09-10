/*
 * 테마 — `#toggle-theme` · `#theme-bar` 와 그 안의 두 고르는 칸을 소유한다.
 * 그리고 **"지금 어느 팔레트·어느 레이아웃인가"** 라는 상태도 여기 것이다.
 *
 * ⭐ 축이 **둘이고 서로 직교한다.** 색과 배치는 다른 결정이다 (`log` 배치에
 * `ide` 팔레트를 쓰는 조합이 성립한다). 그래서 저장값도 루트 표식도 둘로 나눠
 * 둔다 — 하나로 합치면 조합이 표현되지 않고, 나중에 갈라내려면 저장된 값을
 * 이전해야 한다.
 *
 *   색 팔레트    `data-chat-theme`   — **즉시** 전환 (색은 CSS 토큰으로 흐른다)
 *   레이아웃     `data-chat-layout`  — **새로고침**으로 전환 (아래 이유)
 *
 * ⭐ 이 모듈이 하는 일은 딱 하나다: 루트 요소에 `data-chat-theme` 를 찍고, 그
 * 선택을 이 기기에 기억한다. 색 자체는 CSS 의 팔레트 구역(style.css)이 갖고
 * 있고, 화면의 모든 색은 역할 토큰(`var(--…)`)으로 그것을 가리킨다.
 *
 * 그래서 테마를 바꿔도 **메시지 노드를 하나도 다시 만들지 않는다.** DOM 을 손댈
 * 이유가 없으니 가상 스크롤의 높이 측정도, 읽던 스크롤 자리도 그대로다
 * (`rebuiltInView` 는 0 을 유지한다 — `tests/js/render.test.mjs` 가 수치로 잡는다).
 * 노드를 다시 만들어야 색이 바뀌는 구현이라면 그건 틀린 구현이다.
 *
 * ⚠️ 폰트·행간·여백은 건드리지 않는다. 그것들은 **높이를 바꾸므로** 가상화의
 * 측정값이 낡고, 그 순간 스크롤이 튄다. 이번 범위는 색뿐이다.
 *
 * 저장은 `localStorage` 다. 서버 설정으로 올리면 기기 간에 공유되지만, 이 앱은
 * 루프백 전용 개인 앱이라 이득이 거의 없고 배관(엔드포인트·경합·마이그레이션)만
 * 늘어난다. 표시 이름(`composer.js`)이 이미 같은 규약을 쓴다.
 */

/* 저장 키 · 루트 속성 이름.
   ⚠️ `templates/index.html` 의 첫 페인트 조각도 같은 두 문자열을 쓴다 (그쪽은
   흰 화면 번쩍임만 막는다). 둘이 어긋나면 "새로고침하면 테마가 풀린다"가 되므로
   `tests/test_theme.py` 가 일치를 기계로 확인한다. */
export var STORAGE_KEY = 'gitwire-chat.theme';
export var ROOT_ATTR = 'data-chat-theme';
export var LAYOUT_KEY = 'gitwire-chat.layout';
export var LAYOUT_ATTR = 'data-chat-layout';

/* 고를 수 있는 팔레트. `기본` 만 시스템 라이트/다크를 따르고 (속성을 아예 지운다),
   나머지는 전부 다크 계열 터미널 팔레트다.
   ⚠️ 이 목록은 `static/style.css` 의 팔레트 블록과 `templates/index.html` 의
   `<option>` 과 **셋이 같아야** 한다 — 그 일치도 테스트가 본다. */
export var THEMES = [
  { id: 'default', label: '기본', note: '시스템의 라이트/다크를 따른다.' },
  { id: 'log', label: 'log', note: '로그 뷰어 — 중성 어두운 회색.' },
  { id: 'ide', label: 'ide', note: '에디터 통합 터미널 — 장시간 보기 좋다.' },
  { id: 'tty', label: 'tty', note: '인광 초록 콘솔 — 내 말이 더 밝다.' },
  { id: 'tui', label: 'tui', note: 'tmux 계열 — 파란 강조 블록.' }
];

export var DEFAULT_THEME = 'default';

/* 고를 수 있는 **레이아웃**. 구조 자체는 `message-node.js` 의 `STRUCTURES` 가
   갖고 있고, 여기 있는 이름이 그 열쇠다(둘이 어긋나면 테스트가 잡는다).

   ⭐ `ide` 는 색이 아니라 **묶기**가 내용이다 — 같은 사람이 연달아 말하면 발신자
   머리를 한 번만 찍는다. 그 판단은 모델을 소유한 `timeline.js` 가 하고(직전
   메시지와 비교한다), 구조는 그 값만 읽는다. 여기서 아는 것은 이름뿐이다.

   ⭐ `tty` 는 **버리는 것**이 내용이다 — 카드(모서리·바닥·테두리·폭 제한)와 상시
   노출 버튼을 버려 한 줄이 창 폭을 쓴다. 그 버리기는 전부 CSS 가 하고, 구조에서
   다른 것은 프롬프트 표식(`▸`) 조각 하나뿐이다 (`message-node.js`).
   ⚠️ 팔레트에도 `tty` 가 있지만 **다른 축**이다 (`tty` 배치 + `ide` 팔레트 조합이
   성립한다). 두 축이 각자 저장값·루트 표식을 갖는 것이 그 직교성의 근거다.

   ⭐ 레이아웃 전환은 **새로고침으로 간다.** 즉시 전환은 세 위험을 한꺼번에 안는다:
   (1) 가상 스크롤의 높이 측정값이 통째로 낡는다 (구조가 달라지면 높이가 달라진다)
   (2) 그걸 맞추려면 창 안 노드를 다시 만들어야 한다 (= `rebuiltInView` 가 깨진다)
   (3) 픽셀 스크롤 위치가 의미를 잃는다.
   새로고침이면 세 위험이 **존재 자체가 사라진다.** 대신 읽던 자리는 픽셀이 아니라
   **앵커 메시지 id** 로 남겨 복원한다 (`timeline.js` 가 그 일을 소유한다). */
export var LAYOUTS = [
  { id: 'bubbles', label: '말풍선', note: '지금까지의 모습 — 좌우로 갈린 말풍선.' },
  { id: 'log', label: 'log', note: '줄 기반 — 시각·발신자·본문 3열. 내 말은 왼쪽 레일.' },
  { id: 'ide', label: 'ide', note: '연속 발화 묶기 — 같은 사람이 이어 말하면 이름을 한 번만.' },
  { id: 'tty', label: 'tty', note: '터미널 한 줄 — 카드도 상시 버튼도 없다. 가장 촘촘하다.' }
];

export var DEFAULT_LAYOUT = 'bubbles';

export function normalizeLayout(id) {
  for (var i = 0; i < LAYOUTS.length; i++) {
    if (LAYOUTS[i].id === id) { return id; }
  }
  return DEFAULT_LAYOUT;
}

/* ⭐ **함정 하나를 여기서 막는다.** 테마가 못 서면 화면이 망가질 수 있는데, 고르는
   UI 가 그 망가진 영역 안에 있으면 되돌릴 방법이 없다 — 저장값이 남아 새로고침해도
   같은 테마로 뜬다. 그래서 (1) 고르는 칸은 대화 영역 **밖**(사이드바)에 두고,
   (2) 초기화가 실패하면 조립소가 이 함수로 **루트 표식을 걷어내** 기본으로
   떨어뜨린다. 모듈이 생성자에서 터져도 부를 수 있어야 하므로 자유 함수다.
   실패 사실은 조립소가 상태줄에 남긴다 — 조용히 떨어지지 않는다. */
export function fallbackToDefault(doc) {
  var root = doc && (doc.documentElement || doc.body);
  if (!root || !root.removeAttribute) { return false; }
  root.removeAttribute(ROOT_ATTR);
  root.removeAttribute(LAYOUT_ATTR);
  return true;
}

/* 모르는 값은 조용히 기본으로 떨어진다 (옛 이름·손으로 고친 저장값·오타).
   첫 방문도 여기로 온다 — 사용자가 고르지 않았는데 검은 화면이 되면 안 된다. */
export function normalize(id) {
  for (var i = 0; i < THEMES.length; i++) {
    if (THEMES[i].id === id) { return id; }
  }
  return DEFAULT_THEME;
}

export function createTheme(env) {
  var dom = env.dom;
  var bus = env.bus;
  var status = env.status;

  var el = {
    toggle: dom.$('toggle-theme'),
    bar: dom.$('theme-bar'),
    select: dom.$('theme-select'),
    note: dom.$('theme-note'),
    layoutSelect: dom.$('layout-select'),
    layoutNote: dom.$('layout-note')
  };

  var current = DEFAULT_THEME;
  var layout = DEFAULT_LAYOUT;

  /* 토큰은 루트에서 상속돼 내려가므로 찍는 곳도 루트 하나다.
     (`documentElement` 가 없는 환경이면 body 로 내려간다 — 상속은 같다.) */
  function rootNode() {
    return dom.doc.documentElement || dom.doc.body;
  }

  function stamp(id) {
    var root = rootNode();
    if (!root) { return; }
    if (id === DEFAULT_THEME) {
      /* ⭐ 기본은 **속성을 지운다.** 값으로 남겨 두면 `기본`도 팔레트 블록이
         하나 필요해지고, 시스템 라이트/다크 추종을 CSS 에서 두 번 쓰게 된다. */
      if (root.removeAttribute) { root.removeAttribute(ROOT_ATTR); }
      return;
    }
    root.setAttribute(ROOT_ATTR, id);
  }

  function describe(list, id) {
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id) { return list[i].note; }
    }
    return '';
  }

  function save(key, id) {
    try {
      env.localStorage.setItem(key, id);
    } catch (err) {
      /* 프라이빗 모드·저장소 꽉 찬 경우 — 이번 세션에는 적용됐다.
         테마 기억은 부가 기능이라 여기서 채팅을 막지 않는다. */
    }
  }

  function read(key, fallback, normalizer) {
    try {
      return normalizer(env.localStorage.getItem(key));
    } catch (err) {
      return fallback;
    }
  }

  /* 색: 화면 반영 + 기억. 새로고침을 요구하지 않는다 — 속성 하나로 즉시 바뀐다. */
  function apply(id, remember) {
    current = normalize(id);
    stamp(current);
    if (el.select) { el.select.value = current; }
    if (el.note) { dom.setText(el.note, describe(THEMES, current)); }
    if (remember) { save(STORAGE_KEY, current); }
    return current;
  }

  /* 레이아웃: 표식만 맞춘다. **전환은 여기서 하지 않는다** (chooseLayout 이 한다). */
  function applyLayout(id, remember) {
    layout = normalizeLayout(id);
    var root = rootNode();
    if (root) {
      if (layout === DEFAULT_LAYOUT) {
        if (root.removeAttribute) { root.removeAttribute(LAYOUT_ATTR); }
      } else {
        root.setAttribute(LAYOUT_ATTR, layout);
      }
    }
    if (el.layoutSelect) { el.layoutSelect.value = layout; }
    if (el.layoutNote) { dom.setText(el.layoutNote, describe(LAYOUTS, layout)); }
    if (remember) { save(LAYOUT_KEY, layout); }
    return layout;
  }

  /* 사용자가 레이아웃을 골랐다 — 기억하고 **다시 불러온다.**
     같은 것을 고르면 아무 일도 하지 않는다(쓸데없이 새로고침하지 않는다). */
  function chooseLayout(id) {
    var next = normalizeLayout(id);
    if (next === layout) { applyLayout(next, true); return false; }
    applyLayout(next, true);
    /* 읽던 자리를 **앵커 메시지 id** 로 남긴다. 픽셀 위치는 구조가 바뀌면
       의미가 없다. 자리를 아는 것은 대화를 소유한 모듈이라 그쪽에 부탁한다 —
       그 모듈이 죽어 있으면 구독자가 없어 아무 일도 일어나지 않는다(그리고
       복원 없이 맨 아래로 뜬다 — 잃는 것이 자리 하나다). */
    if (bus) { bus.emit('anchor:keep', { layout: next }); }
    if (status) { status.set('레이아웃을 바꾼다 — 다시 불러온다…'); }
    reload();
    return true;
  }

  function reload() {
    var win = env.win;
    if (win && win.location && win.location.reload) { win.location.reload(); }
  }

  function mount() {
    /* 접힌 상태를 **이 모듈이** 정한다. 템플릿의 `hidden` 에만 맡기면 "설정이
       처음부터 펼쳐져 있다"가 코드로 검증되지 않는다 (설정은 상시 노출이 아니다). */
    dom.hide(el.bar);
    dom.on(el.toggle, 'click', function () {
      if (!el.bar) { return; }
      if (el.bar.hidden) { dom.show(el.bar); } else { dom.hide(el.bar); }
    });
    dom.on(el.select, 'change', function () {
      apply(el.select.value, true);
    });
    dom.on(el.layoutSelect, 'change', function () {
      chooseLayout(el.layoutSelect.value);
    });

    /* 저장된 값을 되살린다. index.html 의 첫 페인트 조각이 이미 같은 값을 찍어
       두었을 수 있다 — 같은 값을 다시 찍는 것은 아무 일도 아니고, 그쪽이 없었던
       환경(그 조각이 막힌 경우)에서는 여기가 유일한 적용 지점이 된다. */
    apply(read(STORAGE_KEY, DEFAULT_THEME, normalize), false);
    /* 저장된 이름이 **없는 레이아웃**이어도 여기서 기본으로 떨어진다
       (그리고 첫 페인트 조각이 찍어 둔 표식을 걷어낸다). */
    applyLayout(read(LAYOUT_KEY, DEFAULT_LAYOUT, normalizeLayout), false);
  }

  return {
    mount: mount,
    /* 바깥(테스트·디버깅)에서 고르는 길. UI 와 같은 경로를 쓴다. */
    set: function (id) { return apply(id, true); },
    current: function () { return current; },
    themes: function () { return THEMES.map(function (t) { return t.id; }); },
    /* 레이아웃은 이 페이지가 사는 동안 **바뀌지 않는다** (바꾸면 새로고침이다).
       그래서 조립소가 이 값을 한 번 읽어 `env.layout` 으로 나눠 준다. */
    setLayout: function (id) { return chooseLayout(id); },
    layout: function () { return layout; },
    layouts: function () { return LAYOUTS.map(function (l) { return l.id; }); }
  };
}

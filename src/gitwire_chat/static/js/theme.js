/*
 * 색 테마 — `#toggle-theme` · `#theme-bar` · `#theme-select` · `#theme-note` 를
 * 소유한다. 그리고 **"지금 어느 팔레트인가"** 라는 상태도 여기 것이다.
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

  var el = {
    toggle: dom.$('toggle-theme'),
    bar: dom.$('theme-bar'),
    select: dom.$('theme-select'),
    note: dom.$('theme-note')
  };

  var current = DEFAULT_THEME;

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

  function describe(id) {
    for (var i = 0; i < THEMES.length; i++) {
      if (THEMES[i].id === id) { return THEMES[i].note; }
    }
    return '';
  }

  function save(id) {
    try {
      env.localStorage.setItem(STORAGE_KEY, id);
    } catch (err) {
      /* 프라이빗 모드·저장소 꽉 찬 경우 — 이번 세션에는 적용됐다.
         테마 기억은 부가 기능이라 여기서 채팅을 막지 않는다. */
    }
  }

  function read() {
    try {
      return normalize(env.localStorage.getItem(STORAGE_KEY));
    } catch (err) {
      return DEFAULT_THEME;
    }
  }

  /* 화면 반영 + 기억. 새로고침을 요구하지 않는다 — 속성 하나로 즉시 바뀐다. */
  function apply(id, remember) {
    current = normalize(id);
    stamp(current);
    if (el.select) { el.select.value = current; }
    if (el.note) { dom.setText(el.note, describe(current)); }
    if (remember) { save(current); }
    return current;
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

    /* 저장된 값을 되살린다. index.html 의 첫 페인트 조각이 이미 같은 값을 찍어
       두었을 수 있다 — 같은 값을 다시 찍는 것은 아무 일도 아니고, 그쪽이 없었던
       환경(그 조각이 막힌 경우)에서는 여기가 유일한 적용 지점이 된다. */
    apply(read(), false);
  }

  return {
    mount: mount,
    /* 바깥(테스트·디버깅)에서 고르는 길. UI 와 같은 경로를 쓴다. */
    set: function (id) { return apply(id, true); },
    current: function () { return current; },
    themes: function () { return THEMES.map(function (t) { return t.id; }); }
  };
}

/*
 * 내 **가용 상태** — `#my-status` 와 그 메뉴를 소유하고, "지금 어느 상태인가"라는
 * 값도 여기 것이다.
 *
 * ⭐ 이것은 "지금 이 방을 보고 있나"가 **아니다.** 그 값은 `presence.js` 가 갖고
 * 있고 쓰임새도 다르다(서버가 OS 알림을 띄울지 정한다 — 로컬 전용, 발행 없음).
 * 여기 있는 것은 **"지금 메시지를 읽을 수 있는 상태인가"를 사람이 선언한 값**이고
 * 남에게 보이라고 원격까지 올라간다. 두 물건을 한 모듈에 담으면 둘 중 하나가
 * 바뀔 때마다 다른 하나의 규율을 매번 다시 설명해야 한다.
 *
 * ⭐ **하트비트가 없다.** 값이 바뀌는 계기는 이벤트뿐이다:
 *
 *   | 계기 | 결과 | 누가 발행하나 |
 *   |---|---|---|
 *   | 메시지 전송     | 활동 중  | 서버 (`rooms.send` — 메시지 POST 에 얹힌다) |
 *   | 커서 전진(읽음) | 활동 중  | `reads.js` 의 커서 POST 에 **같이 실린다** |
 *   | 창 닫기         | 자리 비움 | 여기 (`beforeunload`) |
 *   | 직접 고름       | 그 값    | 여기 |
 *
 * 앞의 둘은 **이미 나가는 요청에 얹는다** — 상태 때문에 왕복이 하나 더 생기지
 * 않는다. 서버는 "바뀔 게 없으면 파일을 쓰지 않는다"(`gitwire_chat/reads.py`)를
 * 지키므로, 되풀이되는 선언은 커밋이 되지 않는다.
 *
 * ⭐ **`방해 금지`도 자동 규칙으로 풀린다.** 읽으러 들어왔다 = 받겠다는 뜻이다.
 * Teams 관례(DND 는 자동 해제하지 않는다)와 **의도적으로** 다르다.
 *
 * ⭐ 전역 기본값은 **이 브라우저**에 남는다 (`localStorage`). 서버로 올리면
 * 기기마다 다른 상태를 다루는 배관이 생기는데, 이 값은 원래 "이 사람이 지금
 * 어떤가"라 기기별로 갈릴 이유가 없다. 저장소를 못 읽는 환경(프라이빗 모드·정책)
 * 에서도 화면은 정상이어야 하므로 읽기·쓰기를 전부 try/catch 로 감싼다 — 못 읽으면
 * `활동 중` 이다.
 *
 * ⚠️ 창 닫기의 `자리 비움` 은 **기억하지 않는다.** 그것은 닫힌 창에 대한 사실이지
 * 그 사람이 선언한 기본값이 아니다 — 기억하면 다음에 앱을 열자마자 "자리 비움"인
 * 채로 앉아 있게 된다.
 *
 * ⚠️ 전역 상태를 **모든 방에 일괄 적용하지 않는다** (이번 범위가 아니다). 대신
 * 발행은 `publishTo(방)` 하나로 잘라 두었다 — 나중에 방 목록을 순회하기만 하면 된다.
 */

import { errText } from './dom.js';
import { roomPath } from './api.js';

/* 저장 키. 색 테마·표시 이름과 같은 규약이다 (`gitwire-chat.*`). */
export var STORAGE_KEY = 'gitwire-chat.status';

/* ⭐ 저장·전송되는 값은 **ASCII 열쇠**이고, 사람이 읽는 이름은 여기 표에만 있다.
   문구를 파일에 적으면 그것이 곧 스키마가 되어, 문구를 다듬는 일이 원격 데이터
   이전이 된다 (파이썬 쪽 정본은 `gitwire_chat/reads.py` 의 `STATUSES`). */
export var STATUS_ACTIVE = 'active';
export var STATUS_AWAY = 'away';
export var STATUS_DND = 'dnd';

export var STATUSES = [
  { id: STATUS_ACTIVE, label: '활동 중', note: '지금 메시지를 받는다.' },
  { id: STATUS_AWAY, label: '자리 비움', note: '자리에 없다 — 답이 늦는다.' },
  { id: STATUS_DND, label: '방해 금지', note: '지금은 보지 않는다.' }
];

export var DEFAULT_STATUS = STATUS_ACTIVE;

/* 모르는 값은 조용히 기본으로 떨어진다 (옛 저장값·더 새 버전이 쓴 네 번째 상태).
   "받을 수 있다"로 보는 쪽이 안전하다 — 반대는 상대가 "저 사람은 못 받는다"고
   오해하게 만든다. */
export function normalizeStatus(id) {
  for (var i = 0; i < STATUSES.length; i++) {
    if (STATUSES[i].id === id) { return id; }
  }
  return DEFAULT_STATUS;
}

export function statusLabel(id) {
  for (var i = 0; i < STATUSES.length; i++) {
    if (STATUSES[i].id === id) { return STATUSES[i].label; }
  }
  return STATUSES[0].label;
}

export function createUserStatus(env) {
  var dom = env.dom;
  var bus = env.bus;
  var api = env.api;

  var el = {
    btn: dom.$('my-status'),
    dot: dom.$('my-status-dot'),
    label: dom.$('my-status-label'),
    menu: dom.$('my-status-menu')
  };

  var current = DEFAULT_STATUS;
  var roomId = null;
  var options = [];              /* 메뉴 버튼들 (선택 표시를 갈아 끼운다) */

  /* -------------------------------------------------------- 저장소 */

  function load() {
    try {
      return normalizeStatus(env.localStorage.getItem(STORAGE_KEY));
    } catch (err) {
      /* 저장소를 못 읽는다 = 기억이 없는 것과 같다. 화면은 그대로 뜬다. */
      return DEFAULT_STATUS;
    }
  }

  function remember(id) {
    try {
      env.localStorage.setItem(STORAGE_KEY, id);
    } catch (err) {
      /* 프라이빗 모드·저장소 꽉 참 — 이번 세션에는 적용됐다. */
    }
  }

  /* ---------------------------------------------------------- 화면 */

  function paint() {
    var label = statusLabel(current);
    if (el.btn) {
      /* 색은 CSS 가 이 표식으로 고른다 (JS 는 색을 모른다 — 팔레트가 갖는다). */
      el.btn.setAttribute('data-status', current);
      el.btn.setAttribute('title', '내 상태 · ' + label);
      el.btn.setAttribute('aria-label', '내 상태 · ' + label);
    }
    if (el.label) { dom.setText(el.label, label); }
    for (var i = 0; i < options.length; i++) {
      var on = options[i].dataset.status === current;
      options[i].setAttribute('aria-pressed', on ? 'true' : 'false');
      options[i].className = on ? 'status-option on' : 'status-option';
    }
  }

  function openMenu(on) {
    if (!el.menu) { return; }
    if (on) { dom.show(el.menu); } else { dom.hide(el.menu); }
    if (el.btn) { el.btn.setAttribute('aria-expanded', on ? 'true' : 'false'); }
  }

  function buildMenu() {
    if (!el.menu) { return; }
    el.menu.replaceChildren();
    options = [];
    for (var i = 0; i < STATUSES.length; i++) {
      (function (item) {
        var btn = dom.make('button', 'status-option');
        btn.setAttribute('type', 'button');
        btn.setAttribute('data-status', item.id);
        btn.dataset.status = item.id;
        btn.setAttribute('title', item.note);
        var dot = dom.make('span', 'status-dot', '●');
        dot.setAttribute('aria-hidden', 'true');
        dot.setAttribute('data-status', item.id);
        btn.appendChild(dot);
        btn.appendChild(dom.make('span', 'status-option-label', item.label));
        btn.addEventListener('click', function () { choose(item.id); });
        el.menu.appendChild(btn);
        options.push(btn);
      }(STATUSES[i]));
    }
  }

  /* ---------------------------------------------------------- 발행 */

  /* **방 하나**를 갱신한다. 전역 일괄 적용은 이번 범위가 아니지만, 그때 얹을
     자리가 여기다 (방 목록을 순회하며 이 함수를 부르면 된다). */
  function publishTo(target, how) {
    if (!target) { return Promise.resolve(false); }
    var opts = how || {};
    return api(roomPath(target, '/reads'), {
      method: 'POST',
      body: { status: current },
      /* ⭐ 창이 닫히는 중에도 요청이 살아남게 한다. 이것이 없으면 브라우저가
         언로드와 함께 취소해 `자리 비움` 이 영영 나가지 않는다. */
      keepalive: opts.keepalive === true
    }).then(function (data) {
      /* 읽음 모델의 주인은 `reads.js` 하나다 — 우리가 받은 스냅샷도 그쪽으로
         흘려보낸다. 사본을 하나 더 두면 "누가 소유하나"가 다시 흐려진다. */
      bus.emit('reads:state', { roomId: target, state: data });
      return true;
    })['catch'](function (err) {
      /* 아직 안 붙은 방(409)은 사고가 아니다 — 붙고 나면 다시 알린다. */
      if (err && err.status === 409) { return false; }
      if (env.console && env.console.warn) {
        env.console.warn('[gitwire-chat] 상태를 알리지 못했다 — ' + errText(err));
      }
      return false;
    });
  }

  /* 값을 정한다. `publish` 는 **이미 나가는 요청에 얹히는 경우** 끈다
     (전송·커서 전진 — 그 요청이 같은 값을 싣고 간다). */
  function set(id, opts) {
    var o = opts || {};
    var next = normalizeStatus(id);
    var changed = next !== current;
    current = next;
    paint();
    if (o.remember !== false) { remember(current); }
    if (o.publish && (changed || o.force)) { return publishTo(roomId, o); }
    return Promise.resolve(false);
  }

  /* 사용자가 직접 골랐다 — 같은 값을 다시 골라도 화면은 닫는다. */
  function choose(id) {
    openMenu(false);
    return set(id, { publish: true, remember: true });
  }

  function mount() {
    buildMenu();
    openMenu(false);
    current = load();
    paint();

    dom.on(el.btn, 'click', function () {
      if (!el.menu) { return; }
      openMenu(el.menu.hidden);
    });
    /* 키보드로 빠져나오는 길 (메뉴는 토글 버튼으로도 닫힌다 — 색 테마 칸과 같은
       규약이다: 바깥을 눌러야만 닫히는 UI 는 이 앱에 없다). */
    dom.on(dom.doc, 'keydown', function (e) {
      if (e && e.key === 'Escape') { openMenu(false); }
    });

    /* 방을 열 때 **내 기본값을 그 방에 선언한다.** 서버는 값이 같으면 파일을
       쓰지 않으므로(= 커밋이 없다) 평소에는 공짜이고, 지난번 창 닫기로 남은
       `자리 비움` 은 여기서 풀린다. */
    bus.on('room:switch', function (e) {
      roomId = e.id;
      openMenu(false);
      return publishTo(roomId);
    });

    /* 말을 보냈다 = 활동 중. 발행은 **메시지 POST 에 얹혀** 서버가 한다
       (`rooms.send`) — 여기서는 화면과 기억만 맞춘다. */
    bus.on('draft:add', function () {
      set(STATUS_ACTIVE, { remember: true });
    });

    /* 커서가 전진했다 = 지금 읽고 있다. 발행은 **그 커서 POST 에 얹힌다**
       (`reads.js` 가 이 값을 실어 보낸다 — 그래서 여기서 publish 하지 않는다).
       ⚠️ 이 신호는 동기로 온다: 여기서 값을 바꾼 뒤에 그쪽이 읽어 간다. */
    bus.on('status:active', function () {
      set(STATUS_ACTIVE, { remember: true });
    });

    /* 창을 닫는다 = 자리 비움. **기억하지 않는다** (닫힌 창에 대한 사실이지
       선언이 아니다 — 다음에 열 때 `자리 비움` 으로 앉아 있으면 안 된다). */
    dom.on(env.win, 'beforeunload', function () {
      set(STATUS_AWAY, { publish: true, force: true, remember: false,
        keepalive: true });
    });
  }

  return {
    mount: mount,
    /* 지금 값 — `reads.js` 가 커서 POST 에 실을 때 읽는다. */
    current: function () { return current; },
    set: function (id) { return choose(id); },
    /* 메뉴를 열고 닫는 길 (테스트·디버깅). */
    open: function (on) { openMenu(on !== false); },
    isOpen: function () { return !!(el.menu && !el.menu.hidden); },
    publish: function () { return publishTo(roomId, { force: true }); }
  };
}

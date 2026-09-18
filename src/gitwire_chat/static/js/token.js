/*
 * 자격증명 — `#token-*` 만 소유한다.
 *
 * 두 가지를 한다:
 *
 *   1. **어디 있는지 말한다.** 기반(gitwire)은 자격증명을 기동 시 한 번 읽어
 *      들고 쓴다. 못 찾으면 git 이 대화형으로 묻는데 이 앱은 창 없이 돌아
 *      물어볼 곳이 없다 — 조용히 멈춘다. 그래서 사용자가 **자기 상태**를 알아야
 *      한다: 환경변수 / OS 저장소 / 주소에 박힘 / 없음.
 *   2. **없으면 발급을 거든다.** 새 레포 만들기(`newrepo.js`)와 같은 패턴 —
 *      필요한 것이 미리 채워진 링크로 보내고, 받아 온 값을 OS 저장소에 저장한다.
 *
 * ⚠️ **값이 이 파일을 지나가는 곳은 한 군데다** — 붙여넣기 칸에서 곧바로 POST
 * 본문으로 간다. 화면에 다시 그리지 않고, 콘솔에 찍지 않고, 저장 성공 즉시
 * 칸을 비운다. 서버가 내려주는 탐색 결과에는 **값 자체가 없다**
 * (`gitwire_chat/tokens.py` — 출처만 싣는다).
 *
 * ⚠️ 토큰은 **선택이다.** 공개·로컬 레포는 토큰 없이 그대로 굴러간다. 그래서
 * 이 블록은 접혀 있고, 아무것도 강요하지 않으며, 실패해도 방 등록을 막지 않는다
 * (`boot.js` 의 격리 — 이 단위가 넘어져도 나머지는 그대로 선다).
 */

import { errText } from './dom.js';

/* 저장은 사용자의 자격증명 저장소를 **바꾸는** 동작이라 서버가 "우리 화면에서
   온 요청인가"를 본다. 헤더 이름·값은 갱신 실행과 같은 것이고, 서버 쪽 원천은
   `gitwire_chat/csrf.py` 다 (그 일치는 tests/test_updater.py 가 기계로 본다). */
export var SAVE_HEADER = 'X-Gitwire-Chat';
export var SAVE_HEADER_VALUE = 'update';

export function createToken(env) {
  var dom = env.dom;
  var api = env.api;

  var el = {
    check: dom.$('token-check'),
    found: dom.$('token-found'),
    form: dom.$('token-form'),
    why: dom.$('token-why'),
    link: dom.$('token-link'),
    paste: dom.$('token-paste'),
    save: dom.$('token-save'),
    error: dom.$('token-error')
  };

  var state = null;          /* 마지막 탐색 결과 (값은 들어 있지 않다) */

  function showError(message) {
    if (!el.error) { return; }
    if (!message) { dom.hide(el.error); return; }
    dom.setText(el.error, message);
    dom.show(el.error);
  }

  /* 탐색 결과를 한 줄로. **출처만** 말한다 — 값은 서버도 내려주지 않는다. */
  function render(data) {
    state = data;
    var issue = data.issue || {};
    dom.setText(el.found, data.label + (data.detail ? ' (' + data.detail + ')' : ''));
    dom.show(el.found);

    /* 왜 필요한지 · 어떤 권한인지를 **서버가 준 목록으로** 말한다 (스코프를
       화면에 손으로 적으면 두 곳이 어긋난다 — 원천은 `tokens.GITHUB_SCOPES`). */
    if (data.found) {
      dom.setText(el.why,
        '이미 쓸 수 있는 토큰이 있다. 바꾸려면 아래에서 새로 발급해 저장하면 된다.');
    } else {
      dom.setText(el.why,
        '비공개 레포를 쓰려면 토큰이 필요하다. 아래 링크는 필요한 권한(' +
        (issue.scopes || []).join(', ') + ')만 미리 채워 둔 발급 페이지다 — ' +
        '「Generate token」만 누르면 된다.');
    }
    if (issue.link) {
      el.link.setAttribute('href', issue.link);
      dom.show(el.link);
    } else {
      dom.hide(el.link);
    }
    /* 찾았으면 **접어 둔다**(index.html 의 초기 상태와 같다), 없으면 편다.
       있는 사람에게 발급을 권하지 않는 것이 이 분기의 전부다 — 그래도 토글로
       열 수 있다 (만료·취소된 토큰을 바꾸는 길). */
    if (data.found) { dom.hide(el.form); } else { dom.show(el.form); }
    return data;
  }

  /* 탐색은 `git credential fill` 을 타서 실측 435ms 다 — 페이지가 뜰 때
     자동으로 부르지 않는다. 사용자가 이 줄을 누를 때만 나간다 (갱신 확인이
     주기 폴링을 두지 않는 것과 같은 태도 — `update.js`). */
  function check(fresh) {
    showError('');
    dom.setText(el.check, '확인 중…');
    el.check.disabled = true;
    var path = '/api/token' + (fresh ? '?fresh=1' : '');
    return api(path)
      .then(render)['catch'](function (err) {
        showError(errText(err));
      }).then(function () {
        dom.setText(el.check, '토큰이 있나 확인');
        el.check.disabled = false;
      });
  }

  function save() {
    showError('');
    var value = el.paste.value || '';
    if (!value.trim()) { showError('붙여넣은 토큰이 없다.'); return; }
    dom.setText(el.save, '저장 중…');
    el.save.disabled = true;
    return api('/api/token', {
      method: 'POST',
      headers: (function () { var h = {}; h[SAVE_HEADER] = SAVE_HEADER_VALUE; return h; })(),
      body: { token: value, host: (state && state.host) || 'github.com' }
    }).then(function (data) {
      /* 값이 화면에 남아 있을 이유가 사라졌다 — 즉시 비운다. */
      el.paste.value = '';
      render(data);
      dom.setText(el.found,
        data.label + ' — ' +
        (data.verified
          ? '토큰 주인(' + data.username + ')을 확인하고 저장했습니다.'
          : '저장했습니다.') +
        (data.detail ? ' ' + data.detail : ''));
      dom.show(el.found);
      return data;
    })['catch'](function (err) {
      var payload = err.payload || {};
      showError(errText(err) + (payload.hint ? ' — ' + payload.hint : ''));
    }).then(function () {
      dom.setText(el.save, '저장');
      el.save.disabled = false;
    });
  }

  function mount() {
    dom.on(el.check, 'click', function () {
      /* 한 번 봤으면 블록을 여닫기만 한다 — 누를 때마다 git 을 부르지 않는다. */
      if (state) {
        if (el.form.hidden) { dom.show(el.form); } else { dom.hide(el.form); }
        return;
      }
      check(false);
    });
    dom.on(el.save, 'click', save);
    /* ⚠️ 이 칸은 `<form class="add-room">` 안에 있다 — Enter 가 **방 등록**을
       쏘아 버린다. 토큰을 붙여넣고 Enter 를 누르는 것은 자연스러운 동작이므로
       여기서 가로채 저장으로 돌린다. */
    dom.on(el.paste, 'keydown', function (e) {
      if (e && e.key === 'Enter') {
        if (e.preventDefault) { e.preventDefault(); }
        save();
      }
    });
  }

  return {
    mount: mount,
    check: check,
    save: save,
    /* 마지막 탐색 결과 (값 없음 — 테스트·디버깅이 보는 창). */
    state: function () { return state; }
  };
}

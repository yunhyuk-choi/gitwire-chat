/*
 * 방 등록 폼 — `#toggle-add` · `#add-room` · `#repo-url` · `#room-name` ·
 * `#token-env` · `#add-room-*` 을 소유한다.
 *
 * 등록은 **즉시 돌아온다** — 클론은 서버 백그라운드다. 그래서 여기서는 방으로
 * 바로 들어가고, '받는 중' 은 방 목록/타임라인이 각자 그린다.
 *
 * 레포 만들기 거들기(`newrepo.js`)와는 `repo:ready` 이벤트로만 만난다. 그쪽이
 * 이 모듈의 입력칸을 직접 만지지 않는다는 뜻이다 — 소유가 갈리면 "누가 이 값을
 * 바꿨나"를 추적할 수 있다.
 */

import { errText } from './dom.js';

export function createAddRoom(env) {
  var dom = env.dom;
  var bus = env.bus;
  var api = env.api;

  var el = {
    toggle: dom.$('toggle-add'),
    form: dom.$('add-room'),
    submit: dom.$('add-room-submit'),
    cancel: dom.$('add-room-cancel'),
    error: dom.$('add-room-error'),
    repoUrl: dom.$('repo-url'),
    roomName: dom.$('room-name'),
    tokenEnv: dom.$('token-env')
  };

  var author = '';

  function submit(event) {
    if (event && event.preventDefault) { event.preventDefault(); }
    var url = (el.repoUrl.value || '').trim();
    if (!url) { return; }
    dom.hide(el.error);
    dom.setText(el.submit, '등록 중…');
    el.submit.disabled = true;
    return api('/api/rooms', {
      method: 'POST',
      body: {
        repo_url: url,
        name: (el.roomName.value || '').trim(),
        token_env: (el.tokenEnv.value || '').trim(),
        author: author
      }
    }).then(function (data) {
      el.repoUrl.value = '';
      el.roomName.value = '';
      dom.hide(el.form);
      /* 목록 갱신·방 전환은 방 목록 모듈의 몫이다 (그 노드는 그쪽 것이다). */
      bus.emit('room:added', { id: data.room.id });
    })['catch'](function (err) {
      dom.setText(el.error, errText(err));
      dom.show(el.error);
    }).then(function () {
      dom.setText(el.submit, '방 등록');
      el.submit.disabled = false;
    });
  }

  function mount() {
    dom.on(el.form, 'submit', submit);
    dom.on(el.toggle, 'click', function () {
      if (el.form.hidden) { dom.show(el.form); } else { dom.hide(el.form); }
    });
    dom.on(el.cancel, 'click', function () { dom.hide(el.form); });

    /* 표시 이름은 작성 모듈이 소유한다 — 현재 값만 받아 둔다. */
    bus.on('author:changed', function (e) { author = e.name || ''; });

    /* 레포를 만들고 온 사람: 주소를 손으로 옮겨 적지 않는다. */
    bus.on('repo:ready', function (e) {
      if (!e.url) { return; }
      el.repoUrl.value = e.url;
      submit();
    });
  }

  /* 레포 만들기 거들기가 **읽기만** 하는 창 (방 이름·토큰 환경변수).
     입력칸 자체는 이 모듈 소유라 밖에서 고칠 수 없다. */
  function fields() {
    return {
      name: (el.roomName.value || '').trim(),
      tokenEnv: (el.tokenEnv.value || '').trim()
    };
  }

  return { mount: mount, submit: submit, fields: fields };
}

/*
 * 레포 만들기 거들기 — `#new-repo-*` 만 소유한다.
 *
 * ⚠️ 레포 생성은 계정을 바꾸는 **외부 동작**이라 조용히 하지 않는다. 무엇이
 * 만들어지는지(소유자·이름·공개범위)를 먼저 보여주고, 사용자가 누른 뒤에만 만든다.
 *
 * 만든 주소는 `repo:ready` 로 흘려보낸다 — 방 등록 폼이 그 주소로 이어 붙인다.
 * 사용자가 URL 을 손으로 옮겨 적지 않는 것이 이 블록의 존재 이유다.
 */

import { errText } from './dom.js';

export function createNewRepo(env) {
  var dom = env.dom;
  var bus = env.bus;
  var api = env.api;

  var el = {
    toggle: dom.$('new-repo-toggle'),
    form: dom.$('new-repo-form'),
    owner: dom.$('new-repo-owner'),
    name: dom.$('new-repo-name'),
    check: dom.$('new-repo-check'),
    plan: dom.$('new-repo-plan'),
    link: dom.$('new-repo-link'),
    create: dom.$('new-repo-create'),
    use: dom.$('new-repo-use'),
    error: dom.$('new-repo-error')
  };

  var plan = null;

  /* 방 등록 폼이 이미 받아 둔 값(방 이름·토큰 환경변수)은 **읽기만** 한다.
     그 입력칸은 저쪽 소유라 여기서 고치지 않는다. */
  function draft() {
    return env.roomDraft ? env.roomDraft() : { name: '', tokenEnv: '' };
  }

  function showError(message) {
    if (!el.error) { return; }
    if (!message) { dom.hide(el.error); return; }
    dom.setText(el.error, message);
    dom.show(el.error);
  }

  function body() {
    var d = draft();
    return {
      host: 'github.com',
      name: (el.name.value || '').trim() || d.name,
      owner: (el.owner.value || '').trim(),
      token_env: d.tokenEnv,
      description: d.name
    };
  }

  function makePlan() {
    showError('');
    return api('/api/repos/plan', { method: 'POST', body: body() })
      .then(function (data) {
        plan = data;
        if (el.name && !el.name.value) { el.name.value = data.name || ''; }
        var where = (data.owner ? data.owner + '/' : '') + (data.name || '');
        dom.setText(el.plan,
          data.forge.label + ' 에 ' + where + ' 을(를) 비공개(private)로 만든다.' +
          (data.mode === 'api'
            ? ' 토큰(' + data.token_env + ')이 있어 앱 안에서 바로 만든다.'
            : data.mode === 'link'
              ? ' 링크로 가서 만들고 오면 그 주소로 방을 잇는다.'
              : ' 이 호스트는 거들 수 없다 — 주소를 직접 넣어라.'));
        dom.show(el.plan);
        if (data.detail) { showError(data.detail); }
        if (data.mode === 'api') { dom.show(el.create); } else { dom.hide(el.create); }
        if (data.link) {
          el.link.setAttribute('href', data.link);
          dom.show(el.link);
          if (data.clone_url) { dom.show(el.use); } else { dom.hide(el.use); }
        } else {
          dom.hide(el.link);
          dom.hide(el.use);
        }
      })['catch'](function (err) { showError(errText(err)); });
  }

  function use(url) {
    if (!url) { return; }
    dom.hide(el.form);
    bus.emit('repo:ready', { url: url });
  }

  function create() {
    showError('');
    dom.setText(el.create, '만드는 중…');
    el.create.disabled = true;
    return api('/api/repos', { method: 'POST', body: body() })
      .then(function (data) {
        use(data.repo && data.repo.clone_url);
      })['catch'](function (err) {
        var payload = err.payload || {};
        showError(errText(err) + (payload.hint ? ' — ' + payload.hint : ''));
      }).then(function () {
        dom.setText(el.create, '지금 만들기');
        el.create.disabled = false;
      });
  }

  function mount() {
    dom.on(el.toggle, 'click', function () {
      if (el.form.hidden) {
        dom.show(el.form);
        if (!el.name.value) { el.name.value = draft().name; }
      } else { dom.hide(el.form); }
    });
    dom.on(el.check, 'click', makePlan);
    dom.on(el.create, 'click', create);
    dom.on(el.use, 'click', function () { use(plan && plan.clone_url); });
  }

  return {
    mount: mount,
    plan: makePlan,
    create: create,
    planned: function () { return plan; }
  };
}

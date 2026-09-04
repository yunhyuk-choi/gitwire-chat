/*
 * 작성·전송 — `#composer` · `#text` · `#author` · `#send` · `#reply-chip` 을
 * 소유한다. 그리고 **네트워크 시도**를 소유한다.
 *
 * 화면에 붙은 말풍선은 타임라인 것이고, 그 말을 서버에 보내는 일은 여기 것이다.
 * 둘 사이는 네 개의 이벤트로만 만난다:
 *
 *   composer → timeline : `draft:add` · `draft:settle` · `draft:fail`
 *   timeline → composer : `draft:retry`
 *
 * ⭐ 낙관적 전송 — 서버 응답을 **기다리지 않고** 지금 붙인다. 어차피 갈 것이고,
 * 실패하면 그때 그 말풍선 옆에 '전송 실패' 와 재시도를 준다. 예전에는 POST 응답을
 * 받은 뒤에 붙여서, 원격이 느려지거나 막히면 그대로 멈춘 것처럼 보였다.
 */

import { errText } from './dom.js';

export function createComposer(env) {
  var dom = env.dom;
  var bus = env.bus;
  var api = env.api;
  var status = env.status;

  var el = {
    form: dom.$('composer'),
    text: dom.$('text'),
    author: dom.$('author'),
    send: dom.$('send'),
    replyChip: dom.$('reply-chip'),
    replyLabel: dom.$('reply-label'),
    replyCancel: dom.$('reply-cancel')
  };

  var roomId = null;
  var replyTo = null;
  var author = '';
  var seq = 0;

  /* 임시 ID 는 `~` 로 시작한다. 실제 봉투 ID(`records/...`)보다 사전식으로
     **뒤**라서, 정렬 규칙(ID 오름차순 = 시간순)을 하나도 건드리지 않고 항상
     맨 아래에 놓인다. 서버 응답이 오면 타임라인이 이 키를 진짜 봉투 ID 로
     갈아끼우되 **노드는 그대로 쓴다**. */
  function draftId() {
    seq += 1;
    return '~pending/' + ('000000' + seq).slice(-6);
  }

  function autoGrow() {
    if (!el.text || !el.text.style) { return; }
    el.text.style.height = 'auto';
    var h = el.text.scrollHeight || 0;
    el.text.style.height = Math.min(h, 160) + 'px';
  }

  function setAuthor(value) {
    author = value || '';
    bus.stick('author:changed', { name: author });
  }

  function rememberAuthor() {
    setAuthor((el.author.value || '').trim());
    try { env.localStorage.setItem('gitwire-chat.author', author); }
    catch (err) { /* 프라이빗 모드 등 — 이름 기억은 부가 기능이다 */ }
  }

  function startReply(msg) {
    replyTo = msg.id;
    dom.setText(el.replyLabel, '↩ ' + msg.author + ': ' + msg.text.slice(0, 40));
    dom.show(el.replyChip);
    if (el.text && el.text.focus) { el.text.focus(); }
  }

  function cancelReply() {
    replyTo = null;
    dom.hide(el.replyChip);
  }

  function send() {
    var text = (el.text.value || '').trim();
    if (!text || !roomId) { return; }
    var who = (el.author.value || '').trim() || author;
    var draft = {
      id: draftId(),
      roomId: roomId,
      author: who,
      text: text,
      ts: new Date().toISOString(),
      sender: '',
      kind: 'msg',
      reply_to: replyTo,
      unknown: false,
      /* ⚠️ 여기가 `mine` 을 손으로 세우는 **유일한** 자리다. 그리고 그럴 자격이
         있다 — 이건 아직 봉투가 없는 낙관적 항목(`~pending/…`)이고, 방금 이
         입력칸에서 나왔으니 정의상 내 것이다. 봉투가 도착하는 순간(`draft:settle`)
         부터는 서버가 봉투를 보고 판정한 값이 이 자리를 대신한다. */
      mine: true,
      pending: true,
      failed: false
    };
    el.text.value = '';
    autoGrow();
    cancelReply();
    status.set('');
    bus.emit('draft:add', { draft: draft });
    return post(draft);
  }

  /* 최초 전송과 재시도가 **같은 경로**를 쓴다. */
  function post(draft) {
    var target = draft.roomId;
    return api('/api/rooms/' + encodeURIComponent(target) + '/messages', {
      method: 'POST',
      body: { text: draft.text, author: draft.author, reply_to: draft.reply_to }
    }).then(function (data) {
      if (roomId !== target) { return; }
      bus.emit('draft:settle', { tempId: draft.id, message: data.message });
    })['catch'](function (err) {
      if (roomId !== target) { return; }
      /* ⚠️ 입력칸으로 되돌리지 않는다. 글은 이미 저 말풍선 안에 있다 —
         되돌리면 같은 글이 두 곳에 생긴다. */
      bus.emit('draft:fail', { tempId: draft.id, error: errText(err) });
    });
  }

  function mount() {
    dom.on(el.form, 'submit', function (e) {
      if (e.preventDefault) { e.preventDefault(); }
      send();
    });
    dom.on(el.text, 'keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        if (e.preventDefault) { e.preventDefault(); }
        send();
      }
    });
    dom.on(el.text, 'input', autoGrow);
    dom.on(el.author, 'change', rememberAuthor);
    dom.on(el.replyCancel, 'click', cancelReply);

    bus.on('room:switch', function (e) { roomId = e.id; cancelReply(); });
    bus.on('reply:to', function (e) { startReply(e.message); });
    bus.on('draft:retry', function (e) { post(e.draft); });

    var stored = null;
    try { stored = env.localStorage.getItem('gitwire-chat.author'); }
    catch (err) { stored = null; }
    var body = dom.doc.body;
    setAuthor(stored || (body.dataset ? body.dataset.defaultAuthor : '') ||
      (body.getAttribute ? body.getAttribute('data-default-author') : '') || '');
    if (el.author) { el.author.value = author; }
  }

  return { mount: mount, send: send, author: function () { return author; } };
}

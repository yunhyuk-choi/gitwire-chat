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
 *
 * ⭐ IME(한글·일본어·중국어) 조합 중의 Enter — **조합이 끝난 뒤에 보낸다.**
 *
 * 예전에는 `keydown` 에서 조합 여부를 보지 않고 곧바로 보냈다. 맥 한글 IME 는
 * 그 Enter 로 **조합을 확정**하는데 `keydown` 은 확정보다 **먼저** 오므로, 그때
 * 읽은 `value` 에는 마지막 음절이 아직 없다. 결과가 사용자가 본 두 증상이다 —
 * 마지막 글자가 빠진 채로 전송되고(받는 쪽은 그걸 정확히 받는다), `preventDefault`
 * 로 막힌 그 글자가 **입력칸에 남는다.** 한 원인, 두 증상.
 *
 * 고친 방식은 **시간이 아니라 이벤트**다. 조합 중 Enter 는 보내지 않고 "조합이
 * 확정되면 보낸다"는 표시만 남기고, `compositionend` 가 오면 그때 보낸다.
 * 디바운스(N ms 기다리기)는 "IME 가 그 안에 끝난다"는 보장 없는 가정에 기대고,
 * 어긋나는 순간 **간헐적으로 조용히** 잘려 나간다 — 지금 버그보다 나쁘다.
 *
 * 두 가지가 이 구현의 핵심 위험이고 각각 막아 두었다:
 *   1. `compositionend` 는 **평소 타이핑에서 음절마다** 온다. 그래서 표시는
 *      **Enter 가 세운 것만** 유효하고, 다른 입력이 들어오면 즉시 지운다.
 *      (안 지우면 다음 음절이 확정될 때 엉뚱하게 전송된다.)
 *   2. `compositionend` 없이 조합이 끝나는 예외 환경 — 안전 타이머가 표시를
 *      **지운다(취소 전용).** 절대 그 타이머로 보내지 않는다. 그래야 최악이
 *      "안 보내짐"(사용자가 Enter 를 다시 누른다)이지 "잘려서 보내짐"(모르고
 *      지나간다)이 아니다. 이 비대칭이 설계 의도다.
 *
 * 영문 입력은 조합이 없어 이 경로를 아예 타지 않는다 — 기존대로 즉시 전송이다.
 */

import { errText } from './dom.js';

/* 조합 확정 신호(`compositionend`)를 기다리는 한도(ms). **취소 전용**이다 —
   만료되면 전송 표시만 지운다. 넉넉히 잡는 이유: 짧으면 느린 기기에서 정상
   조합을 취소해 "가끔 안 보내진다"가 된다. 지나도 잃는 것은 Enter 한 번이다. */
var COMMIT_WAIT = 3000;

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

  /* ---- IME 조합 상태. 이 세 값이 위 규율의 전부다. ------------------- */
  var composing = false;      /* 지금 조합 중인가 (compositionstart~end) */
  var sendOnCommit = false;   /* **Enter 가 세운** 표시. 조합이 확정되면 보낸다 */
  var waitId = null;          /* 취소 전용 안전 타이머 */

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

  /* ---------------------------------------------- IME 조합 중의 Enter */

  /* 이 키 이벤트가 **조합 중**에 온 것인가.
     표준은 `isComposing` 이고, 그것을 주지 않는 브라우저는 IME 가 삼킨 키를
     `keyCode 229` 로 보낸다. 둘 다 본다. 우리가 직접 센 `composing` 도 함께
     보는 이유: `compositionend` 가 아직 안 온 시점의 판정을 한 곳으로 모은다. */
  function isComposing(e) {
    return composing || e.isComposing === true || e.keyCode === 229;
  }

  function cancelWait() {
    if (waitId !== null && env.win && env.win.clearTimeout) {
      env.win.clearTimeout(waitId);
    }
    waitId = null;
  }

  /* 표시를 지운다. **전송 의도를 버리는 유일한 문**이다. */
  function disarm() {
    sendOnCommit = false;
    cancelWait();
  }

  /* 조합이 확정되면 보내라고 표시한다 + 취소 전용 안전 타이머를 건다. */
  function armForCommit() {
    sendOnCommit = true;
    cancelWait();
    if (env.win && env.win.setTimeout) {
      waitId = env.win.setTimeout(function () {
        /* ⚠️ 여기서 **보내지 않는다.** 조합이 확정됐다는 신호를 못 받았으므로
           `value` 가 완전한지 알 수 없고, 모르면 보내지 않는 쪽이 안전하다. */
        waitId = null;
        sendOnCommit = false;
      }, COMMIT_WAIT);
    }
  }

  /* 확정 뒤 **한 틱** 양보하고 보낸다.
     ⚠️ 이건 "IME 가 언제 끝나나"를 시간으로 추측하는 것이 아니다 — 끝났다는
     사실은 `compositionend` 가 이미 알려 줬다. 다만 확정된 글자를 `value` 에
     **넣는 시점**이 엔진마다 compositionend 앞/뒤로 갈린다(명세가 둘 다 허용하고,
     사파리가 뒤에 넣는 쪽으로 알려져 있다). 뒤에 넣는 엔진에서 여기서 바로 읽으면
     우리가 고치려는 그 잘림이 그대로 남는다. 한 틱 뒤면 어느 순서든 완전하다. */
  function sendAfterCommit() {
    if (env.win && env.win.setTimeout) {
      env.win.setTimeout(function () { send(); }, 0);
      return;
    }
    send();
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
    /* 어떤 경로로든 실제로 보내는 순간, 기다리던 전송 의도는 소진됐다.
       (조합 확정 경로는 이미 지웠고, 버튼·폼 경로에서 표시가 남아 있을 수 있다.) */
    disarm();
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
    /* 폼 경로(보내기 버튼)에는 IME 가드를 두지 않는다 — **필요가 없다.**
       버튼을 누르려면 포인터가 입력칸을 떠나고, 그 blur 가 조합을 먼저 확정시킨다
       (그 시점에 `compositionend` 가 오고 `value` 가 완전해진다). 여기에 조합
       가드를 달면 오히려 "버튼을 눌렀는데 안 보내진다"가 된다.
       표시 이름 칸(단일 행 input)의 Enter 로 인한 암묵적 제출도 문제가 아니다 —
       조합 중 Enter 는 IME 가 삼켜 기본 동작(제출)이 아예 일어나지 않는다. */
    dom.on(el.form, 'submit', function (e) {
      if (e.preventDefault) { e.preventDefault(); }
      send();
    });
    dom.on(el.text, 'compositionstart', function () {
      composing = true;
      /* 새 조합이 시작됐다 = 이전 Enter 의 의도는 이미 처리됐거나 유효하지 않다. */
      disarm();
    });
    dom.on(el.text, 'compositionend', function () {
      composing = false;
      /* ⭐ 평소 타이핑에서도 음절마다 여기로 온다. **Enter 가 세운 표시**가
         없으면 아무 일도 하지 않는다 — 이 한 줄이 오발송을 막는다. */
      if (!sendOnCommit) { return; }
      disarm();
      sendAfterCommit();
    });
    dom.on(el.text, 'keydown', function (e) {
      var enter = e.key === 'Enter' && !e.shiftKey;
      if (enter && isComposing(e)) {
        /* ⚠️ `preventDefault` 를 **하지 않는다.** 이 Enter 는 IME 가 조합을
           확정하는 데 쓴다. 막으면 확정되지 않고 그 글자가 입력칸에 남는다
           (사용자가 본 바로 그 증상). 우리는 확정 신호를 기다린다. */
        armForCommit();
        return;
      }
      /* 조합 중 Enter 가 아닌 입력이 왔다 = 방금의 전송 의도는 무효다.
         지우지 않으면 **다음 음절이 확정될 때 엉뚱하게 전송된다.** */
      disarm();
      if (enter) {
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

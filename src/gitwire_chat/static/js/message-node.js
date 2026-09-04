/*
 * 메시지 **한 건**의 DOM. 이 파일이 메시지 노드를 만드는 유일한 곳이다.
 *
 * 상태를 갖지 않는다 — 타임라인이 모델을 소유하고, 여기는 "모델 하나 → 노드 하나"
 * 변환만 한다. 그래서 "무엇이 노드를 다시 만들 수 있나"가 한 함수로 좁혀지고,
 * 리렌더 국소성이 감시가 아니라 **구조**에서 나온다.
 *
 * 상태 변화(보내는 중 / 보내지 못했다)는 노드를 다시 만들지 않고 `paintState` 가
 * 같은 노드 위에 덧입힌다.
 *
 * ⚠️ 여기 두 상태는 **"앱이 이 말을 받았나"** 까지만 말한다 (POST 한 번).
 * 그 뒤 "내 기기를 떠나 상대에게 갔나"는 방 단위 사실이라 `outbox.js` 가 그린다.
 * 전송 응답이 원격 push 를 기다리지 않게 된 순간부터 이 둘은 다른 사건이다 —
 * 한 자리에 섞으면 "전송 실패"가 두 가지 전혀 다른 사고를 가리키게 된다.
 */

import { timeLabel } from './dom.js';

export function buildMessage(dom, msg, hooks) {
  var wrap = dom.make('article', 'msg');
  wrap.dataset.id = msg.id;
  wrap.setAttribute('data-id', msg.id);

  var head = dom.make('div', 'msg-head');
  head.appendChild(dom.make('span', 'author', msg.author));
  head.appendChild(dom.make('time', 'ts', timeLabel(msg.ts)));
  wrap.appendChild(head);

  if (msg.reply_to) {
    var quote = dom.make('div', 'quote');
    var target = hooks.lookup ? hooks.lookup(msg.reply_to) : null;
    dom.setText(quote, '↩ ' + (target ? target.author + ': ' + target.text : '이전 메시지'));
    wrap.appendChild(quote);
  }

  /* textContent 만 쓴다 — innerHTML 은 이 앱 어디에도 없다. */
  wrap.appendChild(dom.make('div', 'body', msg.text));

  var actions = dom.make('div', 'msg-actions');
  var reply = dom.make('button', 'link', '답장');
  reply.setAttribute('type', 'button');
  reply.addEventListener('click', function () { hooks.onReply(msg); });
  actions.appendChild(reply);
  wrap.appendChild(actions);

  /* 전송 상태가 앉을 자리. 평소엔 비어 숨어 있다 — 낙관적 전송만 여기에 쓴다.
     노드를 다시 만들지 않고 **이 자리만** 갈아 끼우려고 참조를 들고 있는다. */
  var slot = dom.make('div', 'msg-state');
  wrap.appendChild(slot);
  wrap.stateSlot = slot;
  paintState(dom, wrap, msg, hooks);
  return wrap;
}

/* 전송 상태를 노드에 덧입힌다 — 노드를 새로 만들지 않는다. */
export function paintState(dom, node, msg, hooks) {
  if (!node) { return; }
  var cls = 'msg';
  if (msg.mine) { cls += ' mine'; }
  if (msg.unknown) { cls += ' unknown'; }
  if (msg.pending) { cls += ' pending'; }
  if (msg.failed) { cls += ' failed'; }
  node.className = cls;

  var slot = node.stateSlot;
  if (!slot) { return; }
  slot.replaceChildren();
  if (msg.failed) {
    slot.appendChild(dom.make('span', 'state-text', '보내지 못했다'));
    var again = dom.make('button', 'link retry', '재시도');
    again.setAttribute('type', 'button');
    again.addEventListener('click', function () { hooks.onRetry(msg); });
    slot.appendChild(again);
    slot.hidden = false;
  } else if (msg.pending) {
    slot.appendChild(dom.make('span', 'state-text', '보내는 중…'));
    slot.hidden = false;
  } else {
    slot.hidden = true;
  }
}

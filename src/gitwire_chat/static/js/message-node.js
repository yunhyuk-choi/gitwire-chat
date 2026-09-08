/*
 * 메시지 **한 건**의 DOM. 이 파일이 메시지 노드를 만드는 유일한 곳이다.
 *
 * ⭐ **레이아웃 테마가 갈리는 자리도 여기 하나다.** 이름 → 만드는 함수의 표
 * (`STRUCTURES`)이고, 다른 어디에도 구조 분기가 없다. 그래서 배치를 하나 더
 * 붙이는 일이 "이 표에 한 줄"이 되고, 무엇이 깨지면 어느 구조 탓인지 바로 좁혀진다.
 * 모르는 이름은 **조용히 기본 구조**로 떨어진다 (저장값이 옛 이름일 수 있다).
 *
 * 상태를 갖지 않는다 — 타임라인이 모델을 소유하고, 여기는 "모델 하나 → 노드 하나"
 * 변환만 한다. 그래서 "무엇이 노드를 다시 만들 수 있나"가 한 함수로 좁혀지고,
 * 리렌더 국소성이 감시가 아니라 **구조**에서 나온다.
 *
 * 상태 변화(보내는 중 / 보내지 못했다)는 노드를 다시 만들지 않고 `paintState` 가
 * 같은 노드 위에 덧입힌다. **발신자 머리를 보일지**도 같은 규율이다 (`paintHead`) —
 * 묶기 규칙이 바뀌는 순간(과거를 불러와 묶음이 이어질 때)에도 노드를 다시 만들지
 * 않고 그 자리만 숨긴다. 머리를 보일지 **정하는** 것은 여기가 아니라 모델을
 * 소유한 타임라인이다 (이 파일은 상태를 갖지 않는다).
 *
 * ⚠️ 여기 두 상태는 **"앱이 이 말을 받았나"** 까지만 말한다 (POST 한 번).
 * 그 뒤 "내 기기를 떠나 상대에게 갔나"는 방 단위 사실이라 `outbox.js` 가 그린다.
 * 전송 응답이 원격 push 를 기다리지 않게 된 순간부터 이 둘은 다른 사건이다 —
 * 한 자리에 섞으면 "전송 실패"가 두 가지 전혀 다른 사고를 가리키게 된다.
 */

import { timeLabel, timeParts } from './dom.js';

/* 발신자 → 색 슬롯(1~6). 이름에서 계산하므로 **같은 사람은 언제나 같은 색**이고,
   서버에 색을 저장할 필요가 없다. 색은 이름 옆에 덧붙는 단서다 — 이름 자체가
   그대로 보이므로 색만으로 구분하게 만들지 않는다. */
export var SENDER_SLOTS = 6;

export function senderSlot(name) {
  var text = String(name == null ? '' : name);
  var hash = 0;
  for (var i = 0; i < text.length; i++) {
    hash = (hash * 31 + text.charCodeAt(i)) % 1000003;
  }
  return (hash % SENDER_SLOTS) + 1;
}

/* 이름 → 구조. 레이아웃 테마의 열쇠는 `theme.js` 의 `LAYOUTS` 와 같아야 한다
   (그 일치는 `tests/test_theme.py` 가 확인한다). */
export var STRUCTURES = { bubbles: buildBubble, log: buildLogRow, ide: buildIdeRow };

/* ⭐ **묶는 구조인가** — 같은 사람이 연달아 말하면 발신자 머리를 한 번만 찍는가.
   구조가 정하는 성질이라 구조 표와 **같은 자리**에 둔다. 타임라인은 이름으로
   조회만 하고(구조 분기를 갖지 않고), 그래서 배치를 하나 더 붙일 때 손대는 곳이
   이 파일 하나로 유지된다. */
export var GROUPED = { ide: true };

export function grouped(layout) {
  return GROUPED[layout] === true;
}

/* 배치별 **항목 사이 여백(px)**. 가상화가 세로 좌표를 계산할 때 쓰는 값이라
   CSS 로는 표현할 수 없다 — 화면 밖 항목은 DOM 에 없고, 간격은 가상화가 더한다.
   그래서 구조가 아는 값을 여기 표에 두고 타임라인이 이름으로 조회한다.

   `ide` 만 작다: 한 묶음 안의 줄은 **붙어 있어야** 왼쪽 레일이 끊기지 않는다.
   묶음 **사이**의 간격은 머리가 붙는 줄의 위쪽 여백이 대신 벌려 준다(CSS). */
export var GAPS = { bubbles: 6, log: 6, ide: 2 };

export function itemGap(layout) {
  return GAPS[layout] === undefined ? GAPS.bubbles : GAPS[layout];
}

export function buildMessage(dom, msg, hooks, layout) {
  var build = STRUCTURES[layout] || STRUCTURES.bubbles;
  var wrap = build(dom, msg, hooks);
  /* 전송 상태는 구조와 무관하다 — 어느 구조든 같은 함수가 덧입힌다. */
  paintState(dom, wrap, msg, hooks);
  return wrap;
}

/* 말풍선 구조 (지금까지의 모습). 좌우 정렬은 CSS 의 `.msg.mine` 이 정한다. */
function buildBubble(dom, msg, hooks) {
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
  return wrap;
}

/* 줄 구조 (배치 `log`) — 시각 · 발신자 · 본문 3열.
 *
 * 말풍선과 **같은 조각들**을 쓴다(`.body`·`.quote`·`.msg-actions`·`.msg-state`).
 * 그래서 답장 인용·"보내는 중"·전송 실패·재시도가 배치와 무관하게 그대로 산다 —
 * 상태를 덧입히는 함수(`paintState`)도 하나로 유지된다.
 * 3열 배치·좁은 폭에서 2열로 접기·좁을 때 초 숨기기는 전부 CSS 가 한다.
 */
function buildLogRow(dom, msg, hooks) {
  var wrap = dom.make('article', 'msg');
  wrap.dataset.id = msg.id;
  wrap.setAttribute('data-id', msg.id);
  /* 발신자 색 슬롯. CSS 가 이 값으로 색을 고른다 (JS 는 색을 모른다 —
     색은 팔레트가 갖고, 팔레트가 바뀌면 이 슬롯의 색도 따라 바뀐다). */
  wrap.setAttribute('data-sender', String(senderSlot(msg.author)));

  var parts = timeParts(msg.ts);
  var when = dom.make('time', 'ts');
  when.appendChild(dom.make('span', 'hm', parts ? parts.head : ''));
  /* 초는 별도 조각 — 좁은 폭에서 CSS 가 이것만 숨긴다. */
  when.appendChild(dom.make('span', 'sec', parts ? parts.sec : ''));
  wrap.appendChild(when);

  wrap.appendChild(dom.make('span', 'author', msg.author));

  var line = dom.make('div', 'line');
  if (msg.reply_to) {
    var quote = dom.make('div', 'quote');
    var target = hooks.lookup ? hooks.lookup(msg.reply_to) : null;
    dom.setText(quote, '↩ ' + (target ? target.author + ': ' + target.text : '이전 메시지'));
    line.appendChild(quote);
  }
  line.appendChild(dom.make('div', 'body', msg.text));

  var actions = dom.make('div', 'msg-actions');
  var reply = dom.make('button', 'link', '답장');
  reply.setAttribute('type', 'button');
  reply.addEventListener('click', function () { hooks.onReply(msg); });
  actions.appendChild(reply);
  line.appendChild(actions);

  var slot = dom.make('div', 'msg-state');
  line.appendChild(slot);
  wrap.stateSlot = slot;
  wrap.appendChild(line);
  return wrap;
}

/* 묶는 구조 (배치 `ide`) — 같은 사람이 연달아 말하면 **발신자 머리를 한 번만** 찍는다.
 *
 * ⭐ **묶음이 항목이 아니다.** 항목은 여전히 메시지 한 건이고, 머리는 그 중 첫
 * 메시지가 갖는 **속성**(`msg.head`)이다. 그래서 가상화는 손댈 것이 없다 —
 * 단위가 그대로 메시지다. 묶음을 가상화 단위로 만들면 두 곳에서 깨진다:
 * (1) 묶음 크기에 상한이 없어(한 사람이 연달아 200건) 3건을 보이려고 200건을
 * 그리게 되고 (2) 과거를 불러올 때 묶음이 합쳐져 항목 경계·인덱스·키가 흔들린다
 * (그 위에 '위로 불러오기' 커서가 얹혀 있다).
 *
 * 머리는 **DOM 에 항상 있고 숨을 뿐**이다 (`paintHead`). 묶기 규칙이 바뀌는 순간은
 * 실제로 있다 — 과거를 불러오면 기존 첫 메시지가 머리를 잃는다. 그때 노드를 다시
 * 만들면 `rebuiltInView` 가 깨지므로, **같은 노드 위에서 숨김만** 바꾼다.
 *
 * 말풍선·log 과 **같은 조각들**을 쓴다(`.msg-head`·`.quote`·`.body`·
 * `.msg-actions`·`.msg-state`) — 답장 인용·"보내는 중"·전송 실패·재시도가 배치와
 * 무관하게 그대로 살고, 상태를 덧입히는 함수도 하나로 유지된다.
 * 내 것은 **왼쪽 레일 색만** 바뀐다 (오른쪽으로 옮기지 않는다) — 그것도 CSS 다.
 */
function buildIdeRow(dom, msg, hooks) {
  var wrap = dom.make('article', 'msg');
  wrap.dataset.id = msg.id;
  wrap.setAttribute('data-id', msg.id);
  /* 발신자 색 슬롯 (log 과 같은 규칙 — JS 는 색을 모른다). */
  wrap.setAttribute('data-sender', String(senderSlot(msg.author)));

  /* 머리 — 발신자 + 시각. 묶음의 **첫 줄에만** 보인다. 시각은 분까지다:
     머리는 묶음당 한 번뿐이라 초를 실을 이유가 없다. */
  var head = dom.make('div', 'msg-head');
  head.appendChild(dom.make('span', 'author', msg.author));
  head.appendChild(dom.make('time', 'ts', timeLabel(msg.ts)));
  wrap.appendChild(head);
  /* ⭐ 이 칸을 **가진 구조가 곧 묶는 구조**다. 참조를 들고 있어야 나중에 이
     자리만 숨기고 노드는 그대로 쓸 수 있다 (`paintHead`). */
  wrap.headSlot = head;
  paintHead(wrap, msg);

  var line = dom.make('div', 'line');
  if (msg.reply_to) {
    var quote = dom.make('div', 'quote');
    var target = hooks.lookup ? hooks.lookup(msg.reply_to) : null;
    dom.setText(quote, '↩ ' + (target ? target.author + ': ' + target.text : '이전 메시지'));
    line.appendChild(quote);
  }
  line.appendChild(dom.make('div', 'body', msg.text));

  var actions = dom.make('div', 'msg-actions');
  var reply = dom.make('button', 'link', '답장');
  reply.setAttribute('type', 'button');
  reply.addEventListener('click', function () { hooks.onReply(msg); });
  actions.appendChild(reply);
  line.appendChild(actions);

  var slot = dom.make('div', 'msg-state');
  line.appendChild(slot);
  wrap.stateSlot = slot;
  wrap.appendChild(line);
  return wrap;
}

/* 머리를 보이거나 숨긴다 — **노드를 새로 만들지 않는다.**
 *
 * 돌려주는 값은 "바뀌었나"다. 바뀌었으면 그 항목의 **높이가 달라졌다**는 뜻이고,
 * 그 하나만 다시 재면 된다 (그 판단·재측정은 모델을 소유한 타임라인이 한다).
 *
 * 묶지 않는 구조(말풍선·log)는 `headSlot` 이 없어 **아무 일도 하지 않는다** —
 * 구조 분기가 타임라인으로 새지 않는 지점이 정확히 여기다. */
export function paintHead(node, msg) {
  if (!node || !node.headSlot) { return false; }
  /* 모델이 정하지 않았으면(다른 배치·옛 모델) 보인다 — 머리가 사라지는 쪽으로
     조용히 기울지 않는다. */
  var want = msg.head !== false;
  if (node.headSlot.hidden === !want) { return false; }
  node.headSlot.hidden = !want;
  return true;
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

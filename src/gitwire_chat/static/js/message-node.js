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
 * ⭐ **읽음 카운트**(남이 안 읽은 수)도 같은 규율이다 (`paintReads`) — 그 숫자는
 * *시간에 따라 변하는 파생값*이라(다른 사람의 커서가 움직이면 아래 메시지 전부가
 * 함께 줄어든다) 노드를 다시 만들어 그리면 `rebuiltInView` 가 곧바로 깨진다.
 * 그래서 "보내는 중/실패"와 똑같이 **같은 노드 위에 덧입힌다.**
 *
 * ⚠️ 그리고 이 자리는 **자리를 차지하지 않는다**(CSS 절대 위치 + 미리 비워 둔 오른쪽
 * 여백). 숫자가 붙거나 떨어질 때 높이가 바뀌면 가상 스크롤이 그 항목을 매번 다시
 * 재야 하고, 카운트는 자주 바뀌므로 그 재측정이 상시 비용이 된다. 반대로 '여기부터
 * 새 메시지' 구분선(`paintNewFrom`)은 **방을 열 때 한 항목에 한 번**만 붙으므로
 * 자리를 차지해도 되고, 그때는 머리와 같은 방식으로 그 항목만 다시 잰다.
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
export var STRUCTURES = {
  bubbles: buildBubble, log: buildLogRow, ide: buildIdeRow, tty: buildTtyRow
};

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
   묶음 **사이**의 간격은 머리가 붙는 줄의 위쪽 여백이 대신 벌려 준다(CSS).

   `tty` 는 **0** 이다 — 터미널은 줄 사이에 간격이 없다. 간격을 주는 순간 그게
   "항목이 카드다"라는 신호가 되고, 이 배치가 버리려는 것이 정확히 그것이다. */
export var GAPS = { bubbles: 6, log: 6, ide: 2, tty: 0 };

export function itemGap(layout) {
  return GAPS[layout] === undefined ? GAPS.bubbles : GAPS[layout];
}

/* 카운트가 커도 뱃지가 줄을 밀지 않게 하는 상한. 사람 수라 실제로는 작다. */
export var MAX_READ_COUNT = 99;

export function buildMessage(dom, msg, hooks, layout) {
  var build = STRUCTURES[layout] || STRUCTURES.bubbles;
  var wrap = build(dom, msg, hooks);
  /* 읽음 자리·구분선은 **구조와 무관하다** — 어느 배치든 같은 두 조각을 단다.
     그래서 배치를 하나 더 붙일 때 읽음 표시를 다시 배선하지 않는다. */
  readSlots(dom, wrap);
  /* 전송 상태는 구조와 무관하다 — 어느 구조든 같은 함수가 덧입힌다. */
  paintState(dom, wrap, msg, hooks);
  /* ⚠️ 읽음 카운트는 여기서 그리지 않는다. 한때 `paintReads(wrap, msg.reads)` 가
     있었는데 **서버는 `reads` 를 싣지 않는다** — 그 숫자는 남의 커서가 움직이면
     한꺼번에 바뀌는 파생값이라 메시지에 담을 수 없다(`app.py` 의 `/reads` 도크).
     그래서 그 줄은 언제나 `undefined` 를 그렸고, "서버가 reads 를 안 채운다"는
     오진의 출처가 됐다. 카운트를 덧입히는 곳은 창을 아는 `timeline.js` 한 곳이다. */
  paintNewFrom(dom, wrap, msg.newFrom === true);
  return wrap;
}

/* 읽음 카운트 자리를 노드에 만든다 (평소엔 숨음). 참조를 들고 있는 이유는
   `paintState` 와 같다 — 나중에 **이 자리만** 갈아 끼우고 노드는 그대로 쓰기
   위해서다. **마지막 자식**으로 붙인다: 절대 위치라 순서가 화면에 영향이 없고,
   구조의 앞부분(각 배치가 정한 조각 순서)을 건드리지 않는다. */
function readSlots(dom, wrap) {
  var reads = dom.make('span', 'msg-reads');
  reads.hidden = true;
  /* 스크린 리더에는 숫자만으로는 뜻이 통하지 않는다 — 라벨을 붙인다. */
  reads.setAttribute('title', '이 사람들이 아직 안 읽었다');
  wrap.appendChild(reads);
  wrap.readsSlot = reads;
}

/* 남이 안 읽은 수를 노드에 덧입힌다 — **노드를 새로 만들지 않는다.**

   돌려주는 값은 "바뀌었나"다. 높이는 바뀌지 않으므로(절대 위치) 재측정은 필요
   없다 — 그래서 카운트가 아무리 자주 바뀌어도 가상 스크롤이 흔들리지 않는다. */
export function paintReads(node, count) {
  if (!node || !node.readsSlot) { return false; }
  var n = typeof count === 'number' && count > 0 ? count : 0;
  var text = n === 0 ? '' : (n > MAX_READ_COUNT ? MAX_READ_COUNT + '+' : String(n));
  if (node.readsSlot.textContent === text && node.readsSlot.hidden === (text === '')) {
    return false;
  }
  node.readsSlot.textContent = text;
  node.readsSlot.hidden = text === '';
  return true;
}

/* '여기부터 새 메시지' 구분선을 켜고 끈다 — **메시지 노드는 그대로 쓴다.**
   (다시 만드는 것은 그 안의 작은 줄 하나이고, 메시지 노드가 아니다 —
   `rebuiltInView` 는 메시지 노드를 센다.)

   ⭐ 카운트와 달리 **필요할 때만 만든다.** 이 줄이 붙는 항목은 방을 열 때 **하나**
   뿐이라, 모든 메시지에 숨은 줄을 하나씩 달아 두면 그 방의 메시지 수만큼 헛노드가
   생긴다. 게다가 이 줄은 각 배치가 정한 조각 순서의 **맨 앞**에 와야 해서, 상시
   자식으로 두면 구조 계약(`.ts`·`.author`·`.line` …)이 모두 한 칸씩 밀린다.

   돌려주는 값은 "바뀌었나"이고, 바뀌었으면 그 항목의 **높이가 달라졌다**는 뜻이다
   (구분선은 자리를 차지한다). 그 하나만 다시 재는 판단은 모델을 소유한 타임라인이
   한다 — 머리(`paintHead`)와 같은 규율이다. */
export function paintNewFrom(dom, node, on) {
  if (!node) { return false; }
  var want = on === true;
  var have = !!node.newMarkSlot;
  if (have === want) { return false; }
  if (!want) {
    if (node.newMarkSlot.parentNode === node) { node.removeChild(node.newMarkSlot); }
    node.newMarkSlot = null;
    return true;
  }
  var mark = dom.make('div', 'new-mark');
  mark.appendChild(dom.make('span', 'new-mark-label', '여기부터 새 메시지'));
  /* 맨 위에 둔다 — 이 메시지가 '새 메시지 첫 줄'이라는 뜻이다. */
  node.insertBefore(mark, node.firstChild);
  node.newMarkSlot = mark;
  return true;
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

/* 프롬프트 줄 구조 (배치 `tty`) — **터미널 한 줄**이다.
 *
 * ⭐ 이 배치가 `log` 과 다른 점은 색이 아니라 **버리는 것**이다. 카드(둥근 모서리·
 * 바닥·테두리·폭 제한)와 상시 노출 버튼을 버린다 — 사용자가 "터미널스럽지 않다"고
 * 말한 것의 실체가 그 둘이었다. 버리는 일 자체는 CSS 가 하고(그래서 이 함수는
 * `log` 과 거의 같다), 여기서 다른 것은 조각이 하나 더 있는 것뿐이다:
 *
 *   시각 · `▸` · 발신자 · 본문   ← 한 줄로 흐른다 (격자가 아니다)
 *
 * ⚠️ `▸` 는 **꾸밈**이라 `aria-hidden` 을 붙인다. 스크린 리더가 메시지마다
 * "검은 오른쪽 삼각형"을 읽으면 프롬프트 흉내가 낭독을 망친다. CSS 의
 * `content:` 로 넣으면 그 제어를 못 하므로(가상 요소에는 `aria-hidden` 이 없다)
 * 굳이 진짜 요소로 만든다 — 이 한 가지가 `log` 을 재사용하지 않는 이유다.
 *
 * 말풍선·log·ide 과 **같은 조각들**을 쓴다(`.body`·`.quote`·`.msg-actions`·
 * `.msg-state`). 그래서 답장 인용·"보내는 중"·전송 실패·재시도가 배치와 무관하게
 * 그대로 살고, 상태를 덧입히는 함수도 하나로 유지된다.
 *
 * ⭐ **답장 버튼은 지우지 않는다.** 터미널에 버튼이 없다는 것은 "상시 보이지
 * 않는다"는 뜻이고, "도달할 수 없다"는 뜻이 아니다. 그래서 DOM·탭 순서·스크린
 * 리더에는 그대로 있고, **평소 안 보이게 하는 일만** CSS 가 한다 (호버·초점에
 * 드러나고, 호버가 없는 기기에서는 상시 보인다 — style.css 의 그 구역 도크).
 * ⚠️ 그 감추기는 **자리를 비우지 않는다**(투명하게 만든다) — 드러날 때 줄 높이가
 * 바뀌면 가상 스크롤이 그 항목을 다시 재야 하고, 호버는 매우 잦다.
 */
function buildTtyRow(dom, msg, hooks) {
  var wrap = dom.make('article', 'msg');
  wrap.dataset.id = msg.id;
  wrap.setAttribute('data-id', msg.id);
  /* 발신자 색 슬롯 (log·ide 과 같은 규칙 — JS 는 색을 모른다. 시안의 단색 초록은
     사람이 늘면 이름만으로 훑게 되므로, 팔레트가 가진 6슬롯을 그대로 쓴다). */
  wrap.setAttribute('data-sender', String(senderSlot(msg.author)));

  var parts = timeParts(msg.ts);
  var when = dom.make('time', 'ts');
  when.appendChild(dom.make('span', 'hm', parts ? parts.head : ''));
  /* 초는 별도 조각 — 이 배치에서는 CSS 가 늘 숨긴다(프롬프트 줄은 짧아야 한다).
     조각을 없애지 않는 이유는 `log` 과 같은 구조를 유지하는 값이 더 크기
     때문이다 — 좁은 폭 규칙도, 읽음·구분선 배선도 그 덕에 그대로 쓴다. */
  when.appendChild(dom.make('span', 'sec', parts ? parts.sec : ''));
  wrap.appendChild(when);

  /* 프롬프트 표식. 뜻은 없고 모양만 있다 — 그래서 낭독에서 뺀다. */
  var mark = dom.make('span', 'prompt', '▸');
  mark.setAttribute('aria-hidden', 'true');
  wrap.appendChild(mark);

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

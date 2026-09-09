/*
 * 타임라인 — `#timeline` · `#messages` · `#older-sentinel` · `#jump-latest` ·
 * `#sticky-head` 를 **소유한다.** 대화 모델(무엇이 있고 어떤 순서인가)도 여기
 * 것이고, 다른 어떤 모듈도 이 노드들을 만지지 않는다.
 *
 * ⭐ 그래서 "타임라인을 통째로 다시 그리지 않는다"가 **구조에서 나온다.** 예전에는
 * 모든 화면 조각이 한 스코프에서 같은 상태를 공유해, 그 성질을 전역 규율 +
 * `rebuiltInView` 카운터로 *감시*해야 했다. 지금 카운터는 규율을 지키는 수단이
 * 아니라 **구조가 지켜지는지 확인하는 회귀 감지기**다.
 *
 * 지키는 것:
 *   1. `innerHTML` 을 쓰지 않는다. 텍스트는 전부 `textContent`(`dom.setText`).
 *   2. **창 안에 있는** 노드는 다시 만들지 않는다. 화면 밖으로 나간 노드를
 *      걷어내는 것은 가상화의 정상 동작이라 카운터를 나눈다:
 *        · `stats.recycled`      창 밖으로 나가 걷어낸 수 (0 이 아니어도 된다)
 *        · `stats.rebuiltInView` **창 안에 있었는데 다시 만든 수** ← 항상 0
 *   3. 타임라인을 비우는 곳은 **방을 바꿀 때 한 곳**뿐이다.
 *
 * 가상 스크롤이 안 되면 **느린 대체 렌더로 계속 가지 않는다.** 그건 성능 때문에
 * 붙인 기능이 조용히 빠진 채로 앱이 돌게 만든다 — 대신 이 영역에 결함을 그린다.
 *
 * ⭐ **연속 발화 묶기**(배치 `ide`)의 판단도 여기 것이다 — 모델을 소유한 곳이
 * 여기이기 때문이다. `message-node.js` 는 상태 없는 변환만 하므로 "직전에 누가
 * 말했나"를 알 수 없다. 여기서 각 메시지에 `head`(머리를 찍나)를 달아 주고, 구조는
 * 그 값만 읽는다. 그래서 가상화는 손댈 것이 없다: **단위가 그대로 메시지**다.
 */

import { errText, timeLabel } from './dom.js';
/* 읽음 커서가 받을 수 있는 ID 의 판정. **원천이 하나여야 한다** — 여기서 다시
   `indexOf('~')` 같은 것을 세면 임시 ID 규약이 바뀌는 날 조용히 어긋난다. */
import { isMessageId } from './reads.js';
import {
  buildMessage, paintState, paintHead, paintReads, paintNewFrom,
  senderSlot, grouped, itemGap
} from './message-node.js';

/* 처음 그릴 때 쓰는 높이 추정치(px). 실측되면 바로 대체된다. */
var ESTIMATED_HEIGHT = 64;
/* 읽던 자리를 남기는 칸 (레이아웃 전환은 새로고침이라 저장소를 거쳐야 한다). */
var ANCHOR_KEY = 'gitwire-chat.anchor';
/* 화면 밖에 여유로 더 그리는 개수. 스크롤 시 빈칸이 보이지 않게. */
var OVERSCAN = 6;

/* ⭐ 연속 발화 묶기의 **시간 컷오프**(ms) — 같은 사람이라도 이보다 벌어지면 머리를
 * 다시 찍는다.
 *
 * 왜 필요한가: 없으면 **어제 마지막 말과 오늘 첫 말이 한 묶음**이 된다. 머리를 한
 * 번만 찍는다는 것은 "그 시각 하나가 묶음 전체를 대표한다"는 말이고, 대표할 수
 * 없을 만큼 벌어지면 그 표기가 거짓이 된다.
 *
 * 왜 10분인가:
 *   · 이 앱의 왕복은 실시간 타이핑이 아니다 — 원격 git 을 폴링해서(기본 15초)
 *     받는다. 한 사람이 이어 말하는 사이도 수십 초에서 수 분으로 벌어진다.
 *     Slack 이 쓰는 5분을 그대로 가져오면 **한 호흡의 발화가 중간에서 갈린다.**
 *   · 반대로 30분·1시간으로 늘리면 오전 묶음과 오후 묶음이 붙어, 머리에 찍힌 시각이
 *     아래 줄들이 실제로 말한 시간과 전혀 달라진다.
 *   · 10분은 사람이 "이어서 말하는 중"으로 읽는 상한에 가깝고, 세로 밀도를 버는
 *     이득(반복되는 이름·시각 제거)의 대부분이 그 안에서 나온다.
 *
 * ⚠️ 컷오프만으로 **날 경계**가 막히지는 않는다 — 23:59 와 00:01 은 2분 차이다.
 * 그래서 날이 다르면 간격과 무관하게 새 머리를 찍는다(머리의 시각 표기가 오늘/다른
 * 날에 따라 달라지므로, 한 묶음에 두 날이 섞이면 그 표기 자체가 거짓이 된다). */
export var GROUP_GAP_MS = 10 * 60 * 1000;

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate();
}

/* 이 메시지에 머리를 찍나. **직전 메시지 하나만** 본다 — 그래서 O(1) 이고, 모델이
   바뀔 때 다시 판정해야 하는 범위가 "직전이 바뀐 항목 하나"로 **유계**다. */
export function headVisible(msg, prev) {
  if (!msg) { return false; }
  if (!prev) { return true; }                          /* 대화의 시작 */
  if (prev.author !== msg.author) { return true; }     /* 다른 사람 */
  /* 내것/남의것이 갈리면 같은 이름이어도 새 머리다 (표시 이름은 겹칠 수 있다). */
  if (!!prev.mine !== !!msg.mine) { return true; }
  var before = new Date(prev.ts).getTime();
  var now = new Date(msg.ts).getTime();
  /* 시각을 못 읽으면 묶지 않는다 — 모르는 것을 "가깝다"로 취급하지 않는다. */
  if (isNaN(before) || isNaN(now)) { return true; }
  if (now - before < 0 || now - before > GROUP_GAP_MS) { return true; }
  return !sameDay(new Date(before), new Date(now));
}

export function createTimeline(env) {
  var dom = env.dom;
  var bus = env.bus;
  var api = env.api;
  var status = env.status;
  var win = env.win;

  var el = {
    timeline: dom.$('timeline'),
    messages: dom.$('messages'),
    olderSentinel: dom.$('older-sentinel'),
    olderNote: dom.$('older-note'),
    jumpLatest: dom.$('jump-latest'),
    /* 묶음 중간에서 창이 시작될 때 **누가 말했는지**를 화면 상단에 띄우는 오버레이.
       묶는 배치에서만 쓴다 (아래 `groups`). */
    stickyHead: dom.$('sticky-head'),
    stickyAuthor: dom.$('sticky-author'),
    stickyTs: dom.$('sticky-ts')
  };

  /* 이 페이지의 배치가 **묶는 구조인가.** 배치는 이 페이지가 사는 동안 바뀌지
     않으므로(바꾸면 새로고침) 한 번 조회해 둔다. 구조 분기가 아니라 조회다 —
     표는 `message-node.js` 가 갖고 있다. */
  var groups = grouped(env.layout);
  /* 항목 사이 간격(px). 구조가 아는 값이라 같은 표에서 온다 (CSS 로는 표현할 수
     없다 — 화면 밖 항목은 DOM 에 없고, 간격은 가상화가 더한다). */
  var itemGapPx = itemGap(env.layout);

  /* ---- 소유 상태. 이 모듈 밖에서 쓰는 곳이 없다. ---------------------- */
  var items = [];              /* 정렬된 메시지 모델 (id 오름차순 = 시간순) */
  var nodes = new Map();       /* id → 노드 (지금 창 안에 있는 것만) */
  var known = Object.create(null);   /* id → 모델 (답장 인용을 찾을 때) */
  var seen = new Set();
  var pendings = new Map();    /* 아직 서버 응답을 못 받은, 화면에만 있는 것들 */
  var lastWindow = new Set();
  var virtualizer = null;
  var olderObserver = null;
  var brokenNode = null;
  var rendering = false;
  var renderAgain = false;
  /* 머리가 붙거나 떨어진 항목의 id. 높이가 달라졌으니 **그 하나만** 다시 재야
     한다. 여기 담아 두고 창을 그릴 때 처리한다 — 그 시점에야 `data-index` 가
     맞다(과거를 불러오면 모든 인덱스가 밀린다). */
  var remeasure = new Set();

  var view = {
    roomId: null,
    oldest: null,
    hasMore: false,
    loadingOlder: false,
    loaded: false,
    atBottom: true,
    unseen: 0,
    broken: '',
    /* ⭐ '여기부터 새 메시지' 구분선을 붙일 항목. **방을 열 때 한 번 정하고
       그 방을 보는 동안 바꾸지 않는다** — 내가 읽는 즉시 서버의 '첫 안 읽음'은
       사라지지만, 구분선이 눈앞에서 사라지면 어디까지가 새 것이었는지 알 수 없다. */
    newFrom: null,
    /* 지금까지 **창 안에 들어온** 최대 메시지 ID. 읽음 커서의 근거다 —
       "탭이 열렸다"가 아니라 "그 메시지가 화면에 있었다"가 기준이다. */
    seenMax: ''
  };

  var stats = {
    created: 0, appended: 0, prepended: 0, duplicates: 0,
    recycled: 0, rebuiltInView: 0, measured: 0, cleared: 0,
    olderRequests: 0, anchored: 0, lastAnchor: 0, innerHTML: 0, restored: 0,
    /* 묶기 — 머리 판정이 **뒤집힌** 횟수 · 그래서 노드에 덧입힌 횟수 · 그래서
       높이를 다시 잰 항목 수. 과거를 불러와 묶음이 이어질 때 셋 다 1 이어야
       한다 (무효화가 유계라는 증거다). */
    headChanged: 0, headRepainted: 0, headRemeasured: 0, stickyShown: 0,
    /* 읽음 — 카운트를 **덧입힌** 횟수 · 구분선을 켠 횟수 · 창에 들어온 것을
       알린 횟수. 카운트가 바뀌어도 `rebuiltInView` 는 0 이어야 한다는 규율이
       여기 숫자들과 함께 확인된다. */
    readsPainted: 0, newFromPainted: 0, seenEmitted: 0
  };

  /* 읽음 모델은 **남의 것**이다 (`reads.js`). 우리는 계산을 부탁하고 그 결과를
     노드에 덧입힐 뿐이다. 그 모듈이 서지 못했으면 카운트가 0 이라 아무것도 그리지
     않는다 — 읽음 표시가 없는 것은 대화가 안 되는 것과 다른 급의 사건이다. */
  var NO_READS = { count: function () { return 0; }, firstUnread: function () { return null; } };

  function reads() {
    var mod = env.reads ? env.reads() : null;
    return mod && mod.count ? mod : NO_READS;
  }

  var hooks = {
    lookup: function (id) { return known[id] || null; },
    onReply: function (msg) { bus.emit('reply:to', { message: msg }); },
    onRetry: function (msg) { retry(msg); }
  };

  /* -------------------------------------------------------- 모델 */

  /* 정렬 위치(이진 탐색). 메시지 ID 는 고정폭 타임스탬프로 시작하므로
     사전식 = 시간순이다. 모델이 정렬돼 있으면 DOM 순서를 걱정할 필요가 없다. */
  function insertionIndex(id) {
    var lo = 0;
    var hi = items.length;
    while (lo < hi) {
      var mid = (lo + hi) >> 1;
      if (items[mid].id <= id) { lo = mid + 1; } else { hi = mid; }
    }
    return lo;
  }

  function insertItem(msg) {
    if (!msg || !msg.id) { return false; }
    if (seen.has(msg.id)) { stats.duplicates += 1; return false; }
    seen.add(msg.id);
    known[msg.id] = msg;
    var at = insertionIndex(msg.id);
    items.splice(at, 0, msg);
    /* ⭐ 머리는 **이 메시지의 속성**이다 — 직전 메시지를 보고 지금 정한다.
       (묶음을 항목으로 만들지 않는 이유가 여기서 갚아진다: 새 메시지 하나가
       도착해도 계산은 그 항목 하나, 남에게 영향이 없다.) */
    msg.head = headVisible(msg, at > 0 ? items[at - 1] : null);
    /* **바로 뒤 항목의 '직전'이 바뀌었다.** 그 하나만 다시 판정한다 — 이것이
       무효화의 전부다(끝에 붙는 평상시에는 뒤가 없어 아무 일도 없다). */
    refreshHead(at + 1);
    /* 보류 항목의 임시 ID 는 '위로 불러오기' 커서가 될 수 없다 (서버가 모른다). */
    if (!msg.pending && (!view.oldest || msg.id < view.oldest)) {
      view.oldest = msg.id;
    }
    return true;
  }

  function removeItemAt(at) {
    items.splice(at, 1);
    /* 지운 자리의 **다음 항목**이 새 '직전'을 얻었다 — 그 하나만 다시 판정한다. */
    refreshHead(at);
  }

  /* 한 항목의 머리 판정을 다시 하고, 뒤집혔으면 **그 노드 위에 덧입힌다.**
     노드를 다시 만들지 않는다 (`rebuiltInView` 는 0 을 유지한다).

     높이는 여기서 재지 않는다 — 다음 `paintWindow` 가 창 안 항목을 재면서 그
     하나만 새 높이로 갱신한다. 여기서 재면 `data-index` 가 아직 낡아 있어
     (과거를 불러오면 모든 인덱스가 밀린다) **엉뚱한 항목의 크기를 덮어쓴다.** */
  function refreshHead(index) {
    var msg = items[index];
    if (!msg) { return false; }
    var want = headVisible(msg, index > 0 ? items[index - 1] : null);
    if (msg.head === want) { return false; }
    msg.head = want;
    stats.headChanged += 1;
    if (paintHead(nodes.get(msg.id), msg)) { stats.headRepainted += 1; }
    /* 창 밖이어도 담아 둔다 — 그 항목이 다시 창에 들어올 때(노드를 새로 만들 때)
       라이브러리가 들고 있는 **낡은 크기**를 그때 갈아 준다. */
    remeasure.add(msg.id);
    return true;
  }

  function makeNode(msg) {
    stats.created += 1;
    if (lastWindow.has(msg.id)) {
      /* 창 안에 있던 것을 다시 만들었다 = 리렌더 사고. 세어 두면 테스트가 잡는다. */
      stats.rebuiltInView += 1;
    }
    /* 레이아웃 이름은 조립소가 한 번 정해 준다 — 이 페이지가 사는 동안 안 바뀐다
       (바꾸면 새로고침이라, 전환 중에 구조가 섞이는 상태 자체가 없다). */
    return buildMessage(dom, msg, hooks, env.layout);
  }

  /* ------------------------------------------------- 가상 스크롤 */

  function virtualOptions() {
    var engine = env.virtual;
    return {
      count: items.length,
      getScrollElement: function () { return el.timeline; },
      estimateSize: function () { return ESTIMATED_HEIGHT; },
      getItemKey: function (index) { return items[index] ? items[index].id : index; },
      overscan: OVERSCAN,
      gap: itemGapPx,
      scrollToFn: engine.elementScroll,
      observeElementRect: engine.observeElementRect,
      observeElementOffset: engine.observeElementOffset,
      /* ⭐ 가변 높이: 추정치로 그린 뒤 **실제 높이를 재서** 반영한다.
         메시지 길이가 제각각이라 고정 높이 가정은 성립하지 않는다. */
      measureElement: function (element, entry, instance) {
        stats.measured += 1;
        return engine.measureElement(element, entry, instance);
      },
      onChange: function () { renderWindow(); }
    };
  }

  function ensureVirtualizer() {
    if (virtualizer || !el.timeline) { return virtualizer; }
    if (view.broken) { return null; }
    if (!env.virtual || !env.virtual.Virtualizer) { return null; }
    /* ⚠️ **생성자에서 터질 수 있다.** 실제로 그랬다 — 벤더 번들이 브라우저에 없는
       Node 전역을 참조해 `new Virtualizer(...)` 가 ReferenceError 를 던졌다.
       '라이브러리가 있나'만 보는 방어가 무력했던 이유가 이것이다: 라이브러리는
       **있었고**, 못 쓰는 것이었다. 있음이 아니라 **됨**을 본다. */
    try {
      virtualizer = new env.virtual.Virtualizer(virtualOptions());
      virtualizer._didMount();
      virtualizer._willUpdate();
    } catch (err) {
      virtualizer = null;
      markBroken('가상 스크롤 엔진을 시작하지 못했다 (' + errText(err) + ')');
      return null;
    }
    return virtualizer;
  }

  function totalHeight(v) {
    if (v) { return v.getTotalSize(); }
    return el.timeline ? el.timeline.scrollHeight : 0;
  }

  function syncVirtual() {
    var v = ensureVirtualizer();
    if (!v) { showBroken(); return; }
    v.setOptions(virtualOptions());
    v._willUpdate();
    renderWindow();
  }

  function renderWindow() {
    var v = virtualizer;
    if (!v || !el.messages) { return; }
    /* ⚠️ 재진입 금지. 그리는 도중 `measureElement` 가 크기 변화를 알리면
       라이브러리가 곧바로 onChange 를 다시 부른다. 그대로 두면 창을 반쯤 그린
       상태에서 또 그리기 시작해, 아직 만들지 않은 노드를 "창 안에 있었는데
       없다"로 오판한다. 한 번에 하나만 그리고, 도중 요청은 끝난 뒤 한 번 더. */
    if (rendering) { renderAgain = true; return; }
    rendering = true;
    try {
      paintWindow(v);
    } finally {
      rendering = false;
    }
    if (renderAgain) { renderAgain = false; renderWindow(); }
  }

  function paintWindow(v) {
    var visible = v.getVirtualItems();
    var keep = new Set();
    var reader = reads();
    /* 이번 창에서 **가장 아래까지** 보인 메시지. 읽음 커서의 근거다. */
    var maxSeen = '';
    for (var i = 0; i < visible.length; i++) {
      var vi = visible[i];
      var msg = items[vi.index];
      if (!msg) { continue; }
      keep.add(msg.id);
      var node = nodes.get(msg.id);
      if (!node) {
        node = makeNode(msg);
        nodes.set(msg.id, node);
        el.messages.appendChild(node);
        stats.appended += 1;
      }
      node.setAttribute('data-index', String(vi.index));
      /* ⭐ 줄무늬(홀짝)의 근거는 **모델 인덱스**다. CSS 의 `:nth-child` 로 하면
         가상 스크롤이 노드를 걷어내고 다시 붙일 때 DOM 순서가 모델 순서와
         달라져 **스크롤할 때마다 홀짝이 뒤집힌다.** 인덱스는 이미 여기 있으니
         추가 배관도 없다. (className 이 아니라 속성으로 찍는다 — 상태 클래스를
         paintState 가 통째로 다시 쓰기 때문이다.) */
      node.setAttribute('data-row', vi.index % 2 === 0 ? 'even' : 'odd');
      if (node.style) { node.style.transform = 'translateY(' + vi.start + 'px)'; }
      /* ⭐ 머리가 붙거나 떨어진 항목은 **높이가 달라졌다.** 라이브러리의 기본
         측정은 캐시가 있으면 그 값을 그대로 돌려주므로(갱신은 ResizeObserver 가
         알려 줄 때만 일어난다) 그 사실이 반영되지 않는다. 전체 캐시를 비우면
         (`measure()`) 창 안 **모든** 항목을 다시 재게 되어 "그 하나만"이라는
         성질이 사라진다 — 그래서 이 항목 하나만 갈아 준다. */
      /* ⭐ 읽음 카운트 — **시간에 따라 변하는 파생값**이라 노드를 다시 만들지
         않고 덧입힌다(`paintReads`). 자리를 차지하지 않으므로 높이도 그대로다. */
      if (paintReads(node, reader.count(msg))) { stats.readsPainted += 1; }
      /* '여기부터 새 메시지' — 그 한 항목만, 그 방을 보는 동안 한 번. 이건
         자리를 차지하므로 머리와 같은 방식으로 그 항목만 다시 잰다. */
      if (paintNewFrom(dom, node, view.newFrom === msg.id)) {
        stats.newFromPainted += 1;
        remeasure.add(msg.id);
      }
      /* ⭐ **낙관적 항목은 읽음 커서의 근거가 될 수 없다.** 보내는 중인 항목의
         임시 ID 는 정렬을 위해 실제 봉투 ID 보다 사전식 뒤가 되도록 만들었으므로
         (`composer.js`) 그냥 최대값을 취하면 그것이 채택된다. 실제로 그 값이
         서버·원격까지 올라가 커서를 굳혔고(단조 증가라 되돌릴 수 없다) 카운트가
         영구히 0 이 됐다. 그래서 **여기서** 걸러야 한다 — 받는 쪽에서만 막으면
         `view.seenMax` 가 임시 ID 로 굳어 그 뒤 실제 ID 를 아예 알리지 못한다
         (같은 함정의 다른 얼굴이다). */
      if (msg.id > maxSeen && isMessageId(msg.id)) { maxSeen = msg.id; }
      if (remeasure.has(msg.id)) {
        remeasure['delete'](msg.id);
        stats.headRemeasured += 1;
        v.resizeItem(vi.index, node.offsetHeight);
      }
      v.measureElement(node);          /* 가변 높이 실측 */
    }
    nodes.forEach(function (node, id) {
      if (keep.has(id)) { return; }
      /* 화면 밖 — 걷어낸다. 사고가 아니라 가상화의 정상 동작이다. */
      if (node.parentNode === el.messages) { el.messages.removeChild(node); }
      nodes['delete'](id);
      stats.recycled += 1;
    });
    if (el.messages.style) {
      el.messages.style.height = v.getTotalSize() + 'px';
    }
    lastWindow = keep;
    paintSticky(visible);
    announceSeen(maxSeen);
  }

  /* ⭐ **창 안에 무엇이 있었나**를 읽음 모델에 알린다 (여기가 그것을 아는 곳이다).
     단조 증가만 알린다 — 위로 스크롤해도 읽은 위치가 뒤로 가지 않는다. 디바운스와
     발행 판단은 받는 쪽(`reads.js`)의 몫이다. */
  function announceSeen(id) {
    if (!id || !view.roomId) { return; }
    /* `view.seenMax` 는 단조 증가로 굳는 값이다 — 실제 봉투 ID 가 아닌 값이 여기
       들어오면 그 뒤의 어떤 실제 ID 도 이 문을 통과하지 못한다. 위 창 스캔이
       이미 걸렀지만, 굳는 값의 문 앞에서 한 번 더 본다. */
    if (!isMessageId(id)) { return; }
    if (id <= view.seenMax) { return; }
    view.seenMax = id;
    stats.seenEmitted += 1;
    bus.emit('reads:seen', { roomId: view.roomId, id: id });
  }

  /* ⭐ **머리가 잘리는 문제**를 여기서 막는다.
   *
   * 묶기의 유일한 결함은 "창이 묶음 **중간**에서 시작하면 그 묶음의 머리가 화면
   * 밖이라 누가 말했는지 알 수 없다"였다. 그래서 화면 상단에 현재 묶음의 발신자를
   * 띄운다 (Slack 의 날짜 구분선·iOS 섹션 헤더와 같은 방식).
   *
   * ⚠️ 이것은 **오버레이**다 — 높이 0 의 sticky 상자 안에 절대 위치로 얹혀 있어
   * 흐름을 차지하지 않는다(CSS). 자리를 차지하면 가상화가 계산한 세로 좌표와 실제
   * 픽셀이 어긋나므로, 그 순간 이 장치가 스크롤을 망가뜨리는 원인이 된다.
   *
   * 화면 위 항목이 **자기 머리를 갖고 있으면 띄우지 않는다** — 같은 것이 두 번
   * 보이는 것이 더 나쁘다. */
  function paintSticky(visible) {
    if (!el.stickyHead) { return; }
    /* 묶지 않는 배치에서는 머리가 언제나 붙어 있다 — 띄울 이유가 없다. */
    var top = groups ? topVisible(visible) : null;
    if (!top || top.head !== false) { dom.hide(el.stickyHead); return; }
    dom.setText(el.stickyAuthor, top.author);
    dom.setText(el.stickyTs, timeLabel(top.ts));
    /* 색은 CSS 가 고른다 (여기서는 슬롯 번호와 내것 여부만 넘긴다). */
    el.stickyHead.setAttribute('data-sender', String(senderSlot(top.author)));
    el.stickyHead.className = top.mine ? 'sticky-head mine' : 'sticky-head';
    if (el.stickyHead.hidden) { stats.stickyShown += 1; }
    dom.show(el.stickyHead);
  }

  /* ----------------------------------------- 가상화 실패 = 결함 */

  /* ⭐ 한때 여기에 '격하(degrade)' 가 있었다 — 가상화가 안 되면 전부 그리기로
     계속 가는 폴백이다. 걷어냈다:

     · 앱이 통째로 죽었던 원인은 가상화가 위험해서가 아니라 **벤더 번들을 잘못
       실었기 때문**이고, 그건 고쳤다. 재발은 `test_vendor_assets.py`(Node 전역
       검사)와 `test_browser_smoke.py`(콘솔 Uncaught 0)가 막는다.
     · 폴백은 성능 때문에 붙인 기능이 **조용히 빠진 채로** 앱이 돌게 만든다.
       다음에 벤더를 갱신하다 또 깨지면 아무도 모른 채 느린 채팅을 쓴다.

     그래서 "조용히 느리게 돌아간다"를 없앤다. 대신 상태줄·이 영역·콘솔 **세 곳**
     에 결함을 남긴다. 그리고 이 실패는 여기서 끝난다 — 사이드바·＋·검색은 산다. */
  function markBroken(reason) {
    if (view.broken) { return; }
    view.broken = reason;
    virtualizer = null;
    status.stick('메시지 영역이 동작하지 않는다 — ' + reason);
    if (env.console && env.console.error) {
      env.console.error('[gitwire-chat] 메시지 영역이 동작하지 않는다 — ' + reason);
    }
    showBroken();
  }

  function showBroken() {
    if (!el.messages || !view.broken) { return; }
    if (brokenNode && brokenNode.parentNode === el.messages) { return; }
    brokenNode = dom.make('div', 'broken');
    brokenNode.appendChild(dom.make('strong', '', '메시지를 그릴 수 없다'));
    brokenNode.appendChild(dom.make('div', 'why', view.broken));
    brokenNode.appendChild(dom.make('div', 'why',
      '가상 스크롤은 이 앱이 대화를 그리는 방식 그 자체다. 느린 대체 경로로 ' +
      '감추지 않는다 — 새로고침해도 같으면 static/vendor 의 번들이 깨진 것이다.'));
    el.messages.appendChild(brokenNode);
    if (el.messages.style) { el.messages.style.height = ''; }
  }

  /* ------------------------------------------------ 붙이기·비우기 */

  function append(msg) {
    if (!insertItem(msg)) { return false; }
    syncVirtual();
    return true;
  }

  function prepend(list) {
    var added = 0;
    for (var i = 0; i < list.length; i++) {
      if (insertItem(list[i])) { added += 1; }
    }
    if (!added) { return 0; }
    /* ⭐ 스크롤 점프 방지. 위에 항목을 끼우면 `scrollTop` 은 그대로인데 위쪽
       콘텐츠가 늘어나므로 **보던 화면이 아래로 튄다.** 가상화에서는 늘어난 양이
       DOM 높이가 아니라 **가상화가 계산한 전체 높이**의 차이다(화면 밖 항목은
       DOM 에 없다). 그 차이만큼 `scrollTop` 을 내린다.
       (CSS `overflow-anchor` 는 브라우저마다 달라 믿지 않고 꺼 둔다.) */
    var v = ensureVirtualizer();
    var heightBefore = totalHeight(v);
    var topBefore = el.timeline ? el.timeline.scrollTop : 0;
    stats.prepended += added;
    syncVirtual();
    if (el.timeline) {
      var grew = totalHeight(virtualizer) - heightBefore;
      el.timeline.scrollTop = topBefore + grew;
      stats.anchored += 1;
      stats.lastAnchor = grew;
    }
    return added;
  }

  /* 타임라인을 비우는 **유일한** 지점. 방 전환 = 다른 대화로의 이동이다. */
  function clear() {
    stats.cleared += 1;
    seen = new Set();
    pendings.clear();
    known = Object.create(null);
    view.oldest = null;
    view.unseen = 0;
    view.hasMore = false;
    view.loadingOlder = false;
    items = [];
    nodes.clear();
    lastWindow = new Set();
    remeasure = new Set();
    view.newFrom = null;
    view.seenMax = '';
    brokenNode = null;
    /* 떠 있던 머리는 **다른 방의 사람 이름**이다 — 남겨 두면 빈 화면에 그 이름이
       걸려 있다. (다시 그리는 순간 알맞게 뜬다.) */
    dom.hide(el.stickyHead);
    el.messages.replaceChildren();
    syncVirtual();
  }

  /* ------------------------------------------------------ 스크롤 */

  function nearBottom() {
    if (!el.timeline) { return true; }
    var gap = el.timeline.scrollHeight - el.timeline.scrollTop - el.timeline.clientHeight;
    return gap < 80;
  }

  /* ---------------------------- 읽던 자리 (레이아웃 전환 = 새로고침) */

  /* **화면 위에 걸려 있는 메시지.** 읽던 자리(앵커)와 떠 있는 머리(sticky)가 같은
     질문을 하므로 답하는 곳도 하나다 — 두 곳에서 각자 세면 둘이 어긋난다. */
  function topVisible(visible) {
    if (!virtualizer || !items.length) { return null; }
    var top = el.timeline ? el.timeline.scrollTop : 0;
    var list = visible || virtualizer.getVirtualItems();
    for (var i = 0; i < list.length; i++) {
      if (list[i].end > top) { return items[list[i].index] || null; }
    }
    return items[items.length - 1] || null;
  }

  /* ⭐ 픽셀이 아니라 **앵커 메시지 id** 로 남긴다. 구조가 바뀌면 높이가 달라져
     `scrollTop` 은 의미를 잃지만, "화면 위에 걸려 있던 그 메시지"는 그대로다. */
  function topVisibleId() {
    var msg = topVisible(null);
    return msg ? msg.id : null;
  }

  function keepAnchor() {
    var id = topVisibleId();
    try {
      if (id) {
        env.localStorage.setItem(ANCHOR_KEY,
          JSON.stringify({ room: view.roomId, id: id }));
      } else if (env.localStorage.removeItem) {
        env.localStorage.removeItem(ANCHOR_KEY);
      }
    } catch (err) { /* 저장을 못 하면 맨 아래로 뜬다 — 잃는 것은 자리 하나다 */ }
    return id;
  }

  /* **한 번만** 쓴다. 읽는 즉시 지운다 — 남겨 두면 그 뒤 모든 새로고침이 그
     자리로 되돌아가, 사용자가 "맨 아래로 안 간다"고 느낀다. */
  function takeAnchor(roomId) {
    var raw = null;
    try {
      raw = env.localStorage.getItem(ANCHOR_KEY);
      if (env.localStorage.removeItem) { env.localStorage.removeItem(ANCHOR_KEY); }
    } catch (err) { return null; }
    if (!raw) { return null; }
    var saved = null;
    try { saved = JSON.parse(raw); } catch (err) { return null; }
    if (!saved || saved.room !== roomId || !saved.id) { return null; }
    return saved.id;
  }

  function scrollToId(id) {
    if (!virtualizer) { return false; }
    for (var i = 0; i < items.length; i++) {
      if (items[i].id !== id) { continue; }
      virtualizer.scrollToIndex(i, { align: 'start' });
      renderWindow();
      view.atBottom = nearBottom();
      if (view.atBottom) { dom.hide(el.jumpLatest); }
      stats.restored += 1;
      return true;
    }
    return false;
  }

  function scrollToBottom() {
    if (!el.timeline) { return; }
    /* 가상화에서는 마지막 항목으로 보내는 것이 정확하다 — DOM 높이가 아니라
       가상화가 계산한 전체 높이가 기준이기 때문이다. */
    if (virtualizer && items.length) {
      virtualizer.scrollToIndex(items.length - 1, { align: 'end' });
      renderWindow();
    }
    el.timeline.scrollTop = el.timeline.scrollHeight;
    view.atBottom = true;
    view.unseen = 0;
    dom.hide(el.jumpLatest);
  }

  function onNewRendered(mine) {
    if (mine || view.atBottom) {
      scrollToBottom();
    } else {
      view.unseen += 1;
      if (el.jumpLatest) {
        dom.setText(el.jumpLatest, '새 메시지 ' + view.unseen + '건 ↓');
        dom.show(el.jumpLatest);
      }
    }
  }

  /* ------------------------------------------ 위로 무한 스크롤 */

  function showOlderState() {
    if (!el.olderSentinel) { return; }
    if (view.loadingOlder) {
      dom.setText(el.olderNote, '이전 대화를 불러오는 중…');
    } else if (view.hasMore) {
      dom.setText(el.olderNote, '위로 올리면 이전 대화가 이어진다');
    } else {
      dom.setText(el.olderNote, '대화의 시작');
    }
  }

  /* 트리거는 IntersectionObserver 다 — 스크롤 이벤트마다 계산하지 않는다.
     표식이 화면에 들어오는 순간(=위 끝에 가까워진 순간) 한 번 발화한다. */
  function watchOlder() {
    if (!view.hasMore) { unwatchOlder(); return; }
    if (!el.olderSentinel) { return; }
    if (!env.IntersectionObserver) { return; }   /* 없으면 스크롤 폴백 */
    if (!olderObserver) {
      olderObserver = new env.IntersectionObserver(function (entries) {
        for (var i = 0; i < entries.length; i++) {
          if (entries[i].isIntersecting) { loadOlder(); return; }
        }
      }, { root: el.timeline || null, rootMargin: '200px 0px 0px 0px', threshold: 0 });
    }
    olderObserver.observe(el.olderSentinel);
  }

  function unwatchOlder() {
    if (olderObserver) { olderObserver.disconnect(); }
  }

  /* 중복 방지 3중: (1) 로딩 플래그 (2) 관찰 일시 해제 (3) 더 없으면 아예 멈춤. */
  function loadOlder() {
    if (!view.roomId || !view.oldest) { return; }
    if (view.loadingOlder || !view.hasMore) { return; }
    var roomId = view.roomId;
    view.loadingOlder = true;
    stats.olderRequests += 1;
    if (olderObserver) { olderObserver.unobserve(el.olderSentinel); }
    showOlderState();
    var url = '/api/rooms/' + encodeURIComponent(roomId) +
      '/messages?before=' + encodeURIComponent(view.oldest);
    return api(url).then(function (data) {
      if (view.roomId !== roomId) { return; }
      var added = prepend(data.messages || []);
      /* 서버가 '더 있다'고 해도 실제로 붙은 게 없으면 멈춘다 (무한 루프 방지). */
      view.hasMore = !!data.has_more && added > 0;
      status.set('');
    })['catch'](function (err) {
      status.set(errText(err), true);
    }).then(function () {
      view.loadingOlder = false;
      showOlderState();
      /* 다시 관찰 — 한 쪽으로 화면이 안 찼으면 곧바로 또 발화해서 이어 붙고,
         맨 위에 닿았으면(hasMore=false) 조용히 멈춘다. */
      if (view.roomId === roomId) { watchOlder(); }
    });
  }

  /* ------------------------------------------------ 최근 불러오기 */

  function load() {
    var roomId = view.roomId;
    if (!roomId) { return Promise.resolve(); }
    return api('/api/rooms/' + encodeURIComponent(roomId) + '/messages')
      .then(function (data) {
        if (view.roomId !== roomId) { return; }
        var list = data.messages || [];
        for (var i = 0; i < list.length; i++) { append(list[i]); }
        view.hasMore = !!data.has_more;
        view.loaded = true;
        bus.emit('room:trouble', { roomId: roomId, status: null });
        showOlderState();
        /* ⚠️ 관찰을 **먼저** 붙인다. 맨 아래로 보내는 동작이 스크롤 이벤트를
           일으키는데, 그때 관찰자가 없으면 폴백 경로가 대신 발동해 의도치 않은
           시점에 과거를 불러온다(대화가 짧으면 곧바로 위 끝이기 때문이다). */
        watchOlder();
        /* 레이아웃을 바꿔 새로고침한 직후라면 읽던 자리로 돌아간다.
           그 메시지가 이 쪽에 없으면(오래된 자리) 조용히 맨 아래로 간다. */
        var anchor = takeAnchor(roomId);
        if (!anchor || !scrollToId(anchor)) { scrollToBottom(); }
        status.set('');
      })['catch'](function (err) {
        if (view.roomId !== roomId) { return; }
        /* 409 = 아직 받는 중이거나 실패 — 오류 문구가 아니라 **상태**다.
           그 자리를 그리는 것은 방 목록 모듈의 몫이라 이벤트로 넘긴다. */
        if (err.status === 409) {
          bus.emit('room:trouble', {
            roomId: roomId, status: (err.payload && err.payload.status) || null
          });
          status.set('');
          return;
        }
        status.set(errText(err), true);
      });
  }

  /* ---------------------------------------------- 낙관적 전송 항목 */

  function addPending(draft) {
    if (!append(draft)) { return false; }
    pendings.set(draft.id, draft);
    onNewRendered(true);
    return true;
  }

  function failPending(tempId, error) {
    var draft = pendings.get(tempId);
    if (!draft) { return; }
    draft.pending = false;
    draft.failed = true;
    paintState(dom, nodes.get(tempId), draft, hooks);
    status.set('보내지 못했다 (앱에 기록되지 않았다): ' + errText(error), true);
  }

  function retry(draft) {
    if (!pendings.has(draft.id) || draft.pending) { return; }
    draft.pending = true;
    draft.failed = false;
    paintState(dom, nodes.get(draft.id), draft, hooks);
    status.set('');
    bus.emit('draft:retry', { draft: draft });
  }

  /* SSE 로 온 레코드가 내가 띄운 보류 항목인가. 봉투 ID 는 아직 모르므로
     같은 이름·같은 본문의 **가장 먼저 보낸 것**과 짝짓는다. */
  function matchPending(msg) {
    var found = null;
    pendings.forEach(function (item, id) {
      if (found || item.failed) { return; }
      if (item.author === msg.author && item.text === msg.text) { found = id; }
    });
    return found;
  }

  /* ⭐ 보류 항목을 **서버가 준 진짜 레코드로 갈아끼운다.**
     노드를 다시 만들지 않는다 — 같은 DOM 노드에서 키(봉투 ID)만 바꿔 단다.

     ⚠️ `msg.mine` 을 여기서 세우지 않는다. 이건 봉투가 있는 레코드이고,
     '내 것'인지는 **서버가 봉투를 보고 이미 판정해서 실어 보냈다.**
     여기서 다시 참으로 박으면 판정이 두 곳이 되고, 둘이 어긋나는 날
     어느 쪽이 맞는지 알 수 없다. */
  function settlePending(tempId, msg) {
    var draft = pendings.get(tempId);
    if (!draft) {
      if (append(msg)) { onNewRendered(true); }
      return;
    }
    pendings['delete'](tempId);
    var node = nodes.get(tempId) || null;
    var at = items.indexOf(draft);
    /* 지운 자리의 다음 항목이 새 '직전'을 얻는다 — 그 하나만 다시 판정된다. */
    if (at >= 0) { removeItemAt(at); }
    seen['delete'](tempId);
    delete known[tempId];
    if (node) { nodes['delete'](tempId); }
    var wasInView = lastWindow.has(tempId);
    lastWindow['delete'](tempId);

    if (seen.has(msg.id)) {
      /* 진짜 레코드가 이미 화면에 있다 — 임시 노드만 걷어낸다. */
      if (node && node.parentNode === el.messages) { el.messages.removeChild(node); }
      syncVirtual();
      return;
    }
    msg.pending = false;
    msg.failed = false;
    if (node) {
      node.dataset.id = msg.id;
      node.setAttribute('data-id', msg.id);
      nodes.set(msg.id, node);
      if (wasInView) { lastWindow.add(msg.id); }
      paintState(dom, node, msg, hooks);
    }
    insertItem(msg);
    /* ⚠️ 노드는 임시 항목 것을 **그대로 쓴다.** 그 노드의 머리는 임시 항목의
       자리에서 정해진 것이라, 진짜 레코드의 자리에서 다시 판정한 값과 다를 수
       있다(임시 항목은 항상 맨 아래였다). 노드를 다시 만들지 않고 그 자리만
       맞춘다 — 높이는 뒤이은 `syncVirtual` 이 그 항목만 다시 잰다. */
    if (node && paintHead(node, msg)) {
      stats.headRepainted += 1;
      remeasure.add(msg.id);
    }
    syncVirtual();
  }

  /* ------------------------------------------------------- 배선 */

  function mount() {
    dom.on(el.jumpLatest, 'click', scrollToBottom);
    dom.on(el.timeline, 'scroll', function () {
      view.atBottom = nearBottom();
      if (view.atBottom) { view.unseen = 0; dom.hide(el.jumpLatest); }
      /* IntersectionObserver 가 없는 환경(구형 브라우저)의 폴백.
         있으면 관찰자가 맡으므로 여기서 또 부르지 않는다. */
      if (!olderObserver && view.hasMore && el.timeline.scrollTop < 200) {
        loadOlder();
      }
    });
    /* 반응형 — 창 폭이 바뀌면 줄바꿈이 달라져 **높이가 달라진다.**
       측정값 캐시를 비워 다시 재게 한다(안 그러면 옛 높이로 배치가 어긋난다). */
    dom.on(win, 'resize', function () {
      if (!virtualizer) { return; }
      virtualizer.measure();
      syncVirtual();
    });

    bus.on('room:switch', function (e) {
      view.roomId = e.id;
      view.loaded = false;
      unwatchOlder();
      clear();
      status.set('불러오는 중…');
      return load();
    });
    /* 받는 중이던 방이 준비되면 **그때** 타임라인을 받아온다
       (새 배관을 만들지 않는다 — 방 목록이 미는 상태 신호에 얹었다). */
    bus.on('room:status', function (e) {
      if (e.id !== view.roomId || view.loaded) { return; }
      if (e.status && e.status.state === 'ready') { load(); }
    });
    bus.on('message:new', function (e) {
      if (e.roomId !== view.roomId) { return; }
      var msg = e.message;
      /* 내가 낙관적으로 띄운 그 말이 되돌아온 것이면 **갈아끼운다.**
         POST 응답보다 SSE 가 먼저 오는 경우가 실제로 있고, 그때 짝짓지 않으면
         같은 말이 잠깐 두 줄로 보인다. */
      var settled = matchPending(msg);
      if (settled) { settlePending(settled, msg); onNewRendered(true); return; }
      /* `msg.mine` 은 서버가 봉투를 보고 붙여 보냈다 — 여기서 덮지 않는다.
         (내 다른 탭에서 보낸 말도 이 경로로 들어오고, 그건 내 것이다.) */
      if (append(msg)) { onNewRendered(!!msg.mine); }
    });
    /* ⭐ 남의 커서가 움직였다 (또는 내 낙관적 갱신) — **같은 창을 다시 칠한다.**
       노드를 다시 만들지 않는다(`paintWindow` 는 없는 노드만 만든다). 그래서
       카운트가 시간에 따라 바뀌어도 `rebuiltInView` 는 0 으로 남는다. */
    bus.on('reads:changed', function (e) {
      if (e.roomId !== view.roomId) { return; }
      /* 구분선 자리는 **방을 열 때 한 번** 정한다 (그 뒤 서버의 '첫 안 읽음' 은
         내가 읽는 즉시 사라지지만, 눈앞의 구분선을 지우면 안 된다). */
      if (view.newFrom === null) {
        var first = reads().firstUnread();
        if (first) { view.newFrom = first; }
      }
      renderWindow();
    });
    /* 탭이 다시 보이게 됐다 — 지금 창에 있는 것을 다시 알린다 (`reads.js` 가 조른다).
       창 내용을 아는 것은 우리이므로 답하는 곳도 여기다. */
    bus.on('reads:ask', function () {
      view.seenMax = '';
      renderWindow();
    });
    /* 레이아웃 전환 직전에 조르는 신호 (theme.js). 자리를 아는 것은 우리다. */
    bus.on('anchor:keep', function () { return keepAnchor(); });
    bus.on('draft:add', function (e) { addPending(e.draft); });
    bus.on('draft:settle', function (e) { settlePending(e.tempId, e.message); });
    bus.on('draft:fail', function (e) { failPending(e.tempId, e.error); });

    /* 가상 스크롤. 없음(라이브러리가 안 옴)과 안 됨(와도 못 씀) 둘 다 여기서
       걸리고, 둘 다 **결함으로 드러난다.** */
    if (!env.virtual || !env.virtual.Virtualizer) {
      markBroken('가상 스크롤 라이브러리를 불러오지 못했다 (static/vendor 확인)');
      return;
    }
    ensureVirtualizer();
  }

  return {
    mount: mount,
    /* 이 영역이 못 서면 여기에 결함을 그린다 (조립소가 부른다). */
    fail: markBroken,
    stats: stats,
    view: view,
    items: function () { return items; },
    nodes: function () { return nodes; },
    pendings: function () { return pendings; },
    virtualizer: function () { return virtualizer; },
    renderWindow: renderWindow,
    syncVirtual: syncVirtual,
    append: append,
    prepend: prepend,
    clear: clear,
    load: load,
    keepAnchor: keepAnchor,
    loadOlder: loadOlder,
    watchOlder: watchOlder
  };
}

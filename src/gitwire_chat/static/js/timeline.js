/*
 * 타임라인 — `#timeline` · `#messages` · `#older-sentinel` · `#jump-latest` 를
 * **소유한다.** 대화 모델(무엇이 있고 어떤 순서인가)도 여기 것이고, 다른 어떤
 * 모듈도 이 노드들을 만지지 않는다.
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
 */

import { errText } from './dom.js';
import { buildMessage, paintState } from './message-node.js';

/* 처음 그릴 때 쓰는 높이 추정치(px). 실측되면 바로 대체된다. */
var ESTIMATED_HEIGHT = 64;
/* 메시지 사이 간격(px) — CSS 의 여백을 가상화 계산에 알려 준다. */
var ITEM_GAP = 6;
/* 화면 밖에 여유로 더 그리는 개수. 스크롤 시 빈칸이 보이지 않게. */
var OVERSCAN = 6;

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
    jumpLatest: dom.$('jump-latest')
  };

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

  var view = {
    roomId: null,
    oldest: null,
    hasMore: false,
    loadingOlder: false,
    loaded: false,
    atBottom: true,
    unseen: 0,
    broken: ''
  };

  var stats = {
    created: 0, appended: 0, inserted: 0, prepended: 0, duplicates: 0,
    recycled: 0, rebuiltInView: 0, measured: 0, cleared: 0,
    olderRequests: 0, anchored: 0, lastAnchor: 0, innerHTML: 0
  };

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
    items.splice(insertionIndex(msg.id), 0, msg);
    /* 보류 항목의 임시 ID 는 '위로 불러오기' 커서가 될 수 없다 (서버가 모른다). */
    if (!msg.pending && (!view.oldest || msg.id < view.oldest)) {
      view.oldest = msg.id;
    }
    return true;
  }

  function makeNode(msg) {
    stats.created += 1;
    if (lastWindow.has(msg.id)) {
      /* 창 안에 있던 것을 다시 만들었다 = 리렌더 사고. 세어 두면 테스트가 잡는다. */
      stats.rebuiltInView += 1;
    }
    return buildMessage(dom, msg, hooks);
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
      gap: ITEM_GAP,
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
      if (node.style) { node.style.transform = 'translateY(' + vi.start + 'px)'; }
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
    brokenNode = null;
    el.messages.replaceChildren();
    syncVirtual();
  }

  /* ------------------------------------------------------ 스크롤 */

  function nearBottom() {
    if (!el.timeline) { return true; }
    var gap = el.timeline.scrollHeight - el.timeline.scrollTop - el.timeline.clientHeight;
    return gap < 80;
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
        scrollToBottom();
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
    status.set('전송 실패: ' + errText(error), true);
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
     노드를 다시 만들지 않는다 — 같은 DOM 노드에서 키(봉투 ID)만 바꿔 단다. */
  function settlePending(tempId, msg) {
    var draft = pendings.get(tempId);
    if (!draft) {
      msg.mine = true;
      if (append(msg)) { onNewRendered(true); }
      return;
    }
    pendings['delete'](tempId);
    var node = nodes.get(tempId) || null;
    var at = items.indexOf(draft);
    if (at >= 0) { items.splice(at, 1); }
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
    msg.mine = true;
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
      msg.mine = false;
      if (append(msg)) { onNewRendered(false); }
    });
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
    loadOlder: loadOlder,
    watchOlder: watchOlder
  };
}

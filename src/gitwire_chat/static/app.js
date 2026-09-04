/*
 * gitwire-chat 프런트엔드.
 *
 * ⭐ 이 파일의 단 하나의 규율: **타임라인을 통째로 다시 그리지 않는다.**
 *
 * 서버는 HTML 을 한 번만 준다. 그 뒤로는 SSE 로 JSON 한 건씩 오고, 여기서
 * 노드를 만들어 `appendChild` / `insertBefore` 로 **붙이기만** 한다.
 * 그래서 다음을 지킨다:
 *
 *   1. `innerHTML` 을 **어디에서도 쓰지 않는다.** 텍스트는 전부 `textContent`
 *      로 넣는다 (덤으로 XSS 가 원천 봉쇄된다).
 *   2. **화면 안에 있는** 메시지 노드는 다시 만들지 않는다. 메시지 ID
 *      (= gitwire 봉투 ID)로 중복을 걸러내므로, 같은 메시지가 로컬 에코와 SSE 로
 *      두 번 와도 노드는 한 번만 생긴다.
 *      ⚠️ 화면 **밖**으로 나간 노드는 가상 스크롤이 걷어낸다 — 그건 사고가 아니라
 *      정상 동작이다. 둘을 뭉개면 진짜 리렌더 버그를 못 잡으므로 카운터를 나눈다:
 *        · `stats.recycled`      창 밖으로 나가 걷어낸 수 (정상, 0 이 아니어도 된다)
 *        · `stats.rebuiltInView` **창 안에 계속 있었는데 다시 만든 수** ← 항상 0
 *   3. 타임라인 컨테이너를 비우는 곳은 **방을 바꿀 때 딱 한 곳**뿐이다
 *      (`switchRoom`). 그건 같은 대화의 리렌더가 아니라 다른 대화로의 전환이다.
 *
 * 과거는 **위로 스크롤하면 자동으로 이어 붙는다**(페이지 버튼이 아니다). 그때
 * 지켜야 하는 세 가지는 `prependMessages`(스크롤 보정) · `onNewRendered`
 * (아래쪽 SSE 와 자리 다툼 금지) · `loadOlder`(중복 로드 차단)에 각각 있다.
 *
 * 타임라인은 **가상 스크롤**이다 (@tanstack/virtual-core, static/vendor 에 벤더링).
 * 화면에 보이는 만큼만 DOM 에 둔다 — 수천 건을 거슬러 올라가도 노드 수가 상수에
 * 가깝게 유지된다. 메시지는 길이가 제각각이라 **가변 높이**로 쓰며, 높이는
 * 추정값이 아니라 `measureElement` 로 **실측**해 반영한다.
 * 모델(`items`)이 원본이고 DOM 은 그 창(window)일 뿐이다.
 *
 * 이 세 가지를 눈으로 믿지 않고 **세어서** 확인한다 — `__chat.stats` 가 노드
 * 생성/붙이기/비우기 횟수를 기록하고, stub DOM 테스트가 그 수를 검증한다.
 */
(function (global) {
  'use strict';

  var doc = global.document;

  /* 화면에 보이지 않아도 되는 것들의 원본. DOM 은 이 배열의 '창' 이다. */
  var items = [];              /* 정렬된 메시지 모델 (id 오름차순 = 시간순) */
  var nodes = new Map();       /* id → 노드 (지금 창 안에 있는 것만) */
  var virtualizer = null;
  var lastWindow = new Set();  /* 직전 창의 id 들 (리렌더 사고 판정용) */
  var rendering = false;       /* 그리는 중 (재진입 방지) */
  var renderAgain = false;     /* 그리는 동안 또 요청이 왔다 */

  /* 처음 그릴 때 쓰는 높이 추정치(px). 실측되면 바로 대체된다. */
  var ESTIMATED_HEIGHT = 64;
  /* 메시지 사이 간격(px) — CSS 의 여백을 가상화 계산에 알려 준다. */
  var ITEM_GAP = 6;
  /* 화면 밖에 여유로 더 그리는 개수. 스크롤 시 빈칸이 보이지 않게. */
  var OVERSCAN = 6;

  var state = {
    rooms: [],
    roomId: null,
    client: null,
    source: null,
    seen: null,
    oldest: null,
    author: '',
    replyTo: null,
    atBottom: true,
    unseen: 0,
    hasMore: false,      /* 위쪽에 더 남았나 (서버가 알려준 값) */
    loadingOlder: false, /* 위로 불러오는 중 — 중복 요청 차단 */
    loaded: false,       /* 지금 방의 타임라인을 실제로 받았나 */
    plan: null,          /* '레포 만들기' 계획 (서버가 계산해 준 것) */
    booted: false
  };

  /* 렌더 규율 검증용 카운터. 프로덕션 코드에도 남긴다 — 값이 싸고,
     "리렌더가 없다"를 주장이 아니라 **관측 가능한 수**로 만든다. */
  var stats = {
    created: 0,     /* 만든 메시지 노드 수 */
    appended: 0,    /* 끝에 붙인 수 */
    inserted: 0,    /* 중간에 끼운 수 (순서 보정) */
    prepended: 0,   /* 앞에 붙인 수 (이전 불러오기) */
    duplicates: 0,  /* ID 중복으로 걸러낸 수 */
    recycled: 0,    /* 창 밖으로 나가 걷어낸 수 (가상화의 정상 동작) */
    rebuiltInView: 0, /* ⭐ 창 안에 있었는데 다시 만든 수 — **항상 0** 이어야 한다 */
    measured: 0,    /* 실측된 높이 반영 횟수 (가변 높이가 실제로 도는가) */
    cleared: 0,     /* 타임라인을 비운 횟수 = 방 전환 횟수 */
    olderRequests: 0, /* 위로 불러오기 요청 수 (중복 발화 감시) */
    anchored: 0,    /* 스크롤 보정 횟수 */
    lastAnchor: 0,  /* 마지막 보정량(px) — 0 이면 보정이 안 된 것이다 */
    innerHTML: 0    /* 항상 0 이어야 한다 */
  };

  var olderObserver = null;

  var el = {};
  var virtual = null;          /* @tanstack/virtual-core (index.html 이 실어 준다) */

  function $(id) { return doc.getElementById(id); }

  function setText(node, text) {
    node.textContent = text == null ? '' : String(text);
    return node;
  }

  function make(tag, className, text) {
    var node = doc.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined) { setText(node, text); }
    return node;
  }

  function uid() {
    return 'c' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  }

  /* ------------------------------------------------------------ 네트워크 */

  function api(path, options) {
    var opts = options || {};
    var init = { method: opts.method || 'GET', headers: {} };
    if (opts.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    }
    return global.fetch(path, init).then(function (res) {
      return res.json().then(function (data) {
        if (!res.ok) {
          var err = new Error((data && data.error) || ('HTTP ' + res.status));
          /* 서버가 함께 준 것(연결 상태·사유·힌트)을 잃지 않는다 —
             "왜 안 되는지"를 화면에 남기는 것이 이 앱의 규칙이다. */
          err.status = res.status;
          err.payload = data || {};
          throw err;
        }
        return data;
      });
    });
  }

  function status(text, isError) {
    if (!el.status) { return; }
    setText(el.status, text || '');
    el.status.className = isError ? 'status error' : 'status';
  }

  /* -------------------------------------------------------------- 시간 */

  function timeLabel(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) { return ''; }
    function two(n) { return (n < 10 ? '0' : '') + n; }
    var now = new Date();
    var sameDay = d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
    var hm = two(d.getHours()) + ':' + two(d.getMinutes());
    if (sameDay) { return hm; }
    return (d.getMonth() + 1) + '/' + d.getDate() + ' ' + hm;
  }

  /* ------------------------------------------------------- 노드 만들기 */

  /* 메시지 하나의 DOM 을 만든다. **오직 여기서만** 메시지 노드가 생긴다. */
  function buildMessage(msg) {
    stats.created += 1;
    if (lastWindow.has(msg.id)) {
      /* 창 안에 있던 것을 다시 만들었다 = 리렌더 사고. 세어 두면 테스트가 잡는다. */
      stats.rebuiltInView += 1;
    }
    var wrap = make('article', 'msg');
    if (msg.mine) { wrap.className = 'msg mine'; }
    if (msg.unknown) { wrap.className = wrap.className + ' unknown'; }
    wrap.dataset.id = msg.id;
    wrap.setAttribute('data-id', msg.id);

    var head = make('div', 'msg-head');
    head.appendChild(make('span', 'author', msg.author));
    head.appendChild(make('time', 'ts', timeLabel(msg.ts)));
    wrap.appendChild(head);

    if (msg.reply_to) {
      var quote = make('div', 'quote');
      var target = lookup(msg.reply_to);
      setText(quote, '↩ ' + (target ? target.author + ': ' + target.text : '이전 메시지'));
      wrap.appendChild(quote);
    }

    /* textContent 만 쓴다 — innerHTML 은 이 파일 어디에도 없다. */
    wrap.appendChild(make('div', 'body', msg.text));

    var actions = make('div', 'msg-actions');
    var reply = make('button', 'link', '답장');
    reply.setAttribute('type', 'button');
    reply.addEventListener('click', function () { startReply(msg); });
    actions.appendChild(reply);
    wrap.appendChild(actions);
    return wrap;
  }

  var known = {};
  function remember(msg) { known[msg.id] = msg; }
  function lookup(id) { return known[id] || null; }

  /* ------------------------------------------------- 모델 · 가상 스크롤 */

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

  /* 모델에 1건 넣는다 (DOM 은 건드리지 않는다). 반환값: 실제로 넣었나. */
  function insertItem(msg) {
    if (!msg || !msg.id) { return false; }
    if (state.seen.has(msg.id)) { stats.duplicates += 1; return false; }
    state.seen.add(msg.id);
    remember(msg);
    var index = insertionIndex(msg.id);
    items.splice(index, 0, msg);
    if (!state.oldest || msg.id < state.oldest) { state.oldest = msg.id; }
    return true;
  }

  function virtualOptions() {
    return {
      count: items.length,
      getScrollElement: function () { return el.timeline; },
      estimateSize: function () { return ESTIMATED_HEIGHT; },
      getItemKey: function (index) { return items[index] ? items[index].id : index; },
      overscan: OVERSCAN,
      gap: ITEM_GAP,
      scrollToFn: virtual.elementScroll,
      observeElementRect: virtual.observeElementRect,
      observeElementOffset: virtual.observeElementOffset,
      /* ⭐ 가변 높이: 추정치로 그린 뒤 **실제 높이를 재서** 반영한다.
         메시지 길이가 제각각이라 고정 높이 가정은 성립하지 않는다. */
      measureElement: function (element, entry, instance) {
        stats.measured += 1;
        return virtual.measureElement(element, entry, instance);
      },
      onChange: function () { renderWindow(); }
    };
  }

  function ensureVirtualizer() {
    if (virtualizer || !el.timeline) { return virtualizer; }
    if (!virtual || !virtual.Virtualizer) { return null; }
    virtualizer = new virtual.Virtualizer(virtualOptions());
    virtualizer._didMount();
    virtualizer._willUpdate();
    return virtualizer;
  }

  /* 모델이 바뀐 뒤 창을 다시 계산한다. */
  function syncVirtual() {
    var v = ensureVirtualizer();
    if (!v) { return; }
    v.setOptions(virtualOptions());
    v._willUpdate();
    renderWindow();
  }

  /* ⭐ 창(window) 그리기 — 보이는 것만 DOM 에 둔다.
     · 창 안에 계속 있는 노드는 **같은 객체 그대로** 둔다 (재사용).
     · 창 밖으로 나간 노드만 걷어낸다 (`recycled`).
     · 창 안에 있던 것을 다시 만들면 `rebuiltInView` 가 올라간다 = 사고. */
  function renderWindow() {
    var v = virtualizer;
    if (!v || !el.messages) { return; }
    /* ⚠️ 재진입 금지.
       그리는 도중 `measureElement` 가 크기 변화를 알리면 라이브러리가 곧바로
       onChange 를 다시 부른다. 그대로 두면 창을 반쯤 그린 상태에서 또 그리기
       시작해, 아직 만들지 않은 노드를 "창 안에 있었는데 없다"로 오판한다.
       한 번에 하나만 그리고, 도중에 요청이 오면 끝난 뒤 한 번 더 그린다. */
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
        node = buildMessage(msg);
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
      /* 화면 밖 — 걷어낸다. 이건 사고가 아니라 가상화의 정상 동작이다. */
      if (node.parentNode === el.messages) { el.messages.removeChild(node); }
      nodes['delete'](id);
      stats.recycled += 1;
    });
    if (el.messages.style) {
      el.messages.style.height = v.getTotalSize() + 'px';
    }
    lastWindow = keep;
  }

  /* 새 메시지 1건. 반환값: 실제로 붙였나. */
  function appendMessage(msg) {
    if (!insertItem(msg)) { return false; }
    syncVirtual();
    return true;
  }

  /* '이전 불러오기' — 앞쪽에 한 덩어리로 끼운다. */
  function prependMessages(list) {
    var added = 0;
    for (var i = 0; i < list.length; i++) {
      if (insertItem(list[i])) { added += 1; }
    }
    if (!added) { return 0; }
    /* ⭐ 스크롤 점프 방지.
       위에 항목을 끼우면 `scrollTop` 은 그대로인데 위쪽 콘텐츠가 늘어나므로
       **보던 화면이 아래로 튄다.** 가상화에서는 늘어난 양이 DOM 높이가 아니라
       **가상화가 계산한 전체 높이(getTotalSize)** 의 차이다 — 화면 밖 항목은
       DOM 에 없기 때문이다. 그 차이만큼 `scrollTop` 을 내린다.
       (CSS `overflow-anchor` 는 브라우저마다 달라 믿지 않고 꺼 둔다.) */
    var v = ensureVirtualizer();
    var heightBefore = v ? v.getTotalSize() : 0;
    var topBefore = el.timeline ? el.timeline.scrollTop : 0;
    stats.prepended += added;
    syncVirtual();
    if (el.timeline && v) {
      var grew = v.getTotalSize() - heightBefore;
      el.timeline.scrollTop = topBefore + grew;
      stats.anchored += 1;
      stats.lastAnchor = grew;
    }
    return added;
  }

  /* 타임라인을 비우는 **유일한** 지점. 방 전환 = 다른 대화로의 이동이다. */
  function clearTimeline() {
    stats.cleared += 1;
    state.seen = new global.Set();
    state.oldest = null;
    state.unseen = 0;
    state.hasMore = false;
    state.loadingOlder = false;
    items = [];
    nodes.clear();
    lastWindow = new Set();
    el.messages.replaceChildren();
    syncVirtual();
  }

  /* ------------------------------------------------------------ 스크롤 */

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
    state.atBottom = true;
    state.unseen = 0;
    hide(el.jumpLatest);
  }

  function show(node) { if (node) { node.hidden = false; } }
  function hide(node) { if (node) { node.hidden = true; } }

  function onNewRendered(mine) {
    if (mine || state.atBottom) {
      scrollToBottom();
    } else {
      state.unseen += 1;
      if (el.jumpLatest) {
        setText(el.jumpLatest, '새 메시지 ' + state.unseen + '건 ↓');
        show(el.jumpLatest);
      }
    }
  }

  /* ---------------------------------------------------------- 방 목록 */

  /* 연결 상태 → 사람이 읽는 한 줄. 서버가 준 값만 쓴다(추측하지 않는다). */
  function stateLabel(status) {
    if (!status) { return ''; }
    if (status.state === 'connecting') { return '받는 중…'; }
    if (status.state === 'failed') { return '실패 · ' + (status.detail || '사유 없음'); }
    return '';
  }

  function renderRooms(rooms) {
    state.rooms = rooms || [];
    /* 방 목록은 대화가 아니다 — 짧고, 바뀔 때만 다시 그린다.
       (타임라인의 append-only 규율은 메시지에 대한 것이다.) */
    el.rooms.replaceChildren();
    for (var i = 0; i < state.rooms.length; i++) {
      (function (room) {
        var li = make('li', room.id === state.roomId ? 'room active' : 'room');
        var btn = make('button', 'room-btn');
        btn.setAttribute('type', 'button');
        btn.appendChild(make('span', 'room-name', room.name || room.repo_url));
        btn.appendChild(make('span', 'room-url', room.repo_url));
        var label = stateLabel(room.status);
        if (label) {
          var cls = room.status.state === 'failed' ? 'room-state failed' : 'room-state';
          btn.appendChild(make('span', cls, label));
        }
        btn.addEventListener('click', function () { switchRoom(room.id); });
        li.appendChild(btn);
        if (room.status && room.status.state === 'failed') {
          var retry = make('button', 'link', '재시도');
          retry.setAttribute('type', 'button');
          retry.addEventListener('click', function () { retryRoom(room.id); });
          li.className = li.className + ' room-row';
          li.appendChild(retry);
        }
        el.rooms.appendChild(li);
      }(state.rooms[i]));
    }
    if (state.rooms.length) { hide(el.roomsEmpty); } else { show(el.roomsEmpty); }
    showRoomTrouble();
  }

  function roomStatus(roomId) {
    for (var i = 0; i < state.rooms.length; i++) {
      if (state.rooms[i].id === roomId) { return state.rooms[i].status || null; }
    }
    return null;
  }

  /* 지금 보고 있는 방이 아직 안 붙었으면 그 자리에 사유를 남긴다.
     ⚠️ 방을 목록에서 지우지 않는다 — 사용자가 왜 안 됐는지 볼 수 있어야 한다. */
  function showRoomTrouble(status) {
    if (!el.roomTrouble) { return; }
    var current = status || roomStatus(state.roomId);
    if (!state.roomId || !current || current.state === 'ready') {
      hide(el.roomTrouble);
      return;
    }
    setText(el.roomTroubleText,
      current.state === 'failed'
        ? '이 방을 열지 못했다 — ' + (current.detail || '사유 없음')
        : '방을 받는 중이다 (클론). 끝나면 대화가 바로 뜬다.');
    setText(el.roomTroubleHint, current.hint || '');
    if (el.roomRetry) { el.roomRetry.hidden = current.state !== 'failed'; }
    show(el.roomTrouble);
  }

  function retryRoom(roomId) {
    var id = roomId || state.roomId;
    if (!id) { return; }
    showRoomTrouble({ state: 'connecting' });
    return api('/api/rooms/' + encodeURIComponent(id) + '/retry', { method: 'POST' })
      ['catch'](function (err) { status(String(err.message || err), true); });
  }

  function currentRoom() {
    for (var i = 0; i < state.rooms.length; i++) {
      if (state.rooms[i].id === state.roomId) { return state.rooms[i]; }
    }
    return null;
  }

  /* ------------------------------------------------------------ 가시성 */

  function reportVisibility(visible) {
    if (!state.roomId || !state.client) { return; }
    var path = '/api/rooms/' + encodeURIComponent(state.roomId) + '/visibility';
    var body = { visible: !!visible, client: state.client };
    try {
      api(path, { method: 'POST', body: body })['catch'](function () {});
    } catch (err) { /* 알림 판정용 부가 정보다. 실패해도 대화는 계속된다. */ }
  }

  function isVisible() {
    return !doc.visibilityState || doc.visibilityState === 'visible';
  }

  /* ------------------------------------------------------------- SSE */

  function disconnect() {
    if (state.source) {
      try { state.source.close(); } catch (err) { /* 이미 닫혔다 */ }
      state.source = null;
    }
  }

  function connect(roomId) {
    disconnect();
    if (!global.EventSource) {
      status('이 브라우저는 SSE 를 지원하지 않는다', true);
      return;
    }
    var url = '/api/rooms/' + encodeURIComponent(roomId) +
      '/stream?client=' + encodeURIComponent(state.client);
    var src = new global.EventSource(url);
    state.source = src;

    src.addEventListener('message', function (event) {
      var msg;
      try { msg = JSON.parse(event.data); } catch (err) { return; }
      if (state.roomId !== roomId) { return; }
      msg.mine = false;
      if (appendMessage(msg)) { onNewRendered(false); }
    });
    src.addEventListener('rooms', function (event) {
      try {
        renderRooms(JSON.parse(event.data).rooms);
      } catch (err) { return; }
      /* 받는 중이던 방이 준비되면 **그때** 타임라인을 받아온다.
         (새 배관을 만들지 않는다 — 방 목록을 미는 기존 이벤트에 얹었다.) */
      var current = roomStatus(state.roomId);
      if (!state.loaded && current && current.state === 'ready') {
        loadRecent(state.roomId);
      }
    });
    src.addEventListener('trouble', function (event) {
      try { status('폴링 경고: ' + JSON.parse(event.data).detail, true); }
      catch (err) { /* 무시 */ }
    });
    src.addEventListener('open', function () { status(''); });
    src.addEventListener('error', function () {
      status('연결이 끊겼다 — 다시 연결하는 중', true);
    });
    reportVisibility(isVisible());
  }

  /* --------------------------------------------------------- 방 전환 */

  function switchRoom(roomId) {
    if (!roomId) { return; }
    if (state.roomId && state.roomId !== roomId) { reportVisibility(false); }
    state.roomId = roomId;
    state.loaded = false;
    unwatchOlder();
    clearTimeline();
    renderRooms(state.rooms);
    var room = currentRoom();
    setText(el.roomTitle, room ? (room.name || room.repo_url) : roomId);
    setText(el.roomSub, room ? room.repo_url : '');
    doc.body.dataset.view = 'chat';
    doc.body.setAttribute('data-view', 'chat');
    hide(el.searchResults);
    status('불러오는 중…');
    connect(roomId);
    showRoomTrouble();
    return loadRecent(roomId);
  }

  function loadRecent(roomId) {
    return api('/api/rooms/' + encodeURIComponent(roomId) + '/messages')
      .then(function (data) {
        if (state.roomId !== roomId) { return; }
        var list = data.messages || [];
        for (var i = 0; i < list.length; i++) { appendMessage(list[i]); }
        state.hasMore = !!data.has_more;
        state.loaded = true;
        hide(el.roomTrouble);
        showOlderState();
        /* ⚠️ 관찰을 **먼저** 붙인다. 맨 아래로 보내는 동작이 스크롤 이벤트를
           일으키는데, 그때 관찰자가 없으면 폴백 경로가 대신 발동해 의도치 않은
           시점에 과거를 불러온다(대화가 짧으면 곧바로 위 끝이기 때문이다). */
        watchOlder();
        scrollToBottom();
        status('');
      })['catch'](function (err) {
        if (state.roomId !== roomId) { return; }
        /* 409 = 아직 받는 중이거나 실패 — 오류 문구가 아니라 **상태**로 그린다.
           준비되면 SSE 'rooms' 이벤트가 다시 불러온다. */
        if (err.status === 409) {
          showRoomTrouble(err.payload && err.payload.status);
          status('');
          return;
        }
        status(String(err.message || err), true);
      });
  }

  /* ------------------------------------------- 위로 무한 스크롤 (과거) */

  /* 표식 하나의 문구만 바꾼다 — 버튼이 아니다. 사용자는 스크롤만 한다. */
  function showOlderState() {
    if (!el.olderSentinel) { return; }
    if (state.loadingOlder) {
      setText(el.olderNote, '이전 대화를 불러오는 중…');
    } else if (state.hasMore) {
      setText(el.olderNote, '위로 올리면 이전 대화가 이어진다');
    } else {
      setText(el.olderNote, '대화의 시작');
    }
  }

  /* 트리거는 IntersectionObserver 다 — 스크롤 이벤트마다 계산하지 않는다.
     표식이 화면에 들어오는 순간(=위 끝에 가까워진 순간) 한 번 발화한다. */
  function watchOlder() {
    if (!state.hasMore) { unwatchOlder(); return; }
    if (!el.olderSentinel) { return; }
    if (!global.IntersectionObserver) { return; }   /* 없으면 스크롤 폴백 */
    if (!olderObserver) {
      olderObserver = new global.IntersectionObserver(function (entries) {
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

  /* '이전 불러오기' — 위로 스크롤하면 자동으로 불린다.
     중복 방지 3중: (1) 로딩 플래그 (2) 관찰 일시 해제 (3) 더 없으면 아예 멈춤. */
  function loadOlder() {
    if (!state.roomId || !state.oldest) { return; }
    if (state.loadingOlder || !state.hasMore) { return; }
    var roomId = state.roomId;
    state.loadingOlder = true;
    stats.olderRequests += 1;
    /* 관찰을 잠시 끊는다. 응답이 오기 전에 표식이 계속 보여도 다시 발화하지 않는다. */
    if (olderObserver) { olderObserver.unobserve(el.olderSentinel); }
    showOlderState();
    var url = '/api/rooms/' + encodeURIComponent(roomId) +
      '/messages?before=' + encodeURIComponent(state.oldest);
    return api(url).then(function (data) {
      if (state.roomId !== roomId) { return; }
      var added = prependMessages(data.messages || []);
      /* 서버가 '더 있다'고 해도 실제로 붙은 게 없으면 멈춘다 (무한 루프 방지). */
      state.hasMore = !!data.has_more && added > 0;
      status('');
    })['catch'](function (err) {
      status(String(err.message || err), true);
    }).then(function () {
      state.loadingOlder = false;
      showOlderState();
      /* 다시 관찰 — 한 쪽으로 화면이 안 찼으면 곧바로 또 발화해서 이어 붙고,
         맨 위에 닿았으면(hasMore=false) 조용히 멈춘다. */
      if (state.roomId === roomId) { watchOlder(); }
    });
  }

  /* ---------------------------------------------------------- 보내기 */

  function startReply(msg) {
    state.replyTo = msg.id;
    setText(el.replyLabel, '↩ ' + msg.author + ': ' + msg.text.slice(0, 40));
    show(el.replyChip);
    if (el.text && el.text.focus) { el.text.focus(); }
  }

  function cancelReply() {
    state.replyTo = null;
    hide(el.replyChip);
  }

  function send() {
    var text = (el.text.value || '').trim();
    if (!text || !state.roomId) { return; }
    var author = (el.author.value || '').trim() || state.author;
    var roomId = state.roomId;
    el.text.value = '';
    autoGrow();
    var body = { text: text, author: author, reply_to: state.replyTo };
    cancelReply();
    status('보내는 중…');
    return api('/api/rooms/' + encodeURIComponent(roomId) + '/messages',
      { method: 'POST', body: body })
      .then(function (data) {
        if (state.roomId !== roomId) { return; }
        var msg = data.message;
        msg.mine = true;
        /* 로컬 에코. 잠시 뒤 SSE 로 같은 ID 가 또 와도 중복으로 걸러진다. */
        if (appendMessage(msg)) { onNewRendered(true); }
        status('');
      })['catch'](function (err) {
        status('전송 실패: ' + String(err.message || err), true);
        el.text.value = text;
      });
  }

  /* ------------------------------------------------------------ 검색 */

  function runSearch() {
    var q = (el.searchQ.value || '').trim();
    if (!q || !state.roomId) { return; }
    var url = '/api/rooms/' + encodeURIComponent(state.roomId) +
      '/search?q=' + encodeURIComponent(q);
    return api(url).then(function (data) {
      var list = data.messages || [];
      el.searchList.replaceChildren();
      setText(el.searchSummary, '"' + q + '" — ' + list.length + '건 (서버가 레코드를 뒤졌다)');
      for (var i = 0; i < list.length; i++) {
        var hit = make('div', 'hit');
        hit.appendChild(make('span', 'author', list[i].author));
        hit.appendChild(make('time', 'ts', timeLabel(list[i].ts)));
        hit.appendChild(make('div', 'body', list[i].text));
        el.searchList.appendChild(hit);
      }
      show(el.searchResults);
    })['catch'](function (err) { status(String(err.message || err), true); });
  }

  /* ------------------------------------------------------------ 방 등록 */

  function addRoom(event) {
    if (event && event.preventDefault) { event.preventDefault(); }
    var url = (el.repoUrl.value || '').trim();
    if (!url) { return; }
    hide(el.addRoomError);
    setText(el.addRoomSubmit, '등록 중…');
    el.addRoomSubmit.disabled = true;
    return api('/api/rooms', {
      method: 'POST',
      body: {
        repo_url: url,
        name: (el.roomName.value || '').trim(),
        token_env: (el.tokenEnv.value || '').trim(),
        author: state.author
      }
    }).then(function (data) {
      el.repoUrl.value = '';
      el.roomName.value = '';
      hide(el.addRoom);
      hide(el.newRepoForm);
      /* 등록은 즉시 돌아온다 — 클론은 백그라운드다. 방으로 바로 들어가면
         '받는 중' 이 보이고, 끝나면 SSE 로 대화가 채워진다. */
      return api('/api/rooms').then(function (all) {
        renderRooms(all.rooms);
        return switchRoom(data.room.id);
      });
    })['catch'](function (err) {
      setText(el.addRoomError, String(err.message || err));
      show(el.addRoomError);
    }).then(function () {
      setText(el.addRoomSubmit, '방 등록');
      el.addRoomSubmit.disabled = false;
    });
  }

  /* ------------------------------------------- 레포 만들기 거들기 (G-2) */

  function newRepoError(message) {
    if (!el.newRepoError) { return; }
    if (!message) { hide(el.newRepoError); return; }
    setText(el.newRepoError, message);
    show(el.newRepoError);
  }

  function newRepoBody() {
    return {
      host: 'github.com',
      name: (el.newRepoName.value || '').trim() || (el.roomName.value || '').trim(),
      owner: (el.newRepoOwner.value || '').trim(),
      token_env: (el.tokenEnv.value || '').trim(),
      description: (el.roomName.value || '').trim()
    };
  }

  /* "무엇이 만들어지나" — 누르기 전에 보여준다. 레포 생성은 계정을 바꾸는
     외부 동작이라 조용히 하지 않는다. */
  function planNewRepo() {
    newRepoError('');
    return api('/api/repos/plan', { method: 'POST', body: newRepoBody() })
      .then(function (plan) {
        state.plan = plan;
        if (el.newRepoName && !el.newRepoName.value) {
          el.newRepoName.value = plan.name || '';
        }
        var where = (plan.owner ? plan.owner + '/' : '') + (plan.name || '');
        setText(el.newRepoPlan,
          plan.forge.label + ' 에 ' + where + ' 을(를) 비공개(private)로 만든다.' +
          (plan.mode === 'api'
            ? ' 토큰(' + plan.token_env + ')이 있어 앱 안에서 바로 만든다.'
            : plan.mode === 'link'
              ? ' 링크로 가서 만들고 오면 그 주소로 방을 잇는다.'
              : ' 이 호스트는 거들 수 없다 — 주소를 직접 넣어라.'));
        show(el.newRepoPlan);
        if (plan.detail) { newRepoError(plan.detail); }
        if (plan.mode === 'api') {
          show(el.newRepoCreate);
        } else { hide(el.newRepoCreate); }
        if (plan.link) {
          el.newRepoLink.setAttribute('href', plan.link);
          show(el.newRepoLink);
          if (plan.clone_url) { show(el.newRepoUse); } else { hide(el.newRepoUse); }
        } else {
          hide(el.newRepoLink);
          hide(el.newRepoUse);
        }
      })['catch'](function (err) { newRepoError(String(err.message || err)); });
  }

  /* 만든 주소를 입력칸에 넣고 그대로 방까지 만든다 — 사용자가 URL 을 옮겨
     적지 않는 것이 이 거들기의 핵심이다. */
  function useRepoUrl(url) {
    if (!url) { return; }
    el.repoUrl.value = url;
    hide(el.newRepoForm);
    return addRoom();
  }

  function createNewRepo() {
    newRepoError('');
    setText(el.newRepoCreate, '만드는 중…');
    el.newRepoCreate.disabled = true;
    return api('/api/repos', { method: 'POST', body: newRepoBody() })
      .then(function (data) {
        return useRepoUrl(data.repo && data.repo.clone_url);
      })['catch'](function (err) {
        var payload = err.payload || {};
        newRepoError(String(err.message || err) +
          (payload.hint ? ' — ' + payload.hint : ''));
      }).then(function () {
        setText(el.newRepoCreate, '지금 만들기');
        el.newRepoCreate.disabled = false;
      });
  }

  /* ------------------------------------------------------------ 입력칸 */

  function autoGrow() {
    if (!el.text || !el.text.style) { return; }
    el.text.style.height = 'auto';
    var h = el.text.scrollHeight || 0;
    el.text.style.height = Math.min(h, 160) + 'px';
  }

  function rememberAuthor() {
    var value = (el.author.value || '').trim();
    state.author = value;
    try { global.localStorage.setItem('gitwire-chat.author', value); }
    catch (err) { /* 프라이빗 모드 등 — 이름 기억은 부가 기능이다 */ }
  }

  /* -------------------------------------------------------------- 부팅 */

  function cache() {
    el.rooms = $('rooms');
    el.roomsEmpty = $('rooms-empty');
    el.messages = $('messages');
    el.timeline = $('timeline');
    el.roomTitle = $('room-title');
    el.roomSub = $('room-sub');
    el.status = $('status');
    el.composer = $('composer');
    el.text = $('text');
    el.author = $('author');
    el.send = $('send');
    el.olderSentinel = $('older-sentinel');
    el.olderNote = $('older-note');
    el.jumpLatest = $('jump-latest');
    el.replyChip = $('reply-chip');
    el.replyLabel = $('reply-label');
    el.replyCancel = $('reply-cancel');
    el.addRoom = $('add-room');
    el.addRoomSubmit = $('add-room-submit');
    el.addRoomError = $('add-room-error');
    el.repoUrl = $('repo-url');
    el.roomName = $('room-name');
    el.tokenEnv = $('token-env');
    el.toggleAdd = $('toggle-add');
    el.addRoomCancel = $('add-room-cancel');
    el.roomTrouble = $('room-trouble');
    el.roomTroubleText = $('room-trouble-text');
    el.roomTroubleHint = $('room-trouble-hint');
    el.roomRetry = $('room-retry');
    el.newRepoToggle = $('new-repo-toggle');
    el.newRepoForm = $('new-repo-form');
    el.newRepoOwner = $('new-repo-owner');
    el.newRepoName = $('new-repo-name');
    el.newRepoCheck = $('new-repo-check');
    el.newRepoPlan = $('new-repo-plan');
    el.newRepoLink = $('new-repo-link');
    el.newRepoCreate = $('new-repo-create');
    el.newRepoUse = $('new-repo-use');
    el.newRepoError = $('new-repo-error');
    el.back = $('back');
    el.refresh = $('refresh');
    el.toggleSearch = $('toggle-search');
    el.searchBar = $('search-bar');
    el.searchQ = $('search-q');
    el.searchClose = $('search-close');
    el.searchResults = $('search-results');
    el.searchList = $('search-list');
    el.searchSummary = $('search-summary');
  }

  function on(node, type, fn) { if (node && node.addEventListener) { node.addEventListener(type, fn); } }

  function wire() {
    on(el.composer, 'submit', function (e) { if (e.preventDefault) { e.preventDefault(); } send(); });
    on(el.text, 'keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        if (e.preventDefault) { e.preventDefault(); }
        send();
      }
    });
    on(el.text, 'input', autoGrow);
    on(el.author, 'change', rememberAuthor);
    on(el.jumpLatest, 'click', scrollToBottom);
    on(el.replyCancel, 'click', cancelReply);
    on(el.addRoom, 'submit', addRoom);
    on(el.toggleAdd, 'click', function () {
      if (el.addRoom.hidden) { show(el.addRoom); } else { hide(el.addRoom); }
    });
    on(el.addRoomCancel, 'click', function () { hide(el.addRoom); });
    on(el.roomRetry, 'click', function () { retryRoom(state.roomId); });
    on(el.newRepoToggle, 'click', function () {
      if (el.newRepoForm.hidden) {
        show(el.newRepoForm);
        if (!el.newRepoName.value) {
          el.newRepoName.value = (el.roomName.value || '').trim();
        }
      } else { hide(el.newRepoForm); }
    });
    on(el.newRepoCheck, 'click', planNewRepo);
    on(el.newRepoCreate, 'click', createNewRepo);
    on(el.newRepoUse, 'click', function () {
      useRepoUrl(state.plan && state.plan.clone_url);
    });
    on(el.back, 'click', function () {
      doc.body.dataset.view = 'rooms';
      doc.body.setAttribute('data-view', 'rooms');
    });
    on(el.refresh, 'click', function () {
      if (!state.roomId) { return; }
      status('당겨오는 중…');
      api('/api/rooms/' + encodeURIComponent(state.roomId) + '/refresh', { method: 'POST' })
        .then(function (data) { status(data.delivered ? '새 메시지 ' + data.delivered + '건' : '새 메시지 없음'); })
        ['catch'](function (err) { status(String(err.message || err), true); });
    });
    on(el.toggleSearch, 'click', function () {
      if (el.searchBar.hidden) { show(el.searchBar); } else { hide(el.searchBar); hide(el.searchResults); }
    });
    on(el.searchBar, 'submit', function (e) { if (e.preventDefault) { e.preventDefault(); } runSearch(); });
    on(el.searchClose, 'click', function () { hide(el.searchBar); hide(el.searchResults); });
    on(el.timeline, 'scroll', function () {
      state.atBottom = nearBottom();
      if (state.atBottom) { state.unseen = 0; hide(el.jumpLatest); }
      /* IntersectionObserver 가 없는 환경(구형 브라우저)의 폴백.
         있으면 관찰자가 맡으므로 여기서 또 부르지 않는다. */
      if (!olderObserver && state.hasMore && el.timeline.scrollTop < 200) {
        loadOlder();
      }
    });
    /* 반응형 — 창 폭이 바뀌면 줄바꿈이 달라져 **높이가 달라진다.**
       측정값 캐시를 비워 다시 재게 한다(안 그러면 옛 높이로 배치가 어긋난다). */
    on(global, 'resize', function () {
      if (!virtualizer) { return; }
      virtualizer.measure();
      syncVirtual();
    });
    on(doc, 'visibilitychange', function () { reportVisibility(isVisible()); });
    on(global, 'beforeunload', function () { reportVisibility(false); });
  }

  function boot() {
    if (state.booted) { return; }
    state.booted = true;
    virtual = global.TanStackVirtual || null;
    cache();
    if (!virtual || !virtual.Virtualizer) {
      /* 벤더링된 파일이라 정상 설치에서는 없을 수 없다. 없으면 조용히 다르게
         동작하지 말고 분명히 말한다. */
      status('가상 스크롤 라이브러리를 불러오지 못했다 (static/vendor 확인)', true);
      return;
    }
    ensureVirtualizer();
    state.client = uid();
    state.seen = new global.Set();
    var stored = null;
    try { stored = global.localStorage.getItem('gitwire-chat.author'); }
    catch (err) { stored = null; }
    state.author = stored || (doc.body.dataset ? doc.body.dataset.author : '') ||
      (doc.body.getAttribute ? doc.body.getAttribute('data-default-author') : '') || '';
    if (el.author) { el.author.value = state.author; }
    wire();
    return api('/api/rooms').then(function (data) {
      renderRooms(data.rooms || []);
      if (data.rooms && data.rooms.length) { return switchRoom(data.rooms[0].id); }
      status('');
    })['catch'](function (err) { status(String(err.message || err), true); });
  }

  global.__chat = {
    boot: boot,
    state: state,
    stats: stats,
    items: function () { return items; },
    nodes: function () { return nodes; },
    virtualizer: function () { return virtualizer; },
    renderWindow: renderWindow,
    syncVirtual: syncVirtual,
    appendMessage: appendMessage,
    prependMessages: prependMessages,
    clearTimeline: clearTimeline,
    switchRoom: switchRoom,
    loadOlder: loadOlder,
    watchOlder: watchOlder,
    renderRooms: renderRooms,
    retryRoom: retryRoom,
    planNewRepo: planNewRepo,
    createNewRepo: createNewRepo,
    send: send,
    runSearch: runSearch,
    addRoom: addRoom,
    connect: connect,
    disconnect: disconnect
  };

  if (doc && doc.addEventListener) {
    if (doc.readyState === 'loading') {
      doc.addEventListener('DOMContentLoaded', boot);
    } else if (doc.readyState) {
      boot();
    }
  }
}(typeof globalThis !== 'undefined' ? globalThis : this));

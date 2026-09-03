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
 *   2. 이미 화면에 있는 메시지 노드는 **다시 만들지도, 지우지도 않는다.**
 *      메시지 ID(= gitwire 봉투 ID)로 중복을 걸러내므로, 같은 메시지가 로컬
 *      에코와 SSE 로 두 번 와도 노드는 한 번만 생긴다.
 *   3. 타임라인 컨테이너를 비우는 곳은 **방을 바꿀 때 딱 한 곳**뿐이다
 *      (`switchRoom`). 그건 같은 대화의 리렌더가 아니라 다른 대화로의 전환이다.
 *
 * 이 세 가지를 눈으로 믿지 않고 **세어서** 확인한다 — `__chat.stats` 가 노드
 * 생성/붙이기/비우기 횟수를 기록하고, stub DOM 테스트가 그 수를 검증한다.
 */
(function (global) {
  'use strict';

  var doc = global.document;

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
    maybeMore: false,
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
    cleared: 0,     /* 타임라인을 비운 횟수 = 방 전환 횟수 */
    innerHTML: 0    /* 항상 0 이어야 한다 */
  };

  var el = {};

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
        if (!res.ok) { throw new Error((data && data.error) || ('HTTP ' + res.status)); }
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

  /* --------------------------------------------------------- 붙이기 */

  /* 정렬 위치. 메시지 ID 는 고정폭 타임스탬프로 시작하므로 사전식 = 시간순. */
  function insertionIndex(id) {
    var kids = el.messages.children;
    var i = kids.length;
    while (i > 0) {
      var kidId = kids[i - 1].dataset ? kids[i - 1].dataset.id : '';
      if (!kidId || kidId <= id) { break; }
      i -= 1;
    }
    return i;
  }

  /* 새 메시지 1건을 화면에 붙인다. 반환값: 실제로 붙였나.
     ⚠️ 이미 있는 노드는 절대 건드리지 않는다. */
  function appendMessage(msg) {
    if (!msg || !msg.id) { return false; }
    if (state.seen.has(msg.id)) { stats.duplicates += 1; return false; }
    state.seen.add(msg.id);
    remember(msg);

    var node = buildMessage(msg);
    var kids = el.messages.children;
    var index = insertionIndex(msg.id);
    if (index >= kids.length) {
      el.messages.appendChild(node);
      stats.appended += 1;
    } else {
      el.messages.insertBefore(node, kids[index]);
      stats.inserted += 1;
    }
    if (!state.oldest || msg.id < state.oldest) { state.oldest = msg.id; }
    return true;
  }

  /* '이전 불러오기' — 앞쪽에 한 덩어리로 끼운다. 기존 노드는 그대로 남는다. */
  function prependMessages(list) {
    var frag = doc.createDocumentFragment();
    var added = 0;
    for (var i = 0; i < list.length; i++) {
      var msg = list[i];
      if (!msg || !msg.id || state.seen.has(msg.id)) {
        if (msg && msg.id) { stats.duplicates += 1; }
        continue;
      }
      state.seen.add(msg.id);
      remember(msg);
      frag.appendChild(buildMessage(msg));
      added += 1;
      if (!state.oldest || msg.id < state.oldest) { state.oldest = msg.id; }
    }
    if (!added) { return 0; }
    var before = el.timeline ? el.timeline.scrollHeight : 0;
    var first = el.messages.children.length ? el.messages.children[0] : null;
    el.messages.insertBefore(frag, first);
    stats.prepended += added;
    if (el.timeline) {
      /* 스크롤 점프 방지 — 늘어난 높이만큼 내려 읽던 자리를 유지한다. */
      el.timeline.scrollTop = el.timeline.scrollTop + (el.timeline.scrollHeight - before);
    }
    return added;
  }

  /* 타임라인을 비우는 **유일한** 지점. 방 전환 = 다른 대화로의 이동이다. */
  function clearTimeline() {
    stats.cleared += 1;
    state.seen = new global.Set();
    state.oldest = null;
    state.unseen = 0;
    el.messages.replaceChildren();
  }

  /* ------------------------------------------------------------ 스크롤 */

  function nearBottom() {
    if (!el.timeline) { return true; }
    var gap = el.timeline.scrollHeight - el.timeline.scrollTop - el.timeline.clientHeight;
    return gap < 80;
  }

  function scrollToBottom() {
    if (!el.timeline) { return; }
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
        btn.addEventListener('click', function () { switchRoom(room.id); });
        li.appendChild(btn);
        el.rooms.appendChild(li);
      }(state.rooms[i]));
    }
    if (state.rooms.length) { hide(el.roomsEmpty); } else { show(el.roomsEmpty); }
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
      try { renderRooms(JSON.parse(event.data).rooms); } catch (err) { /* 무시 */ }
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
    return loadRecent(roomId);
  }

  function loadRecent(roomId) {
    return api('/api/rooms/' + encodeURIComponent(roomId) + '/messages')
      .then(function (data) {
        if (state.roomId !== roomId) { return; }
        var list = data.messages || [];
        for (var i = 0; i < list.length; i++) { appendMessage(list[i]); }
        state.maybeMore = !!data.maybe_more;
        if (state.maybeMore) { show(el.loadOlder); } else { hide(el.loadOlder); }
        scrollToBottom();
        status('');
      })['catch'](function (err) { status(String(err.message || err), true); });
  }

  function loadOlder() {
    if (!state.roomId || !state.oldest) { return; }
    var roomId = state.roomId;
    var url = '/api/rooms/' + encodeURIComponent(roomId) +
      '/messages?before=' + encodeURIComponent(state.oldest);
    status('이전 대화를 불러오는 중…');
    return api(url).then(function (data) {
      if (state.roomId !== roomId) { return; }
      var added = prependMessages(data.messages || []);
      state.maybeMore = !!data.maybe_more && added > 0;
      if (state.maybeMore) { show(el.loadOlder); } else { hide(el.loadOlder); }
      status(added ? '' : '더 이전 대화가 없다');
    })['catch'](function (err) { status(String(err.message || err), true); });
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
    el.loadOlder = $('load-older');
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
    on(el.loadOlder, 'click', loadOlder);
    on(el.jumpLatest, 'click', scrollToBottom);
    on(el.replyCancel, 'click', cancelReply);
    on(el.addRoom, 'submit', addRoom);
    on(el.toggleAdd, 'click', function () {
      if (el.addRoom.hidden) { show(el.addRoom); } else { hide(el.addRoom); }
    });
    on(el.addRoomCancel, 'click', function () { hide(el.addRoom); });
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
    });
    on(doc, 'visibilitychange', function () { reportVisibility(isVisible()); });
    on(global, 'beforeunload', function () { reportVisibility(false); });
  }

  function boot() {
    if (state.booted) { return; }
    state.booted = true;
    cache();
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
    appendMessage: appendMessage,
    prependMessages: prependMessages,
    clearTimeline: clearTimeline,
    switchRoom: switchRoom,
    loadOlder: loadOlder,
    renderRooms: renderRooms,
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

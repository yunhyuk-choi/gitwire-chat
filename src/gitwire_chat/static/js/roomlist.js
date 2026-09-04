/*
 * 방 목록 — `#rooms` · `#rooms-empty` · `#room-title` · `#room-sub` · `#back` ·
 * `#room-trouble*` 을 소유한다. 그리고 "지금 어느 방인가"를 방송한다.
 *
 * 방 목록은 대화가 아니다 — 짧고, 바뀔 때만 다시 그린다 (`replaceChildren`).
 * 타임라인의 append-only 규율은 **메시지**에 대한 것이라 여기엔 적용되지 않는다.
 * 그 둘이 한 파일에 있으면 이 구분을 매번 설명해야 한다 — 그래서 갈랐다.
 *
 * ⚠️ 실패한 방을 목록에서 **지우지 않는다.** 사용자가 왜 안 됐는지 볼 수 있어야
 * 하고, 재시도도 거기서 한다.
 */

import { errText } from './dom.js';

export function createRoomList(env) {
  var dom = env.dom;
  var bus = env.bus;
  var api = env.api;
  var status = env.status;

  var el = {
    rooms: dom.$('rooms'),
    roomsEmpty: dom.$('rooms-empty'),
    roomTitle: dom.$('room-title'),
    roomSub: dom.$('room-sub'),
    back: dom.$('back'),
    trouble: dom.$('room-trouble'),
    troubleText: dom.$('room-trouble-text'),
    troubleHint: dom.$('room-trouble-hint'),
    retry: dom.$('room-retry')
  };

  var rooms = [];
  var roomId = null;

  function find(id) {
    for (var i = 0; i < rooms.length; i++) {
      if (rooms[i].id === id) { return rooms[i]; }
    }
    return null;
  }

  function statusOf(id) {
    var room = find(id);
    return room ? (room.status || null) : null;
  }

  /* 연결 상태 → 사람이 읽는 한 줄. 서버가 준 값만 쓴다(추측하지 않는다). */
  function stateLabel(st) {
    if (!st) { return ''; }
    if (st.state === 'connecting') { return '받는 중…'; }
    if (st.state === 'failed') { return '실패 · ' + (st.detail || '사유 없음'); }
    return '';
  }

  function render(list) {
    rooms = list || [];
    el.rooms.replaceChildren();
    for (var i = 0; i < rooms.length; i++) {
      (function (room) {
        var li = dom.make('li', room.id === roomId ? 'room active' : 'room');
        var btn = dom.make('button', 'room-btn');
        btn.setAttribute('type', 'button');
        btn.appendChild(dom.make('span', 'room-name', room.name || room.repo_url));
        btn.appendChild(dom.make('span', 'room-url', room.repo_url));
        var label = stateLabel(room.status);
        if (label) {
          var cls = room.status.state === 'failed' ? 'room-state failed' : 'room-state';
          btn.appendChild(dom.make('span', cls, label));
        }
        btn.addEventListener('click', function () { select(room.id); });
        li.appendChild(btn);
        if (room.status && room.status.state === 'failed') {
          var again = dom.make('button', 'link', '재시도');
          again.setAttribute('type', 'button');
          again.addEventListener('click', function () { retryRoom(room.id); });
          li.className = li.className + ' room-row';
          li.appendChild(again);
        }
        el.rooms.appendChild(li);
      }(rooms[i]));
    }
    if (rooms.length) { dom.hide(el.roomsEmpty); } else { dom.show(el.roomsEmpty); }
    showTrouble();
    /* 현재 방의 상태 신호를 방송한다 — 타임라인이 '받는 중 → 준비됨' 을
       이 신호에 얹어 받는다 (새 배관을 만들지 않는다). */
    if (roomId) { bus.emit('room:status', { id: roomId, status: statusOf(roomId) }); }
  }

  /* 지금 보고 있는 방이 아직 안 붙었으면 그 자리에 사유를 남긴다. */
  function showTrouble(st) {
    if (!el.trouble) { return; }
    var current = st || statusOf(roomId);
    if (!roomId || !current || current.state === 'ready') {
      dom.hide(el.trouble);
      return;
    }
    dom.setText(el.troubleText,
      current.state === 'failed'
        ? '이 방을 열지 못했다 — ' + (current.detail || '사유 없음')
        : '방을 받는 중이다 (클론). 끝나면 대화가 바로 뜬다.');
    dom.setText(el.troubleHint, current.hint || '');
    if (el.retry) { el.retry.hidden = current.state !== 'failed'; }
    dom.show(el.trouble);
  }

  function select(id) {
    if (!id) { return; }
    roomId = id;
    render(rooms);
    var room = find(id);
    dom.setText(el.roomTitle, room ? (room.name || room.repo_url) : id);
    dom.setText(el.roomSub, room ? room.repo_url : '');
    dom.doc.body.dataset.view = 'chat';
    dom.doc.body.setAttribute('data-view', 'chat');
    var done = bus.emit('room:switch', { id: id });
    showTrouble();
    return done;
  }

  function retryRoom(id) {
    var target = id || roomId;
    if (!target) { return; }
    showTrouble({ state: 'connecting' });
    return api('/api/rooms/' + encodeURIComponent(target) + '/retry', { method: 'POST' })
      ['catch'](function (err) { status.set(errText(err), true); });
  }

  /* 방 목록의 단일 원천은 서버다. 받아서 방송하면 그리는 것은 위 `render` 다. */
  function reload() {
    return api('/api/rooms').then(function (data) {
      bus.emit('rooms:list', { rooms: data.rooms || [] });
      return data.rooms || [];
    });
  }

  function mount() {
    dom.on(el.back, 'click', function () {
      dom.doc.body.dataset.view = 'rooms';
      dom.doc.body.setAttribute('data-view', 'rooms');
    });
    dom.on(el.retry, 'click', function () { retryRoom(roomId); });

    bus.on('rooms:list', function (e) { render(e.rooms); });
    /* 타임라인이 409(받는 중·실패)를 만나면 그 자리를 여기서 그린다 —
       `#room-trouble` 은 이 모듈의 노드이므로 남이 만지지 않는다. */
    bus.on('room:trouble', function (e) {
      if (e.roomId !== roomId) { return; }
      showTrouble(e.status);
    });
    /* 새 방이 등록되면 목록을 다시 받고 그 방으로 들어간다. */
    bus.on('room:added', function (e) {
      reload().then(function () { select(e.id); });
    });
  }

  return {
    mount: mount,
    reload: reload,
    render: render,
    select: select,
    retryRoom: retryRoom,
    roomId: function () { return roomId; },
    rooms: function () { return rooms; }
  };
}

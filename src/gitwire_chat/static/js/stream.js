/*
 * 서버에서 오는 것들 — SSE 연결과 `#refresh`(지금 당겨오기) 버튼을 소유한다.
 *
 * 서버는 HTML 을 한 번만 주고, 그 뒤로는 JSON 한 건씩 흘려보낸다. 이 모듈은
 * 그것을 받아 **버스에 올릴 뿐** 화면을 그리지 않는다 — 무엇을 어떻게 그릴지는
 * 각 영역의 주인이 정한다.
 *
 *   `message:new`  → 타임라인이 붙인다
 *   `rooms:list`   → 방 목록이 그린다 (그리고 현재 방의 상태를 다시 방송한다)
 *   `trouble`      → 상태줄
 */

import { errText } from './dom.js';

export function createStream(env) {
  var dom = env.dom;
  var bus = env.bus;
  var api = env.api;
  var status = env.status;

  var el = { refresh: dom.$('refresh') };
  var roomId = null;
  var source = null;

  function disconnect() {
    if (source) {
      try { source.close(); } catch (err) { /* 이미 닫혔다 */ }
      source = null;
    }
  }

  function connect(id) {
    disconnect();
    if (!env.EventSource) {
      status.set('이 브라우저는 SSE 를 지원하지 않는다', true);
      return;
    }
    var url = '/api/rooms/' + encodeURIComponent(id) +
      '/stream?client=' + encodeURIComponent(env.client);
    var src = new env.EventSource(url);
    source = src;

    src.addEventListener('message', function (event) {
      var msg;
      try { msg = JSON.parse(event.data); } catch (err) { return; }
      bus.emit('message:new', { roomId: id, message: msg });
    });
    src.addEventListener('rooms', function (event) {
      var data;
      try { data = JSON.parse(event.data); } catch (err) { return; }
      bus.emit('rooms:list', { rooms: data.rooms || [] });
    });
    src.addEventListener('trouble', function (event) {
      try { status.set('폴링 경고: ' + JSON.parse(event.data).detail, true); }
      catch (err) { /* 무시 */ }
    });
    src.addEventListener('open', function () { status.set(''); });
    src.addEventListener('error', function () {
      status.set('연결이 끊겼다 — 다시 연결하는 중', true);
    });
  }

  /* 사용자가 **명시적으로** 누른 당겨오기. 조회 경로와 달리 여기서는 서버가
     원격을 실제로 본다(그게 이 버튼의 존재 이유다). */
  function refresh() {
    if (!roomId) { return; }
    status.set('당겨오는 중…');
    return api('/api/rooms/' + encodeURIComponent(roomId) + '/refresh', { method: 'POST' })
      .then(function (data) {
        status.set(data.delivered ? '새 메시지 ' + data.delivered + '건' : '새 메시지 없음');
      })['catch'](function (err) { status.set(errText(err), true); });
  }

  function mount() {
    dom.on(el.refresh, 'click', refresh);
    bus.on('room:switch', function (e) {
      roomId = e.id;
      connect(e.id);
    });
  }

  return {
    mount: mount,
    connect: connect,
    disconnect: disconnect,
    refresh: refresh,
    source: function () { return source; }
  };
}

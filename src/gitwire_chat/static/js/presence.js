/*
 * 가시성 보고 — "이 탭이 지금 이 방을 보고 있나"를 서버에 알린다.
 *
 * 서버는 이 값으로 **OS 알림을 띄울지**를 정한다(보고 있으면 안 띄운다).
 * DOM 노드를 소유하지 않고 document 의 가시성 이벤트만 듣는다 — 그래서 화면
 * 어느 영역과도 얽히지 않는다.
 */

export function createPresence(env) {
  var dom = env.dom;
  var bus = env.bus;
  var api = env.api;

  var roomId = null;

  function isVisible() {
    var vs = dom.doc.visibilityState;
    return !vs || vs === 'visible';
  }

  function report(id, visible) {
    if (!id) { return; }
    var path = '/api/rooms/' + encodeURIComponent(id) + '/visibility';
    api(path, { method: 'POST', body: { visible: !!visible, client: env.client } })
      ['catch'](function () { /* 보고 실패가 채팅을 막지 않는다 */ });
  }

  function mount() {
    dom.on(dom.doc, 'visibilitychange', function () { report(roomId, isVisible()); });
    dom.on(env.win, 'beforeunload', function () { report(roomId, false); });

    bus.on('room:switch', function (e) {
      if (roomId && roomId !== e.id) { report(roomId, false); }
      roomId = e.id;
      report(roomId, isVisible());
    });
  }

  return { mount: mount, report: report };
}

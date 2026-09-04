/*
 * 아웃박스 표시 — `#outbox`(그리고 그 안의 두 노드)**만** 소유한다.
 *
 * ⭐ 왜 이 표시가 생겼나
 *
 * 전송 응답이 원격 push 를 기다리지 않게 되면서 "보냈다"의 의미가 갈라졌다:
 *
 *   · 말풍선의 `보내는 중…` / `보내지 못했다` = **앱이 이 말을 받았나** (POST).
 *     실패하면 아무 데도 기록되지 않은 것이라, 재시도는 그 말풍선의 일이다.
 *   · 여기(방 단위) = **내 기기를 떠나 상대에게 갔나** (git push).
 *
 * 둘을 한 표시에 섞으면 "전송 실패"가 두 가지 전혀 다른 사고를 가리키게 된다.
 * 그래서 자리도, 문구도, 소유 모듈도 나눴다.
 *
 * ⚠️ 서버가 말하는 세 상태(`synced`/`sending`/`stuck`) 중 **`stuck` 만 그린다.**
 * `sending` 은 정상 경로이고 보통 몇 초다 — 매번 띄우면 사람이 곧 무시하게 되고,
 * 그러면 진짜 사고도 같이 묻힌다. 판정은 서버가 하고(`outbox.py`), 여기는 그
 * 값을 그리기만 한다.
 */

import { errText } from './dom.js';
import { roomPath } from './api.js';

export function createOutbox(env) {
  var dom = env.dom;
  var bus = env.bus;
  var api = env.api;
  var status = env.status;

  var el = {
    box: dom.$('outbox'),
    text: dom.$('outbox-text'),
    retry: dom.$('outbox-retry')
  };

  var roomId = null;
  var state = null;
  /* 서버가 방 목록에 실어 보낸 최신 값. 방 목록과 방 전환은 **순서가 정해져
     있지 않다**(부팅은 목록 → 전환 순서라 목록만 보면 첫 그리기를 놓친다).
     그래서 목록을 들고 있다가 둘 중 무엇이 오든 같은 함수로 다시 그린다. */
  var rooms = [];

  /* 상태 하나 → 화면 하나. 이 함수가 이 모듈의 전부다 (서버 값이 어디로 들어와도
     같은 문을 지난다 — 최초 그리기든 SSE 든). */
  function paint(next) {
    state = next || null;
    if (!state || state.state !== 'stuck') {
      dom.hide(el.box);
      return;
    }
    var n = state.pending || 0;
    var head = n
      ? ('아직 상대에게 못 간 말이 ' + n + '건 있다')
      : '아직 상대에게 못 간 말이 있다';
    dom.setText(
      el.text,
      head + (state.detail ? ' — ' + state.detail : '') +
      ' · 내 기기에는 남아 있고, 계속 다시 시도한다.'
    );
    dom.show(el.box);
  }

  /* 지금 방의 서버측 아웃박스 값 (없으면 null = 그릴 것 없음). */
  function fromList() {
    for (var i = 0; i < rooms.length; i++) {
      if (rooms[i].id === roomId) { return rooms[i].outbox || null; }
    }
    return null;
  }

  function retry() {
    if (!roomId) { return; }
    status.set('다시 보내는 중…');
    return api(roomPath(roomId, '/outbox'), { method: 'POST' })
      .then(function (data) {
        status.set('');
        /* 서버가 준 그 순간의 상태를 그대로 반영한다. 성공했는지는 곧 SSE 가
           말해 준다 — 여기서 낙관적으로 지우면 실패를 감추는 게 된다. */
        if (data && data.outbox) { paint(data.outbox); }
      })['catch'](function (err) { status.set(errText(err), true); });
  }

  function mount() {
    dom.on(el.retry, 'click', retry);
    bus.on('room:switch', function (e) { roomId = e.id; paint(fromList()); });
    /* 최초 그리기 — 방 목록에 실려 온다. 방을 막 열었을 때 이미 못 나간 말이
       있으면 *다음 변화를 기다리지 않고* 바로 보여야 한다. */
    bus.on('rooms:list', function (e) {
      rooms = e.rooms || [];
      paint(fromList());
    });
    /* 이후 변화 — 방 하나짜리 이벤트로 온다. */
    bus.on('outbox:state', function (e) {
      if (e.roomId === roomId) { paint(e.state); }
    });
  }

  return {
    mount: mount,
    paint: paint,
    retry: retry,
    state: function () { return state; }
  };
}

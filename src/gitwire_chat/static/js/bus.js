/*
 * 모듈 사이의 **유일한** 통신로.
 *
 * 예전에는 모든 화면 조각이 하나의 IIFE 안에서 같은 `state` 객체를 읽고 썼다.
 * 그래서 "누가 무엇을 바꿨나"를 말할 수 없었고, 초기화 하나가 터지면 전부 죽었다.
 * 지금은 모듈이 **자기 상태와 자기 DOM 영역만** 소유하고, 남에게 알릴 일은
 * 여기로 흘린다. 의존이 이름 있는 이벤트로 드러나고, 구독자가 없으면(그 모듈이
 * 실패해서 서지 못했으면) 그냥 아무 일도 일어나지 않는다 — 격리가 저절로 된다.
 *
 * ⚠️ 한 구독자의 예외가 다른 구독자를 죽이지 않는다. 그렇다고 **삼키지도 않는다** —
 * `onError` 로 올려 보내고, 부트가 그것을 화면·콘솔에 남긴다.
 *
 * `stick()` 은 "마지막 값이 곧 현재 상태"인 신호용이다(예: 표시 이름). 나중에
 * 붙은 구독자도 즉시 현재 값을 받으므로 **모듈을 세우는 순서에 의존하지 않는다** —
 * 순서 의존은 우리가 방금 걷어낸 그 병이다.
 */

export function createBus(onError) {
  var handlers = Object.create(null);
  var sticky = Object.create(null);

  function fail(type, err) {
    if (onError) { onError(type, err); } else { throw err; }
  }

  /* 구독자가 promise 를 돌려주면 그것도 기다린다 — 그래야 부트가 "방을 열고
     대화까지 채웠다"를 하나의 promise 로 이어 붙일 수 있다. */
  function call(fn, type, payload) {
    try {
      var out = fn(payload);
      if (out && typeof out.then === 'function') {
        return out.then(null, function (err) { fail(type, err); });
      }
      return out;
    } catch (err) {
      fail(type, err);
    }
  }

  function on(type, fn) {
    (handlers[type] = handlers[type] || []).push(fn);
    if (type in sticky) { call(fn, type, sticky[type]); }
    return function off() {
      var list = handlers[type] || [];
      var i = list.indexOf(fn);
      if (i >= 0) { list.splice(i, 1); }
    };
  }

  /* 구독자는 **동기로** 불린다 (화면 갱신이 다음 틱으로 밀리면 안 된다).
     반환값만 promise 라, 기다리고 싶은 호출자가 기다릴 수 있다. */
  function emit(type, payload) {
    var list = (handlers[type] || []).slice();
    var out = [];
    for (var i = 0; i < list.length; i++) { out.push(call(list[i], type, payload)); }
    return Promise.all(out);
  }

  function stick(type, payload) {
    sticky[type] = payload;
    emit(type, payload);
  }

  return { on: on, emit: emit, stick: stick };
}

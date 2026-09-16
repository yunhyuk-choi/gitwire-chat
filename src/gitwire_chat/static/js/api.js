/*
 * 서버 호출 한 겹. DOM 을 모르고 상태도 없다.
 *
 * 실패해도 **서버가 함께 준 것**(연결 상태·사유·힌트)을 잃지 않는다 —
 * "왜 안 되는지"를 화면에 남기는 것이 이 앱의 규칙이라, 그 재료가 예외 객체에
 * 실려 있어야 한다.
 *
 * `options.headers` 는 호출한 쪽이 헤더를 **하나만** 얹을 수 있게 열어 둔
 * 구멍이다. 지금 그걸 쓰는 곳은 갱신 실행 하나뿐이고(`update.js`), 이유는
 * 서버가 그 헤더로 "우리 화면에서 온 요청인가"를 가르기 때문이다
 * (`gitwire_chat/csrf.py`). 모든 요청에 일괄로 붙이지 않는다 — 문이 필요한
 * 곳은 한 곳이고, 여기 열어 두면 "왜 이 헤더가 붙나"가 호출 지점에 남는다.
 */

export function createApi(fetchImpl) {
  return function api(path, options) {
    var opts = options || {};
    var init = { method: opts.method || 'GET', headers: {} };
    var extra = opts.headers || {};
    for (var name in extra) {
      if (Object.prototype.hasOwnProperty.call(extra, name)) {
        init.headers[name] = extra[name];
      }
    }
    if (opts.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    }
    /* ⭐ `keepalive` 는 **창이 닫히는 중에도 요청을 살려 두라**는 표준 플래그다.
       쓰는 곳은 언로드 경로 하나뿐이고(가용 상태의 `자리 비움`), 그 자리에서는
       이것이 없으면 브라우저가 요청을 취소해 마지막 선언이 영영 나가지 않는다.
       모든 요청에 일괄로 붙이지 않는다 — 브라우저가 이 플래그에 별도의 (작은)
       본문 상한을 두기 때문이고, 필요한 곳이 한 곳이면 호출 지점에 이유가 남는다. */
    if (opts.keepalive) { init.keepalive = true; }
    return fetchImpl(path, init).then(function (res) {
      return res.json().then(function (data) {
        if (!res.ok) {
          var err = new Error((data && data.error) || ('HTTP ' + res.status));
          err.status = res.status;
          err.payload = data || {};
          throw err;
        }
        return data;
      });
    });
  };
}

export function roomPath(roomId, suffix) {
  return '/api/rooms/' + encodeURIComponent(roomId) + (suffix || '');
}

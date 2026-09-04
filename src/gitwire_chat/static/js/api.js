/*
 * 서버 호출 한 겹. DOM 을 모르고 상태도 없다.
 *
 * 실패해도 **서버가 함께 준 것**(연결 상태·사유·힌트)을 잃지 않는다 —
 * "왜 안 되는지"를 화면에 남기는 것이 이 앱의 규칙이라, 그 재료가 예외 객체에
 * 실려 있어야 한다.
 */

export function createApi(fetchImpl) {
  return function api(path, options) {
    var opts = options || {};
    var init = { method: opts.method || 'GET', headers: {} };
    if (opts.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    }
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

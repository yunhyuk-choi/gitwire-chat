/*
 * 검색 — `#toggle-search` · `#search-bar` · `#search-q` · `#search-results` 를
 * 소유한다.
 *
 * ⭐ 서버가 **레코드 파일을 뒤진다.** 그래서 가상 스크롤 때문에 DOM 에 없는
 * 과거도 그대로 찾힌다 — 화면에 무엇이 붙어 있는지와 무관하다. 결과 목록은
 * 대화가 아니라 조회 결과이므로 검색할 때마다 통째로 다시 그린다(그게 맞다).
 */

import { errText, timeLabel } from './dom.js';

export function createSearch(env) {
  var dom = env.dom;
  var bus = env.bus;
  var api = env.api;
  var status = env.status;

  var el = {
    toggle: dom.$('toggle-search'),
    bar: dom.$('search-bar'),
    query: dom.$('search-q'),
    close: dom.$('search-close'),
    results: dom.$('search-results'),
    list: dom.$('search-list'),
    summary: dom.$('search-summary')
  };

  var roomId = null;

  function run() {
    var q = (el.query.value || '').trim();
    if (!q || !roomId) { return; }
    var url = '/api/rooms/' + encodeURIComponent(roomId) +
      '/search?q=' + encodeURIComponent(q);
    return api(url).then(function (data) {
      var list = data.messages || [];
      el.list.replaceChildren();
      dom.setText(el.summary, '"' + q + '" — ' + list.length + '건 (서버가 레코드를 뒤졌다)');
      for (var i = 0; i < list.length; i++) {
        var hit = dom.make('div', 'hit');
        hit.appendChild(dom.make('span', 'author', list[i].author));
        hit.appendChild(dom.make('time', 'ts', timeLabel(list[i].ts)));
        hit.appendChild(dom.make('div', 'body', list[i].text));
        el.list.appendChild(hit);
      }
      dom.show(el.results);
    })['catch'](function (err) { status.set(errText(err), true); });
  }

  function mount() {
    dom.on(el.toggle, 'click', function () {
      if (el.bar.hidden) {
        dom.show(el.bar);
      } else {
        dom.hide(el.bar);
        dom.hide(el.results);
      }
    });
    dom.on(el.bar, 'submit', function (e) {
      if (e.preventDefault) { e.preventDefault(); }
      run();
    });
    dom.on(el.close, 'click', function () {
      dom.hide(el.bar);
      dom.hide(el.results);
    });

    bus.on('room:switch', function (e) {
      roomId = e.id;
      dom.hide(el.results);      /* 다른 방의 검색 결과를 들고 가지 않는다 */
    });
  }

  return { mount: mount, run: run };
}

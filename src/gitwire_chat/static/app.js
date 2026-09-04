/*
 * 진입점. **여기서만** 브라우저 전역을 만진다.
 *
 * 나머지 모듈(`js/*.js`)은 `document`·`fetch`·`EventSource` 같은 전역을 직접
 * 집어오지 않고 전부 주입받는다. 그래서
 *   · 모듈이 자기 실행 환경을 몰라도 되고,
 *   · 테스트가 stub DOM 을 그대로 끼워 **진짜 코드**를 구동할 수 있으며,
 *   · 무엇에 의존하는지가 import 와 인자로 눈에 보인다.
 *
 * 가상 스크롤 엔진도 여기서 **직접 import** 한다. 예전에는 인라인 모듈이
 * `window.TanStackVirtual` 에 담아 두고 classic 스크립트인 app.js 가 그것을
 * 집어갔는데, 그 방식은 "실렸나 / 그때 실렸나"라는 순서 문제를 만든다.
 * import 는 그 문제를 정의상 없앤다 — 못 실으면 여기서 즉시 드러난다.
 */

import * as virtual from './vendor/tanstack-virtual-core/index.js';
import { createApp } from './js/boot.js';

var chat = createApp({
  doc: document,
  win: window,
  fetch: function (path, init) { return window.fetch(path, init); },
  EventSource: window.EventSource,
  IntersectionObserver: window.IntersectionObserver,
  localStorage: window.localStorage,
  console: window.console,
  virtual: virtual
});

/* 디버깅·테스트가 붙는 창. 앱 코드는 이걸 쓰지 않는다. */
window.__chat = chat;

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', function () { chat.boot(); });
} else {
  chat.boot();
}

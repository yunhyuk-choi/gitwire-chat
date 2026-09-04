/*
 * DOM 헬퍼 — 상태가 없다. 모든 모듈이 이걸 통해서만 노드를 만든다.
 *
 * ⭐ `innerHTML` 이 이 앱 어디에도 없다는 규율은 여기서 시작한다. 텍스트를 넣는
 * 길이 `setText`(= `textContent`) 하나뿐이면, HTML 문자열로 화면을 갱신하는
 * 경로가 애초에 생기지 않는다 (덤으로 XSS 원천 봉쇄).
 *
 * `document` 를 전역에서 집어오지 않고 **주입받는다** — 그래야 테스트가 stub
 * DOM 을 그대로 끼울 수 있고, 모듈이 자기 실행 환경을 몰라도 된다.
 */

export function createDom(doc) {
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

  function show(node) { if (node) { node.hidden = false; } }
  function hide(node) { if (node) { node.hidden = true; } }

  function on(node, type, fn) {
    if (node && node.addEventListener) { node.addEventListener(type, fn); }
  }

  return { doc: doc, $: $, make: make, setText: setText, show: show, hide: hide, on: on };
}

/* 예외를 사람이 읽는 한 줄로. (예외 객체·문자열·이벤트 무엇이 와도 된다.) */
export function errText(err) {
  if (!err) { return '알 수 없는 오류'; }
  if (err.message) { return String(err.message); }
  return String(err);
}

export function uid() {
  return 'c' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}

/* 표시 시각 — 오늘이면 HH:MM, 아니면 M/D HH:MM. */
export function timeLabel(iso) {
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

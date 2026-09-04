/*
 * 상태줄 — `#status` **하나만** 소유한다.
 *
 * ⭐ 조용한 실패 금지. 이 앱을 쓰는 사람은 개발자 콘솔을 열지 않으므로, 콘솔에만
 * 남는 예외는 "아무 일도 안 일어나는 앱"과 구분되지 않는다.
 *
 * 그래서 두 종류를 구분한다:
 *   · `set()`  — 일상적인 진행 상황 ("불러오는 중…"). 다음 것이 덮는다.
 *   · `stick()`— **사라지면 안 되는 사실** (초기화 실패·결함). 일상적인 `set('')`
 *                로 지워지지 않는다. 지워지는 순간부터 다시 조용한 실패다.
 */

export function createStatusBar(dom) {
  var node = dom.$('status');
  var sticky = '';

  function set(text, isError) {
    if (!node) { return; }
    if (!text && sticky) {
      dom.setText(node, sticky);
      node.className = 'status error';
      return;
    }
    dom.setText(node, text || '');
    node.className = isError ? 'status error' : 'status';
  }

  function stick(text) {
    sticky = text || '';
    set(sticky, true);
  }

  return {
    set: set,
    stick: stick,
    sticky: function () { return sticky; }
  };
}

/*
 * 아주 작은 stub DOM.
 *
 * 브라우저가 없으니 app.js 를 진짜 DOM 대신 이 위에서 돌린다. 목적은 화면을
 * 재현하는 것이 아니라 **DOM 조작을 세는 것**이다:
 *
 *   - 노드를 몇 개 만들었나 (createElement)
 *   - 붙였나 / 끼웠나 / 지웠나 (appendChild / insertBefore / removeChild)
 *   - innerHTML 을 쓴 적이 있나  ← 있으면 그 자체로 실패다
 *
 * 그래서 "전체 리렌더가 없다"를 눈이 아니라 **수**로 판정할 수 있다.
 *
 * 가상 스크롤(@tanstack/virtual-core)이 들어오면서 **레이아웃 흉내**가 조금 필요해졌다.
 * 라이브러리는 진짜다(대역이 아니다) — 그것이 읽는 값만 우리가 준다:
 *   - 스크롤 요소의 `offsetHeight`(뷰포트 높이)·`scrollTop`·`scrollTo`
 *   - 항목 노드의 `offsetHeight`  ← **가변 높이의 원천**
 *   - `ownerDocument.defaultView`(라이브러리가 window 를 찾는 경로)
 * 높이는 노드에 심어 둔 `_height` 를 쓴다. 테스트가 메시지마다 다른 값을 넣어
 * "길이가 제각각인 대화"를 만든다.
 */

export class ClassList {
  constructor(node) { this.node = node; }
  add(name) {
    const parts = String(this.node.className || '').split(/\s+/).filter(Boolean);
    if (parts.indexOf(name) < 0) { parts.push(name); }
    this.node.className = parts.join(' ');
  }
  remove(name) {
    this.node.className = String(this.node.className || '')
      .split(/\s+/).filter(Boolean).filter((p) => p !== name).join(' ');
  }
  contains(name) {
    return String(this.node.className || '').split(/\s+/).indexOf(name) >= 0;
  }
}

export class Node {
  constructor(tag, doc) {
    this.tagName = String(tag || '').toUpperCase();
    this.doc = doc;
    this.ownerDocument = doc;
    this.children = [];
    this.parent = null;
    this.attrs = {};
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this.className = '';
    this.hidden = false;
    this.disabled = false;
    this.value = '';
    this.scrollTop = 0;
    this.clientHeight = 0;
    this.replaceCount = 0;
    this.removedByReplace = 0;
    this._text = '';
    this.isFragment = false;
    /* 이 노드가 차지하는 높이(px). 테스트가 정하면 가변 높이가 된다. */
    this._height = 0;
    this._width = 400;
  }

  /* 가상화 라이브러리가 항목 높이를 읽는 곳. */
  get offsetHeight() {
    if (this._height) { return this._height; }
    /* 정하지 않았으면 자손 수로 대충 만든다 (예전 scrollHeight 규칙과 같은 뜻). */
    let n = 0;
    const walk = (node) => { for (const c of node.children) { n += 1; walk(c); } };
    walk(this);
    return n * 20;
  }
  set offsetHeight(value) { this._height = value; }
  get offsetWidth() { return this._width; }
  set offsetWidth(value) { this._width = value; }

  getBoundingClientRect() {
    return {
      width: this.offsetWidth, height: this.offsetHeight,
      top: 0, left: 0, right: this.offsetWidth, bottom: this.offsetHeight
    };
  }

  /* 스크롤 요소로 쓰일 때 (라이브러리가 부른다). */
  scrollTo(options) {
    const top = options && typeof options === 'object' ? options.top : options;
    if (typeof top === 'number') {
      this.scrollTop = Math.max(0, Math.min(top, this.scrollHeight));
      this.dispatch('scroll');
    }
  }

  /* textContent 만 쓰게 하려는 것이 요점이다. innerHTML 은 세팅되면 카운트한다. */
  get textContent() {
    if (this.children.length) {
      return this.children.map((c) => c.textContent).join('');
    }
    return this._text;
  }
  set textContent(value) {
    this.children.forEach((c) => { c.parent = null; });
    this.children = [];
    this._text = value == null ? '' : String(value);
  }

  /* 스크롤 높이.
     가상 스크롤은 컨테이너에 **전체 높이**를 style.height 로 적는다 — 화면 밖
     항목은 DOM 에 없으므로 자손 수로 재면 틀린다. 그래서 style.height 가 있으면
     그것이 우선이고, 자식들의 style.height 합도 반영한다. */
  get scrollHeight() {
    const own = parseFloat(this.style && this.style.height) || 0;
    if (own) { return own; }
    let total = 0;
    for (const child of this.children) {
      const h = parseFloat(child.style && child.style.height) || 0;
      total += h || child.offsetHeight;
    }
    if (total) { return total; }
    let n = 0;
    const walk = (node) => { for (const c of node.children) { n += 1; walk(c); } };
    walk(this);
    return n * 20;
  }
  set scrollHeight(_v) { /* 계산값이라 대입은 무시한다 */ }

  get innerHTML() { return ''; }
  set innerHTML(value) { this.doc.counts.innerHTML += 1; this._text = String(value); }

  /* 진짜 DOM 과 같은 이름을 쓴다 — 앱 코드가 표준 API 로 쓰게. */
  get parentNode() { return this.parent; }
  get classList() { return new ClassList(this); }
  get firstChild() { return this.children.length ? this.children[0] : null; }
  get lastChild() { return this.children.length ? this.children[this.children.length - 1] : null; }
  get nextSibling() {
    if (!this.parent) { return null; }
    const i = this.parent.children.indexOf(this);
    return this.parent.children[i + 1] || null;
  }

  _adopt(node) {
    if (node.isFragment) { return node.children.splice(0, node.children.length); }
    if (node.parent) {
      const i = node.parent.children.indexOf(node);
      if (i >= 0) { node.parent.children.splice(i, 1); this.doc.counts.moved += 1; }
    }
    return [node];
  }

  appendChild(node) {
    const items = this._adopt(node);
    for (const item of items) { item.parent = this; this.children.push(item); }
    this.doc.counts.appendChild += 1;
    return node;
  }

  insertBefore(node, ref) {
    const items = this._adopt(node);
    let index = ref ? this.children.indexOf(ref) : -1;
    if (index < 0) { index = this.children.length; }
    for (let i = 0; i < items.length; i++) {
      items[i].parent = this;
      this.children.splice(index + i, 0, items[i]);
    }
    this.doc.counts.insertBefore += 1;
    return node;
  }

  removeChild(node) {
    const i = this.children.indexOf(node);
    if (i >= 0) { this.children.splice(i, 1); node.parent = null; this.doc.counts.removeChild += 1; }
    return node;
  }

  replaceChildren() {
    this.doc.counts.replaceChildren += 1;
    this.doc.counts.removedByReplace += this.children.length;
    this.replaceCount += 1;
    this.removedByReplace += this.children.length;
    this.children.forEach((c) => { c.parent = null; });
    this.children = [];
  }

  remove() { if (this.parent) { this.parent.removeChild(this); } }

  setAttribute(name, value) {
    this.attrs[name] = String(value);
    if (name.indexOf('data-') === 0) {
      const key = name.slice(5).replace(/-([a-z])/g, (m, c) => c.toUpperCase());
      this.dataset[key] = String(value);
    }
  }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null; }

  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  removeEventListener(type, fn) {
    const list = this.listeners[type] || [];
    const i = list.indexOf(fn);
    if (i >= 0) { list.splice(i, 1); }
  }
  dispatch(type, event) {
    for (const fn of (this.listeners[type] || []).slice()) { fn(event || { type }); }
  }
  focus() {}
  querySelector() { return null; }
}

export class StubDocument {
  constructor() {
    this.counts = {
      createElement: 0, appendChild: 0, insertBefore: 0, removeChild: 0,
      replaceChildren: 0, removedByReplace: 0, innerHTML: 0, moved: 0
    };
    this.byId = {};
    this.readyState = 'loading';
    this.visibilityState = 'visible';
    this.listeners = {};
    this.body = new Node('body', this);
    /* ResizeObserver 는 일부러 두지 않는다 — 없으면 라이브러리가 offsetHeight
       폴백을 쓰고, 그래야 테스트가 높이를 **결정론적으로** 통제할 수 있다. */
    this.window = {
      document: this, setTimeout, clearTimeout,
      requestAnimationFrame: (fn) => setTimeout(fn, 0),
      cancelAnimationFrame: clearTimeout,
      addEventListener() {}, removeEventListener() {}
    };
  }
  createElement(tag) { this.counts.createElement += 1; return new Node(tag, this); }
  /* 라이브러리가 `scrollElement.ownerDocument.defaultView` 로 window 를 찾는다. */
  get defaultView() { return this.window; }
  createDocumentFragment() {
    const frag = new Node('#fragment', this);
    frag.isFragment = true;
    return frag;
  }
  getElementById(id) { return this.byId[id] || null; }
  register(id) {
    const node = new Node('div', this);
    node.id = id;
    this.byId[id] = node;
    return node;
  }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  dispatch(type, event) { for (const fn of (this.listeners[type] || []).slice()) { fn(event || { type }); } }
}

/* index.html 이 실제로 갖고 있는 id 들. app.js 가 찾는 것과 어긋나면
   여기가 먼저 깨지므로 템플릿↔스크립트 계약 검사도 된다. */
export const ELEMENT_IDS = [
  'rooms', 'rooms-empty', 'messages', 'timeline', 'room-title', 'room-sub',
  'status', 'composer', 'text', 'author', 'send', 'older-sentinel', 'older-note',
  'jump-latest',
  'reply-chip', 'reply-label', 'reply-cancel', 'add-room', 'add-room-submit',
  'add-room-error', 'repo-url', 'room-name', 'token-env', 'toggle-add',
  'add-room-cancel', 'back', 'refresh', 'toggle-search', 'search-bar',
  'search-q', 'search-close', 'search-results', 'search-list', 'search-summary',
  'room-trouble', 'room-trouble-text', 'room-trouble-hint', 'room-retry',
  'new-repo-toggle', 'new-repo-form', 'new-repo-owner', 'new-repo-name',
  'new-repo-check', 'new-repo-plan', 'new-repo-link', 'new-repo-create',
  'new-repo-use', 'new-repo-error',
  'outbox', 'outbox-text', 'outbox-retry'
];

/* IntersectionObserver 대역.
 *
 * 진짜 관찰자는 "표식이 화면에 들어오면" 알아서 발화한다. 여기서는 그 순간을
 * 테스트가 `trigger()` 로 만든다 — 발화 시점을 손에 쥐어야 "연속 발화해도 요청은
 * 한 번인가", "맨 위에 닿으면 조용히 멈추는가"를 셀 수 있다.
 *
 * `observe`/`unobserve`/`disconnect` 를 진짜처럼 지키는 것이 요점이다:
 * 앱이 로딩 중에 관찰을 끊었다면 `trigger()` 를 해도 콜백이 오면 안 된다.
 */
export class StubIntersectionObserver {
  constructor(callback, options) {
    this.callback = callback;
    this.options = options || {};
    this.observing = new Set();
    this.disconnected = false;
    StubIntersectionObserver.created.push(this);
  }
  observe(node) { this.observing.add(node); this.disconnected = false; }
  unobserve(node) { this.observing.delete(node); }
  disconnect() { this.observing.clear(); this.disconnected = true; }
  /* 관찰 중인 표식이 화면에 들어왔다고 알린다. 관찰이 끊겼으면 아무 일도 없다. */
  trigger() {
    const entries = [...this.observing].map((node) => ({
      target: node, isIntersecting: true, intersectionRatio: 1
    }));
    if (!entries.length) { return 0; }
    this.callback(entries, this);
    return entries.length;
  }
  static reset() { StubIntersectionObserver.created = []; }
  static get current() {
    return StubIntersectionObserver.created[StubIntersectionObserver.created.length - 1];
  }
}
StubIntersectionObserver.created = [];

export class StubEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    this.closed = false;
    StubEventSource.opened.push(this);
  }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  close() { this.closed = true; }
  emit(type, data) {
    const event = { type, data: typeof data === 'string' ? data : JSON.stringify(data) };
    for (const fn of (this.listeners[type] || []).slice()) { fn(event); }
  }
  static reset() { StubEventSource.opened = []; }
  static get current() { return StubEventSource.opened[StubEventSource.opened.length - 1]; }
}
StubEventSource.opened = [];

/* 아주 작은 fetch 대역: 경로 → 응답 JSON. 호출 기록도 남긴다.
   응답에 `__http` 를 넣으면 그 상태코드로 답한다 (409 처럼 '오류가 아닌 상태'). */
export function makeFetch(routes) {
  const calls = [];
  const fetch = function (path, init) {
    calls.push({ path: path, init: init || {} });
    /* ⚠️ **가장 긴** 접두사가 이긴다. 먼저 선언된 것이 이기게 하면
       '/api/rooms' 가 '/api/rooms/r1/search?...' 를 삼켜, 테스트가 엉뚱한
       응답을 받고도 조용히 통과한다(실제로 한 번 그랬다). */
    const key = Object.keys(routes)
      .filter((k) => path.indexOf(k) === 0)
      .sort((a, b) => b.length - a.length)[0] || null;
    const handler = key ? routes[key] : null;
    const raw = handler
      ? (typeof handler === 'function' ? handler(path, init) : handler)
      : { error: 'stub 라우트 없음: ' + path };
    const code = raw && raw.__http ? raw.__http : (key ? 200 : 404);
    const body = Object.assign({}, raw);
    delete body.__http;
    return Promise.resolve({
      ok: code >= 200 && code < 300,
      status: code,
      json: function () { return Promise.resolve(body); }
    });
  };
  fetch.calls = calls;
  return fetch;
}

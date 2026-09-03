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

  /* 실제 브라우저처럼 자손 수에 비례해 늘어나는 값 (스크롤 앵커링 검증용). */
  get scrollHeight() {
    let n = 0;
    const walk = (node) => { for (const c of node.children) { n += 1; walk(c); } };
    walk(this);
    return n * 20;
  }
  set scrollHeight(_v) { /* 계산값이라 대입은 무시한다 */ }

  get innerHTML() { return ''; }
  set innerHTML(value) { this.doc.counts.innerHTML += 1; this._text = String(value); }

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
  }
  createElement(tag) { this.counts.createElement += 1; return new Node(tag, this); }
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
  'status', 'composer', 'text', 'author', 'send', 'load-older', 'jump-latest',
  'reply-chip', 'reply-label', 'reply-cancel', 'add-room', 'add-room-submit',
  'add-room-error', 'repo-url', 'room-name', 'token-env', 'toggle-add',
  'add-room-cancel', 'back', 'refresh', 'toggle-search', 'search-bar',
  'search-q', 'search-close', 'search-results', 'search-list', 'search-summary'
];

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

/* 아주 작은 fetch 대역: 경로 → 응답 JSON. 호출 기록도 남긴다. */
export function makeFetch(routes) {
  const calls = [];
  const fetch = function (path, init) {
    calls.push({ path: path, init: init || {} });
    const key = Object.keys(routes).find((k) => path.indexOf(k) === 0);
    const handler = key ? routes[key] : null;
    const body = handler
      ? (typeof handler === 'function' ? handler(path, init) : handler)
      : { error: 'stub 라우트 없음: ' + path };
    return Promise.resolve({
      ok: !!key,
      status: key ? 200 : 404,
      json: function () { return Promise.resolve(body); }
    });
  };
  fetch.calls = calls;
  return fetch;
}

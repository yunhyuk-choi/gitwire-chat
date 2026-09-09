/*
 * ⭐ "전체 리렌더가 없다"를 **세어서** 증명한다.
 *
 * 브라우저가 없으니 stub DOM 위에서 **진짜 모듈**을 구동하고, DOM 조작 횟수와
 * **노드의 동일성(===)** 을 확인한다. 노드가 같은 객체로 남아 있다는 것이
 * "다시 그리지 않았다"의 가장 강한 증거다 — 다시 그렸다면 새 객체일 수밖에 없다.
 *
 * ⭐ 앱이 ES 모듈로 갈라진 뒤로는 `vm` 으로 통짜 스크립트를 평가하지 않는다.
 * `createApp(runtime)` 을 **그대로 import** 해서 stub 런타임(document·fetch·
 * EventSource…)을 주입한다. 프로덕션이 실제로 하는 일과 같은 모양이라
 * (`static/app.js` 가 진짜 전역으로 같은 함수를 부른다) 하네스와 실물이 어긋나지
 * 않는다.
 *
 * 실행: node tests/js/render.test.mjs
 * 실패하면 종료코드가 0 이 아니다 (pytest 가 그걸 본다).
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import url from 'node:url';

import {
  ELEMENT_IDS, StubDocument, StubEventSource, StubIntersectionObserver, makeFetch
} from './stub-dom.mjs';

const here = path.dirname(url.fileURLToPath(import.meta.url));
const ROOT = path.resolve(here, '..', '..');
const STATIC = path.join(ROOT, 'src', 'gitwire_chat', 'static');
const INDEX_HTML = path.join(ROOT, 'src', 'gitwire_chat', 'templates', 'index.html');

const indexHtml = fs.readFileSync(INDEX_HTML, 'utf8');

/* 우리 소스 전부 (벤더 제외) — 정적 검사가 이 목록을 훑는다. */
const OUR_JS = [path.join(STATIC, 'app.js')].concat(
  fs.readdirSync(path.join(STATIC, 'js')).sort()
    .map((name) => path.join(STATIC, 'js', name))
);

/* ⭐ 가상 스크롤 엔진은 **진짜**를 쓴다 (벤더링된 그 파일 그대로).
   대역으로 바꾸면 "가상화가 실제로 도는가"를 아무것도 증명하지 못한다. */
const virtual = await import(
  url.pathToFileURL(path.join(STATIC, 'vendor', 'tanstack-virtual-core', 'index.js')).href
);

/* 진짜 조립소. 프로덕션 진입점(static/app.js)이 부르는 그 함수다. */
const { createApp } = await import(
  url.pathToFileURL(path.join(STATIC, 'js', 'boot.js')).href
);

/* 레이아웃 이음새를 **직접** 들여다보는 두 모듈 (표와 목록이 짝인지 본다). */
const nodeMod = await import(
  url.pathToFileURL(path.join(STATIC, 'js', 'message-node.js')).href
);
const themeMod = await import(
  url.pathToFileURL(path.join(STATIC, 'js', 'theme.js')).href
);
/* 묶기 규칙(시간 컷오프·머리 판정)은 **모델을 소유한** 타임라인 것이다.
   상수를 테스트가 베끼면 둘이 어긋나도 통과하므로 원천에서 읽는다. */
const timelineMod = await import(
  url.pathToFileURL(path.join(STATIC, 'js', 'timeline.js')).href
);

/* ⭐ 읽음 카운트 공식의 **유일한 원천**. 테스트가 공식을 베끼면 둘이 어긋나도
   통과하므로 원천에서 가져온다 (디바운스 값도 같은 이유). */
const readsMod = await import(
  url.pathToFileURL(path.join(STATIC, 'js', 'reads.js')).href
);

/* 갱신 모듈의 상수(주기·포기 횟수·화면 표식 헤더)를 **원천에서** 읽는다 —
   테스트가 숫자를 베끼면 둘이 어긋나도 통과한다. */
const updateMod = await import(
  url.pathToFileURL(path.join(STATIC, 'js', 'update.js')).href
);

const results = [];
function test(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => { results.push(['PASS', name]); },
      (err) => { results.push(['FAIL', name, err && err.message]); });
}

/* ------------------------------------------------------------- 도우미 */

/* 가상화가 실제로 읽는 높이. 테스트가 노드에 심어 준다(stub DOM 의 _height). */
function heightsFor(doc, sizes) {
  const list = doc.getElementById('messages');
  for (const node of list.children) {
    const id = node.dataset ? node.dataset.id : '';
    if (sizes[id] !== undefined) { node.offsetHeight = sizes[id]; }
  }
}

function msg(n, text, author) {
  const stamp = '20260903T0100' + String(n).padStart(2, '0') + '000Z';
  return {
    id: 'records/20260903/' + stamp + '-a-' + String(n).padStart(6, '0') + '.json',
    author: author || '앨리스',
    text: text || ('메시지 ' + n),
    ts: '2026-09-03T01:00:' + String(n).padStart(2, '0') + 'Z',
    sender: 'a.host',
    kind: 'msg',
    reply_to: null,
    unknown: false,
    /* 서버가 봉투를 보고 판정해서 실어 보내는 값. 기본은 '남의 것'이고,
       내 것을 만들려면 `mineMsg()` 를 쓴다 — 화면은 이 값만 본다. */
    mine: false
  };
}

/* 서버가 '내 것'이라고 판정해서 보낸 레코드. */
function mineMsg(n, text) {
  return Object.assign(msg(n, text, '기본이름'), { sender: 'me.host', mine: true });
}

/* 읽음 스냅샷 대역. 서버가 주는 모양 그대로다 (`gitwire_chat/reads.py`). */
function emptyReads() {
  return { me: 'me@x.io', person: 'me@x.io', cursor: '', unread: 0,
    first_unread: null, participants: [] };
}

function who(key, cursor, senders) {
  return {
    person: key, key: key, cursor: cursor || '',
    senders: senders || [key.split('@')[0] + '.host'], updated_at: ''
  };
}

function readsOf(list, extra) {
  return Object.assign(emptyReads(), { participants: list }, extra || {});
}

/* 노드에 그려진 읽음 카운트 (없으면 빈 문자열). */
function readCount(node) {
  const slot = node.readsSlot;
  if (!slot || slot.hidden) { return ''; }
  return slot.textContent;
}

/* 말풍선이 오른쪽(내 것)에 있나 — CSS 는 `.msg.mine` 하나로 그걸 정한다. */
function isMine(node) {
  return String(node.className).split(' ').indexOf('mine') >= 0;
}

/* window 대역 — 모듈이 전역을 직접 집어오지 않으므로 이 정도면 충분하다.
 *
 * 타이머는 **테스트가 쥔다**(진짜 setTimeout 이 아니다). 이유가 둘:
 *   · IME 안전 타이머(3초)를 실제로 기다리지 않고 만료시킬 수 있다.
 *   · "언제 도는가"가 손에 있어야 "확정 신호 없이는 안 보낸다"를 셀 수 있다.
 */
function stubWindow() {
  return {
    listeners: {},
    timers: [],
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    removeEventListener(type, fn) {
      const list = this.listeners[type] || [];
      const i = list.indexOf(fn);
      if (i >= 0) { list.splice(i, 1); }
    },
    dispatch(type, event) {
      for (const fn of (this.listeners[type] || []).slice()) { fn(event || { type }); }
    },
    setTimeout(fn, ms) {
      this.timers.push({ fn: fn, ms: ms || 0, cleared: false });
      return this.timers.length;                 /* 1-based id */
    },
    clearTimeout(id) {
      const t = this.timers[id - 1];
      if (t) { t.cleared = true; }
    },
    /* `upto` ms 이하로 걸린 콜백을 지금 돌린다. 돌린 개수를 돌려준다. */
    runTimers(upto) {
      const limit = upto === undefined ? 0 : upto;
      const due = this.timers.filter((t) => !t.cleared && t.ms <= limit);
      for (const t of due) { t.cleared = true; t.fn(); }
      return due.length;
    },
    pendingTimers() { return this.timers.filter((t) => !t.cleared).map((t) => t.ms); },
    /* 레이아웃 전환은 **새로고침**으로 간다 — 진짜로 다시 불러올 수는 없으니 센다. */
    reloads: 0,
    location: null
  };
}

function boot(options) {
  const opts = options || {};
  const doc = new StubDocument();
  for (const id of ELEMENT_IDS) { doc.register(id); }
  // 실제 DOM 처럼 messages 를 timeline 안에 넣는다 (스크롤 계산이 성립하도록).
  doc.getElementById('timeline').appendChild(doc.getElementById('messages'));
  /* 뷰포트 높이 — 이 안에 들어가는 만큼만 DOM 에 남는다. */
  doc.getElementById('timeline').offsetHeight = opts.viewport || 300;
  doc.getElementById('timeline').clientHeight = opts.viewport || 300;
  doc.body.setAttribute('data-default-author', '기본이름');
  StubEventSource.reset();
  StubIntersectionObserver.reset();

  const rooms = opts.rooms || [
    { id: 'r1', repo_url: 'https://example.invalid/one.git', name: '첫 방' },
    { id: 'r2', repo_url: 'https://example.invalid/two.git', name: '둘째 방' }
  ];
  const messages = opts.messages || [msg(1), msg(2), msg(3)];

  /* 서버 대역: `before=` 커서로 과거를 한 쪽씩 준다 (진짜 keyset 페이징처럼). */
  const past = opts.past || [];
  const pageSize = opts.pageSize || 2;
  function olderPage(path) {
    const before = decodeURIComponent((path.split('before=')[1] || '').split('&')[0]);
    const upto = past.filter((m) => m.id < before);
    const slice = upto.slice(Math.max(0, upto.length - pageSize));
    return { messages: slice, has_more: upto.length > slice.length };
  }

  const routes = {
    '/api/rooms/r1/messages?before=': olderPage,
    '/api/rooms/r2/messages': { messages: opts.room2 || [msg(50, '둘째 방 메시지')], has_more: false },
    '/api/rooms/r1/messages': { messages: messages, has_more: !!opts.hasMore },
    '/api/rooms/r1/visibility': { ok: true },
    '/api/rooms/r2/visibility': { ok: true },
    /* 읽음 스냅샷 — 커서 지도만 온다 (카운트는 화면이 파생시킨다). */
    '/api/rooms/r1/reads': opts.reads || emptyReads(),
    '/api/rooms/r2/reads': opts.reads2 || emptyReads(),
    '/api/rooms': { rooms: rooms }
  };
  /* 테스트가 특정 경로만 갈아끼울 수 있게 한다 (키 순서는 그대로 유지된다). */
  Object.assign(routes, opts.routes || {});
  const fetchStub = makeFetch(routes);

  /* 콘솔을 기록한다 — "실패가 드러나는가"의 한 축이다. */
  const consoleErrors = [];
  const runtime = {
    doc: doc,
    win: stubWindow(),
    /* 대역을 나중에 갈아끼울 수 있게 런타임을 통해 늦게 부른다 (boot.js 도 그렇다). */
    fetch: fetchStub,
    EventSource: StubEventSource,
    IntersectionObserver: opts.noObserver ? undefined : StubIntersectionObserver,
    /* `opts.stored` 로 초기값을 심는다 — "재기동 후에도 유지되나"는 저장소에
       값이 남아 있는 상태로 다시 부팅해 보는 것으로만 증명된다. */
    localStorage: {
      _v: Object.assign({}, opts.stored || {}),
      getItem(k) { return this._v[k] || null; },
      setItem(k, v) { this._v[k] = v; },
      removeItem(k) { delete this._v[k]; }
    },
    console: {
      log: console.log.bind(console),
      warn: console.warn.bind(console),
      error: (...args) => { consoleErrors.push(args.map(String).join(' ')); }
    },
    /* 기본은 **진짜** 엔진. 실패 경로를 보는 테스트만 여기에 다른 것을 넣는다. */
    virtual: ('virtual' in opts) ? opts.virtual : virtual
  };

  /* location 은 win 을 가리켜야 하므로 리터럴 밖에서 묶는다. */
  runtime.win.location = { reload() { runtime.win.reloads += 1; } };

  const chat = createApp(runtime);
  /* ⭐ 초기화 단위 하나를 **실제로 터뜨린다.** 격리는 "그렇게 짰다"가 아니라
     터뜨려 보고 나머지가 살아 있는지로만 증명된다. */
  if (opts.sabotage) { opts.sabotage(doc, runtime); }
  return Promise.resolve(chat.boot())
    .then(() => ({ doc, chat, fetchStub, context: runtime, consoleErrors }));
}

/* 특정 요소의 배선을 터뜨린다 (그 모듈의 mount 가 던지게 만든다). */
function breakWiring(doc, id, message) {
  const node = doc.getElementById(id);
  node.addEventListener = () => { throw new Error(message || ('배선 실패: ' + id)); };
  return node;
}

/* ------------------------------------------------------------- 테스트 */

await test('템플릿의 id 와 스크립트가 찾는 id 가 어긋나지 않는다', () => {
  for (const id of ELEMENT_IDS) {
    assert.ok(indexHtml.includes('id="' + id + '"'), 'index.html 에 없는 id: ' + id);
  }
});

await test('우리 소스 어디에도 innerHTML 대입이 없다 (정적 검사 · 전 모듈)', () => {
  const hits = [];
  for (const file of OUR_JS) {
    fs.readFileSync(file, 'utf8').split('\n').forEach((line, i) => {
      if (/\.innerHTML\s*=/.test(line) ||
        /insertAdjacentHTML|outerHTML\s*=|document\.write/.test(line)) {
        hits.push(path.basename(file) + ':' + (i + 1));
      }
    });
  }
  assert.deepEqual(hits, [], 'HTML 문자열 주입 흔적: ' + JSON.stringify(hits));
});

await test('모듈이 브라우저 전역을 직접 집어오지 않는다 (진입점만 예외)', () => {
  /* 전역을 직접 만지면 주입이 무의미해지고 테스트가 실물과 어긋난다. */
  const hits = [];
  for (const file of OUR_JS) {
    if (path.basename(file) === 'app.js') { continue; }   // 진입점이 그 일을 한다
    fs.readFileSync(file, 'utf8').split('\n').forEach((line, i) => {
      if (/^\s*\*/.test(line) || /^\s*\/\*/.test(line)) { return; }  // 주석
      if (/\b(window|globalThis)\b/.test(line) || /(^|[^.\w])document\./.test(line)) {
        hits.push(path.basename(file) + ':' + (i + 1) + ' ' + line.trim());
      }
    });
  }
  assert.deepEqual(hits, [], '모듈이 전역을 직접 집어온다: ' + JSON.stringify(hits));
});

await test('부팅: 최근 메시지가 노드로 딱 한 번씩 만들어진다', async () => {
  const { doc, chat } = await boot();
  const list = doc.getElementById('messages');
  assert.equal(list.children.length, 3);
  assert.equal(chat.stats.created, 3);
  assert.equal(chat.stats.appended, 3);
  assert.equal(chat.stats.innerHTML, 0);
  assert.equal(doc.counts.innerHTML, 0);
  assert.equal(doc.counts.removeChild, 0);
  // 타임라인을 비운 건 방을 처음 여는 그 한 번뿐이다.
  assert.equal(chat.stats.cleared, 1);
  assert.equal(list.children[0].textContent.includes('메시지 1'), true);
});

await test('⭐ SSE 로 새 메시지가 와도 기존 노드는 같은 객체로 남는다', async () => {
  const { doc, chat } = await boot();
  const list = doc.getElementById('messages');
  const before = list.children.slice();          // 노드 3개의 참조를 붙잡는다
  const createdBefore = chat.stats.created;
  const clearedBefore = chat.stats.cleared;

  for (let i = 4; i <= 8; i++) {
    StubEventSource.current.emit('message', msg(i));
  }

  assert.equal(list.children.length, 8);
  // 새로 만든 노드는 정확히 5개 — 기존 3개는 다시 만들지 않았다.
  assert.equal(chat.stats.created - createdBefore, 5);
  // 그리고 그 3개는 **같은 객체**로, 같은 자리에 그대로 있다.
  for (let i = 0; i < before.length; i++) {
    assert.equal(list.children[i], before[i], i + '번 노드가 교체됐다');
  }
  assert.equal(chat.stats.cleared, clearedBefore);   // 비운 적 없음
  assert.equal(doc.counts.removeChild, 0);           // 지운 적 없음
  // 타임라인에서 노드가 제거된 적은 **한 번도 없다** (방 전환 때만 비운다).
  assert.equal(list.removedByReplace, 0);
  assert.equal(doc.counts.innerHTML, 0);
});

await test('같은 메시지가 두 번 와도 노드는 하나다 (멱등)', async () => {
  const { doc, chat } = await boot();
  const list = doc.getElementById('messages');
  const same = msg(9);

  StubEventSource.current.emit('message', same);
  assert.equal(list.children.length, 4);
  const node = list.children[3];

  StubEventSource.current.emit('message', same);   // 재전달
  StubEventSource.current.emit('message', same);   // 로컬 에코와 겹침
  assert.equal(list.children.length, 4);
  assert.equal(list.children[3], node);            // 같은 객체 그대로
  assert.equal(chat.stats.duplicates, 2);
});

await test('순서가 뒤집혀 와도 제자리에 들어가고 기존 노드는 그대로다', async () => {
  const { doc, chat } = await boot();
  const list = doc.getElementById('messages');
  const nodesById = new Map(list.children.map((c) => [c.dataset.id, c]));

  StubEventSource.current.emit('message', msg(7, '나중 것'));
  StubEventSource.current.emit('message', msg(5, '먼저 것이지만 늦게 도착'));

  /* ⭐ 정렬의 원본은 이제 **모델**이다 (DOM 은 화면에 보이는 창일 뿐이라
     붙은 순서가 곧 시간순은 아니다). */
  const ids = chat.items().map((m) => m.id);
  assert.deepEqual(ids, ids.slice().sort(), '모델의 시간순 정렬이 깨졌다');

  /* 화면에서도 세로 위치(translateY)가 시간순이어야 한다. */
  const placed = list.children
    .map((c) => [c.dataset.id, parseFloat(String(c.style.transform).replace(/[^0-9.]/g, ''))])
    .sort((a, b) => a[1] - b[1])
    .map((pair) => pair[0]);
  assert.deepEqual(placed, placed.slice().sort(), '화면 배치가 시간순이 아니다');

  /* 그리고 원래 있던 노드는 **같은 객체 그대로** 남아 있다. */
  assert.equal(chat.stats.rebuiltInView, 0);
  for (const [id, node] of nodesById) {
    if (chat.nodes().has(id)) { assert.equal(chat.nodes().get(id), node, id + ' 가 교체됐다'); }
  }
});

/* 과거 메시지 6건 (어제 것 — ID 가 오늘 것보다 사전식으로 앞선다). */
function pastMessages(count) {
  const out = [];
  for (let i = 0; i < count; i++) {
    const m = msg(i, '아주 예전 ' + i);
    m.id = 'records/20260902/20260902T0100' + String(i).padStart(2, '0') + '000Z-a-x.json';
    out.push(m);
  }
  return out;
}

/* 큐를 비운다 (fetch 대역 → prepend → 가상화 재계산까지 흘려보낸다).
   가상화가 한 턴 더 쓰는 경우가 있어 두 번 돌린다. */
function settle() {
  return new Promise((resolve) => setTimeout(resolve, 0))
    .then(() => new Promise((resolve) => setTimeout(resolve, 0)));
}

/* 위로 올라간 상태를 만든다 (부팅 직후엔 맨 아래에 붙어 있다). */
function scrollUp(doc) {
  const timeline = doc.getElementById('timeline');
  timeline.scrollTop = 0;
  timeline.dispatch('scroll');
  return timeline;
}

await test('⭐ 위로 로드: 스크롤 위치가 보존된다 (보정 전후 수치)', async () => {
  const { doc, chat } = await boot({ hasMore: true, past: pastMessages(6) });
  await settle();          /* 부팅 직후 라이브러리의 스크롤 정리를 흘려보낸다 */
  const list = doc.getElementById('messages');
  const timeline = scrollUp(doc);
  await settle();
  const before = list.children.slice();

  const heightBefore = timeline.scrollHeight;
  const topBefore = timeline.scrollTop;

  StubIntersectionObserver.current.trigger();   // 표식이 보였다 = 위 끝에 닿았다
  await settle();                                // 진행 중 요청이 끝나기를 기다린다

  const heightAfter = timeline.scrollHeight;
  const grew = heightAfter - heightBefore;
  console.log('      스크롤 보정: height ' + heightBefore + ' → ' + heightAfter +
    ' (+' + grew + '), scrollTop ' + topBefore + ' → ' + timeline.scrollTop);

  assert.ok(grew > 0, '위쪽 콘텐츠가 늘지 않았다 (전제 실패) — ' +
    JSON.stringify({ heightBefore: heightBefore, heightAfter: heightAfter,
      items: chat.items().length, prepended: chat.stats.prepended }));
  assert.equal(timeline.scrollTop, topBefore + grew, '보던 자리가 아래로 튀었다');
  assert.equal(chat.stats.lastAnchor, grew);
  assert.equal(chat.stats.prepended, 2);
  assert.equal(chat.items().length, 5, '모델에 과거 2건이 안 들어왔다');

  /* ⭐ 재정의한 불변식.
     (옛 불변식은 "DOM 의 모든 노드가 그대로"였지만, 가상 스크롤에서는 화면 밖
     노드를 걷어내는 것이 정상이다. 그래서 **창 안에 남아 있는 것**만 본다.) */
  const stillInView = before.filter(function (node) {
    return chat.nodes().has(node.dataset.id);
  });
  assert.ok(stillInView.length > 0, '창 안에 남은 노드가 하나도 없다 (전제 실패)');
  for (const node of stillInView) {
    assert.equal(chat.nodes().get(node.dataset.id), node,
      node.dataset.id + ' 가 창 안에 있는데 교체됐다');
  }
  assert.equal(chat.stats.rebuiltInView, 0, '창 안 노드를 다시 만들었다');
  assert.equal(list.removedByReplace, 0, '타임라인을 통째로 비웠다');
  assert.equal(doc.counts.innerHTML, 0);
});

await test('⭐ 트리거가 연속 발화해도 요청은 한 번이다 (중복 로드 방지)', async () => {
  const { doc, chat, context } = await boot({ hasMore: true, past: pastMessages(6) });
  scrollUp(doc);
  const observer = StubIntersectionObserver.current;

  observer.trigger();
  observer.trigger();          // 로딩 중 재발화 — 무시돼야 한다
  observer.trigger();
  const pending = chat.loadOlder();   // 로딩 중이면 아무것도 하지 않는다
  await pending;
  await settle();

  const olderCalls = context.fetch.calls.filter((c) => c.path.indexOf('before=') >= 0);
  console.log('      before= 요청 수: ' + olderCalls.length +
    ', stats.olderRequests: ' + chat.stats.olderRequests);
  assert.equal(olderCalls.length, 1, '중복 요청이 나갔다');
  assert.equal(chat.stats.olderRequests, 1);
  assert.equal(chat.stats.prepended, 2);
});

await test('⭐ 맨 위에 닿으면 조용히 멈춘다 (더 요청하지 않는다)', async () => {
  const { doc, chat, context } = await boot({ hasMore: true, past: pastMessages(3) });
  scrollUp(doc);

  // 2건 → 1건 → 끝. 세 번째 발화에서는 요청 자체가 나가지 않아야 한다.
  for (let i = 0; i < 4; i++) {
    const observer = StubIntersectionObserver.current;
    if (observer) { observer.trigger(); }
    await chat.loadOlder();
    await settle();
  }

  const olderCalls = context.fetch.calls.filter((c) => c.path.indexOf('before=') >= 0);
  console.log('      before= 요청 수: ' + olderCalls.length +
    ' (과거 3건 / 쪽 크기 2 → 2회면 끝)');
  assert.equal(olderCalls.length, 2, '끝에 닿고도 계속 물었다');
  assert.equal(chat.state.hasMore, false);
  assert.equal(chat.stats.prepended, 3);
  assert.equal(doc.getElementById('older-note').textContent, '대화의 시작');
  assert.equal(StubIntersectionObserver.current.observing.size, 0, '관찰을 안 끊었다');
});

await test('⭐ 위로 읽는 중에 온 새 메시지가 읽던 자리를 뺏지 않는다', async () => {
  const { doc, chat } = await boot({ hasMore: true, past: pastMessages(6) });
  const timeline = scrollUp(doc);
  StubIntersectionObserver.current.trigger();
  await settle();

  const topBefore = timeline.scrollTop;
  StubEventSource.current.emit('message', msg(9, '새로 온 말'));   // 아래쪽 SSE

  assert.equal(timeline.scrollTop, topBefore, '새 메시지가 화면을 아래로 끌고 갔다');
  assert.equal(doc.getElementById('jump-latest').hidden, false, '새 메시지 표시가 없다');
  assert.ok(doc.getElementById('jump-latest').textContent.indexOf('새 메시지') >= 0);

  // 맨 아래로 내려가면 따라가기가 다시 켜진다.
  chat.state.atBottom = true;
  StubEventSource.current.emit('message', msg(10, '또 하나'));
  assert.equal(timeline.scrollTop, timeline.scrollHeight);
});

await test('IntersectionObserver 가 없으면 스크롤 폴백으로 이어 붙인다', async () => {
  const { doc, chat, context } = await boot({
    hasMore: true, past: pastMessages(6), noObserver: true
  });
  const timeline = doc.getElementById('timeline');
  timeline.scrollTop = 10;          // 위 끝 근처
  timeline.dispatch('scroll');
  await settle();

  const olderCalls = context.fetch.calls.filter((c) => c.path.indexOf('before=') >= 0);
  assert.ok(olderCalls.length >= 1, '스크롤 폴백이 아예 안 돌았다');
  assert.ok(chat.stats.prepended >= 2, '과거가 붙지 않았다');
  assert.equal(chat.stats.rebuiltInView, 0);
});

await test('방을 바꿀 때만 타임라인을 비운다', async () => {
  const { doc, chat } = await boot();
  const list = doc.getElementById('messages');
  assert.equal(chat.stats.cleared, 1);

  await chat.switchRoom('r2');

  assert.equal(chat.stats.cleared, 2);             // 딱 한 번 더
  assert.equal(list.children.length, 1);           // 둘째 방 내용만
  assert.equal(list.children[0].textContent.includes('둘째 방 메시지'), true);
  assert.equal(doc.counts.innerHTML, 0);
});

await test('메시지 본문은 textContent 로만 들어간다 (HTML 이 실행되지 않는다)', async () => {
  const { doc } = await boot();
  const evil = msg(20, '<img src=x onerror="alert(1)">');
  StubEventSource.current.emit('message', evil);
  const node = doc.getElementById('messages').children[3];
  assert.ok(node.textContent.includes('<img src=x onerror='),
    '본문이 텍스트로 남아 있어야 한다');
  assert.equal(doc.counts.innerHTML, 0);
});

await test('보내기: 로컬 에코가 즉시 붙고 뒤이은 SSE 는 중복으로 걸러진다', async () => {
  const sent = msg(30, '내가 방금 보낸 말', '나');
  const doc0 = await boot();
  const { doc, chat, context } = doc0;
  // 전송 라우트를 추가한다.
  context.fetch = makeFetch({
    '/api/rooms/r1/messages': () => ({ message: sent }),
    '/api/rooms': { rooms: [] }
  });
  doc.getElementById('text').value = '내가 방금 보낸 말';

  await chat.send();
  const list = doc.getElementById('messages');
  assert.equal(list.children.length, 4);
  const echoed = list.children[3];

  StubEventSource.current.emit('message', sent);   // 잠시 뒤 폴링으로 되돌아온 같은 레코드
  assert.equal(list.children.length, 4);
  assert.equal(list.children[3], echoed);
});

await test('가시성 변화를 서버에 보고한다 (OS 알림 판정의 근거)', async () => {
  const { doc, context } = await boot();
  const before = context.fetch.calls.length;
  doc.visibilityState = 'hidden';
  doc.dispatch('visibilitychange');
  const call = context.fetch.calls.slice(before).find((c) => c.path.indexOf('/visibility') >= 0);
  assert.ok(call, '가시성 보고가 나가지 않았다');
  assert.equal(JSON.parse(call.init.body).visible, false);
});

/* ------------------------------------------------- I. 가상 스크롤 */

/* 긴 대화 하나 만들기 (n건). */
function manyMessages(n) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const stamp = '20260903T' + String(Math.floor(i / 60)).padStart(2, '0') +
      String(i % 60).padStart(2, '0') + '00000Z';
    out.push({
      id: 'records/20260903/' + stamp + '-a-' + String(i).padStart(6, '0') + '.json',
      author: '앨리스', text: '메시지 ' + i, ts: '2026-09-03T01:00:00Z',
      sender: 'a.host', kind: 'msg', reply_to: null, unknown: false
    });
  }
  return out;
}

await test('⭐ 긴 대화에서도 DOM 노드 수가 상수에 가깝게 유지된다', async () => {
  const many = manyMessages(2000);
  const { doc, chat } = await boot({ messages: many, viewport: 300 });
  const list = doc.getElementById('messages');

  console.log('      메시지 ' + chat.items().length + '건 · DOM 노드 ' +
    list.children.length + '개 · 전체 높이 ' + chat.virtualizer().getTotalSize() + 'px');

  assert.equal(chat.items().length, 2000, '모델에는 전부 있어야 한다');
  assert.ok(list.children.length < 40,
    'DOM 에 ' + list.children.length + '개가 남았다 (가상화가 안 됐다)');

  /* 위로 한참 올라가도 노드 수는 그대로다 (걷어내고 새로 그린다). */
  const timeline = doc.getElementById('timeline');
  const peak = [];
  for (const offset of [20000, 40000, 60000, 1000]) {
    timeline.scrollTop = offset;
    timeline.dispatch('scroll');
    await settle();
    peak.push(list.children.length);
  }
  console.log('      스크롤하며 본 DOM 노드 수: ' + peak.join(', '));
  assert.ok(Math.max.apply(null, peak) < 40, '스크롤 중 노드가 쌓였다: ' + peak);
  assert.ok(chat.stats.recycled > 0, '창 밖 노드를 걷어낸 적이 없다 (가상화 아님)');
  assert.equal(chat.stats.rebuiltInView, 0, '창 안 노드를 다시 만들었다 (리렌더 사고)');
  assert.equal(doc.counts.innerHTML, 0);
});

await test('⭐ 가변 높이: 실제 높이를 재서 반영한다 (고정 높이 가정 없음)', async () => {
  const list3 = [msg(1, '짧다'), msg(2, '아주 긴 메시지 '.repeat(30)), msg(3, '보통')];
  const { doc, chat } = await boot({ messages: list3, viewport: 300 });
  const list = doc.getElementById('messages');
  const ids = chat.items().map((m) => m.id);

  /* 브라우저가 잰 높이를 흉내낸다: 짧은 것 40, 아주 긴 것 260, 보통 60 */
  const sizes = {};
  sizes[ids[0]] = 40; sizes[ids[1]] = 260; sizes[ids[2]] = 60;
  heightsFor(doc, sizes);
  /* 브라우저라면 ResizeObserver 가 알려 준다. stub 에는 없으므로 앱이 창 크기
     변화 때 하는 것과 같은 일(측정 캐시 비우기)을 직접 시킨다. */
  chat.virtualizer().measure();
  chat.syncVirtual();
  await settle();

  const measured = chat.virtualizer().getVirtualItems().map((v) => v.size);
  console.log('      실측 높이: ' + measured.join(', ') +
    ' · 전체 ' + chat.virtualizer().getTotalSize() + 'px');
  assert.deepEqual(measured, [40, 260, 60], '높이가 실측되지 않았다(추정치 그대로)');

  /* 세로 위치가 실측 높이 + 간격(6)으로 누적된다 — 고정 높이였다면 균등했을 것. */
  const starts = chat.virtualizer().getVirtualItems().map((v) => v.start);
  assert.deepEqual(starts, [0, 46, 312]);
  assert.equal(list.children.length, 3);
});

await test('⭐ 재정의한 불변식이 진짜 리렌더 사고를 잡는다 (대조군)', async () => {
  const { doc, chat } = await boot({ messages: manyMessages(50), viewport: 300 });
  const list = doc.getElementById('messages');

  /* (1) 정상 — 창 밖 제거는 일어나도 rebuiltInView 는 0 */
  doc.getElementById('timeline').scrollTop = 1500;
  doc.getElementById('timeline').dispatch('scroll');
  await settle();
  assert.ok(chat.stats.recycled > 0, '창 밖 제거가 없었다 (전제 실패)');
  assert.equal(chat.stats.rebuiltInView, 0);

  /* (2) 대조군 — 창 안 노드를 몰래 버리고 다시 그리게 만든다.
     = "화면에 그대로 있는 메시지를 다시 만들었다" 는 사고. 카운터가 잡아야 한다. */
  const inView = chat.virtualizer().getVirtualItems().length;
  const before = chat.stats.rebuiltInView;
  chat.nodes().forEach(function (node, id) {
    if (node.parentNode) { node.parentNode.removeChild(node); }
    chat.nodes()['delete'](id);
  });
  chat.renderWindow();

  console.log('      대조군: 창 안 ' + inView + '개를 버리고 다시 그림 → rebuiltInView ' +
    before + ' → ' + chat.stats.rebuiltInView);
  assert.ok(chat.stats.rebuiltInView >= inView,
    '리렌더 사고를 못 잡았다 — 불변식이 무력해졌다');
  assert.equal(list.children.length, inView, '다시 그린 뒤 창 크기는 같아야 한다');
});

/* ------------------------------------------- G. 연결 상태 · 레포 만들기 */

const CONNECTING_ROOMS = [{
  id: 'r1', repo_url: 'https://example.invalid/one.git', name: '첫 방',
  status: { state: 'connecting', detail: '', code: '', hint: '' }
}];

await test('⭐ 받는 중인 방: 자리에 안내가 남고, 준비되면 저절로 채워진다', async () => {
  let ready = false;
  const { doc, chat } = await boot({
    rooms: CONNECTING_ROOMS,
    routes: {
      '/api/rooms/r1/messages': () => (ready
        ? { messages: [msg(1), msg(2)], has_more: false }
        : { __http: 409, error: '방을 받는 중이다 — 잠시 뒤 다시 보인다',
            status: { state: 'connecting', detail: '', hint: '' } })
    }
  });
  const list = doc.getElementById('messages');
  const trouble = doc.getElementById('room-trouble');

  assert.equal(trouble.hidden, false, '받는 중 안내가 안 보인다');
  assert.ok(doc.getElementById('room-trouble-text').textContent.indexOf('받는 중') >= 0);
  assert.equal(doc.getElementById('room-retry').hidden, true, '받는 중엔 재시도가 없다');
  assert.equal(list.children.length, 0);
  assert.equal(chat.stats.created, 0);

  // 클론이 끝났다 — 서버가 기존 'rooms' 이벤트로 알린다 (새 배관 없음).
  ready = true;
  StubEventSource.current.emit('rooms', {
    rooms: [Object.assign({}, CONNECTING_ROOMS[0], { status: { state: 'ready' } })]
  });
  await settle();

  assert.equal(list.children.length, 2, '준비된 뒤 타임라인이 안 채워졌다');
  assert.equal(trouble.hidden, true);
  assert.equal(chat.stats.cleared, 1, '타임라인을 다시 비웠다 (전체 리렌더)');
  assert.equal(list.removedByReplace, 0);
  assert.equal(doc.counts.innerHTML, 0);
});

await test('⭐ 실패한 방: 사라지지 않고 사유·안내가 남고 재시도가 된다', async () => {
  const failed = [{
    id: 'r1', repo_url: 'https://example.invalid/one.git', name: '첫 방',
    status: {
      state: 'failed', code: 'auth', detail: '인증에 실패했다 (토큰이 없거나 권한이 없다)',
      hint: '환경변수 GITWIRE_TOKEN 에 토큰을 넣고 앱을 다시 띄워라'
    }
  }];
  const { doc, context } = await boot({
    rooms: failed,
    routes: {
      '/api/rooms/r1/messages': { __http: 409, error: '인증에 실패했다', status: failed[0].status },
      '/api/rooms/r1/retry': { status: { state: 'connecting' } }
    }
  });

  // 방은 목록에 그대로 있고, 상태가 함께 보인다.
  const rooms = doc.getElementById('rooms');
  assert.equal(rooms.children.length, 1, '실패한 방이 목록에서 사라졌다');
  assert.ok(rooms.children[0].textContent.indexOf('실패') >= 0);
  assert.ok(doc.getElementById('room-trouble-text').textContent.indexOf('인증에 실패') >= 0);
  assert.ok(doc.getElementById('room-trouble-hint').textContent.indexOf('GITWIRE_TOKEN') >= 0);
  assert.equal(doc.getElementById('room-retry').hidden, false);

  doc.getElementById('room-retry').dispatch('click');
  await settle();
  const retried = context.fetch.calls.filter((c) => c.path.indexOf('/retry') >= 0);
  assert.equal(retried.length, 1, '재시도 요청이 안 나갔다');
  assert.equal(retried[0].init.method, 'POST');
});

await test('⭐ 레포 만들기(API): 만든 주소가 그대로 방이 된다 (손으로 옮기지 않는다)', async () => {
  const { doc, chat, context } = await boot({
    routes: {
      '/api/repos/plan': {
        forge: { kind: 'github', host: 'github.com', label: 'GitHub' },
        mode: 'api', owner: 'yunhyuk-choi', name: 'our-room', private: true,
        link: 'https://github.com/new?name=our-room&visibility=private&owner=yunhyuk-choi',
        clone_url: 'https://github.com/yunhyuk-choi/our-room.git',
        token_env: 'GITWIRE_TOKEN', detail: ''
      },
      '/api/repos': {
        repo: { full_name: 'yunhyuk-choi/our-room', private: true,
                clone_url: 'https://github.com/yunhyuk-choi/our-room.git' }
      }
    }
  });
  doc.getElementById('room-name').value = '우리 방';

  await chat.planNewRepo();
  const plan = doc.getElementById('new-repo-plan');
  assert.equal(plan.hidden, false);
  // 무엇이 만들어지는지 **누르기 전에** 보인다.
  assert.ok(plan.textContent.indexOf('yunhyuk-choi/our-room') >= 0, plan.textContent);
  assert.ok(plan.textContent.indexOf('비공개') >= 0);
  assert.equal(doc.getElementById('new-repo-create').hidden, false);

  await chat.createNewRepo();
  await settle();

  // 만든 주소가 그대로 등록 요청에 실린다 (사용자가 어디에도 붙여넣지 않았다).
  const posted = context.fetch.calls.filter(
    (c) => c.path === '/api/rooms' && c.init.method === 'POST');
  assert.equal(posted.length, 1, '방 등록까지 이어지지 않았다');
  assert.equal(JSON.parse(posted[0].init.body).repo_url,
    'https://github.com/yunhyuk-choi/our-room.git');
});

await test('레포 만들기(링크): 프리필 링크를 주고, 만들고 오면 그 주소로 잇는다', async () => {
  const link = 'https://github.com/new?name=our-room&visibility=private';
  const { doc, chat, context } = await boot({
    routes: {
      '/api/repos/plan': {
        forge: { kind: 'github', host: 'github.com', label: 'GitHub' },
        mode: 'link', owner: 'yunhyuk-choi', name: 'our-room', private: true,
        link: link, clone_url: 'https://github.com/yunhyuk-choi/our-room.git',
        token_env: 'GITWIRE_TOKEN', detail: ''
      }
    }
  });

  await chat.planNewRepo();
  assert.equal(doc.getElementById('new-repo-link').getAttribute('href'), link);
  assert.equal(doc.getElementById('new-repo-link').hidden, false);
  assert.equal(doc.getElementById('new-repo-create').hidden, true, 'API 없이 만들기 버튼이 떴다');
  assert.equal(doc.getElementById('new-repo-use').hidden, false);

  doc.getElementById('new-repo-use').dispatch('click');
  await settle();
  const posted = context.fetch.calls.filter(
    (c) => c.path === '/api/rooms' && c.init.method === 'POST');
  assert.equal(posted.length, 1);
  assert.equal(JSON.parse(posted[0].init.body).repo_url,
    'https://github.com/yunhyuk-choi/our-room.git');
});

/* ------------------------- 모듈 격리 · 가상화 실패는 결함이다 (주입 실증) */

/* ⭐ 실제로 당한 사고의 축소판.
   벤더 번들이 브라우저에 없는 Node 전역을 참조해 **`Virtualizer` 생성자에서**
   터졌다. 모듈 평가는 성공했으므로 라이브러리는 멀쩡해 보였고, "없으면 알린다"는
   방어는 그대로 통과했다. 그 예외가 통짜 `boot()` 을 끊어 배선에 도달하지
   못했고 — 화면의 **모든** 버튼이 죽었다.

   ⚠️ 한때 그 답이 '격하(degrade)' — 가상화 없이 전부 그리기 — 였다. 걷어냈다.
   가상 스크롤은 성능 때문에 붙인 기능이고, 폴백은 그 기능이 **조용히 빠진 채로**
   앱이 돌게 만든다. 지금 요구는 둘이다:

     (1) 가상화가 안 되면 **결함으로 드러난다** (조용히 느려지지 않는다).
     (2) 그 실패는 **타임라인 모듈에만 갇힌다** — 가상화는 메시지 리스트 하나에만
         거는 것이므로 `+` 버튼·방 목록·검색이 같이 죽을 이유가 없다.

   그리고 (2) 는 "그렇게 짰다"가 아니라 **터뜨려 보고** 확인한다. */
const brokenVirtual = Object.assign({}, virtual, {
  Virtualizer: function () { throw new ReferenceError('process is not defined'); }
});

/* 타임라인이 죽어도 살아 있어야 하는 것들 — 하나하나 **눌러서** 확인한다. */
function assertRestOfAppWorks(what, doc, chat, context) {
  assert.ok(context.fetch.calls.some((c) => c.path === '/api/rooms'),
    what + ': /api/rooms 를 부르지 않았다 = 배선에 도달하지 못했다');
  assert.ok(doc.getElementById('rooms').children.length > 0,
    what + ': 방 목록이 그려지지 않았다');

  const addRoom = doc.getElementById('add-room');
  const wasHidden = addRoom.hidden;
  doc.getElementById('toggle-add').dispatch('click');   /* 사용자가 누른 그 ＋ */
  assert.notEqual(addRoom.hidden, wasHidden,
    what + ': ＋ 버튼에 핸들러가 안 붙었다 (이번 사고의 증상 그 자체)');

  const searchBar = doc.getElementById('search-bar');
  const searchHidden = searchBar.hidden;
  doc.getElementById('toggle-search').dispatch('click');
  assert.notEqual(searchBar.hidden, searchHidden, what + ': 검색 토글이 죽었다');

  doc.getElementById('back').dispatch('click');
  assert.equal(doc.body.dataset.view, 'rooms', what + ': 뒤로 가기가 죽었다');
}

/* 실패가 **드러나는가** — 상태줄·메시지 영역·콘솔 세 곳. */
function assertTimelineBroken(what, doc, chat, consoleErrors) {
  assert.ok(chat.state.broken, what + ': 결함으로 표시되지 않았다');
  const status = doc.getElementById('status');
  assert.ok(status.textContent.indexOf('메시지 영역') >= 0,
    what + ': 상태줄에 안 남았다 — ' + JSON.stringify(status.textContent));
  const list = doc.getElementById('messages');
  assert.ok(list.children.some((c) => String(c.className).indexOf('broken') >= 0),
    what + ': 대화 자리에 결함 표시가 없다');
  assert.ok(consoleErrors.some((line) => line.indexOf('gitwire-chat') >= 0),
    what + ': 콘솔에 안 남았다');
  /* ⭐ 그리고 **느린 대체 경로로 계속 가지 않는다.** */
  assert.equal(list.children.filter((c) => String(c.className).indexOf('msg') === 0).length, 0,
    what + ': 가상화 없이 메시지를 그리고 있다 (조용히 느려지는 경로가 남았다)');
  assert.equal(chat.stats.innerHTML, 0);
  assert.equal(doc.counts.innerHTML, 0);
}

await test('⭐ 가상화 엔진이 못 돌면(생성자 예외) 결함으로 드러나고, 나머지는 산다', async () => {
  const { doc, chat, context, consoleErrors } = await boot({ virtual: brokenVirtual });
  assertTimelineBroken('생성자 예외', doc, chat, consoleErrors);
  assertRestOfAppWorks('생성자 예외', doc, chat, context);
});

await test('⭐ 가상화 라이브러리가 아예 없어도 같다 — 드러나고, 나머지는 산다', async () => {
  const { doc, chat, context, consoleErrors } = await boot({ virtual: null });
  assertTimelineBroken('라이브러리 없음', doc, chat, consoleErrors);
  assertRestOfAppWorks('라이브러리 없음', doc, chat, context);
});

await test('⭐ 타임라인 모듈이 던져도 그 모듈만 실패한다 (주입 실패로 실증)', async () => {
  const { doc, chat, context, consoleErrors } = await boot({
    sabotage: (d) => breakWiring(d, 'timeline', '타임라인 배선을 일부러 터뜨렸다')
  });

  const units = chat.failures().map((f) => f.unit);
  assert.deepEqual(units, ['타임라인'], '다른 모듈까지 실패했다: ' + units.join(','));
  assertTimelineBroken('타임라인 주입 실패', doc, chat, consoleErrors);
  assertRestOfAppWorks('타임라인 주입 실패', doc, chat, context);
});

await test('⭐ 반대로 다른 모듈이 던져도 타임라인은 정상이다 (주입 실패로 실증)', async () => {
  const { doc, chat, consoleErrors } = await boot({
    sabotage: (d) => breakWiring(d, 'toggle-add', '방 추가 배선을 일부러 터뜨렸다')
  });

  const units = chat.failures().map((f) => f.unit);
  assert.deepEqual(units, ['방 추가'], '엉뚱한 모듈이 함께 죽었다: ' + units.join(','));

  /* 타임라인은 **완전히 정상**이다 — 가상화도, 불변식도. */
  assert.equal(chat.state.broken, '', '타임라인이 덩달아 결함이 됐다');
  const list = doc.getElementById('messages');
  assert.equal(list.children.length, 3, '메시지가 안 그려졌다');
  StubEventSource.current.emit('message', msg(4));
  assert.equal(list.children.length, 4, '새 메시지가 안 붙었다');
  assert.equal(chat.stats.rebuiltInView, 0);
  assert.equal(chat.stats.innerHTML, 0);

  /* 실패는 드러난다. 그리고 다른 모듈(검색)은 그대로 동작한다. */
  assert.ok(doc.getElementById('status').textContent.indexOf('방 추가') >= 0,
    '상태줄에 실패한 모듈이 안 남았다');
  assert.ok(consoleErrors.some((line) => line.indexOf('방 추가') >= 0), '콘솔에 안 남았다');
  const searchBar = doc.getElementById('search-bar');
  const searchHidden = searchBar.hidden;
  doc.getElementById('toggle-search').dispatch('click');
  assert.notEqual(searchBar.hidden, searchHidden, '검색까지 죽었다');
});

await test('결함 안내는 일상적인 빈 status 로 지워지지 않는다', async () => {
  const { doc, chat } = await boot({ virtual: null, rooms: [] });
  /* 방이 0개면 부트가 마지막에 status('') 로 상태줄을 비운다.
     그때 결함까지 지워지면 그 순간부터 다시 조용한 실패가 된다. */
  assert.ok(chat.state.broken);
  assert.ok(doc.getElementById('status').textContent.indexOf('메시지 영역') >= 0,
    '결함 안내가 지워졌다 — ' + JSON.stringify(doc.getElementById('status').textContent));
});

/* ------------------------------------------------- 낙관적 전송 (즉시 렌더) */

/* 응답을 **내가 원할 때** 주는 fetch 대역.
   즉시 resolve 되는 대역으로는 "기다리는 동안"이 존재하지 않아 낙관적
   업데이트를 아무것도 증명하지 못한다. */
function deferredFetch(extra) {
  const routes = Object.assign({ '/api/rooms': { rooms: [] } }, extra || {});
  const waiting = [];
  const fetch = function (path, init) {
    const opts = init || {};
    fetch.calls.push({ path, init: opts });
    if (path.indexOf('/messages') >= 0 && opts.method === 'POST') {
      return new Promise((resolve) => { waiting.push(resolve); });
    }
    const key = Object.keys(routes).find((k) => path.indexOf(k) === 0);
    const body = key ? routes[key] : { error: 'stub 라우트 없음: ' + path };
    return Promise.resolve({
      ok: !!key, status: key ? 200 : 404, json: () => Promise.resolve(body)
    });
  };
  fetch.calls = [];
  fetch.waiting = waiting;
  fetch.answer = function (body, code) {
    const resolve = waiting.shift();
    assert.ok(resolve, '기다리는 POST 가 없다');
    resolve({ ok: code === undefined || code < 300, status: code || 201,
      json: () => Promise.resolve(body) });
    return settle();
  };
  return fetch;
}

/* 서브트리에서 클래스로 노드 찾기 (재시도 버튼처럼 안쪽에 있는 것들). */
function findByClass(node, cls) {
  for (const child of node.children) {
    if (String(child.className).split(' ').indexOf(cls) >= 0) { return child; }
    const found = findByClass(child, cls);
    if (found) { return found; }
  }
  return null;
}

async function startSending(text) {
  const booted = await boot();
  const { doc, chat, context } = booted;
  const list = doc.getElementById('messages');
  const before = list.children.length;
  context.fetch = deferredFetch();
  doc.getElementById('text').value = text;
  const sending = chat.send();
  await settle();
  return Object.assign(booted, {
    list, before, sending, bubble: list.children[before]
  });
}

await test('⭐ 낙관적 전송: 응답을 기다리지 않고 지금 뜬다', async () => {
  const { doc, chat, list, before, bubble } = await startSending('지금 바로 떠야 한다');

  // 서버는 아직 아무 말도 하지 않았다.
  assert.equal(chat.pendings().size, 1);
  assert.equal(list.children.length, before + 1);
  assert.ok(bubble.textContent.includes('지금 바로 떠야 한다'));
  assert.ok(String(bubble.className).includes('pending'), '보내는 중 표시가 없다');
  assert.ok(bubble.textContent.includes('보내는 중'));
  assert.equal(doc.getElementById('text').value, '', '입력칸이 비워지지 않았다');
  assert.equal(chat.stats.innerHTML, 0);
  assert.equal(doc.counts.innerHTML, 0);
});

await test('⭐ 성공 시 진짜 봉투 ID 로 갈아끼우되 노드를 다시 만들지 않는다', async () => {
  const { chat, context, list, before, bubble, sending } =
    await startSending('갈아끼울 말');
  const createdBefore = chat.stats.created;

  const real = msg(30, '갈아끼울 말', '기본이름');
  await context.fetch.answer({ message: real });
  await sending;

  assert.equal(list.children.length, before + 1);
  assert.equal(list.children[before], bubble, '노드가 교체됐다');
  assert.equal(chat.stats.created, createdBefore, '노드를 새로 만들었다');
  assert.equal(chat.stats.rebuiltInView, 0);          // ⭐ 불변식
  assert.equal(bubble.dataset.id, real.id);
  assert.equal(bubble.getAttribute('data-id'), real.id);
  assert.equal(String(bubble.className).includes('pending'), false);
  assert.equal(bubble.textContent.includes('보내는 중'), false);
  assert.equal(chat.pendings().size, 0);

  // 모델은 여전히 시간순이고, 임시 ID 는 흔적도 없다.
  const ids = chat.items().map((m) => m.id);
  assert.deepEqual(ids, ids.slice().sort());
  assert.equal(ids.filter((id) => id.indexOf('~') === 0).length, 0);
  assert.equal(chat.state.oldest.indexOf('~'), -1, '임시 ID 가 페이징 커서가 됐다');
});

await test('⭐ 실패: 말풍선이 남고 "보내지 못했다" + 재시도가 붙는다 (입력 내용 안 잃음)', async () => {
  const { doc, chat, context, list, before, bubble, sending } =
    await startSending('실패할 말');

  await context.fetch.answer({ error: '원격이 죽었다' }, 500);
  await sending;

  assert.ok(String(bubble.className).includes('failed'));
  /* ⚠️ 문구가 '전송 실패' 에서 바뀌었다. 전송 응답이 원격 push 를 기다리지 않게
     되면서 이 실패는 **앱이 이 말을 받지 못했다**(어디에도 기록되지 않았다)를
     뜻하게 됐다. "상대에게 못 갔다"는 방 단위 사실이고 아웃박스 띠가 그린다. */
  assert.ok(bubble.textContent.includes('보내지 못했다'));
  assert.ok(bubble.textContent.includes('재시도'));
  // 글은 말풍선 안에 그대로 있다 — 입력칸으로 되돌리지 않는다(같은 글이 두 곳에
  // 생기는 것을 막는다).
  assert.ok(bubble.textContent.includes('실패할 말'));
  assert.equal(doc.getElementById('text').value, '');
  assert.equal(list.children.length, before + 1);
  assert.ok(doc.getElementById('status').textContent.includes('보내지 못했다'));

  // 재시도 — 사용자가 그 버튼을 누른다.
  const createdBefore = chat.stats.created;
  const button = findByClass(bubble, 'retry');
  assert.ok(button, '재시도 버튼이 없다');
  button.dispatch('click');
  await settle();
  assert.ok(String(bubble.className).includes('pending'), '다시 보내는 중이 아니다');

  const real = msg(31, '실패할 말', '기본이름');
  await context.fetch.answer({ message: real });
  assert.equal(list.children[before], bubble, '재시도가 노드를 갈아치웠다');
  assert.equal(chat.stats.created, createdBefore);
  assert.equal(chat.stats.rebuiltInView, 0);
  assert.equal(bubble.dataset.id, real.id);
  assert.equal(chat.pendings().size, 0);
});

await test('⭐ SSE 가 응답보다 먼저 와도 같은 말이 두 번 뜨지 않는다', async () => {
  const { chat, context, list, before, bubble, sending } =
    await startSending('겹치는 말');
  const createdBefore = chat.stats.created;
  const real = msg(32, '겹치는 말', '기본이름');

  // 서버 응답보다 폴링(SSE)이 먼저 도착하는 실제 순서.
  StubEventSource.current.emit('message', real);
  assert.equal(list.children.length, before + 1, '같은 말이 두 줄로 떴다');
  assert.equal(list.children[before], bubble);
  assert.equal(bubble.dataset.id, real.id);
  assert.equal(chat.stats.created, createdBefore);

  // 뒤늦게 온 응답도 아무것도 어지르지 않는다.
  await context.fetch.answer({ message: real });
  await sending;
  assert.equal(list.children.length, before + 1);
  assert.equal(list.children[before], bubble);
  assert.equal(chat.stats.rebuiltInView, 0);
  assert.equal(chat.pendings().size, 0);
  assert.equal(chat.items().filter((m) => m.text === '겹치는 말').length, 1);
});

/* ------------------------------------------- 아웃박스 (아직 못 나간 말) */

await test('⭐ 정상(synced/sending)에는 아웃박스 띠가 뜨지 않는다', async () => {
  const { doc, chat } = await boot();
  const box = doc.getElementById('outbox');
  assert.equal(box.hidden, true, '아무 일도 없는데 띠가 떴다');

  StubEventSource.current.emit('outbox',
    { room: 'r1', state: 'sending', pending: 2, detail: '' });
  assert.equal(box.hidden, true, '정상 경로(sending)인데 띠가 떴다');
  assert.equal(chat.outbox().state, 'sending');

  StubEventSource.current.emit('outbox',
    { room: 'r1', state: 'synced', pending: 0, detail: '' });
  assert.equal(box.hidden, true);
});

await test('⭐ stuck 이면 띠가 뜨고, 낫는 순간 사라진다 (조용한 실패 금지)', async () => {
  const { doc } = await boot();
  const box = doc.getElementById('outbox');

  StubEventSource.current.emit('outbox', {
    room: 'r1', state: 'stuck', pending: 3,
    detail: '인증에 실패했다 (토큰이 없거나 권한이 없다)'
  });
  assert.equal(box.hidden, false, '못 나갔는데 아무것도 안 보인다');
  const text = doc.getElementById('outbox-text').textContent;
  assert.ok(text.includes('3건'), text);
  assert.ok(text.includes('아직 상대에게 못 간'), text);
  assert.ok(text.includes('인증에 실패'), text);
  /* 로컬에는 남아 있다는 사실이 함께 보여야 한다 — 안 그러면 사용자가 다시 쓴다. */
  assert.ok(text.includes('내 기기에는 남아 있'), text);

  StubEventSource.current.emit('outbox',
    { room: 'r1', state: 'synced', pending: 0, detail: '' });
  assert.equal(box.hidden, true, '나갔는데 경고가 남았다');
});

await test('⭐ 아웃박스 띠의 "다시 보내기"가 서버를 부른다', async () => {
  const { doc, fetchStub } = await boot({
    routes: { '/api/rooms/r1/outbox': { outbox: { state: 'sending', pending: 1, detail: '' } } }
  });
  StubEventSource.current.emit('outbox',
    { room: 'r1', state: 'stuck', pending: 1, detail: '네트워크에 연결하지 못했다' });
  assert.equal(doc.getElementById('outbox').hidden, false);

  doc.getElementById('outbox-retry').dispatch('click');
  await settle();
  const calls = fetchStub.calls.filter((c) => c.path === '/api/rooms/r1/outbox');
  assert.equal(calls.length, 1, '다시 보내기가 서버를 부르지 않았다');
  assert.equal(calls[0].init.method, 'POST');
  /* 서버가 준 그 순간의 상태를 그대로 반영한다 (낙관적으로 지우지 않는다). */
  assert.equal(doc.getElementById('outbox').hidden, true);
});

await test('방을 막 열었을 때 이미 못 나간 말이 있으면 바로 보인다', async () => {
  const { doc } = await boot({
    rooms: [
      { id: 'r1', repo_url: 'https://example.invalid/one.git', name: '첫 방',
        outbox: { state: 'stuck', pending: 2, detail: '네트워크에 연결하지 못했다' } },
      { id: 'r2', repo_url: 'https://example.invalid/two.git', name: '둘째 방' }
    ]
  });
  assert.equal(doc.getElementById('outbox').hidden, false,
    '최초 그리기에서 못 나간 말을 놓쳤다');
  assert.ok(doc.getElementById('outbox-text').textContent.includes('2건'));
});

await test('다른 방의 아웃박스 상태가 지금 방에 새지 않는다', async () => {
  const { doc } = await boot();
  StubEventSource.current.emit('outbox',
    { room: 'r2', state: 'stuck', pending: 9, detail: '남의 방 사고' });
  assert.equal(doc.getElementById('outbox').hidden, true);
});

await test('보류 중에 방을 바꾸면 그 방의 말이 새 방에 새지 않는다', async () => {
  const { chat, context, list, sending } = await startSending('첫 방에서 쓴 말');
  const real = msg(33, '첫 방에서 쓴 말', '기본이름');

  await chat.switchRoom('r2');
  assert.equal(chat.pendings().size, 0, '방을 바꿨는데 보류가 남았다');
  const after = list.children.length;

  await context.fetch.answer({ message: real });
  await sending;
  assert.equal(list.children.length, after, '다른 방 메시지가 새어 들어왔다');
});

/* -------------------------------------------- '내 것' 판정 (좌우 배치) */

/*
 * ⭐ `mine` 은 **서버가 봉투를 보고 판정해서 실어 보낸 값**이다. 화면은 그 값만
 * 본다 — 여기 테스트가 지키는 것은 "화면이 스스로 다시 판정하지 않는다"이다.
 * 예전에는 전송 직후에만 손으로 참을 박아서, 새로고침하면 내가 쓴 말이 전부
 * 남의 것으로 넘어갔다.
 */

await test("⭐ 최초 로드: 서버 판정대로 좌우가 갈린다", async () => {
  const { doc, chat } = await boot({
    messages: [mineMsg(1, '내가 쓴 말'), msg(2, '남이 쓴 말'), mineMsg(3, '또 내 말')]
  });
  const list = doc.getElementById('messages');
  assert.deepEqual(list.children.map(isMine), [true, false, true]);
  assert.equal(chat.stats.rebuiltInView, 0);
});

await test("위로 페이징으로 올라온 과거도 같은 판정을 지킨다", async () => {
  const past = [mineMsg(1, '오래된 내 말'), msg(2, '오래된 남의 말')];
  const { doc, chat } = await boot({
    past: past, pageSize: 2, hasMore: true, messages: [msg(9, '최근')]
  });
  await chat.loadOlder();
  await settle();
  const list = doc.getElementById('messages');
  const mineNode = list.children.find((n) => n.textContent.includes('오래된 내 말'));
  const otherNode = list.children.find((n) => n.textContent.includes('오래된 남의 말'));
  assert.ok(mineNode && otherNode, '과거가 올라오지 않았다');
  assert.equal(isMine(mineNode), true, '위로 올린 내 말이 남의 것이 됐다');
  assert.equal(isMine(otherNode), false);
  assert.equal(chat.stats.rebuiltInView, 0);
});

await test("SSE: 남의 말은 왼쪽, 내 다른 탭이 보낸 말은 오른쪽", async () => {
  const { doc, chat } = await boot();
  const list = doc.getElementById('messages');
  const before = list.children.length;

  StubEventSource.current.emit('message', msg(20, '남이 보낸 말'));
  StubEventSource.current.emit('message', mineMsg(21, '내 다른 탭이 보낸 말'));
  await settle();

  const other = list.children.find((n) => n.textContent.includes('남이 보낸 말'));
  const own = list.children.find((n) => n.textContent.includes('내 다른 탭이 보낸 말'));
  assert.ok(other && own, 'SSE 메시지가 붙지 않았다');
  assert.equal(isMine(other), false);
  assert.equal(isMine(own), true);
  assert.equal(list.children.length, before + 2);
  assert.equal(chat.stats.rebuiltInView, 0);
});

await test("⭐ 낙관적 항목은 봉투가 없어도 내 것이다", async () => {
  const { bubble } = await startSending('아직 봉투가 없는 말');
  assert.ok(String(bubble.className).includes('pending'));
  assert.equal(isMine(bubble), true, '내가 방금 쓴 말이 내 것이 아니다');
});

await test("⭐ 봉투가 도착하면 서버 판정이 그 자리를 대신한다", async () => {
  /* 화면이 '보내는 중이던 것 = 무조건 내 것'으로 박아 두면, 봉투가 온 뒤에도
     화면의 판정과 서버의 판정이 갈린다. 그 하드코딩이 없다는 것을 **서버가
     반대로 말하게 해서** 증명한다 (실제로는 일어나지 않는 상황이다). */
  const first = await startSending('서버가 아니라고 말할 말');
  await first.context.fetch.answer({ message: msg(40, '서버가 아니라고 말할 말') });
  await first.sending;
  assert.equal(isMine(first.bubble), false, '화면이 서버 판정을 무시했다');
  assert.equal(first.chat.stats.rebuiltInView, 0);

  /* 정상적인 경우 — 서버도 내 것이라고 말하고, 말풍선은 오른쪽에 그대로 있다. */
  const second = await startSending('서버도 내 것이라 할 말');
  await second.context.fetch.answer({ message: mineMsg(41, '서버도 내 것이라 할 말') });
  await second.sending;
  assert.equal(isMine(second.bubble), true);
  assert.equal(second.chat.stats.rebuiltInView, 0);
});

await test("검색 결과도 같은 표식을 단다", async () => {
  const { doc, chat } = await boot({
    routes: {
      '/api/rooms/r1/search': {
        messages: [mineMsg(5, '검색된 내 말'), msg(6, '검색된 남의 말')], query: '검색'
      }
    }
  });
  doc.getElementById('search-q').value = '검색';
  await chat.runSearch();
  await settle();
  const hits = doc.getElementById('search-list').children;
  assert.equal(hits.length, 2);
  assert.deepEqual(hits.map((h) => String(h.className)), ['hit mine', 'hit']);
});

/* --------------------------------------------- IME(한글) 조합 중의 Enter */

/*
 * ⭐ 맥에서 **마지막 글자가 씹혀 전송되던** 버그의 회귀 방지.
 *
 * 원인: `keydown` 은 IME 의 조합 확정보다 **먼저** 온다. 그래서 그때 읽은
 * `value` 에는 마지막 음절이 없다. 아래 테스트는 그 순간을 그대로 재현한다 —
 * 조합 중에는 value 가 짧고, 확정될 때 마지막 음절이 들어온다.
 *
 * 지키는 것 두 개가 대칭이 아니다(의도한 비대칭):
 *   · **잘려서 보내지는 일은 없어야 한다** (모르고 지나가는 사고)
 *   · 못 보내는 것은 허용한다 (사용자가 Enter 를 다시 누르면 된다)
 */

/* 키 이벤트 대역. `preventDefault` 를 불렀는지 기록한다 — 조합 중 Enter 를
   막으면 IME 가 확정을 못 하고 그 글자가 입력칸에 남는다(보고된 두 번째 증상). */
function keyEvent(key, extra) {
  return Object.assign({
    key: key, shiftKey: false, prevented: false,
    preventDefault() { this.prevented = true; }
  }, extra || {});
}

function posts(context) {
  /* ⚠️ **메시지 전송만** 센다. 읽음 표시(`/reads`)·가시성 보고도 POST 이므로
     전부 세면 "Enter 로 보냈나"의 판정이 다른 기능의 유무에 묶인다. */
  return context.fetch.calls.filter(
    (c) => (c.init || {}).method === 'POST' && c.path.indexOf('/messages') >= 0
  );
}

function bodyOf(call) { return JSON.parse(call.init.body); }

async function imeBoot() {
  const booted = await boot();
  booted.context.fetch = deferredFetch();
  booted.text = booted.doc.getElementById('text');
  booted.list = booted.doc.getElementById('messages');
  booted.before = booted.list.children.length;
  return booted;
}

/* IME 안전 타이머만 골라낸다 — 읽음 표시의 디바운스 타이머(`reads.js`)가 같은
   창(window)의 타이머를 쓰므로, 전체 목록으로 세면 이 판정이 그 모듈의 존재
   여부에 묶인다. 여기서 보려는 것은 **IME 타이머 하나**다. */
function imeTimers(context) {
  return context.win.pendingTimers()
    .filter((ms) => ms !== readsMod.MARK_DEBOUNCE_MS);
}

await test('⭐ 조합 중 Enter: 그 자리에서 보내지 않고, 확정된 뒤 **완전한 본문**을 보낸다', async () => {
  const { doc, context, text, list, before } = await imeBoot();

  /* 맥 한글 IME 로 "안녕하세요다" 를 치고 Enter — 이 순간 value 에는 마지막
     음절('다')이 아직 없다. 이게 버그가 나던 정확한 상태다. */
  text.dispatch('compositionstart');
  text.value = '안녕하세요';
  const enter = keyEvent('Enter', { isComposing: true });
  text.dispatch('keydown', enter);
  await settle();

  assert.equal(posts(context).length, 0, '조합 중인데 전송됐다 (잘린 본문이 나갔다)');
  assert.equal(list.children.length, before, '말풍선이 붙었다 (낙관적 전송이 나갔다)');
  assert.equal(text.value, '안녕하세요', '입력칸이 비워졌다');
  assert.equal(enter.prevented, false,
    'preventDefault 로 IME 의 조합 확정을 막았다 — 그 글자가 입력칸에 남는다');

  /* IME 가 그 Enter 로 조합을 확정한다: 마지막 음절이 들어오고 신호가 온다. */
  text.value = '안녕하세요다';
  text.dispatch('compositionend');
  assert.equal(posts(context).length, 0,
    '확정 신호와 같은 틱에 보냈다 — 엔진에 따라 value 가 아직 안 찬다');

  context.win.runTimers(0);              /* 한 틱 양보 후 전송 */
  await settle();

  const sent = posts(context);
  assert.equal(sent.length, 1, '확정됐는데 전송되지 않았다');
  assert.equal(bodyOf(sent[0]).text, '안녕하세요다', '마지막 글자가 씹혔다');
  assert.equal(text.value, '', '전송했는데 입력칸이 남았다');
  assert.equal(list.children.length, before + 1);
  assert.ok(list.children[before].textContent.includes('안녕하세요다'));
  assert.deepEqual(imeTimers(context), [], '안전 타이머가 남았다');
});

await test('같은 판정이 `keyCode 229` 경로에서도 선다 (isComposing 을 안 주는 브라우저)', async () => {
  const { context, text } = await imeBoot();
  /* compositionstart 를 일부러 주지 않는다 — 키 이벤트만으로 판정되는지 본다. */
  text.value = '테스트';
  const enter = keyEvent('Enter', { keyCode: 229 });
  text.dispatch('keydown', enter);
  await settle();
  assert.equal(posts(context).length, 0, '229 를 조합 중으로 보지 않았다');
  assert.equal(enter.prevented, false);

  text.value = '테스트다';
  text.dispatch('compositionend');
  context.win.runTimers(0);
  await settle();
  assert.equal(bodyOf(posts(context)[0]).text, '테스트다');
});

await test('⭐ 평소 타이핑의 조합 확정으로는 전송되지 않는다 (표시 오염 방지)', async () => {
  const { context, text } = await imeBoot();

  /* (1) Enter 없이 음절만 확정 — `compositionend` 는 평소 타이핑에서도 온다. */
  for (const syllable of ['안', '안녕', '안녕하']) {
    text.dispatch('compositionstart');
    text.value = syllable;
    text.dispatch('compositionend');
  }
  context.win.runTimers(0);
  await settle();
  assert.equal(posts(context).length, 0, 'Enter 를 누르지 않았는데 전송됐다');

  /* (2) 조합 중 Enter 로 표시를 세운 **뒤에 다른 글자를 더 쳤다** = 전송 의도 무효.
     이걸 지우지 않으면 다음 음절 확정 때 엉뚱하게 나간다. */
  text.dispatch('compositionstart');
  text.value = '안녕하';
  text.dispatch('keydown', keyEvent('Enter', { isComposing: true }));
  text.dispatch('keydown', keyEvent('세', { isComposing: true }));
  text.value = '안녕하세';
  text.dispatch('compositionend');
  context.win.runTimers(0);
  await settle();
  assert.equal(posts(context).length, 0, '전송 의도를 취소했는데 나갔다');
  assert.equal(text.value, '안녕하세', '입력칸 내용이 사라졌다');
});

await test('안전 타이머는 **취소 전용** — 만료되면 전송되지 않고 표시만 풀린다', async () => {
  const { context, text } = await imeBoot();
  text.dispatch('compositionstart');
  text.value = '확정 신호가 안 오는 환경';
  text.dispatch('keydown', keyEvent('Enter', { isComposing: true }));
  await settle();
  assert.deepEqual(imeTimers(context), [3000], '안전 타이머가 걸리지 않았다');

  context.win.runTimers(3000);           /* 신호를 못 받은 채 한도가 지났다 */
  await settle();
  assert.equal(posts(context).length, 0,
    '⚠️ 안전 타이머가 **보냈다** — 확정을 모르는 상태의 전송이라 잘릴 수 있다');
  assert.equal(text.value, '확정 신호가 안 오는 환경', '입력 내용을 잃었다');

  /* 늦게 신호가 와도 표시는 이미 풀렸다 — 조용히 나가지 않는다. */
  text.dispatch('compositionend');
  context.win.runTimers(0);
  await settle();
  assert.equal(posts(context).length, 0);

  /* 그리고 모듈은 멀쩡하다 — 사용자가 Enter 를 다시 누르면 보내진다. */
  text.dispatch('keydown', keyEvent('Enter'));
  await settle();
  assert.equal(posts(context).length, 1, '다시 누른 Enter 로도 안 보내진다');
  assert.equal(bodyOf(posts(context)[0]).text, '확정 신호가 안 오는 환경');
});

await test('영문 입력은 아무 변화가 없다 — Enter 한 번에 즉시 전송', async () => {
  const { context, text } = await imeBoot();
  text.value = 'ship it';
  const enter = keyEvent('Enter', { isComposing: false });
  text.dispatch('keydown', enter);
  await settle();
  assert.equal(posts(context).length, 1, '영문 Enter 가 지연됐다');
  assert.equal(bodyOf(posts(context)[0]).text, 'ship it');
  assert.equal(enter.prevented, true, '줄바꿈 기본 동작을 막지 않았다');
  assert.equal(text.value, '');
  assert.deepEqual(imeTimers(context), [], '영문인데 IME 타이머가 걸렸다');
});

await test('Shift+Enter 는 줄바꿈이다 (조합 중에도 보내지 않는다)', async () => {
  const { context, text } = await imeBoot();
  text.value = '첫 줄';
  const shifted = keyEvent('Enter', { shiftKey: true, isComposing: true });
  text.dispatch('keydown', shifted);
  text.dispatch('compositionend');        /* 확정돼도 전송 의도가 없다 */
  context.win.runTimers(0);
  await settle();
  assert.equal(posts(context).length, 0, 'Shift+Enter 로 전송됐다');
  assert.equal(shifted.prevented, false, '줄바꿈을 막았다');
  assert.equal(text.value, '첫 줄');
});

await test('⭐ 대조군: 조합 여부를 보지 않고 보내면 잘린 본문이 나간다 (버그 재현)', async () => {
  /* 이 테스트들이 **실제 버그를 겨냥하는지**를 증명한다. 같은 상태에서 가드를
     지나쳐 곧바로 보내면(= 고치기 전 핸들러가 하던 일) 마지막 음절이 빠진다. */
  const { chat, context, text } = await imeBoot();
  text.dispatch('compositionstart');
  text.value = '안녕하세요';              /* 조합 중 — '다' 가 아직 없다 */

  chat.send();                            /* 가드 없는 경로 (응답은 기다리지 않는다) */
  await settle();

  const sent = posts(context);
  assert.equal(sent.length, 1);
  assert.equal(bodyOf(sent[0]).text, '안녕하세요',
    '대조군이 성립하지 않는다 — 이 경로에서 잘림이 재현되지 않으면 위 테스트가 무엇도 증명하지 못한다');
  assert.notEqual(bodyOf(sent[0]).text, '안녕하세요다');
});

await test('보내기 버튼(폼 제출)은 확정된 완전한 본문을 보낸다', async () => {
  /* 버튼을 누르려면 포인터가 입력칸을 떠나고, 그 blur 가 조합을 먼저 확정시킨다.
     그래서 폼 경로에는 IME 가드를 두지 않는다 — 두면 "눌렀는데 안 보내진다"가 된다. */
  const { doc, context, text } = await imeBoot();
  text.dispatch('compositionstart');
  text.value = '버튼으로 보낸다';
  text.value = '버튼으로 보낸다요';        /* blur → IME 확정 */
  text.dispatch('compositionend');
  context.win.runTimers(0);
  await settle();
  assert.equal(posts(context).length, 0, 'Enter 도 없이 전송됐다');

  doc.getElementById('composer').dispatch('submit', { preventDefault() {} });
  await settle();
  assert.equal(bodyOf(posts(context)[0]).text, '버튼으로 보낸다요');
  assert.equal(text.value, '');
});

/* ------------------------------------------------------- 레이아웃 테마 */

/*
 * ⭐ 단계 A — **이음새만** 냈다. 구조는 지금 것(말풍선) 하나만 출하한다.
 *
 * 여기서 지키는 것:
 *   · 구조 분기는 `message-node.js` 의 표 **한 곳**뿐이고, 모르는 이름은 기본으로
 *     떨어진다 (저장값이 옛 이름일 수 있다).
 *   · 전환은 **새로고침**으로 간다 — 그래서 "전환 중 노드 재생성"이라는 상태가
 *     존재하지 않는다. 대신 읽던 자리를 **앵커 메시지 id** 로 복원한다.
 *   · 테마가 못 서면 **기본으로 떨어지고 화면에 말한다.** 고르는 칸은 대화 영역
 *     밖(사이드바)에 있어 망가진 테마에 갇히지 않는다.
 */

const LAYOUT_KEY = 'gitwire-chat.layout';
const ANCHOR_KEY = 'gitwire-chat.anchor';
const THEME_ATTR_FOR_FALLBACK = 'data-chat-theme';

function themeAttr(doc) {
  return doc.documentElement.getAttribute(THEME_ATTR_FOR_FALLBACK);
}

function layoutAttr(doc) {
  return doc.documentElement.getAttribute('data-chat-layout');
}

await test('구조 분기는 한 곳이고, 모르는 레이아웃 이름은 기본으로 떨어진다', () => {
  const names = Object.keys(nodeMod.STRUCTURES);
  assert.deepEqual(names, ['bubbles', 'log', 'ide'], '구조 표가 달라졌다: ' + names);
  /* 구조가 아는 나머지 두 표(묶는가 · 항목 간격)도 **같은 이름 공간**을 쓴다.
     어긋나면 "구조는 있는데 묶기·간격이 없는" 배치가 생긴다. */
  for (const name of Object.keys(nodeMod.GROUPED)) {
    assert.ok(names.indexOf(name) >= 0, '묶기 표에 없는 배치가 있다: ' + name);
  }
  for (const name of Object.keys(nodeMod.GAPS)) {
    assert.ok(names.indexOf(name) >= 0, '간격 표에 없는 배치가 있다: ' + name);
  }
  assert.equal(nodeMod.grouped('ide'), true);
  assert.equal(nodeMod.grouped('bubbles'), false);
  assert.equal(nodeMod.grouped('없는배치'), false);
  assert.equal(nodeMod.itemGap('없는배치'), nodeMod.GAPS.bubbles);
  /* 이름 → 구조 표와 고를 수 있는 목록이 **짝**이어야 한다. 어긋나면 "고를 수는
     있는데 구조가 없는" 이름이 생긴다. */
  assert.deepEqual(themeMod.LAYOUTS.map((l) => l.id).sort(), names.sort());
  assert.equal(themeMod.normalizeLayout('없는배치'), 'bubbles');
  assert.equal(themeMod.normalizeLayout(null), 'bubbles');

  /* 실제로 만들어 본다 — 모르는 이름을 줘도 말풍선이 나온다(빈 화면이 아니다). */
  const doc = new StubDocument();
  const dom = { doc: doc, make: (t, c, x) => {
    const n = doc.createElement(t); if (c) { n.className = c; }
    if (x !== undefined) { n.textContent = x; } return n;
  }, setText: (n, t) => { n.textContent = t; }, hide: (n) => { n.hidden = true; },
    show: (n) => { n.hidden = false; } };
  const one = msg(1, '어떤 구조로든 그려진다');
  const built = nodeMod.buildMessage(dom, one, { lookup: () => null }, '없는배치');
  assert.equal(String(built.className), 'msg');
  assert.ok(built.textContent.includes('어떤 구조로든 그려진다'));
});

await test('⭐ 앵커: 읽던 자리를 메시지 id 로 남기고, 새로고침 뒤 그 자리로 돌아온다', async () => {
  const many = manyMessages(200);
  const first = await boot({ messages: many, viewport: 300 });
  await settle();
  const timeline = first.doc.getElementById('timeline');
  timeline.scrollTop = 4000;
  timeline.dispatch('scroll');
  await settle();

  /* 지금 화면 위에 걸린 메시지 = 읽던 자리. */
  const anchorId = first.chat.keepAnchor();
  assert.ok(anchorId, '앵커를 못 잡았다');
  const saved = JSON.parse(first.context.localStorage.getItem(ANCHOR_KEY));
  assert.deepEqual(saved, { room: 'r1', id: anchorId });
  /* ⚠️ 픽셀이 아니다 — 구조가 바뀌면 높이가 달라져 픽셀은 의미가 없다. */
  assert.equal(JSON.stringify(saved).indexOf('scrollTop'), -1);

  /* 새로고침 = 저장소만 들고 처음부터 다시 부팅한다. */
  const stored = {};
  stored[ANCHOR_KEY] = JSON.stringify(saved);
  const again = await boot({ messages: many, viewport: 300, stored: stored });
  await settle();

  const shown = again.doc.getElementById('messages').children.map((n) => n.dataset.id);
  assert.ok(shown.indexOf(anchorId) >= 0, '읽던 메시지가 화면에 없다');
  assert.equal(again.chat.stats.restored, 1, '복원 경로를 타지 않았다');
  assert.equal(again.chat.state.atBottom, false, '맨 아래로 가 버렸다');
  assert.equal(again.chat.stats.rebuiltInView, 0);
  /* ⭐ 앵커는 **한 번만** 쓴다 — 안 지우면 그 뒤 모든 새로고침이 그 자리로 간다. */
  assert.equal(again.context.localStorage.getItem(ANCHOR_KEY), null, '앵커가 남았다');

  /* 가장 강한 증거: 다시 물어본 "화면 위에 걸린 메시지"가 **같은 메시지**다. */
  const nowTop = again.chat.keepAnchor();
  console.log('      앵커 복원: 남긴 것 …' + anchorId.slice(-20) +
    ' / 복원 후 화면 위 …' + String(nowTop).slice(-20) +
    ' (scrollTop ' + again.doc.getElementById('timeline').scrollTop + ')');
  assert.equal(nowTop, anchorId, '읽던 자리가 아니다');
});

await test('앵커가 없거나 다른 방 것이면 조용히 맨 아래로 뜬다', async () => {
  const many = manyMessages(50);
  /* (1) 앵커 없음 — 지금까지의 동작 그대로. */
  const plain = await boot({ messages: many, viewport: 300 });
  await settle();
  assert.equal(plain.chat.state.atBottom, true);
  assert.equal(plain.chat.stats.restored, 0);

  /* (2) 다른 방에서 남긴 앵커 — 이 방에 적용하면 엉뚱한 자리로 간다. */
  const stored = {};
  stored[ANCHOR_KEY] = JSON.stringify({ room: 'r2', id: many[10].id });
  const other = await boot({ messages: many, viewport: 300, stored: stored });
  await settle();
  assert.equal(other.chat.stats.restored, 0, '다른 방의 앵커로 스크롤했다');
  assert.equal(other.chat.state.atBottom, true);

  /* (3) 이 방 것이지만 지금 안 불러온 메시지 — 조용히 맨 아래로. */
  const gone = {};
  gone[ANCHOR_KEY] = JSON.stringify({ room: 'r1', id: 'records/없는/메시지.json' });
  const missing = await boot({ messages: many, viewport: 300, stored: gone });
  await settle();
  assert.equal(missing.chat.stats.restored, 0);
  assert.equal(missing.chat.state.atBottom, true);
});

await test('같은 배치를 다시 골라도 새로고침하지 않는다', async () => {
  const { chat, context } = await boot();
  assert.equal(chat.layout(), 'bubbles');
  assert.equal(chat.setLayout('bubbles'), false, '같은 값인데 전환했다');
  assert.equal(context.win.reloads, 0, '쓸데없이 새로고침했다');
  /* 없는 이름은 기본으로 정규화되므로 이 경우도 전환이 아니다. */
  assert.equal(chat.setLayout('없는배치'), false);
  assert.equal(context.win.reloads, 0);
  assert.equal(context.localStorage.getItem(LAYOUT_KEY), 'bubbles');
});

await test('⭐ 폴백: 저장된 배치 이름이 없는 것이면 기본으로 떨어진다', async () => {
  const stored = {};
  stored[LAYOUT_KEY] = 'log-옛이름';
  const { doc, chat } = await boot({ stored: stored });
  assert.equal(chat.layout(), 'bubbles');
  assert.equal(layoutAttr(doc), null, '없는 배치 표식이 루트에 남았다');
  /* 첫 페인트 조각이 찍어 둔 표식도 여기서 걷힌다 (아래 계약 테스트가 짝을 본다). */
  assert.equal(doc.getElementById('layout-select').value, 'bubbles');
});

await test('⭐ 폴백: 테마가 못 서면 기본으로 떨어지고 **화면에 말한다**', async () => {
  /* ⚠️ 이게 이번 단계의 가장 위험한 함정이다 — 망가진 테마가 화면을 먹으면
     고르는 UI 도 같이 먹혀 되돌릴 방법이 없다(저장값이 남아 새로고침해도 같다).
     그래서 실패하면 루트 표식을 걷어내 **지금까지의 모습**으로 떨어뜨린다. */
  const stored = {};
  stored['gitwire-chat.theme'] = 'tty';
  stored[LAYOUT_KEY] = 'bubbles';
  const { doc, chat, consoleErrors } = await boot({
    stored: stored,
    sabotage: (doc2) => breakWiring(doc2, 'toggle-theme', '테마 배선 실패')
  });

  assert.equal(themeAttr(doc), null, '실패했는데 색 표식이 남았다');
  assert.equal(layoutAttr(doc), null, '실패했는데 배치 표식이 남았다');
  assert.equal(chat.layout(), 'bubbles', '구조가 기본으로 떨어지지 않았다');

  /* 조용히 떨어지지 않는다 — 상태줄·콘솔·failures 세 곳에 남는다. */
  assert.ok(doc.getElementById('status').textContent.indexOf('초기화 실패') >= 0);
  assert.ok(doc.getElementById('status').textContent.indexOf('테마') >= 0);
  assert.ok(consoleErrors.join(' ').indexOf('테마') >= 0);
  assert.equal(chat.failures()[0].unit, '테마');

  /* 그리고 대화는 멀쩡하다 (테마 실패가 화면을 먹지 않는다). */
  assert.equal(doc.getElementById('messages').children.length, 3);
  /* 고르는 칸은 **대화 영역 밖**(사이드바)에 그대로 있다 — 갇히지 않는다. */
  assert.ok(doc.getElementById('theme-select'), '색 고르는 칸이 사라졌다');
  assert.ok(doc.getElementById('layout-select'), '배치 고르는 칸이 사라졌다');
});

await test('log 배치로 뜨면 줄 구조로 그려진다 (시각·발신자·본문 3조각)', async () => {
  const stored = {};
  stored[LAYOUT_KEY] = 'log';
  const { doc, chat } = await boot({
    stored: stored,
    messages: [msg(1, '첫 줄', '앨리스'), mineMsg(2, '내 줄')]
  });
  assert.equal(chat.layout(), 'log');
  assert.equal(layoutAttr(doc), 'log', '루트 표식이 없다 — CSS 가 안 걸린다');

  const rows = doc.getElementById('messages').children;
  assert.equal(rows.length, 2);
  const row = rows[0];
  /* 3조각: `.ts` · `.author` · `.line`. 말풍선의 `.msg-head` 는 없다. */
  /* 3조각 + 읽음 카운트 자리(마지막·절대 위치라 순서가 화면에 영향 없다). */
  assert.deepEqual(row.children.map((c) => String(c.className)),
    ['ts', 'author', 'line', 'msg-reads']);
  assert.equal(row.children[1].textContent, '앨리스');
  /* 시각은 초까지, 그리고 초는 **별도 조각**이다 (좁은 폭에서 CSS 가 이것만 숨긴다). */
  const when = row.children[0];
  assert.deepEqual(when.children.map((c) => String(c.className)), ['hm', 'sec']);
  /* 오늘이면 HH:MM, 다른 날이면 M/D HH:MM — 어느 쪽이든 분까지가 이 조각이다. */
  assert.ok(/\d{2}:\d{2}$/.test(when.children[0].textContent), when.children[0].textContent);
  assert.ok(/^:\d{2}$/.test(when.children[1].textContent), when.children[1].textContent);
  /* 내 것은 **위치가 아니라** 클래스로 갈린다 (줄 기반에서 좌우는 성립하지 않는다). */
  assert.equal(isMine(rows[1]), true);
  assert.equal(chat.stats.rebuiltInView, 0);
  assert.equal(doc.counts.innerHTML, 0);
});

await test('발신자 색 슬롯은 이름에서 나온다 (같은 사람 = 언제나 같은 색)', async () => {
  assert.equal(nodeMod.senderSlot('앨리스'), nodeMod.senderSlot('앨리스'));
  const slots = ['앨리스', '밥', '캐럴', '데이브', '이브', '프랭크', '그레이스']
    .map(nodeMod.senderSlot);
  for (const s of slots) {
    assert.ok(s >= 1 && s <= nodeMod.SENDER_SLOTS, '슬롯이 범위를 벗어났다: ' + s);
  }
  assert.ok(new Set(slots).size >= 4, '이름 7개가 색 4가지도 안 된다: ' + slots);

  const stored = {};
  stored[LAYOUT_KEY] = 'log';
  const { doc } = await boot({
    stored: stored, messages: [msg(1, '하나', '앨리스'), msg(2, '둘', '밥')]
  });
  const rows = doc.getElementById('messages').children;
  assert.equal(rows[0].getAttribute('data-sender'), String(nodeMod.senderSlot('앨리스')));
  assert.equal(rows[1].getAttribute('data-sender'), String(nodeMod.senderSlot('밥')));
});

await test('⭐ 줄무늬는 **모델 인덱스** 기준이다 (스크롤해도 홀짝이 뒤집히지 않는다)', async () => {
  const stored = {};
  stored[LAYOUT_KEY] = 'log';
  const { doc, chat } = await boot({
    stored: stored, messages: manyMessages(200), viewport: 300
  });
  const list = doc.getElementById('messages');
  const timeline = doc.getElementById('timeline');

  function check(where) {
    const ids = chat.items().map((m) => m.id);
    let domOrderDiffers = false;
    let lastIndex = -1;
    for (const node of list.children) {
      const index = Number(node.getAttribute('data-index'));
      /* 모델에서의 자리와 홀짝 표식이 맞나 */
      assert.equal(ids[index], node.dataset.id, where + ': data-index 가 모델과 다르다');
      assert.equal(node.getAttribute('data-row'), index % 2 === 0 ? 'even' : 'odd',
        where + ': ' + index + '번의 홀짝이 틀렸다');
      if (index < lastIndex) { domOrderDiffers = true; }
      lastIndex = index;
    }
    return domOrderDiffers;
  }

  check('처음');
  let sawShuffle = false;
  /* ⚠️ 마지막 두 개가 요점이다: 창이 **겹치는** 상태로 위로 조금 올라가면
     남아 있는 노드 뒤에 낮은 인덱스가 붙어 DOM 순서가 모델 순서와 어긋난다. */
  for (const offset of [3000, 9000, 14000, 13600, 500]) {
    timeline.scrollTop = offset;
    timeline.dispatch('scroll');
    await settle();
    sawShuffle = check('scrollTop ' + offset) || sawShuffle;
  }
  /* ⭐ 대조군의 자리: 가상 스크롤은 노드를 걷어내고 **끝에 다시 붙인다.** 그래서
     DOM 순서가 모델 순서와 실제로 어긋난다 — `:nth-child` 로 칠했다면 바로 여기서
     홀짝이 뒤집혔을 것이다. 어긋남을 확인해 두는 것이 그 증거다. */
  console.log('      DOM 순서가 모델 순서와 어긋난 적: ' + sawShuffle +
    ' · 걷어낸 노드 ' + chat.stats.recycled + '개 · rebuiltInView ' +
    chat.stats.rebuiltInView);
  assert.equal(sawShuffle, true,
    'DOM 순서가 한 번도 어긋나지 않았다 — 이 테스트가 무엇도 증명하지 못한다');
  assert.ok(chat.stats.recycled > 0);
  assert.equal(chat.stats.rebuiltInView, 0);
});

await test('log 배치에서도 답장 인용·보내는 중·재시도가 산다', async () => {
  const stored = {};
  stored[LAYOUT_KEY] = 'log';
  const quoted = msg(1, '원래 말', '앨리스');
  const reply = msg(2, '답장이다', '밥');
  reply.reply_to = quoted.id;
  const { doc, chat, context } = await boot({
    stored: stored, messages: [quoted, reply]
  });
  const rows = doc.getElementById('messages').children;
  const line = rows[1].children[2];
  assert.equal(String(line.children[0].className), 'quote');
  assert.ok(line.children[0].textContent.includes('앨리스: 원래 말'), '인용이 비었다');
  /* 답장 버튼도 줄 안에 있다 */
  assert.ok(findByClass(rows[1], 'link'), '답장 버튼이 없다');

  /* 낙관적 전송 → 실패 → 재시도가 같은 노드 위에서 돈다 (구조와 무관하다). */
  context.fetch = deferredFetch();
  doc.getElementById('text').value = '보내는 중이 보여야 한다';
  const sending = chat.send();
  await settle();
  const bubble = doc.getElementById('messages').children[2];
  assert.ok(bubble.textContent.includes('보내는 중'), '보내는 중이 안 보인다');
  await context.fetch.answer({ error: '원격이 죽었다' }, 500);
  await sending;
  assert.ok(bubble.textContent.includes('보내지 못했다'));
  assert.ok(findByClass(bubble, 'retry'), '재시도가 없다');
  assert.equal(chat.stats.rebuiltInView, 0);
});

/* ----------------------------------------- 배치 `ide` — 연속 발화 묶기 */

/*
 * ⭐ 여기서 지키는 것은 "묶여 보이나"가 아니라 **묶기가 가상화를 깨지 않나**다.
 *
 * 묶기를 가상화 단위로 만들면(묶음 = 항목) 두 곳에서 깨진다: 묶음 크기에 상한이
 * 없어 3건을 보이려고 200건을 그리게 되고, 과거를 불러올 때 묶음이 합쳐져 항목
 * 경계·인덱스·키가 흔들린다. 그래서 단위는 **메시지**로 두고 머리를 첫 메시지의
 * 속성으로 만들었다. 그 선택이 실제로 값을 갚는지를 아래가 수치로 본다:
 *   · 머리가 잘려도 누가 말했는지 화면에 있다 (떠 있는 머리)
 *   · 과거를 불러와 묶음이 이어질 때 **그 항목 하나만** 다시 잰다
 *   · 한 사람이 연달아 수백 건 말해도 DOM 노드 수는 상수에 가깝다
 *   · `rebuiltInView` 는 어느 경로에서도 0
 */

/* 로컬 시각 한 점. ⚠️ **로컬**이어야 한다 — 날 경계는 사용자가 보는 달력에서
   갈리므로, UTC 문자열로 적으면 이 테스트가 실행 기계의 시간대에 따라 갈린다. */
function when(y, mo, d, hh, mm, ss) {
  return new Date(y, mo - 1, d, hh, mm, ss || 0, 0);
}

/* 묶기 테스트용 발화 한 건. id 는 그 시각의 epoch 를 고정폭으로 적어
   **사전식 = 시간순**을 지킨다 (앱의 정렬 규약과 같다). */
function turn(author, at, extra) {
  return Object.assign({
    id: 'records/talk/' + String(at.getTime()).padStart(15, '0') + '.json',
    author: author,
    text: author + ' 의 말 ' + at.getHours() + ':' + at.getMinutes(),
    ts: at.toISOString(),
    sender: 'x.host', kind: 'msg', reply_to: null, unknown: false, mine: false
  }, extra || {});
}

function idebooted(options) {
  const opts = Object.assign({}, options || {});
  opts.stored = Object.assign({}, opts.stored || {});
  opts.stored[LAYOUT_KEY] = 'ide';
  return boot(opts);
}

/* 창 안 노드들 중 **머리가 보이는** 것의 수. 0 이면 화면에 발신자가 없다는 뜻이다. */
function visibleHeads(doc) {
  return doc.getElementById('messages').children
    .filter((node) => node.children.length && !node.children[0].hidden).length;
}

await test('⭐ ide 배치: 같은 사람이 이어 말하면 발신자 머리를 한 번만 찍는다', async () => {
  const talk = [
    turn('bc.lee', when(2026, 9, 3, 14, 12)),
    turn('bc.lee', when(2026, 9, 3, 14, 12, 31)),
    turn('kihong', when(2026, 9, 3, 14, 14)),
    turn('기본이름', when(2026, 9, 3, 14, 16), { mine: true })
  ];
  const { doc, chat } = await idebooted({ messages: talk });
  assert.equal(chat.layout(), 'ide');
  assert.equal(layoutAttr(doc), 'ide', '루트 표식이 없다 — CSS 가 안 걸린다');

  const rows = doc.getElementById('messages').children;
  assert.equal(rows.length, 4);
  /* 두 조각: `.msg-head`(발신자·시각) + `.line`(인용·본문·답장·상태). */
  /* 두 조각 + 읽음 카운트 자리 (마지막·절대 위치). */
  assert.deepEqual(rows[0].children.map((c) => String(c.className)),
    ['msg-head', 'line', 'msg-reads']);
  assert.deepEqual(rows[0].children[0].children.map((c) => String(c.className)),
    ['author', 'ts']);
  assert.equal(rows[0].children[0].children[0].textContent, 'bc.lee');

  /* ⭐ 머리 판정은 **모델**에 있다 (구조는 그 값만 읽는다). */
  assert.deepEqual(chat.items().map((m) => m.head), [true, false, true, true]);

  /* ⭐ DOM 에서는 머리를 **지우지 않고 숨긴다.** 지우면 되살릴 때(과거를 불러와
     묶음이 갈릴 때) 노드를 다시 만들어야 하고, 그 순간 rebuiltInView 가 깨진다. */
  assert.equal(rows[0].children[0].hidden, false);
  assert.equal(rows[1].children[0].hidden, true, '묶였는데 머리가 그대로 보인다');
  assert.equal(rows[1].children[0].children[0].textContent, 'bc.lee',
    '머리를 지워 버렸다 (숨기는 것이어야 한다)');

  /* 세로 밀도 이득의 실체 — 묶인 줄은 머리만큼 낮다. */
  console.log('      묶음 첫 줄 ' + rows[0].offsetHeight + 'px · 묶인 줄 ' +
    rows[1].offsetHeight + 'px · 항목 간격 ' + nodeMod.itemGap('ide') + 'px');
  assert.ok(rows[1].offsetHeight < rows[0].offsetHeight,
    '묶여도 높이가 같다 (머리가 자리를 먹고 있다)');

  /* 내것/남의것은 그대로 갈린다 (묶기가 그 표식을 먹지 않는다). */
  assert.equal(isMine(rows[3]), true);
  assert.equal(isMine(rows[1]), false);
  assert.equal(rows[2].getAttribute('data-sender'), String(nodeMod.senderSlot('kihong')));

  assert.equal(chat.stats.rebuiltInView, 0);
  assert.equal(doc.counts.innerHTML, 0);
});

await test('⭐ 시간 컷오프: 같은 사람이라도 벌어지면 머리를 다시 찍는다 (날이 바뀌면 무조건)', async () => {
  const cutoff = timelineMod.GROUP_GAP_MS;
  assert.ok(cutoff > 0, '컷오프가 없다');
  const head = timelineMod.headVisible;

  /* 순수 함수로 먼저 — 경계 안/밖. */
  const base = turn('bc.lee', when(2026, 9, 3, 14, 0));
  const inside = turn('bc.lee', new Date(when(2026, 9, 3, 14, 0).getTime() + cutoff - 1000));
  const outside = turn('bc.lee', new Date(when(2026, 9, 3, 14, 0).getTime() + cutoff + 1000));
  assert.equal(head(inside, base), false, '컷오프 안인데 머리를 다시 찍는다');
  assert.equal(head(outside, base), true, '컷오프를 넘었는데 묶었다');
  /* 시각을 못 읽으면 묶지 않는다 (모르는 것을 '가깝다'로 취급하지 않는다). */
  assert.equal(head(Object.assign({}, inside, { ts: '언제인지 모른다' }), base), true);
  /* 같은 이름이어도 내것/남의것이 갈리면 새 머리다. */
  assert.equal(head(Object.assign({}, inside, { mine: true }), base), true);

  /* ⭐ 날 경계 — 컷오프만으로는 막히지 않는다 (23:59 와 00:01 은 2분 차이다). */
  const lastNight = turn('bc.lee', when(2026, 9, 3, 23, 59));
  const thisMorning = turn('bc.lee', when(2026, 9, 4, 0, 1));
  assert.equal(head(thisMorning, lastNight), true,
    '어제 마지막 말과 오늘 첫 말이 한 묶음이 됐다');

  /* 그리고 실제 화면에서도 같다.
     ⚠️ 판정은 **바로 앞 줄**과 비교한다 — 그래서 화면 확인용 대화는 앞 줄과의
     간격이 뜻대로 되게 따로 세운다 (`outside` 를 그냥 이어 붙이면 그 직전은
     `inside` 라서 2초 차이가 되고, 이 테스트가 컷오프를 재지 못한다). */
  const talk = [
    turn('bc.lee', when(2026, 9, 3, 14, 0)),        /* 대화의 시작 */
    turn('bc.lee', when(2026, 9, 3, 14, 5)),        /* +5분 — 묶인다 */
    turn('bc.lee', when(2026, 9, 3, 14, 30)),       /* +25분 — 컷오프 밖 */
    turn('bc.lee', when(2026, 9, 3, 23, 59)),       /* 한참 뒤 */
    turn('bc.lee', when(2026, 9, 4, 0, 1))          /* +2분이지만 **날이 다르다** */
  ];
  const { doc, chat } = await idebooted({ messages: talk });
  assert.deepEqual(chat.items().map((m) => m.head), [true, false, true, true, true]);
  const rows = doc.getElementById('messages').children;
  assert.deepEqual(rows.map((n) => n.children[0].hidden), [false, true, false, false, false]);
  console.log('      컷오프 ' + (cutoff / 60000) + '분 · 안(' +
    ((cutoff - 1000) / 60000).toFixed(2) + '분) 묶임 · 밖 새 머리 · 날 경계 새 머리');
});

await test('⭐ 묶음 중간에서 창이 시작해도 누가 말했는지 화면에 있다 (떠 있는 머리)', async () => {
  /* manyMessages 는 전부 같은 사람·같은 시각이다 = **묶음 하나**. */
  const { doc, chat } = await idebooted({ messages: manyMessages(200), viewport: 300 });
  await settle();
  const timeline = doc.getElementById('timeline');
  const sticky = doc.getElementById('sticky-head');

  timeline.scrollTop = 4000;
  timeline.dispatch('scroll');
  await settle();

  /* 전제: 창 안에 **머리가 하나도 없다.** 이게 아니면 이 테스트는 아무것도
     증명하지 못한다 (묶기의 유일한 결함이 바로 이 상태다). */
  assert.equal(visibleHeads(doc), 0, '창 안에 머리가 있어 전제가 성립하지 않는다');
  const top = chat.items().find((m) => m.id === chat.keepAnchor());
  assert.equal(top.head, false, '화면 위 항목이 묶음의 첫 줄이다 (전제 실패)');

  /* ⭐ 그래서 떠 있는 머리가 그 자리를 메운다. */
  assert.equal(sticky.hidden, false, '누가 말했는지 화면에 없다 (머리가 잘렸다)');
  assert.equal(doc.getElementById('sticky-author').textContent, '앨리스');
  assert.ok(doc.getElementById('sticky-ts').textContent, '떠 있는 머리에 시각이 없다');
  assert.equal(sticky.getAttribute('data-sender'), String(nodeMod.senderSlot('앨리스')));
  console.log('      창 안 노드 ' + doc.getElementById('messages').children.length +
    '개 · 그 중 머리 보이는 것 ' + visibleHeads(doc) + '개 · 떠 있는 머리 「' +
    doc.getElementById('sticky-author').textContent + '」 (hidden=' + sticky.hidden + ')');

  /* (1) 맨 위로 돌아가 머리가 화면에 있으면 **겹쳐 뜨지 않는다.** */
  timeline.scrollTop = 0;
  timeline.dispatch('scroll');
  await settle();
  assert.equal(visibleHeads(doc), 1, '맨 위인데 머리가 안 보인다 (전제 실패)');
  assert.equal(sticky.hidden, true, '머리가 보이는데 떠 있는 머리까지 떴다 (중복)');

  /* (2) 오버레이라 **높이 측정에 영향이 없다** — 노드를 다시 만들지 않았다. */
  assert.equal(chat.stats.rebuiltInView, 0);
  assert.ok(chat.stats.stickyShown > 0, '떠 있는 머리가 한 번도 안 떴다');

  /* (3) 묶지 않는 배치에서는 아예 뜨지 않는다 (머리가 언제나 붙어 있으므로). */
  const plain = await boot({ messages: manyMessages(200), viewport: 300 });
  await settle();
  const t2 = plain.doc.getElementById('timeline');
  t2.scrollTop = 4000;
  t2.dispatch('scroll');
  await settle();
  assert.equal(plain.doc.getElementById('sticky-head').hidden, true,
    '말풍선 배치에서 떠 있는 머리가 떴다');
});

await test('⭐ 과거를 불러와 묶음이 이어지면 **그 항목 하나만** 다시 잰다', async () => {
  /* 같은 사람(앨리스)이 이어 말한 대화를 과거·현재로 갈라 둔다 — 이어 붙는 순간
     기존 첫 메시지가 머리를 잃는다. 그게 이 배치의 유일한 무효화 경계다. */
  const t0 = when(2026, 9, 3, 14, 0).getTime();
  const past = [0, 1, 2, 3].map((i) => turn('앨리스', new Date(t0 + i * 20000)));
  const now = [4, 5, 6].map((i) => turn('앨리스', new Date(t0 + i * 20000)));

  const { doc, chat } = await idebooted({
    messages: now, past: past, hasMore: true, pageSize: 2, viewport: 300
  });
  await settle();
  const list = doc.getElementById('messages');
  scrollUp(doc);
  await settle();

  const v = chat.virtualizer();
  const firstId = chat.items()[0].id;
  const firstNode = chat.nodes().get(firstId);
  assert.ok(firstNode, '기존 첫 메시지가 창 안에 없다 (전제 실패)');
  assert.equal(chat.items()[0].head, true, '기존 첫 메시지에 머리가 없다 (전제 실패)');
  /* 지금 잰 크기를 전부 떠 둔다 — "무엇이 다시 재졌나"의 기준선이다. */
  const sizesBefore = new Map(v.itemSizeCache);
  const nodesBefore = new Map(chat.nodes());
  const flipsBefore = chat.stats.headChanged;
  const paintsBefore = chat.stats.headRepainted;
  const remeasuredBefore = chat.stats.headRemeasured;
  const createdBefore = chat.stats.created;

  StubIntersectionObserver.current.trigger();   /* 위 끝에 닿았다 = 과거를 잇는다 */
  await settle();

  /* (1) 판정이 뒤집힌 항목은 **하나**다. */
  assert.equal(chat.stats.headChanged - flipsBefore, 1,
    '머리 판정이 뒤집힌 항목이 하나가 아니다');
  assert.equal(chat.stats.headRepainted - paintsBefore, 1);
  assert.equal(chat.items().find((m) => m.id === firstId).head, false,
    '묶음이 이어졌는데 머리가 그대로다');

  /* (2) ⭐ **다시 잰 항목도 하나**다 — 가상화의 크기 캐시(키 = 메시지 id)에서
     값이 달라진 키를 센다. 다른 항목은 창 안에서 다시 재도 값이 그대로다. */
  const changed = [...sizesBefore.keys()]
    .filter((key) => v.itemSizeCache.get(key) !== sizesBefore.get(key));
  console.log('      크기가 달라진 항목: ' + changed.length + '개 · 기존 항목 ' +
    sizesBefore.size + '개 중 · 머리를 잃은 항목 ' + sizesBefore.get(firstId) +
    'px → ' + v.itemSizeCache.get(firstId) + 'px');
  assert.equal(chat.stats.headRemeasured - remeasuredBefore, 1,
    '다시 잰 항목이 하나가 아니다');
  assert.deepEqual(changed, [firstId], '머리를 잃은 항목 말고도 크기가 바뀌었다');
  assert.ok(v.itemSizeCache.get(firstId) < sizesBefore.get(firstId),
    '머리를 잃었는데 높이가 줄지 않았다 (측정이 안 됐다)');

  /* (3) 노드는 **같은 객체 그대로**다 — 다시 만들지 않았다. */
  assert.equal(chat.nodes().get(firstId), firstNode, '머리를 고치려고 노드를 갈았다');
  assert.equal(firstNode.children[0].hidden, true, '그 노드의 머리가 숨지 않았다');
  assert.equal(firstNode.children[0].children[0].textContent, '앨리스',
    '머리를 지워 버렸다 (숨기는 것이어야 한다)');
  for (const [id, node] of nodesBefore) {
    if (chat.nodes().has(id)) {
      assert.equal(chat.nodes().get(id), node, id + ' 가 교체됐다');
    }
  }
  assert.equal(chat.stats.rebuiltInView, 0, '창 안 노드를 다시 만들었다');
  assert.equal(list.removedByReplace, 0, '타임라인을 통째로 비웠다');
  console.log('      새로 만든 노드 ' + (chat.stats.created - createdBefore) +
    '개 (이어 붙은 과거 ' + chat.stats.prepended + '건) · rebuiltInView ' +
    chat.stats.rebuiltInView);
});

await test('⭐ 한 사람이 연달아 수백 건 말해도 DOM 노드 수는 상수에 가깝다', async () => {
  /* 묶음을 가상화 단위로 만들었으면 **여기서 깨진다** — 이 방은 묶음이 하나라
     3건을 보이려고 400건을 그리게 된다. */
  const { doc, chat } = await idebooted({ messages: manyMessages(400), viewport: 300 });
  const list = doc.getElementById('messages');

  /* 전제: 전부 한 묶음이다 (머리를 가진 항목이 1개). */
  const heads = chat.items().filter((m) => m.head).length;
  assert.equal(heads, 1, '한 묶음이 아니다 (전제 실패): 머리 ' + heads + '개');
  assert.equal(chat.items().length, 400);

  console.log('      한 묶음 400건 · DOM 노드 ' + list.children.length + '개 · 전체 높이 ' +
    chat.virtualizer().getTotalSize() + 'px');
  assert.ok(list.children.length < 40,
    'DOM 에 ' + list.children.length + '개가 남았다 (묶음을 항목으로 만든 것과 같다)');

  const timeline = doc.getElementById('timeline');
  const peak = [];
  for (const offset of [4000, 12000, 24000, 500]) {
    timeline.scrollTop = offset;
    timeline.dispatch('scroll');
    await settle();
    peak.push(list.children.length);
  }
  console.log('      스크롤하며 본 DOM 노드 수: ' + peak.join(', '));
  assert.ok(Math.max.apply(null, peak) < 40, '스크롤 중 노드가 쌓였다: ' + peak);
  assert.ok(chat.stats.recycled > 0, '창 밖 노드를 걷어낸 적이 없다 (가상화 아님)');
  assert.equal(chat.stats.rebuiltInView, 0);
  assert.equal(doc.counts.innerHTML, 0);
});

await test('ide 배치에서도 답장 인용·보내는 중·재시도·아웃박스가 산다', async () => {
  const quoted = turn('앨리스', when(2026, 9, 3, 14, 0));
  const reply = turn('밥', when(2026, 9, 3, 14, 1));
  reply.reply_to = quoted.id;
  const { doc, chat, context } = await idebooted({ messages: [quoted, reply] });
  const rows = doc.getElementById('messages').children;
  const line = rows[1].children[1];
  assert.equal(String(line.children[0].className), 'quote');
  assert.ok(line.children[0].textContent.includes('앨리스: '), '인용이 비었다');
  assert.ok(findByClass(rows[1], 'link'), '답장 버튼이 없다');

  /* 낙관적 전송 → 실패 → 재시도가 같은 노드 위에서 돈다 (구조와 무관하다). */
  context.fetch = deferredFetch();
  doc.getElementById('text').value = '보내는 중이 보여야 한다';
  const sending = chat.send();
  await settle();
  const mineRow = doc.getElementById('messages').children[2];
  assert.equal(isMine(mineRow), true, '내 말이 내 것으로 표시되지 않았다');
  /* 내 말은 남의 말 뒤에 오므로 **머리가 붙는다** (다른 사람이다). */
  assert.equal(mineRow.children[0].hidden, false);
  assert.ok(mineRow.textContent.includes('보내는 중'), '보내는 중이 안 보인다');
  await context.fetch.answer({ error: '원격이 죽었다' }, 500);
  await sending;
  assert.ok(mineRow.textContent.includes('보내지 못했다'));
  assert.ok(findByClass(mineRow, 'retry'), '재시도가 없다');

  /* 아웃박스 띠는 배치와 무관하다 (입력창 위, 방 전체에 걸리는 띠다). */
  StubEventSource.current.emit('outbox', {
    room: 'r1', state: 'stuck', pending: 1, detail: '원격이 막혔다'
  });
  assert.equal(doc.getElementById('outbox').hidden, false, '아웃박스 띠가 안 뜬다');
  assert.equal(chat.stats.rebuiltInView, 0);
});

await test('⭐ 배치를 바꾸면 읽던 자리를 남기고 **새로고침한다** (즉시 전환하지 않는다)', async () => {
  const { doc, chat, context } = await boot({
    messages: manyMessages(120), viewport: 300
  });
  await settle();
  const timeline = doc.getElementById('timeline');
  timeline.scrollTop = 2500;
  timeline.dispatch('scroll');
  await settle();
  const nodesBefore = doc.getElementById('messages').children.slice();
  const createdBefore = chat.stats.created;

  assert.equal(chat.setLayout('log'), true, '전환이 일어나지 않았다');

  /* (1) 저장됐다 — 새로고침 뒤에도 그 배치로 뜬다. */
  assert.equal(context.localStorage.getItem(LAYOUT_KEY), 'log');
  /* (2) 읽던 자리를 **앵커 메시지 id** 로 남겼다 (픽셀이 아니다). */
  const saved = JSON.parse(context.localStorage.getItem(ANCHOR_KEY));
  assert.equal(saved.room, 'r1');
  assert.ok(saved.id, '앵커 id 가 없다');
  /* (3) 새로고침을 불렀다. */
  assert.equal(context.win.reloads, 1, '새로고침하지 않았다');
  /* (4) ⭐ **즉시 전환하지 않았다** — 이 페이지의 노드는 하나도 손대지 않았다.
     (즉시 전환하면 높이 캐시가 낡고 창 안 노드를 다시 만들어야 한다.) */
  assert.equal(chat.stats.created, createdBefore, '전환하려고 노드를 만들었다');
  assert.equal(chat.stats.rebuiltInView, 0);
  assert.deepEqual(doc.getElementById('messages').children, nodesBefore);
  assert.ok(doc.getElementById('status').textContent.indexOf('다시 불러온다') >= 0,
    '새로고침한다는 사실을 화면에 말하지 않았다');
});

await test('타임라인이 죽어 있어도 배치 전환은 진행된다 (앵커만 없다)', async () => {
  const { chat, context } = await boot({
    sabotage: (doc2) => breakWiring(doc2, 'timeline', '타임라인 배선 실패')
  });
  assert.equal(chat.setLayout('log'), true);
  assert.equal(context.win.reloads, 1, '전환이 막혔다');
  assert.equal(context.localStorage.getItem(ANCHOR_KEY), null);
});

/* ------------------------------------------------------------ 색 테마 */

/*
 * ⭐ 여기서 지키는 것은 색이 예쁜가가 아니라 **테마 전환이 화면을 건드리지
 * 않는가**다. 색은 CSS 토큰으로 흐르므로 DOM 을 손댈 이유가 없다 — 노드를 다시
 * 만드는 구현이면 가상 스크롤의 측정값이 낡고 읽던 자리가 튄다. 그래서 노드
 * 동일성(===)·`rebuiltInView`·`scrollTop`·DOM 조작 횟수를 전부 수치로 본다.
 */

const THEME_KEY = 'gitwire-chat.theme';
const THEME_ATTR = 'data-chat-theme';

await test('첫 방문 기본값은 `기본` — 루트에 아무 속성도 찍지 않는다', async () => {
  const { doc, chat } = await boot();
  assert.equal(themeAttr(doc), null, '고르지도 않았는데 테마가 찍혔다');
  assert.equal(chat.theme(), 'default');
  /* 고르지 않았으면 저장도 하지 않는다 (다음 배포에서 기본값을 바꿀 여지를 남긴다). */
  assert.equal(chat.modules.theme.current(), 'default');
});

await test('테마를 고르면 즉시 루트에 찍히고 이 기기에 남는다', async () => {
  const { doc, chat, context } = await boot();
  for (const id of ['log', 'ide', 'tty', 'tui']) {
    chat.setTheme(id);
    assert.equal(themeAttr(doc), id, id + ' 가 찍히지 않았다');
    assert.equal(context.localStorage.getItem(THEME_KEY), id);
  }
  /* 기본으로 되돌리면 **속성을 지운다** = 시스템 라이트/다크 추종으로 복귀. */
  chat.setTheme('default');
  assert.equal(themeAttr(doc), null, '기본인데 속성이 남았다 (시스템 추종이 깨진다)');
  assert.equal(context.localStorage.getItem(THEME_KEY), 'default');
});

await test('고르는 UI(select)가 실제로 테마를 바꾼다', async () => {
  const { doc, chat } = await boot();
  const bar = doc.getElementById('theme-bar');
  assert.equal(bar.hidden, true, '설정 칸이 처음부터 펼쳐져 있다');

  doc.getElementById('toggle-theme').dispatch('click');
  assert.equal(bar.hidden, false, '◐ 를 눌렀는데 펼쳐지지 않았다');

  const select = doc.getElementById('theme-select');
  select.value = 'tui';
  select.dispatch('change');
  assert.equal(themeAttr(doc), 'tui');
  assert.equal(chat.theme(), 'tui');
  /* 무엇을 고른 상태인지 칸에 남는다 (다시 열었을 때 현재 값이 보여야 한다). */
  assert.equal(select.value, 'tui');
  assert.ok(doc.getElementById('theme-note').textContent.length > 0, '설명이 비었다');

  doc.getElementById('toggle-theme').dispatch('click');
  assert.equal(bar.hidden, true, '다시 누르면 접혀야 한다');
});

await test('⭐ 재기동해도 고른 테마가 유지된다 (첫 페인트 전에 찍힌다)', async () => {
  const stored = {};
  stored[THEME_KEY] = 'tty';
  const { doc, chat } = await boot({ stored: stored });
  assert.equal(themeAttr(doc), 'tty', '저장된 테마가 되살아나지 않았다');
  assert.equal(chat.theme(), 'tty');
  assert.equal(doc.getElementById('theme-select').value, 'tty');
});

await test('모르는 저장값은 조용히 기본으로 떨어진다 (검은 화면 사고 방지)', async () => {
  const stored = {};
  stored[THEME_KEY] = 'solarized-없는테마';
  const { doc, chat } = await boot({ stored: stored });
  assert.equal(themeAttr(doc), null);
  assert.equal(chat.theme(), 'default');
});

await test('⭐ 테마를 바꿔도 메시지 노드를 다시 만들지 않는다 (rebuiltInView 0)', async () => {
  const { doc, chat } = await boot({ messages: manyMessages(200), viewport: 300 });
  const list = doc.getElementById('messages');
  await settle();

  /* 위로 조금 올라가 "읽던 자리"를 만든다 (맨 아래면 보존됐는지 알 수 없다). */
  const timeline = doc.getElementById('timeline');
  timeline.scrollTop = 3000;
  timeline.dispatch('scroll');
  await settle();

  const nodesBefore = list.children.slice();
  const idsBefore = nodesBefore.map((n) => n.dataset.id);
  const snapshot = {
    created: chat.stats.created,
    appended: chat.stats.appended,
    recycled: chat.stats.recycled,
    cleared: chat.stats.cleared,
    measured: chat.stats.measured,
    createElement: doc.counts.createElement,
    appendChild: doc.counts.appendChild,
    removeChild: doc.counts.removeChild,
    replaceChildren: doc.counts.replaceChildren
  };
  const topBefore = timeline.scrollTop;
  const heightBefore = timeline.scrollHeight;

  for (const id of ['log', 'ide', 'tty', 'tui', 'default']) { chat.setTheme(id); }
  await settle();

  console.log('      테마 5번 전환 · DOM 노드 ' + list.children.length +
    '개 · created ' + snapshot.created + '→' + chat.stats.created +
    ' · rebuiltInView ' + chat.stats.rebuiltInView +
    ' · scrollTop ' + topBefore + '→' + timeline.scrollTop);

  /* (1) 노드를 하나도 만들지 않았다 — 색은 CSS 로 흐른다. */
  assert.equal(chat.stats.created, snapshot.created, '메시지 노드를 다시 만들었다');
  assert.equal(chat.stats.appended, snapshot.appended);
  assert.equal(chat.stats.recycled, snapshot.recycled, '창 밖으로 걷어낸 것이 생겼다');
  assert.equal(chat.stats.cleared, snapshot.cleared, '타임라인을 비웠다');
  assert.equal(chat.stats.rebuiltInView, 0);

  /* (2) DOM 조작 자체가 0 이다 (테마는 루트 속성 한 줄이 전부다). */
  assert.equal(doc.counts.createElement, snapshot.createElement);
  assert.equal(doc.counts.appendChild, snapshot.appendChild);
  assert.equal(doc.counts.removeChild, snapshot.removeChild);
  assert.equal(doc.counts.replaceChildren, snapshot.replaceChildren);
  assert.equal(doc.counts.innerHTML, 0);

  /* (3) 같은 객체가 같은 자리에 그대로 있다. */
  assert.equal(list.children.length, nodesBefore.length);
  for (let i = 0; i < nodesBefore.length; i++) {
    assert.equal(list.children[i], nodesBefore[i], i + '번 노드가 교체됐다');
  }
  assert.deepEqual(list.children.map((n) => n.dataset.id), idsBefore);

  /* (4) 높이 측정도, 읽던 자리도 그대로다 — 색만 바꾸면 높이는 변하지 않는다.
     (폰트·행간·여백을 함께 건드리면 여기가 깨진다. 그래서 이번 범위는 색뿐이다.) */
  assert.equal(chat.stats.measured, snapshot.measured, '높이를 다시 쟀다');
  assert.equal(timeline.scrollHeight, heightBefore, '전체 높이가 변했다');
  assert.equal(timeline.scrollTop, topBefore, '테마를 바꿨는데 읽던 자리를 잃었다');
});

await test('⭐ 테마 모듈이 못 서도 나머지 화면은 산다 (배선 실패로 실증)', async () => {
  const { doc, chat, consoleErrors } = await boot({
    sabotage: (doc2) => breakWiring(doc2, 'toggle-theme', '테마 배선 실패')
  });
  /* 대화·방 목록은 그대로 뜬다. */
  assert.equal(doc.getElementById('messages').children.length, 3);
  assert.ok(doc.getElementById('rooms').children.length > 0);
  /* 색은 기본으로 간다 (속성이 안 찍힌다) — 조용히는 아니고 드러난다. */
  assert.equal(themeAttr(doc), null);
  assert.equal(chat.failures().length, 1);
  assert.equal(chat.failures()[0].unit, '테마');
  assert.ok(doc.getElementById('status').textContent.indexOf('초기화 실패') >= 0);
  assert.ok(consoleErrors.join(' ').indexOf('테마') >= 0);
});

await test('테마가 죽어도 저장된 값은 첫 페인트 조각이 살려 둔다 (계약 일치)', () => {
  /* 모듈과 템플릿이 **같은 키·같은 속성**을 써야 이 폴백이 성립한다.
     (파이썬 쪽 test_theme.py 가 문자열 일치를 다시 확인한다.) */
  const mod = fs.readFileSync(path.join(STATIC, 'js', 'theme.js'), 'utf8');
  assert.ok(mod.includes("'" + THEME_KEY + "'"), 'theme.js 의 저장 키가 다르다');
  assert.ok(mod.includes("'" + THEME_ATTR + "'"), 'theme.js 의 루트 속성이 다르다');
  assert.ok(indexHtml.includes("'" + THEME_KEY + "'"), 'index.html 의 저장 키가 다르다');
  assert.ok(indexHtml.includes("'" + THEME_ATTR + "'"), 'index.html 의 루트 속성이 다르다');
});

/* ---------------------------------------------------------- 갱신 알림 */

/* 확인 응답 대역. `behind` 로 "새 것이 있다/없다"를 만든다. */
function checkRoute(payload) {
  return { '/api/update/check': payload };
}

await test('⭐ 켤 때 원격을 보지 않는다 (몰래 나가는 네트워크를 만들지 않는다)', async () => {
  const { fetchStub, doc } = await boot();
  const went = fetchStub.calls.filter((c) => c.path.indexOf('/api/update') === 0);
  assert.equal(went.length, 0, '누르지 않았는데 원격을 봤다: ' + JSON.stringify(went));
  /* 띠는 있고, 안내·명령은 아직 아무것도 안 쓰여 있다. */
  assert.ok(doc.getElementById('check-update'));
  assert.equal(doc.getElementById('update-note').textContent, '');
  assert.equal(doc.getElementById('update-cmd').textContent, '');
  /* 처음 상태는 템플릿이 정한다 (stub 은 HTML 을 파싱하지 않으므로 원문을 본다). */
  assert.ok(/id="update-note"[^>]*hidden/.test(indexHtml), '안내가 처음부터 보인다');
  assert.ok(/id="update-cmd"[^>]*hidden/.test(indexHtml), '명령이 처음부터 보인다');
  assert.ok(/id="copy-update-cmd"[^>]*hidden/.test(indexHtml));
});

await test('누르면 확인하고, 새 것이 있으면 칠 명령을 보여준다', async () => {
  const { doc, chat, fetchStub } = await boot({
    routes: checkRoute({
      behind: true, installed: 'a'.repeat(40), remote: 'b'.repeat(40),
      command: 'python -m gitwire_chat update'
    })
  });
  await chat.checkUpdate();
  const went = fetchStub.calls.filter((c) => c.path === '/api/update/check');
  assert.equal(went.length, 1);
  assert.equal(went[0].init.method, 'POST');
  const note = doc.getElementById('update-note');
  const cmd = doc.getElementById('update-cmd');
  assert.equal(note.hidden, false);
  assert.ok(note.textContent.indexOf('새 버전이 있다') >= 0, note.textContent);
  assert.equal(cmd.hidden, false);
  assert.equal(cmd.textContent, 'python -m gitwire_chat update');
  assert.equal(doc.getElementById('copy-update-cmd').hidden, false);
});

await test('최신이면 명령을 보여주지 않는다 (칠 것이 없다)', async () => {
  const { doc, chat } = await boot({
    routes: checkRoute({ behind: false, installed: 'a'.repeat(40), remote: 'a'.repeat(40) })
  });
  await chat.checkUpdate();
  assert.ok(doc.getElementById('update-note').textContent.indexOf('최신이다') >= 0);
  assert.equal(doc.getElementById('update-cmd').hidden, true);
});

await test('⭐ 앱이 최신이어도 라이브러리가 밀렸으면 그렇게 말한다', async () => {
  /* 조용한 실패 방지 — 이 문장이 없으면 화면은 같은 커밋 두 개를 나란히
     보여주며 "새 버전이 있다"고 말한다(앱 커밋은 그대로다). 무엇이 왜
     갱신되는지 사용자가 알 수 없다. */
  const same = 'a'.repeat(40);
  const { doc, chat } = await boot({
    routes: checkRoute({
      behind: true, self_behind: false, installed: same, remote: same,
      command: 'python -m gitwire_chat update',
      deps: [{
        name: 'gitwire', behind: true,
        installed: 'c'.repeat(40), remote: 'd'.repeat(40)
      }]
    })
  });
  await chat.checkUpdate();
  const note = doc.getElementById('update-note');
  assert.equal(note.hidden, false);
  assert.ok(note.textContent.indexOf('라이브러리가 밀렸다') >= 0, note.textContent);
  assert.ok(note.textContent.indexOf('gitwire') >= 0, note.textContent);
  assert.ok(note.textContent.indexOf('cccccccccccc') >= 0, '의존 커밋을 안 보여준다');
  /* 확인 단계에서도 무엇이 올라가는지 그대로 말해야 한다. */
  chat.askUpdate();
  assert.ok(note.textContent.indexOf('gitwire') >= 0, note.textContent);
  assert.equal(doc.getElementById('update-cmd').hidden, false);
});

await test('앱이 밀렸으면 의존도 함께 한 줄에 실린다', async () => {
  const { doc, chat } = await boot({
    routes: checkRoute({
      behind: true, self_behind: true,
      installed: 'a'.repeat(40), remote: 'b'.repeat(40),
      command: 'python -m gitwire_chat update',
      deps: [
        { name: 'gitwire', behind: true, installed: 'c'.repeat(40), remote: 'd'.repeat(40) },
        { name: 'quiet', behind: false, installed: 'e'.repeat(40), remote: 'e'.repeat(40) }
      ]
    })
  });
  await chat.checkUpdate();
  const note = doc.getElementById('update-note');
  assert.ok(note.textContent.indexOf('새 버전이 있다') >= 0, note.textContent);
  assert.ok(note.textContent.indexOf('gitwire') >= 0, note.textContent);
  assert.ok(note.textContent.indexOf('quiet') < 0, '최신인 의존까지 늘어놓는다');
});

await test('⭐ 확인이 실패하면 사유와 힌트가 화면에 드러난다', async () => {
  const { doc, chat } = await boot({
    routes: checkRoute({
      __http: 400, error: 'git 설치본이 아니다', hint: 'pip install … 로 한 번 설치한다'
    })
  });
  await chat.checkUpdate();
  const note = doc.getElementById('update-note');
  assert.equal(note.hidden, false);
  assert.ok(note.className.indexOf('bad') >= 0, note.className);
  assert.ok(note.textContent.indexOf('git 설치본이 아니다') >= 0, note.textContent);
  assert.ok(note.textContent.indexOf('pip install') >= 0, '서버 힌트를 버렸다');
});

await test('복사할 수 없는 브라우저면 직접 복사하라고 말한다', async () => {
  const { doc, chat, context } = await boot({
    routes: checkRoute({
      behind: true, installed: 'a'.repeat(40), remote: 'b'.repeat(40),
      command: 'python -m gitwire_chat update'
    })
  });
  await chat.checkUpdate();
  context.win.navigator = {};                    /* clipboard 없음 */
  await chat.copyUpdateCommand();
  const note = doc.getElementById('update-note');
  assert.ok(note.textContent.indexOf('직접 골라 복사') >= 0, note.textContent);
  /* 명령은 그대로 남아 있어야 한다 (직접 복사할 대상이니까). */
  assert.equal(doc.getElementById('update-cmd').hidden, false);
});

/* ------------------------------------------- 갱신 실행 (누르면 끝까지) */

/* 갱신 시나리오 한 벌. 서버 응답을 **테스트가 쥔다** — 갱신 중에는 서버가 죽고
   다시 뜨므로, 응답이 도중에 바뀌는 것 자체가 검증 대상이다. */
function updateRoutes(state) {
  return {
    '/api/update/check': () => state.check,
    '/api/update/run': () => state.run,
    '/api/version': () => state.version
  };
}

function behind(extra) {
  return Object.assign({
    behind: true,
    installed: 'a'.repeat(40),
    remote: 'b'.repeat(40),
    command: 'python -m gitwire_chat update',
    compare: 'https://example.invalid/compare/aaa...bbb'
  }, extra || {});
}

/* 서버가 "띄웠다"고 답한 모양 (202). `serving.pid` 는 **지금** 서빙 중인
   프로세스다 — 화면은 이 값이 달라지는 것으로 새 서버를 가른다. */
function started(pid) {
  return {
    __http: 202,
    started: true,
    run: { log: 'C:/chats/update-run.log', pid: 4242, started_at: 1 },
    check: behind(),
    serving: { pid: pid || 111, version: '0.2.0', asset_stamp: 'abc123' }
  };
}

/* 갱신을 시작시킨 직후까지 진행한다 (확인 → 지금 갱신 → 갱신한다). */
async function upToRunning(state) {
  const ctx = await boot({ routes: updateRoutes(state) });
  await ctx.chat.checkUpdate();
  ctx.chat.askUpdate();
  await ctx.chat.runUpdate();
  return ctx;
}

await test('처음에는 갱신 버튼도 비교 링크도 숨어 있다 (템플릿이 정한다)', async () => {
  const { doc, fetchStub } = await boot();
  for (const id of ['run-update', 'confirm-update', 'cancel-update', 'update-compare']) {
    assert.ok(new RegExp('id="' + id + '"[^>]*hidden').test(indexHtml),
      id + ' 이 처음부터 보인다');
    assert.equal(doc.getElementById(id).textContent, '');
  }
  const went = fetchStub.calls.filter((c) => c.path.indexOf('/api/update') === 0);
  assert.equal(went.length, 0, '누르지 않았는데 나갔다: ' + JSON.stringify(went));
});

await test('새 것이 있으면 「지금 갱신」과 비교 링크가 뜬다 (확인은 아직 아무것도 안 한다)', async () => {
  const state = { check: behind(), run: started(111), version: { pid: 111 } };
  const { doc, chat, fetchStub } = await boot({ routes: updateRoutes(state) });
  await chat.checkUpdate();
  assert.equal(chat.updatePhase(), 'asked');
  assert.equal(doc.getElementById('run-update').hidden, false);
  assert.equal(doc.getElementById('confirm-update').hidden, true);
  const link = doc.getElementById('update-compare');
  assert.equal(link.hidden, false);
  assert.equal(link.href, 'https://example.invalid/compare/aaa...bbb');
  /* 칠 명령은 그대로 남아 있다 — CLI 가 여전히 정본이다. */
  assert.equal(doc.getElementById('update-cmd').textContent, 'python -m gitwire_chat update');
  assert.equal(fetchStub.calls.filter((c) => c.path === '/api/update/run').length, 0);
});

await test('「지금 갱신」은 무엇이 바뀌는지 보여주고 **확인을 받는다** (아직 안 나간다)', async () => {
  const state = { check: behind(), run: started(111), version: { pid: 111 } };
  const { doc, chat, fetchStub } = await boot({ routes: updateRoutes(state) });
  const runs = () => fetchStub.calls.filter((c) => c.path === '/api/update/run').length;
  await chat.checkUpdate();
  chat.askUpdate();
  assert.equal(chat.updatePhase(), 'confirm');
  const note = doc.getElementById('update-note');
  assert.ok(note.textContent.indexOf('정말 갱신한다') >= 0, note.textContent);
  assert.ok(note.textContent.indexOf('aaaaaaaaaaaa') >= 0, '무엇이 바뀌는지 안 보여줬다');
  assert.equal(doc.getElementById('confirm-update').hidden, false);
  assert.equal(doc.getElementById('cancel-update').hidden, false);
  assert.equal(doc.getElementById('run-update').hidden, true);
  assert.equal(runs(), 0, '확인 전에 갱신이 나갔다');

  /* 취소하면 원래대로. 아무것도 시작되지 않았다. */
  chat.cancelUpdate();
  assert.equal(chat.updatePhase(), 'asked');
  assert.equal(doc.getElementById('run-update').hidden, false);
  assert.equal(doc.getElementById('confirm-update').hidden, true);
  assert.equal(runs(), 0);
});

await test('⭐ 확인하면 갱신을 시작시키고 화면 표식 헤더를 붙여 보낸다', async () => {
  const state = { check: behind(), run: started(111), version: { pid: 111 } };
  const { doc, chat, fetchStub, context } = await upToRunning(state);
  const went = fetchStub.calls.filter((c) => c.path === '/api/update/run');
  assert.equal(went.length, 1);
  assert.equal(went[0].init.method, 'POST');
  /* ⭐ 이 헤더가 드라이브바이를 막는 그 헤더다 (서버 쪽은 csrf.py). */
  assert.equal(went[0].init.headers[updateMod.GUARD_HEADER], updateMod.GUARD_VALUE);
  assert.equal(chat.updatePhase(), 'running');
  const note = doc.getElementById('update-note');
  assert.ok(note.textContent.indexOf('갱신 중') >= 0, note.textContent);
  assert.equal(note.className.indexOf('bad'), -1);
  /* 버튼은 전부 사라지고, 새 서버를 물어볼 타이머가 걸린다. */
  assert.equal(doc.getElementById('run-update').hidden, true);
  assert.equal(doc.getElementById('confirm-update').hidden, true);
  assert.ok(context.win.pendingTimers().indexOf(updateMod.POLL_MS) >= 0,
    '기다릴 타이머를 걸지 않았다: ' + JSON.stringify(context.win.pendingTimers()));
});

await test('⭐ 서버를 잃는 구간을 버틴다 — 기다렸다가 새 버전이 답하면 새 화면으로 간다', async () => {
  const state = { check: behind(), run: started(111), version: { pid: 111 } };
  const { doc, chat, context } = await upToRunning(state);
  const note = doc.getElementById('update-note');

  /* ① 아직 옛 서버가 답한다 — 도착으로 착각하지 않는다. */
  await chat.pollUpdate();
  assert.equal(chat.updatePhase(), 'running');
  assert.ok(note.textContent.indexOf('옛 서버') >= 0, note.textContent);
  assert.equal(context.win.reloads, 0);

  /* ② 서버가 죽었다 — 요청이 전부 실패한다. 오류로 띄우고 끝내지 않는다. */
  state.version = { __down: true };
  await chat.pollUpdate();
  assert.equal(chat.updatePhase(), 'running');
  assert.ok(note.textContent.indexOf('기다린다') >= 0, note.textContent);
  assert.equal(note.className.indexOf('bad'), -1, '기다리는 구간을 오류로 띄웠다');
  assert.equal(context.win.reloads, 0);

  /* ③ 새 서버가 떴다 (pid 가 달라졌다) → 알아서 새 화면으로. */
  state.version = { pid: 222, version: '0.2.0', asset_stamp: 'zzz' };
  await chat.pollUpdate();
  assert.equal(chat.updatePhase(), 'done');
  assert.equal(context.win.reloads, 1, '새 버전이 떴는데 화면이 그대로다');
});

await test('⭐ 안 돌아오면 포기하고 칠 명령과 기록 위치를 알려준다 (무한 대기 금지)', async () => {
  const state = { check: behind(), run: started(111), version: { __down: true } };
  const { doc, chat, context } = await upToRunning(state);
  for (let i = 0; i < updateMod.GIVE_UP_TRIES; i += 1) { await chat.pollUpdate(); }

  assert.equal(chat.updatePhase(), 'stuck');
  const note = doc.getElementById('update-note');
  assert.ok(note.className.indexOf('bad') >= 0, note.className);
  assert.ok(note.textContent.indexOf('그만둔다') >= 0, note.textContent);
  assert.ok(note.textContent.indexOf('update-run.log') >= 0, '어디를 보라고 안 말했다');
  assert.equal(doc.getElementById('update-cmd').textContent, 'python -m gitwire_chat update');
  assert.equal(doc.getElementById('update-cmd').hidden, false);
  assert.equal(context.win.reloads, 0);

  /* 남아 있던 타이머가 터져도 다시 기다리기 시작하지 않는다. */
  context.win.runTimers(updateMod.POLL_MS);
  assert.equal(chat.updatePhase(), 'stuck');
  assert.equal(context.win.reloads, 0);
});

await test('⭐ 두 번 눌러도 한 번만 나간다 · 서버의 "이미 돌고 있다"도 드러낸다', async () => {
  const state = { check: behind(), run: started(111), version: { pid: 111 } };
  const { chat, fetchStub } = await boot({ routes: updateRoutes(state) });
  const runs = () => fetchStub.calls.filter((c) => c.path === '/api/update/run').length;
  await chat.checkUpdate();
  chat.askUpdate();
  const first = chat.runUpdate();
  chat.runUpdate();                       /* 연타 — 응답도 오기 전에 한 번 더 */
  await first;
  assert.equal(runs(), 1, '연타가 두 번 나갔다');
  /* 갱신이 도는 중에는 눌러도 아무 일이 없다. */
  chat.askUpdate();
  await chat.runUpdate();
  assert.equal(runs(), 1);
  assert.equal(chat.updatePhase(), 'running');

  /* 다른 탭이 먼저 시작했으면 서버가 409 로 거절한다 — 그 사유를 드러낸다. */
  const busy = {
    check: behind(),
    run: {
      __http: 409, code: 'busy',
      error: '이미 갱신이 돌고 있다 — 한 번에 하나만 돈다',
      hint: '5초 전에 시작했다. 진행 상황: C:/chats/update-run.log'
    }
  };
  const other = await boot({ routes: updateRoutes(busy) });
  await other.chat.checkUpdate();
  other.chat.askUpdate();
  await other.chat.runUpdate();
  const note = other.doc.getElementById('update-note');
  assert.ok(note.className.indexOf('bad') >= 0, note.className);
  assert.ok(note.textContent.indexOf('이미 갱신이 돌고 있다') >= 0, note.textContent);
  assert.ok(note.textContent.indexOf('update-run.log') >= 0, '서버 힌트를 버렸다');
  /* 다시 시도할 길이 남아 있어야 한다. */
  assert.equal(other.chat.updatePhase(), 'asked');
  assert.equal(other.doc.getElementById('run-update').hidden, false);
});

await test('바뀔 게 없으면 아무것도 하지 않고 그렇게 말한다', async () => {
  /* 확인한 뒤 누군가 이미 갱신해 버린 경우 — 서버가 "띄우지 않았다"고 답한다. */
  const state = {
    check: behind(),
    run: {
      started: false, code: 'current',
      check: { behind: false, installed: 'b'.repeat(40) }
    },
    version: { pid: 111 }
  };
  const { doc, chat, context } = await upToRunning(state);
  assert.equal(chat.updatePhase(), 'idle');
  const note = doc.getElementById('update-note');
  assert.ok(note.textContent.indexOf('최신이다') >= 0, note.textContent);
  assert.equal(doc.getElementById('update-cmd').hidden, true);
  assert.equal(doc.getElementById('run-update').hidden, true);
  assert.equal(context.win.pendingTimers().indexOf(updateMod.POLL_MS), -1,
    '기다릴 것이 없는데 기다린다');
});

await test('⭐ 서버가 거절하면 사유와 힌트가 화면에 드러난다 (조용한 실패 금지)', async () => {
  const state = {
    check: behind(),
    run: {
      __http: 403, code: 'forbidden',
      error: '우리 화면이 붙이는 요청 헤더가 없다 (X-Gitwire-Chat: update).',
      hint: '터미널에서 갱신하려면: python -m gitwire_chat update'
    },
    version: { pid: 111 }
  };
  const { doc, chat, context } = await upToRunning(state);
  const note = doc.getElementById('update-note');
  assert.ok(note.className.indexOf('bad') >= 0, note.className);
  assert.ok(note.textContent.indexOf('시작하지 못했다') >= 0, note.textContent);
  assert.ok(note.textContent.indexOf('X-Gitwire-Chat') >= 0, '서버 사유를 버렸다');
  assert.ok(note.textContent.indexOf('python -m gitwire_chat update') >= 0, '서버 힌트를 버렸다');
  assert.equal(context.win.pendingTimers().indexOf(updateMod.POLL_MS), -1,
    '시작도 못 했는데 기다린다');
  assert.equal(doc.getElementById('run-update').hidden, false, '다시 시도할 길이 없다');
});

await test('갱신 버튼 배선이 터져도 나머지 화면은 산다', async () => {
  const { doc, chat } = await boot({
    sabotage: (doc) => breakWiring(doc, 'run-update', '갱신 버튼 배선 실패')
  });
  assert.equal(doc.getElementById('messages').children.length, 3);
  assert.deepEqual(chat.failures().map((f) => f.unit), ['갱신 알림']);
  assert.ok(doc.getElementById('status').textContent.indexOf('초기화 실패') >= 0);
});

await test('갱신 알림이 못 서도 나머지 화면은 산다 (배선 실패로 실증)', async () => {
  const { doc, chat } = await boot({
    sabotage: (doc) => breakWiring(doc, 'check-update', '확인 버튼 배선 실패')
  });
  assert.equal(doc.getElementById('messages').children.length, 3);
  assert.ok(doc.getElementById('rooms').children.length > 0);
  const units = chat.failures().map((f) => f.unit);
  assert.deepEqual(units, ['갱신 알림']);
  assert.ok(doc.getElementById('status').textContent.indexOf('초기화 실패') >= 0);
});

/* ------------------------------------------------------- 읽음 표시 */

await test('⭐ 카운트는 커서에서 파생된다 — 저장값이 아니다 (공식 단위 검증)', () => {
  const m = { id: 'records/20260903/m5.json', sender: 'a.host' };
  /* 참가자 3명: 작성자(a) · 아직 안 읽은 b · 이미 읽은 c */
  const list = [
    who('a@x.io', 'records/20260903/m9.json', ['a.host']),
    who('b@x.io', 'records/20260903/m1.json', ['b.host']),
    who('c@x.io', 'records/20260903/m9.json', ['c.host'])
  ];
  assert.equal(readsMod.countUnread(list, m), 1);
  /* 작성자는 세지 않는다 — 그의 커서가 뒤에 있어도 (`p ≠ A`). */
  list[0].cursor = '';
  assert.equal(readsMod.countUnread(list, m), 1);
  /* 커서가 아예 없는 사람(설치만 하고 안 켠 **유령**)은 **그냥 센다.** */
  list.push(who('ghost@x.io', '', ['g.host']));
  assert.equal(readsMod.countUnread(list, m), 2);
  /* 참가자 집합이 비어 있으면 0 (분모를 모르면 아무것도 주장하지 않는다). */
  assert.equal(readsMod.countUnread([], m), 0);
});

await test('⭐ 커서 하나가 여러 칸 전진하면 **아래 메시지 전부**의 카운트가 줄어든다', () => {
  /* 저장값이면 메시지마다 갱신해야 하므로 이 성질이 성립하지 않는다. */
  const list = [
    who('me@x.io', 'records/20260903/m9.json', ['me.host']),
    who('b@x.io', 'records/20260903/m1.json', ['b.host'])
  ];
  const msgs = [2, 3, 4, 5].map((n) => (
    { id: 'records/20260903/m' + n + '.json', sender: 'a.host' }
  ));
  assert.deepEqual(msgs.map((m) => readsMod.countUnread(list, m)), [1, 1, 1, 1]);
  /* b 가 m4 까지 읽었다 — **한 번의 커서 이동**이다. */
  list[1].cursor = 'records/20260903/m4.json';
  assert.deepEqual(msgs.map((m) => readsMod.countUnread(list, m)), [0, 0, 0, 1]);
});

await test('⭐ 카운트가 바뀌어도 노드를 다시 만들지 않는다 (rebuiltInView 0)', async () => {
  const list = [who('me@x.io', '', ['me.host']), who('b@x.io', '', ['b.host'])];
  const { doc, chat } = await boot({ reads: readsOf(list) });
  const before = chat.nodes().get(msg(2).id);
  const created = doc.counts.createElement;

  assert.equal(readCount(before), '2', '처음 카운트가 그려지지 않았다');

  /* 남이 읽었다 — 서버가 미는 갱신 (SSE `reads`). */
  chat.bus.emit('reads:state', {
    roomId: 'r1',
    state: readsOf([
      who('me@x.io', '', ['me.host']),
      who('b@x.io', msg(3).id, ['b.host'])
    ])
  });

  const after = chat.nodes().get(msg(2).id);
  assert.equal(after, before, '노드를 다시 만들었다 (같은 객체가 아니다)');
  assert.equal(chat.stats.rebuiltInView, 0);
  assert.equal(readCount(after), '1', '카운트가 줄어들지 않았다');
  assert.ok(chat.stats.readsPainted > 0, '덧입힌 흔적이 없다');
  assert.equal(doc.counts.innerHTML, 0);
  assert.equal(doc.counts.createElement, created, '노드를 새로 만들었다');
});

await test('다 읽으면 숫자가 사라진다 (0 을 그리지 않는다)', async () => {
  const list = [who('me@x.io', '', ['me.host']), who('b@x.io', '', ['b.host'])];
  const { chat } = await boot({ reads: readsOf(list) });
  const node = chat.nodes().get(msg(2).id);
  assert.equal(readCount(node), '2');

  chat.bus.emit('reads:state', {
    roomId: 'r1',
    state: readsOf([
      who('me@x.io', msg(3).id, ['me.host']),
      who('b@x.io', msg(3).id, ['b.host'])
    ])
  });
  assert.equal(readCount(node), '', '다 읽었는데 숫자가 남아 있다');
  assert.equal(node.readsSlot.hidden, true);
});

await test('⭐ 창에 들어온 최대 메시지를 디바운스해서 알린다 (스크롤마다 보내지 않는다)', async () => {
  const { chat, context, fetchStub } = await boot();
  const posts = () => fetchStub.calls.filter(
    (c) => c.path.indexOf('/reads') >= 0 && c.init && c.init.method === 'POST'
  );
  /* 그리는 동안 이미 창 안에 있었다 — 그런데 **아직 나가지 않았다.** */
  assert.equal(posts().length, 0, '확정 신호 없이 보냈다');
  assert.ok(chat.stats.seenEmitted > 0, '창 내용을 알리지 않았다');

  /* 디바운스 타이머가 만료되면 한 번 나간다 (여러 번 알렸어도 한 번). */
  const fired = context.win.runTimers(readsMod.MARK_DEBOUNCE_MS);
  assert.ok(fired > 0, '디바운스 타이머가 없다');
  await Promise.resolve();
  assert.equal(posts().length, 1, posts().map((c) => c.path).join(' · '));
  assert.equal(JSON.parse(posts()[0].init.body).cursor, msg(3).id,
    '가장 아래까지 보인 메시지가 아니다');
});

await test('탭이 안 보이면 읽은 것으로 치지 않는다', async () => {
  const { doc, chat, context, fetchStub } = await boot();
  context.win.runTimers(readsMod.MARK_DEBOUNCE_MS);   /* 처음 것을 흘려보낸다 */
  await Promise.resolve();
  const before = fetchStub.calls.length;

  doc.visibilityState = 'hidden';
  chat.appendMessage(msg(9, '숨은 동안 온 말'));
  context.win.runTimers(readsMod.MARK_DEBOUNCE_MS);
  await Promise.resolve();
  const posted = fetchStub.calls.slice(before).filter(
    (c) => c.path.indexOf('/reads') >= 0 && c.init && c.init.method === 'POST'
  );
  assert.deepEqual(posted, [], '탭이 숨었는데 읽었다고 알렸다');
});

await test('내 커서는 낙관적으로 먼저 움직인다 (응답을 기다리지 않는다)', async () => {
  const list = [who('me@x.io', '', ['me.host']), who('b@x.io', '', ['b.host'])];
  /* 서버 응답이 아예 오지 않는 상황(느린 push·끊긴 네트워크)에서도 화면은 바로
     반영돼야 한다 — 내가 읽은 것은 내 컴퓨터에서 이미 사실이다. */
  const snapshot = readsOf(list, { me: 'me@x.io' });
  const { chat, context } = await boot({
    reads: snapshot,
    routes: {
      '/api/rooms/r1/reads': (p, init) => (
        init && init.method === 'POST' ? { __down: true } : snapshot
      )
    }
  });
  const node = chat.nodes().get(msg(2).id);
  assert.equal(readCount(node), '2');
  context.win.runTimers(readsMod.MARK_DEBOUNCE_MS);
  await Promise.resolve();
  await Promise.resolve();
  /* 내가 읽었으므로 내 몫이 빠진다 (서버 응답이 실패해도). */
  assert.equal(readCount(node), '1', '낙관적 갱신이 없다');
});

await test('구분선은 방을 열 때 한 항목에만 붙고, 읽어도 사라지지 않는다', async () => {
  const list = [who('me@x.io', msg(1).id, ['me.host'])];
  const { chat } = await boot({
    reads: readsOf(list, { cursor: msg(1).id, unread: 2, first_unread: msg(2).id })
  });
  const marked = chat.items().filter((m) => {
    const node = chat.nodes().get(m.id);
    return node && node.newMarkSlot && node.newMarkSlot.hidden === false;
  });
  assert.deepEqual(marked.map((m) => m.id), [msg(2).id]);
  assert.equal(chat.stats.newFromPainted, 1);
  /* 그 항목만 높이를 다시 쟀다 (전체 캐시를 비우지 않았다). */
  assert.equal(chat.stats.headRemeasured, 1);

  /* 내가 다 읽어도 눈앞의 구분선은 남는다 (어디까지가 새 것이었나의 표식이다). */
  chat.bus.emit('reads:state', {
    roomId: 'r1',
    state: readsOf(list, { cursor: msg(3).id, unread: 0, first_unread: null })
  });
  const still = chat.nodes().get(msg(2).id);
  assert.equal(still.newMarkSlot.hidden, false, '구분선이 사라졌다');
  assert.equal(chat.stats.rebuiltInView, 0);
});

await test('방을 바꾸면 구분선이 그 방의 것으로 갈린다', async () => {
  const { chat } = await boot({
    reads: readsOf([who('me@x.io', msg(1).id, ['me.host'])],
      { cursor: msg(1).id, first_unread: msg(2).id })
  });
  assert.equal(chat.state.newFrom, msg(2).id);
  await chat.switchRoom('r2');
  assert.equal(chat.state.newFrom, null, '남의 방 구분선이 남았다');
});

await test('방 목록에 내가 안 읽은 개수가 뱃지로 뜬다 (999+ 상한)', async () => {
  const { doc, chat } = await boot({
    rooms: [
      { id: 'r1', repo_url: 'https://example.invalid/one.git', name: '첫 방', unread: 0 },
      { id: 'r2', repo_url: 'https://example.invalid/two.git', name: '둘째 방', unread: 3 }
    ]
  });
  function badges() {
    const found = [];
    const walk = (n) => {
      for (const c of n.children) {
        if (String(c.className).indexOf('room-unread') >= 0) { found.push(c.textContent); }
        walk(c);
      }
    };
    walk(doc.getElementById('rooms'));
    return found;
  }
  assert.deepEqual(badges(), ['3'], '뱃지가 방마다 맞지 않다 (0 은 그리지 않는다)');

  chat.renderRooms([
    { id: 'r1', repo_url: 'https://example.invalid/one.git', name: '첫 방', unread: 1200 },
    { id: 'r2', repo_url: 'https://example.invalid/two.git', name: '둘째 방', unread: 0 }
  ]);
  assert.deepEqual(badges(), ['999+']);
});

await test('읽음 스냅샷을 못 받아도 대화는 그대로 뜬다 (카운트만 0)', async () => {
  /* 읽음 표시가 없는 것은 대화가 안 되는 것과 다른 급의 사건이다. */
  const { doc, chat } = await boot({
    routes: { '/api/rooms/r1/reads': { __down: true } }
  });
  assert.equal(doc.getElementById('messages').children.length, 3);
  assert.equal(chat.stats.rebuiltInView, 0);
  assert.equal(readCount(chat.nodes().get(msg(2).id)), '');
  assert.equal(doc.getElementById('status').textContent.indexOf('초기화 실패'), -1);
});

/* --------------------- ⭐ 실사용 경로 — 낙관적 전송을 거친 커서 전진
 *
 * ⚠️ **기존 테스트가 이 버그를 놓친 이유가 여기 있다.** 위의 읽음 테스트들은
 * 전부 `msg(N).id`(실제 봉투 ID)를 커서로 기대하거나 `POST /reads` 를 직접
 * 불렀다. 실사용은 그 앞에 한 단계가 더 있다 — **보내는 중인 낙관적 항목이
 * 타임라인에 들어 있는 상태**에서 창 스캔이 최대값을 고른다. 임시 ID 는 사전식
 * 최대값이 되도록 만든 값이라 그 상태에서만 오염이 발생한다.
 *
 * 그래서 여기서는 커서를 손으로 넘기지 않는다. `chat.send()` 로 낙관적 항목을
 * 만들고, 디바운스를 실제로 만료시켜 **POST 로 나가는 몸통**을 검사한다.
 */

/* boot 의 라우트(특히 `/reads`)를 살린 채, `POST /messages` 만 내가 원할 때
   응답하는 대역으로 갈아끼운다 — "보내는 중" 상태를 실제로 만들어야 한다. */
function deferredOverBoot(booted) {
  const inner = booted.fetchStub;
  const waiting = [];
  const fetch = function (path, init) {
    const opts = init || {};
    fetch.calls.push({ path, init: opts });
    if (path.indexOf('/messages') >= 0 && opts.method === 'POST') {
      return new Promise((resolve) => { waiting.push(resolve); });
    }
    return inner(path, init);
  };
  fetch.calls = [];
  fetch.waiting = waiting;
  fetch.answer = function (body, code) {
    const resolve = waiting.shift();
    assert.ok(resolve, '기다리는 POST 가 없다');
    resolve({ ok: code === undefined || code < 300, status: code || 201,
      json: () => Promise.resolve(body) });
    return settle();
  };
  booted.context.fetch = fetch;
  return fetch;
}

function readPosts(fetch) {
  return fetch.calls.filter(
    (c) => c.path.indexOf('/reads') >= 0 && c.init && c.init.method === 'POST'
  ).map((c) => JSON.parse(c.init.body).cursor);
}

await test('⭐ 실사용 경로: 보내는 중인 낙관적 항목이 읽음 커서가 되지 않는다', async () => {
  const booted = await boot();
  const { doc, chat, context } = booted;
  context.win.runTimers(readsMod.MARK_DEBOUNCE_MS);      /* 부팅 몫을 흘려보낸다 */
  await settle();
  const fetch = deferredOverBoot(booted);

  doc.getElementById('text').value = '보내는 중인 말';
  const sending = chat.send();
  await settle();

  /* 전제: 낙관적 항목이 **정말로** 타임라인의 최대 ID 다 (이 조건이 없으면
     테스트가 버그를 재현할 수 없다 — 대조군이 성립하지 않는다). */
  const ids = chat.items().map((m) => m.id);
  const temp = ids.filter((id) => id.indexOf('~pending/') === 0);
  assert.equal(temp.length, 1, '낙관적 항목이 없다 — 실사용 경로를 밟지 못했다');
  assert.equal(ids.slice().sort().pop(), temp[0], '임시 ID 가 최대값이 아니다');
  assert.ok(chat.nodes().get(temp[0]), '낙관적 항목이 창 안에 그려지지 않았다');

  /* ⭐ 그 상태에서 디바운스가 만료된다 = 실사용에서 커서가 전진하는 순간. */
  context.win.runTimers(readsMod.MARK_DEBOUNCE_MS);
  await settle();

  const posted = readPosts(fetch);
  for (const cur of posted) {
    assert.ok(readsMod.isMessageId(cur),
      '읽음 커서로 실제 봉투 ID 가 아닌 값이 나갔다: ' + cur);
    assert.equal(cur.indexOf('~'), -1, '임시 ID 가 서버로 나갔다: ' + cur);
  }
  /* 커서는 **실제로 본 마지막 봉투**까지만 간다 (조용히 안 보내는 것도 결함이다). */
  assert.deepEqual(posted, [], '아직 나갈 것이 없다 — 부팅 몫은 이미 흘려보냈다');
  assert.equal(chat.readsStats().skipped >= 0, true);

  /* 봉투가 도착하면 그 **실제 ID** 로 커서가 전진한다. */
  const real = msg(30, '보내는 중인 말', '기본이름');
  await fetch.answer({ message: real });
  await sending;
  context.win.runTimers(readsMod.MARK_DEBOUNCE_MS);
  await settle();
  const after = readPosts(fetch);
  assert.deepEqual(after, [real.id],
    '봉투가 도착했는데 커서가 실제 ID 로 전진하지 않았다: ' + JSON.stringify(after));
});

await test('⭐ 임시 ID 가 창에 있어도 단조 증가가 굳지 않는다 (실제 ID 로 되돌아온다)', async () => {
  /* ⚠️ 이것이 이 버그의 **되돌릴 수 없음**이다 — `~`(0x7E) 가 `records/`(0x72)
     보다 사전식 뒤라 임시 ID 가 한 번 최대값으로 채택되면, 커서가 `max()` 인
     구조에서는 그 뒤 어떤 실제 ID 도 커서를 움직이지 못한다. 알리는 쪽에서
     걸러야 하는 이유가 이것이다 (받는 쪽만 막으면 `seenMax` 가 굳는다). */
  const booted = await boot();
  const { doc, chat, context } = booted;
  context.win.runTimers(readsMod.MARK_DEBOUNCE_MS);
  await settle();
  const fetch = deferredOverBoot(booted);

  doc.getElementById('text').value = '먼저 보내는 말';
  const sending = chat.send();
  await settle();
  context.win.runTimers(readsMod.MARK_DEBOUNCE_MS);      /* 오염될 수 있던 순간 */
  await settle();

  /* 그 뒤 **남의 새 메시지**가 도착한다 (실제 봉투 ID). */
  chat.appendMessage(msg(40, '남이 한 말'));
  await settle();
  context.win.runTimers(readsMod.MARK_DEBOUNCE_MS);
  await settle();

  const posted = readPosts(fetch);
  assert.ok(posted.indexOf(msg(40).id) >= 0,
    '임시 ID 때문에 실제 ID 가 커서로 나가지 못했다: ' + JSON.stringify(posted));
  assert.ok(posted.every((c) => readsMod.isMessageId(c)), JSON.stringify(posted));

  await fetch.answer({ message: msg(30, '먼저 보내는 말', '기본이름') });
  await sending;
});

await test('⭐ 오염된 커서(`~pending/…`)를 만나면 카운트가 되살아난다 (복구 경로)', () => {
  /* 실제 방에 저장돼 있던 그 값이다 (원격까지 올라가 있었다). 서버가 위생을
     하더라도 화면이 스스로 지켜야 한다 — 그 값이 그대로 들어오면 `cursor < M`
     이 **모든** 참가자에게 거짓이라 카운트가 조용히 0 이 된다. */
  const m = { id: msg(2).id, sender: 'a.host' };
  const dirty = [
    who('b@x.io', '~pending/000001', ['b.host']),
    who('c@x.io', '~pending/000004', ['c.host'])
  ];
  /* 원본 공식은 오염 값을 "읽었다"로 읽는다 — 그게 이 버그의 조용한 얼굴이다. */
  assert.equal(readsMod.countUnread(dirty, m), 0, '전제가 깨졌다 (오염이 0 을 만든다)');
  /* 판정 함수가 그것을 커서로 인정하지 않는다. */
  assert.equal(readsMod.isMessageId('~pending/000001'), false);
  assert.equal(readsMod.isMessageId(msg(2).id), true);
});

await test('⭐ 오염된 스냅샷이 들어와도 화면 카운트가 뜬다 (모델이 떨군다)', async () => {
  const { chat } = await boot({
    reads: readsOf([
      who('me@x.io', '~pending/000004', ['me.host']),
      who('b@x.io', '~pending/000001', ['b.host'])
    ], { cursor: '~pending/000004' })
  });
  /* 오염 값을 "없음"으로 떨궜으므로 두 사람 다 안 읽은 것으로 세어진다.
     (조용히 0 이 되는 것보다 과다가 낫다 — 사람이 알아채고 스크롤하면 사라진다.) */
  assert.equal(readCount(chat.nodes().get(msg(2).id)), '2',
    '오염된 커서 때문에 카운트가 조용히 사라졌다');
  assert.equal(chat.reads().cursor, '', '오염된 내 커서를 그대로 들고 있다');
  assert.deepEqual(chat.reads().participants.map((p) => p.cursor), ['', '']);
  assert.equal(chat.stats.rebuiltInView, 0);
});

await test('임시 ID 를 커서로 밀어 넣으려 하면 거부하고 콘솔에 남긴다 (조용한 실패 금지)', async () => {
  const { chat, context, fetchStub } = await boot();
  const before = fetchStub.calls.length;
  assert.equal(chat.markRead('~pending/000009'), false, '임시 ID 를 받아들였다');
  context.win.runTimers(readsMod.MARK_DEBOUNCE_MS);
  await settle();
  const posted = fetchStub.calls.slice(before).filter(
    (c) => c.path.indexOf('/reads') >= 0 && c.init && c.init.method === 'POST'
  ).map((c) => JSON.parse(c.init.body).cursor);
  assert.deepEqual(posted.filter((c) => c.indexOf('~') === 0), [],
    '임시 ID 가 서버로 나갔다');
});

await test('읽음 카운트 자리는 세 배치 모두에 있다 (구조 분기가 새지 않는다)', async () => {
  for (const layout of ['bubbles', 'log', 'ide']) {
    const { doc, chat } = await boot({
      stored: { 'gitwire-chat.layout': layout },
      reads: readsOf([who('me@x.io', '', ['me.host']), who('b@x.io', '', ['b.host'])])
    });
    assert.equal(chat.layout(), layout);
    const node = chat.nodes().get(msg(2).id);
    assert.ok(node.readsSlot, layout + ' 배치에 읽음 자리가 없다');
    assert.equal(readCount(node), '2', layout + ' 배치에서 카운트가 안 그려진다');
    /* 구분선은 **필요할 때** 그 항목에만 생긴다 (평소엔 노드가 아예 없다). */
    assert.equal(node.newMarkSlot, undefined,
      layout + ' 배치에서 구분선이 미리 만들어져 있다');
    assert.equal(nodeMod.paintNewFrom(
      { make: (t, c, x) => { const n = doc.createElement(t); n.className = c || ''; if (x !== undefined) { n.textContent = x; } return n; } },
      node, true
    ), true, layout + ' 배치에서 구분선이 붙지 않는다');
    assert.ok(String(node.children[0].className).indexOf('new-mark') >= 0,
      layout + ' 배치에서 구분선이 맨 위에 오지 않는다');
  }
});

/* -------------------------------------------------------------- 보고 */

let failed = 0;
for (const [status, name, detail] of results) {
  if (status === 'FAIL') { failed += 1; }
  console.log(status + '  ' + name + (detail ? '\n      ' + detail : ''));
}
console.log((results.length - failed) + '/' + results.length + ' 통과');
process.exit(failed ? 1 : 0);

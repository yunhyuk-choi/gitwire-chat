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
    unknown: false
  };
}

/* window 대역 — 모듈이 전역을 직접 집어오지 않으므로 이 정도면 충분하다. */
function stubWindow() {
  return {
    listeners: {},
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    removeEventListener(type, fn) {
      const list = this.listeners[type] || [];
      const i = list.indexOf(fn);
      if (i >= 0) { list.splice(i, 1); }
    },
    dispatch(type, event) {
      for (const fn of (this.listeners[type] || []).slice()) { fn(event || { type }); }
    }
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
    localStorage: {
      _v: {},
      getItem(k) { return this._v[k] || null; },
      setItem(k, v) { this._v[k] = v; }
    },
    console: {
      log: console.log.bind(console),
      warn: console.warn.bind(console),
      error: (...args) => { consoleErrors.push(args.map(String).join(' ')); }
    },
    /* 기본은 **진짜** 엔진. 실패 경로를 보는 테스트만 여기에 다른 것을 넣는다. */
    virtual: ('virtual' in opts) ? opts.virtual : virtual
  };

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

await test('⭐ 실패: 말풍선이 남고 "전송 실패" + 재시도가 붙는다 (입력 내용 안 잃음)', async () => {
  const { doc, chat, context, list, before, bubble, sending } =
    await startSending('실패할 말');

  await context.fetch.answer({ error: '원격이 죽었다' }, 500);
  await sending;

  assert.ok(String(bubble.className).includes('failed'));
  assert.ok(bubble.textContent.includes('전송 실패'));
  assert.ok(bubble.textContent.includes('재시도'));
  // 글은 말풍선 안에 그대로 있다 — 입력칸으로 되돌리지 않는다(같은 글이 두 곳에
  // 생기는 것을 막는다).
  assert.ok(bubble.textContent.includes('실패할 말'));
  assert.equal(doc.getElementById('text').value, '');
  assert.equal(list.children.length, before + 1);
  assert.ok(doc.getElementById('status').textContent.includes('전송 실패'));

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

/* -------------------------------------------------------------- 보고 */

let failed = 0;
for (const [status, name, detail] of results) {
  if (status === 'FAIL') { failed += 1; }
  console.log(status + '  ' + name + (detail ? '\n      ' + detail : ''));
}
console.log((results.length - failed) + '/' + results.length + ' 통과');
process.exit(failed ? 1 : 0);

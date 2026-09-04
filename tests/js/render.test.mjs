/*
 * ⭐ "전체 리렌더가 없다"를 **세어서** 증명한다.
 *
 * 브라우저가 없으니 stub DOM 위에서 app.js 를 실제로 구동하고, DOM 조작 횟수와
 * **노드의 동일성(===)** 을 확인한다. 노드가 같은 객체로 남아 있다는 것이
 * "다시 그리지 않았다"의 가장 강한 증거다 — 다시 그렸다면 새 객체일 수밖에 없다.
 *
 * 실행: node tests/js/render.test.mjs
 * 실패하면 종료코드가 0 이 아니다 (pytest 가 그걸 본다).
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import url from 'node:url';
import vm from 'node:vm';

import {
  ELEMENT_IDS, StubDocument, StubEventSource, StubIntersectionObserver, makeFetch
} from './stub-dom.mjs';

const here = path.dirname(url.fileURLToPath(import.meta.url));
const ROOT = path.resolve(here, '..', '..');
const APP_JS = path.join(ROOT, 'src', 'gitwire_chat', 'static', 'app.js');
const INDEX_HTML = path.join(ROOT, 'src', 'gitwire_chat', 'templates', 'index.html');

const source = fs.readFileSync(APP_JS, 'utf8');
const indexHtml = fs.readFileSync(INDEX_HTML, 'utf8');

const results = [];
function test(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => { results.push(['PASS', name]); },
      (err) => { results.push(['FAIL', name, err && err.message]); });
}

/* ------------------------------------------------------------- 도우미 */

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

function boot(options) {
  const opts = options || {};
  const doc = new StubDocument();
  for (const id of ELEMENT_IDS) { doc.register(id); }
  // 실제 DOM 처럼 messages 를 timeline 안에 넣는다 (스크롤 계산이 성립하도록).
  doc.getElementById('timeline').appendChild(doc.getElementById('messages'));
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

  const fetchStub = makeFetch({
    '/api/rooms/r1/messages?before=': olderPage,
    '/api/rooms/r2/messages': { messages: opts.room2 || [msg(50, '둘째 방 메시지')], has_more: false },
    '/api/rooms/r1/messages': { messages: messages, has_more: !!opts.hasMore },
    '/api/rooms/r1/visibility': { ok: true },
    '/api/rooms/r2/visibility': { ok: true },
    '/api/rooms': { rooms: rooms }
  });

  const context = {
    document: doc,
    fetch: fetchStub,
    EventSource: StubEventSource,
    IntersectionObserver: opts.noObserver ? undefined : StubIntersectionObserver,
    console: console,
    localStorage: {
      _v: {},
      getItem(k) { return this._v[k] || null; },
      setItem(k, v) { this._v[k] = v; }
    },
    addEventListener() {},
    setTimeout: setTimeout,
    clearTimeout: clearTimeout
  };
  vm.createContext(context);
  vm.runInContext(source, context, { filename: 'app.js' });

  const chat = context.__chat;
  assert.ok(chat, 'app.js 가 __chat 을 노출하지 않았다');
  return Promise.resolve(chat.boot()).then(() => ({ doc, chat, fetchStub, context }));
}

/* ------------------------------------------------------------- 테스트 */

await test('템플릿의 id 와 스크립트가 찾는 id 가 어긋나지 않는다', () => {
  for (const id of ELEMENT_IDS) {
    assert.ok(indexHtml.includes('id="' + id + '"'), 'index.html 에 없는 id: ' + id);
  }
});

await test('app.js 어디에도 innerHTML 대입이 없다 (정적 검사)', () => {
  const hits = source.split('\n')
    .map((line, i) => [i + 1, line])
    .filter(([, line]) => /\.innerHTML\s*=/.test(line) ||
      /insertAdjacentHTML|outerHTML\s*=|document\.write/.test(line));
  assert.deepEqual(hits, [], 'HTML 문자열 주입 흔적: ' + JSON.stringify(hits));
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

await test('순서가 뒤집혀 와도 제자리에 끼우고 남은 노드는 안 건드린다', async () => {
  const { doc, chat } = await boot();
  const list = doc.getElementById('messages');
  const before = list.children.slice();

  StubEventSource.current.emit('message', msg(7, '나중 것'));
  StubEventSource.current.emit('message', msg(5, '먼저 것이지만 늦게 도착'));

  const texts = list.children.map((c) => c.dataset.id);
  const sorted = texts.slice().sort();
  assert.deepEqual(texts, sorted, '시간순 정렬이 깨졌다');
  assert.equal(chat.stats.inserted, 1);            // 한 건만 중간 삽입
  for (let i = 0; i < before.length; i++) {
    assert.equal(list.children[i], before[i]);
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

/* 마이크로태스크 큐를 비운다 (fetch 대역 → prepend 까지 흘려보낸다). */
function settle() {
  return new Promise((resolve) => setTimeout(resolve, 0));
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
  const list = doc.getElementById('messages');
  const timeline = scrollUp(doc);
  const before = list.children.slice();

  const heightBefore = timeline.scrollHeight;
  const topBefore = timeline.scrollTop;

  StubIntersectionObserver.current.trigger();   // 표식이 보였다 = 위 끝에 닿았다
  await settle();                                // 진행 중 요청이 끝나기를 기다린다

  const heightAfter = timeline.scrollHeight;
  const grew = heightAfter - heightBefore;
  console.log('      스크롤 보정: height ' + heightBefore + ' → ' + heightAfter +
    ' (+' + grew + '), scrollTop ' + topBefore + ' → ' + timeline.scrollTop);

  assert.ok(grew > 0, '위쪽 콘텐츠가 늘지 않았다 (테스트 전제가 깨졌다)');
  assert.equal(timeline.scrollTop, topBefore + grew, '보던 자리가 아래로 튀었다');
  assert.equal(chat.stats.lastAnchor, grew);
  assert.equal(chat.stats.prepended, 2);

  // 앞에 끼웠을 뿐 기존 노드는 **같은 객체**로 그대로다 (전체 리렌더 0).
  assert.equal(list.children.length, 5);
  for (let i = 0; i < before.length; i++) {
    assert.equal(list.children[i + 2], before[i], i + '번 노드가 교체됐다');
  }
  assert.equal(doc.counts.removeChild, 0);
  assert.equal(list.removedByReplace, 0);
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
  assert.equal(olderCalls.length, 1);
  assert.equal(chat.stats.prepended, 2);
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

/* -------------------------------------------------------------- 보고 */

let failed = 0;
for (const [status, name, detail] of results) {
  if (status === 'FAIL') { failed += 1; }
  console.log(status + '  ' + name + (detail ? '\n      ' + detail : ''));
}
console.log((results.length - failed) + '/' + results.length + ' 통과');
process.exit(failed ? 1 : 0);

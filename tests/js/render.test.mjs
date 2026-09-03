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

import { ELEMENT_IDS, StubDocument, StubEventSource, makeFetch } from './stub-dom.mjs';

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

  const rooms = opts.rooms || [
    { id: 'r1', repo_url: 'https://example.invalid/one.git', name: '첫 방' },
    { id: 'r2', repo_url: 'https://example.invalid/two.git', name: '둘째 방' }
  ];
  const messages = opts.messages || [msg(1), msg(2), msg(3)];

  const fetchStub = makeFetch({
    '/api/rooms/r1/messages?before=': () => ({ messages: opts.older || [], maybe_more: false }),
    '/api/rooms/r2/messages': { messages: opts.room2 || [msg(50, '둘째 방 메시지')], maybe_more: false },
    '/api/rooms/r1/messages': { messages: messages, maybe_more: !!opts.maybeMore },
    '/api/rooms/r1/visibility': { ok: true },
    '/api/rooms/r2/visibility': { ok: true },
    '/api/rooms': { rooms: rooms }
  });

  const context = {
    document: doc,
    fetch: fetchStub,
    EventSource: StubEventSource,
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

await test('이전 불러오기는 앞에 붙이기만 하고 스크롤을 유지한다', async () => {
  const older = [msg(0, '아주 예전 1'), msg(1, '아주 예전 2')].map((m, i) => {
    m.id = 'records/20260902/2026090' + '2T0100' + String(i).padStart(2, '0') + '000Z-a-x.json';
    return m;
  });
  const { doc, chat } = await boot({ maybeMore: true, older: older });
  const list = doc.getElementById('messages');
  const timeline = doc.getElementById('timeline');
  const before = list.children.slice();
  timeline.scrollTop = 0;

  await chat.loadOlder();

  assert.equal(list.children.length, 5);
  assert.equal(chat.stats.prepended, 2);
  // 원래 3개는 그대로, 순서만 뒤로 밀렸다.
  for (let i = 0; i < before.length; i++) {
    assert.equal(list.children[i + 2], before[i]);
  }
  assert.ok(timeline.scrollTop > 0, '스크롤 앵커링이 안 됐다 (읽던 자리가 튄다)');
  assert.equal(doc.counts.innerHTML, 0);
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

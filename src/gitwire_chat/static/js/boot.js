/*
 * 조립소 — 모듈을 만들고, 버스를 쥐어 주고, 각각 **독립적으로** 세운다.
 *
 * ⭐ 왜 이렇게 갈랐나
 *
 * 예전에는 1000줄짜리 통짜 IIFE 안에서 방 목록·방 추가·레포 만들기·타임라인·
 * 가상화·무한 스크롤·검색·알림·SSE 가 **같은 전역 상태를 공유**했다. 결과가 둘:
 *
 *   1. 초기화 하나가 던지면 그 뒤가 전부 실행되지 않았다. 가상 스크롤은 메시지
 *      리스트 하나에만 거는 것인데 `+` 버튼·방 목록·검색이 같이 죽었다.
 *      배선을 앞으로 옮기는 것은 고장 지점을 뒤로 민 것일 뿐 구조는 그대로다.
 *   2. "타임라인을 다시 그리지 않는다"가 구조에서 나오는 성질이 아니라, 전역
 *      규율 + 카운터로 *감시*해야 하는 것이 됐다. 순서가 거꾸로다.
 *
 * 지금은 모듈이 **자기 DOM 영역과 자기 상태만** 소유하고 남의 노드를 만지지
 * 않는다. 그러면 격리도 리렌더 국소성도 감싸서 만드는 게 아니라 **저절로** 나온다.
 *
 * 모듈 간 통신은 두 가지뿐이다:
 *   · 이벤트 (`bus`) — 알림·요청. 구독자가 없으면(그 모듈이 실패했으면) 무시된다.
 *   · 명시적 주입 (`env`) — 도구(dom·api·status)와, 읽기 전용 창 하나(`roomDraft`).
 *
 * ⚠️ 실패를 **삼키지 않는다.** 실패한 단위는 `__chat.failures` 에 남고, 상태줄과
 * 콘솔에 드러나며, 자기 영역이 있는 단위(타임라인)는 그 자리에도 결함을 그린다.
 */

import { createDom, errText, uid } from './dom.js';
import { createBus } from './bus.js';
import { createApi } from './api.js';
import { createStatusBar } from './statusbar.js';
import { createRoomList } from './roomlist.js';
import { createAddRoom } from './addroom.js';
import { createNewRepo } from './newrepo.js';
import { createComposer } from './composer.js';
import { createTimeline } from './timeline.js';
import { createSearch } from './search.js';
import { createStream } from './stream.js';
import { createPresence } from './presence.js';

export function createApp(runtime) {
  var dom = createDom(runtime.doc);
  /* fetch 를 **늦게 묶는다** — 런타임이 바뀌어도(테스트가 대역을 갈아끼운다)
     같은 api 핸들이 계속 유효하다. */
  var api = createApi(function (path, init) { return runtime.fetch(path, init); });
  var status = createStatusBar(dom);
  var failures = [];
  var modules = {};
  var booted = false;

  function note(unit, err) {
    failures.push({ unit: unit, error: errText(err) });
    if (runtime.console && runtime.console.error) {
      runtime.console.error('[gitwire-chat] 초기화 실패 · ' + unit, err);
    }
  }

  var bus = createBus(function (type, err) { note('이벤트 ' + type, err); });

  var env = {
    dom: dom,
    bus: bus,
    api: api,
    status: status,
    win: runtime.win,
    console: runtime.console,
    localStorage: runtime.localStorage,
    EventSource: runtime.EventSource,
    IntersectionObserver: runtime.IntersectionObserver,
    virtual: runtime.virtual,
    client: uid()
  };

  /* 단위 하나를 세운다. 던지면 **그 단위만** 실패하고 나머지는 그대로 선다. */
  function setup(unit, factory, onFail) {
    var mod = null;
    try {
      mod = factory(env);
      mod.mount();
      return mod;
    } catch (err) {
      note(unit, err);
      if (onFail) {
        try { onFail(mod, err); } catch (inner) { /* 보고 경로까지 죽이지 않는다 */ }
      }
      return mod;
    }
  }

  /* 실패를 한 줄로 드러낸다. 사라지지 않는 안내라 status('') 로 지워지지 않는다. */
  function report() {
    var broken = modules.timeline && modules.timeline.view.broken;
    var parts = [];
    if (broken) { parts.push('메시지 영역: ' + broken); }
    for (var i = 0; i < failures.length; i++) {
      if (failures[i].unit === '타임라인' && broken) { continue; }
      parts.push(failures[i].unit + ': ' + failures[i].error);
    }
    if (!parts.length) { return; }
    status.stick('초기화 실패 — ' + parts.join(' · ') + ' (앱 결함이다)');
  }

  /* ⭐ 조용한 실패 금지 — 어디서 터지든 화면 아래 한 줄로 드러낸다.
     이 앱을 쓰는 사람은 개발자 콘솔을 열지 않는다. */
  function watchCrashes() {
    dom.on(runtime.win, 'error', function (e) {
      status.stick('화면 스크립트에서 오류가 났다 — ' + errText(e && (e.error || e.message || e)));
    });
    dom.on(runtime.win, 'unhandledrejection', function (e) {
      status.stick('처리되지 않은 오류가 났다 — ' + errText(e && e.reason));
    });
  }

  function boot() {
    if (booted) { return; }
    booted = true;

    setup('오류 감시', function () { return { mount: watchCrashes }; });

    /* 여기부터는 서로 **무관한** 단위들이다. 안전장치는 순서가 아니라 격리다. */
    modules.roomlist = setup('방 목록', createRoomList);
    modules.addroom = setup('방 추가', createAddRoom);
    env.roomDraft = function () {
      return modules.addroom ? modules.addroom.fields() : { name: '', tokenEnv: '' };
    };
    modules.newrepo = setup('레포 만들기', createNewRepo);
    modules.composer = setup('보내기', createComposer);
    modules.search = setup('검색', createSearch);
    modules.stream = setup('받기', createStream);
    modules.presence = setup('알림·가시성', createPresence);
    modules.timeline = setup('타임라인', createTimeline, function (mod, err) {
      /* 타임라인의 실패는 **타임라인에 갇힌다.** 대신 그 자리에 결함을 그린다. */
      if (mod && mod.fail) {
        mod.fail('타임라인을 세우지 못했다 (' + errText(err) + ')');
      }
    });

    report();

    /* 방 목록의 단일 원천은 서버다. 방 목록 모듈이 죽어 있어도 이 호출은 나간다 —
       그래야 "무엇이 살아 있나"를 사람이 볼 수 있다. */
    return api('/api/rooms').then(function (data) {
      var list = data.rooms || [];
      bus.emit('rooms:list', { rooms: list });
      if (!list.length) { status.set(''); return; }
      if (modules.roomlist) { return modules.roomlist.select(list[0].id); }
      return bus.emit('room:switch', { id: list[0].id });
    })['catch'](function (err) { status.set(errText(err), true); });
  }

  var chat = {
    boot: boot,
    bus: bus,
    modules: modules,
    failures: function () { return failures; },
    /* --- 아래는 테스트·디버깅이 붙는 창이다. 각 모듈의 것을 그대로 노출한다. --- */
    items: function () { return modules.timeline ? modules.timeline.items() : []; },
    nodes: function () { return modules.timeline ? modules.timeline.nodes() : new Map(); },
    pendings: function () { return modules.timeline ? modules.timeline.pendings() : new Map(); },
    virtualizer: function () { return modules.timeline ? modules.timeline.virtualizer() : null; },
    renderWindow: function () { if (modules.timeline) { modules.timeline.renderWindow(); } },
    syncVirtual: function () { if (modules.timeline) { modules.timeline.syncVirtual(); } },
    appendMessage: function (m) { return modules.timeline ? modules.timeline.append(m) : false; },
    prependMessages: function (l) { return modules.timeline ? modules.timeline.prepend(l) : 0; },
    clearTimeline: function () { if (modules.timeline) { modules.timeline.clear(); } },
    loadOlder: function () { return modules.timeline ? modules.timeline.loadOlder() : undefined; },
    watchOlder: function () { if (modules.timeline) { modules.timeline.watchOlder(); } },
    switchRoom: function (id) { if (modules.roomlist) { return modules.roomlist.select(id); } },
    renderRooms: function (l) { if (modules.roomlist) { modules.roomlist.render(l); } },
    retryRoom: function (id) { if (modules.roomlist) { return modules.roomlist.retryRoom(id); } },
    addRoom: function () { return modules.addroom ? modules.addroom.submit() : undefined; },
    planNewRepo: function () { return modules.newrepo ? modules.newrepo.plan() : undefined; },
    createNewRepo: function () { return modules.newrepo ? modules.newrepo.create() : undefined; },
    send: function () { return modules.composer ? modules.composer.send() : undefined; },
    runSearch: function () { return modules.search ? modules.search.run() : undefined; },
    connect: function (id) { if (modules.stream) { modules.stream.connect(id); } },
    disconnect: function () { if (modules.stream) { modules.stream.disconnect(); } }
  };

  /* 타임라인이 소유한 상태를 그대로 들여다보는 창 (별도 사본이 아니다 —
     전역 상태를 하나 더 만드는 순간 "누가 소유하나"가 다시 흐려진다). */
  Object.defineProperty(chat, 'state', {
    get: function () { return modules.timeline ? modules.timeline.view : {}; }
  });
  Object.defineProperty(chat, 'stats', {
    get: function () { return modules.timeline ? modules.timeline.stats : {}; }
  });

  return chat;
}

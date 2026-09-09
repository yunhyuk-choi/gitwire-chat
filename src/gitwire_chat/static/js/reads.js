/*
 * 읽음 표시 — **읽음 모델**을 소유한다. DOM 은 하나도 만지지 않는다.
 *
 * 화면에 그리는 일은 그 노드의 주인이 한다:
 *   · 메시지에 붙는 카운트·구분선 → `timeline.js` (메시지 노드의 주인)
 *   · 방 목록 뱃지               → `roomlist.js` (`#rooms` 의 주인)
 * 이 모듈이 하는 일은 셋이다 — **커서 지도를 들고 있고**, 카운트를 **계산해 주고**,
 * "내가 여기까지 읽었다"를 **서버에 알린다.**
 *
 * ⭐ 카운트는 저장값이 아니라 파생값이다
 * ------------------------------------
 *     메시지 M(작성자 A) 의 카운트 = |{ p ∈ 참가자, p ≠ A, cursor(p) < M }|
 *
 * 서버는 **커서 지도만** 준다(사람 수만큼). 그래서 누군가의 커서가 한 번 전진하면
 * 그 아래 메시지 **전부**의 카운트가 같이 줄어든다 — 메시지마다 숫자를 저장하고
 * 갱신하는 구조로는 이 일이 공짜가 아니다.
 *
 * ⭐ 커서 전진의 기준 — "탭이 보이는 동안 화면에 들어온 최대 메시지"
 * ---------------------------------------------------------------
 * "탭이 열렸다"와 "그 메시지를 읽었다"는 다르다. 가상 스크롤은 어느 항목이 창 안에
 * 있었는지 정확히 알고 있으므로(`timeline.js` 가 `reads:seen` 으로 알려 준다) 그
 * 최대값을 쓴다. 그리고 **디바운스**한다 — 스크롤 한 번에 push 를 만들지 않는다.
 *
 * ⚠️⭐ 그 "최대 메시지"에 **낙관적 항목이 섞이면 안 된다** (실측된 전면 고장)
 * ---------------------------------------------------------------------
 * 보내는 중인 항목의 임시 ID(`~pending/…`, `composer.js`)는 정렬을 위해 실제 봉투
 * ID 보다 사전식으로 **뒤**가 되도록 만들었다. 그래서 그것이 창 안에 있으면 언제나
 * 최대값이고, 그 값이 커서로 채택돼 서버·원격까지 올라갔다. 커서는 단조 증가라
 * (`max()`) **한 번 오염되면 실제 ID 로 되돌아갈 수 없다** — 그 뒤로 안 읽은
 * 개수와 방 안 카운트가 영구히 0 이 됐다 (숫자가 아예 화면에 뜨지 않는다).
 *
 * 그래서 커서로 받는 값은 **실제 봉투 ID 만**이다 (`isMessageId`). 판정을 세 곳에
 * 둔다 — 알리는 쪽(`timeline.js`), 받는 쪽(여기 `seen`), 저장하는 쪽(서버
 * `gitwire_chat/reads.py`, 아니면 HTTP 400). 하나만 두면 새 호출자가 우회한다.
 *
 * ⭐ 발행은 best-effort 이고 메시지에 양보한다
 * -----------------------------------------
 * POST 는 서버의 로컬 커서만 즉시 움직이고, 원격 push 는 아웃박스가 뒤에서 민다
 * (`gitwire_chat/rooms.py` · `outbox.py`). 읽음 표시 때문에 메시지 전송이 늦어지는
 * 일이 없다. 내 화면은 **낙관적**으로 먼저 반영한다.
 *
 * ⚠️ 지연은 구조적이다 — 상대가 읽음 → 상대 push → 내 폴링. 숫자가 폴 주기만큼
 * 늦게 줄어든다. 우회로가 없으므로 감추지 않는다 (README 에 사실로 적혀 있다).
 */

import { errText } from './dom.js';

/* 화면에 들어온 것을 서버에 알리기까지 기다리는 시간(ms).
 *
 * 왜 이 값인가: 스크롤은 한 번에 수십 번의 창 갱신을 만든다. 그때마다 알리면
 * 방 하나에서 초당 여러 번 POST 가 나가고, 그 하나하나가 커서 파일을 다시 쓰는
 * 일이라 push 거리가 늘어난다. 반대로 너무 길면 "봤는데 상대 화면에서 계속 안
 * 읽음"이 오래 남는다. 폴 주기(기본 15초)에 비하면 1.2초는 무시할 만하다 —
 * 어차피 상대가 그것을 보는 시점은 자기 폴 주기에 묶인다.
 */
export var MARK_DEBOUNCE_MS = 1200;

/* ⭐ **실제 봉투 ID 인가** — 커서가 받을 수 있는 값의 유일한 판정이다.
 *
 * 형식은 기반(gitwire)이 정한다: `records/<날짜8>/<타임스탬프17>-<발신자>-<난수>.json`
 * (파이썬 쪽 정본은 `gitwire.is_record_id`). 여기서 다시 쓰는 이유는 브라우저가
 * 그 모듈을 부를 수 없기 때문이고, 둘이 어긋나지 않게 **테스트가 같은 값으로 양쪽을
 * 검증한다**(`tests/js/render.test.mjs` · `tests/test_reads.py`).
 *
 * 앞이 고정폭 타임스탬프라는 성질이 판정의 힘이다 — 임시 ID(`~pending/000004`)나
 * 아직 아무것도 아닌 값(`''`·`'?'`)은 여기를 통과하지 못한다.
 */
var MESSAGE_ID_RE = /^records\/(\d{8})\/(\d{8}T\d{9}Z)-[A-Za-z0-9_.@+]+-[A-Za-z0-9]+\.json$/;

export function isMessageId(value) {
  if (typeof value !== 'string') { return false; }
  var m = MESSAGE_ID_RE.exec(value);
  /* 날짜 칸은 타임스탬프에서 파생된 값이다 — 어긋난 경로는 이 채널이 만든 것이 아니다. */
  return !!m && m[1] === m[2].slice(0, 8);
}

/* ⭐ 카운트 공식 — **여기가 유일한 원천**이다 (서버에도, 다른 파일에도 없다).
 *
 * `participants` 는 서버가 준 커서 지도, `msg` 는 화면의 메시지 하나다.
 * 판정은 둘뿐이다:
 *
 *   1. **작성자는 세지 않는다** (`p ≠ A`). 봉투에는 사람 키가 아니라 설치본
 *      식별자(`sender`)만 있으므로, 각 참가자가 자기 파일에 적어 둔 `senders`
 *      목록으로 짝짓는다 (`gitwire_chat/reads.py`).
 *   2. **커서가 그 메시지보다 앞이면 안 읽은 것이다** (`cursor(p) < M`).
 *      메시지 ID 는 고정폭 타임스탬프로 시작해 사전식 = 시간순이라 문자열
 *      비교가 곧 시간 비교다. 커서가 아예 없으면(한 번도 안 읽음) 안 읽은 것이다.
 *
 * ⚠️ 오래 안 움직인 커서를 분모에서 빼지 않는다 — 설치만 하고 앱을 안 켜는 사람의
 * 카운트는 영구히 1 로 남는데, **그게 사실이다.** 조용히 "다 읽음"이 되는 것이
 * 더 나쁘다.
 */
export function countUnread(participants, msg) {
  if (!participants || !participants.length || !msg || !msg.id) { return 0; }
  var n = 0;
  for (var i = 0; i < participants.length; i++) {
    var p = participants[i];
    if (!p) { continue; }
    var senders = p.senders || [];
    if (msg.sender && senders.indexOf(msg.sender) >= 0) { continue; }  /* 작성자 */
    if (!p.cursor || p.cursor < msg.id) { n += 1; }
  }
  return n;
}

export function createReads(env) {
  var dom = env.dom;
  var bus = env.bus;
  var api = env.api;
  var win = env.win;

  var roomId = null;
  /* 서버가 준 스냅샷. **사본을 두 벌 만들지 않는다** — 화면은 이걸 읽는다. */
  var model = { me: '', person: '', cursor: '', firstUnread: null, participants: [] };
  /* 아직 서버에 알리지 않은, 화면에 들어온 최대 메시지 ID. */
  var pending = '';
  var timer = null;

  var stats = { marks: 0, posts: 0, skipped: 0, applied: 0 };

  function visible() {
    var vs = dom.doc.visibilityState;
    return !vs || vs === 'visible';
  }

  /* 서버가 준 커서를 그대로 믿지 않는다 — 형식이 아니면 **없음**으로 본다.
     서버도 같은 위생을 하지만(`gitwire_chat/reads.py`), 화면이 스스로 지키지
     않으면 구버전 서버·원격에 남은 오염 값 하나가 카운트를 전부 0 으로 만든다.
     "없음"으로 떨구면 그 사람은 안 읽은 것으로 세어진다 — 과다는 눈에 보이고
     사라지지만, 조용한 0 은 아무도 못 알아챈다. */
  function sane(cursor) {
    return isMessageId(cursor) ? cursor : '';
  }

  function apply(data) {
    if (!data) { return; }
    var list = data.participants || [];
    var clean = [];
    for (var i = 0; i < list.length; i++) {
      var p = list[i];
      if (!p) { continue; }
      clean.push(sane(p.cursor) === (p.cursor || '')
        ? p : Object.assign({}, p, { cursor: sane(p.cursor) }));
    }
    model = {
      me: data.me || '',
      person: data.person || '',
      cursor: sane(data.cursor),
      firstUnread: data.first_unread || null,
      participants: clean
    };
    stats.applied += 1;
    bus.emit('reads:changed', { roomId: roomId });
  }

  /* 방을 열 때 한 번. 실패해도 대화는 그대로 뜬다 — 읽음 표시만 비어 있다. */
  function load(id) {
    return api('/api/rooms/' + encodeURIComponent(id) + '/reads')
      .then(function (data) {
        if (roomId !== id) { return; }
        apply(data);
      })['catch'](function (err) {
        /* 조용히 넘기지 않는다. 다만 상태줄을 뺏지도 않는다 — 읽음 표시가 없는
           것은 대화가 안 되는 것과 다른 급의 사건이다. */
        if (env.console && env.console.warn) {
          env.console.warn('[gitwire-chat] 읽음 상태를 받지 못했다 — ' + errText(err));
        }
      });
  }

  /* ⭐ 내가 읽은 위치를 서버에 알린다. **낙관적** — 화면은 먼저 반영한다. */
  function flush() {
    timer = null;
    var id = pending;
    var room = roomId;
    if (!room || !id) { return; }
    /* POST 로 나가는 값의 **마지막 관문**. `seen` 이 이미 걸렀지만, 서버에
       보내는 자리에서 한 번 더 본다 — 오염이 원격까지 올라간 사고였다. */
    if (!isMessageId(id)) { pending = ''; stats.skipped += 1; return; }
    if (model.cursor && id <= model.cursor) { stats.skipped += 1; return; }
    pending = '';
    /* 내 쪽 낙관적 갱신: 내 커서가 앞으로 갔으므로 내가 세어지던 카운트가
       바로 줄어든다. 서버 응답을 기다리지 않는다. */
    model.cursor = id;
    for (var i = 0; i < model.participants.length; i++) {
      if (model.participants[i].key === model.me) {
        model.participants[i] = Object.assign({}, model.participants[i], { cursor: id });
      }
    }
    stats.marks += 1;
    bus.emit('reads:changed', { roomId: room });
    stats.posts += 1;
    return api('/api/rooms/' + encodeURIComponent(room) + '/reads', {
      method: 'POST', body: { cursor: id }
    }).then(function (data) {
      if (roomId !== room) { return; }
      apply(data);                 /* 서버 값이 정본이다 (내 추측을 덮는다) */
    })['catch'](function (err) {
      /* 실패해도 로컬 커서는 그대로 둔다 — 내가 읽은 것은 사실이고, 다음
         전진에서 다시 알린다. 조용히 넘기지 않고 콘솔에 남긴다. */
      if (env.console && env.console.warn) {
        env.console.warn('[gitwire-chat] 읽음 알림 실패 — ' + errText(err));
      }
    });
  }

  function schedule() {
    if (timer !== null) { return; }
    timer = win.setTimeout(flush, MARK_DEBOUNCE_MS);
  }

  /* 타임라인이 "이것까지 화면에 들어왔다"를 알려 준다. 단조 증가만 받는다. */
  function seen(id) {
    if (!id || !roomId) { return false; }
    /* ⭐ 커서는 **실제 봉투 ID 만** 받는다. 보내는 중인 낙관적 항목의 임시
       ID(`~pending/…`)가 여기로 들어와 커서가 됐고, 그것이 사전식 최대값이라
       단조 증가에 굳어 카운트가 영구히 0 이 됐다 (위 도크). 조용히 넘기지 않고
       콘솔에 남긴다 — 이 경로로 뭔가 들어오면 그것은 결함이다. */
    if (!isMessageId(id)) {
      if (env.console && env.console.warn) {
        env.console.warn('[gitwire-chat] 읽음 커서로 쓸 수 없는 ID 를 무시했다 — ' + id);
      }
      return false;
    }
    if (!visible()) { return false; }        /* 탭이 안 보이면 읽은 것이 아니다 */
    if (id <= pending) { return false; }
    if (model.cursor && id <= model.cursor) { return false; }
    pending = id;
    schedule();
    return true;
  }

  function count(msg) {
    return countUnread(model.participants, msg);
  }

  function mount() {
    bus.on('room:switch', function (e) {
      roomId = e.id;
      pending = '';
      if (timer !== null) { win.clearTimeout(timer); timer = null; }
      model = { me: '', person: '', cursor: '', firstUnread: null, participants: [] };
      return load(e.id);
    });
    /* 타임라인이 창을 그릴 때마다 알려 준다 (창 안에 무엇이 있는지 아는 곳). */
    bus.on('reads:seen', function (e) {
      if (e.roomId !== roomId) { return; }
      seen(e.id);
    });
    /* 서버가 미는 갱신 — 남의 커서가 움직였다 (`stream.js`). */
    bus.on('reads:state', function (e) {
      if (e.roomId !== roomId) { return; }
      apply(e.state);
    });
    /* 탭이 다시 보이면 그 순간 화면에 있는 것을 읽은 것으로 본다. 창 내용을
       아는 것은 타임라인이라 여기서는 **조르기만** 한다. */
    dom.on(dom.doc, 'visibilitychange', function () {
      if (visible()) { bus.emit('reads:ask', {}); }
    });
  }

  return {
    mount: mount,
    count: count,
    seen: seen,
    /* 지금 모델 (별도 사본이 아니다 — 소유자가 하나여야 한다). */
    model: function () { return model; },
    firstUnread: function () { return model.firstUnread; },
    me: function () { return model.me; },
    flushNow: flush,
    stats: stats
  };
}

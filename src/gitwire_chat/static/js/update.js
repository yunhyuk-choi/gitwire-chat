/*
 * 갱신 — `#update-bar` 와 그 안의 것들 **만** 소유한다.
 *
 * 하는 일이 셋이다:
 *   ① **묻는다** (`POST /api/update/check`) — ⭐ 사용자가 누를 때만.
 *   ② 무엇이 바뀌는지 보여주고 **확인을 받은 뒤 갱신을 시작시킨다**
 *      (`POST /api/update/run`).
 *   ③ ⭐ **서버를 잃는 구간을 버틴다.** 갱신 중에는 우리 서버가 죽는다. 그때
 *      "오류"를 띄우고 끝내지 않고 **기다리는 상태**로 있다가, 새 버전이 답하면
 *      알아서 새 화면으로 간다.
 *
 * ⭐ 여기서 앱을 갱신하지는 않는다. ② 는 **정본 CLI**
 * (`python -m gitwire_chat update`)를 서버가 분리된 프로세스로 띄우게 하는
 * 방아쇠일 뿐이다. 그 CLI 가 멈춤·설치·재기동·실패 시 되돌리기를 이미 한다 —
 * 화면이 그걸 다시 구현하지 않는다. 명령과 복사 버튼이 그대로 남아 있는 이유도
 * 같다: **앱이 아예 안 뜨는 상태**에서는 버튼이 없으므로 CLI 가 정본이어야 한다.
 *
 * 왜 이제는 버튼으로 실행하나 (예전 판단을 뒤집었다)
 * ------------------------------------------------
 * 예전 근거는 이랬다 — "루프백 · 인증 없음 설계에서 '누르면 앱을 죽이고 갈아
 * 치우는' 엔드포인트는 아무 웹페이지의 폼 전송 하나로 트리거되고 막을 수단이
 * 없다." **앞부분은 맞고 뒷부분이 틀렸다.** 드라이브바이는 로그인 없이도 막힌다:
 * 이 모듈은 요청에 커스텀 헤더(`X-Gitwire-Chat`)를 붙이고, 서버는 그것 +
 * 브라우저가 붙이는 `Sec-Fetch-Site` + `Origin` 을 본다. 평범한 HTML 폼은
 * 커스텀 헤더를 붙일 수 없고, 스크립트로 붙이면 크로스 오리진에서는 preflight
 * 가 먼저 막는다. 근거와 한계는 `gitwire_chat/csrf.py` 도크에 있다.
 *
 * 왜 주기적으로 원격을 보지 않나
 * -----------------------------
 * 확인은 `git ls-remote` 한 번 = 실제 네트워크 왕복이다. 그걸 폴링으로 돌리면
 * 로컬 앱이 사용자가 모르는 사이에 외부 호스트로 나가는 동작이 하나 생긴다.
 * 이 앱의 다른 어떤 것도 그렇게 하지 않는다(방 폴링은 사용자가 등록한 레포만
 * 본다). 그래서 **사용자가 누를 때만** 본다. 누르기 전에는 이 모듈이 네트워크를
 * 전혀 쓰지 않는다.
 *
 * 서버가 죽은 구간을 어떻게 버티나
 * -------------------------------
 * 시작시킨 뒤 `GET /api/version` 을 주기적으로 물어본다. 판정 기준은 **pid** 다:
 *   · 응답이 오고 pid 가 **그대로** → 아직 옛 서버다 (CLI 가 원격을 보는 중).
 *   · 요청이 **실패한다** → 서버가 멈췄다. 오류가 아니라 *예정된 구간*이다.
 *   · 응답이 오고 pid 가 **달라졌다** → 새 서버다 → 화면을 다시 불러온다.
 * 버전 문자열이나 자원 도장으로 가르지 않는다 — 같은 버전으로 여러 번 배포하고,
 * 정적 파일이 안 바뀌면 도장도 그대로다. 바뀌는 것은 프로세스뿐이다.
 * 새로고침이 새 파일을 받는 것은 자원 도장이 이미 해 준다(셸은 no-store).
 */

import { errText } from './dom.js';

/* 서버가 "우리 화면에서 온 요청인가"를 가르는 헤더. ⚠️ `gitwire_chat/csrf.py`
   와 **같은 문자열**이어야 한다 — 어긋나면 버튼이 403 만 받는다.
   그 일치는 `tests/test_updater.py` 가 기계로 확인한다. */
export var GUARD_HEADER = 'X-Gitwire-Chat';
export var GUARD_VALUE = 'update';

/* 새 서버가 떴나 물어보는 주기(ms). */
export var POLL_MS = 1000;

/* ⭐ 무한 대기 금지 — 이만큼 물어보고도 새 서버가 없으면 **포기하고 사람에게
   알린다.** 벽시계가 아니라 **물어본 횟수**로 세는 이유: 이 모듈은 시계를
   주입받지 않고, 횟수는 테스트가 그대로 셀 수 있다. 실제 경과 시간은 항상
   `POLL_MS × 횟수` **이상**이다(서버가 죽어 있으면 요청이 실패하는 데도 시간이
   걸린다) — 즉 넉넉한 쪽으로 틀린다. 일찍 포기하지 않는다. */
export var GIVE_UP_TRIES = 240;

/* 손으로 칠 명령 (서버가 안 알려줄 때의 마지막 폴백). */
var FALLBACK_COMMAND = 'python -m gitwire_chat update';

export function createUpdate(env) {
  var dom = env.dom;
  var api = env.api;
  var win = env.win;

  var button = dom.$('check-update');
  var note = dom.$('update-note');
  var command = dom.$('update-cmd');
  var copy = dom.$('copy-update-cmd');
  var compare = dom.$('update-compare');
  var runBtn = dom.$('run-update');
  var okBtn = dom.$('confirm-update');
  var noBtn = dom.$('cancel-update');

  /* 상태는 이것뿐이고 화면은 여기서만 나온다.
     idle(볼 것 없음) · asked(새 것이 있다) · confirm(확인 중) ·
     running(갱신 중 — 서버를 잃는 구간) · done(새 서버 도착) · stuck(포기했다) */
  var phase = 'idle';
  var busy = false;              /* 요청이 나가 있거나 갱신이 돌고 있다 */
  var last = null;               /* 마지막 확인 결과 */
  var beforePid = 0;             /* 갱신 전 서버 pid — 새 서버를 이걸로 가른다 */
  var tries = 0;
  var lost = false;              /* 서버가 실제로 응답을 멈춘 적이 있나 */
  var runLog = '';
  var timer = 0;

  function say(text, kind) {
    if (!note) { return; }
    dom.setText(note, text || '');
    note.className = 'update-note' + (kind ? ' ' + kind : '');
    if (text) { dom.show(note); } else { dom.hide(note); }
  }

  function showCommand(text) {
    if (!command) { return; }
    dom.setText(command, text || '');
    if (text) { dom.show(command); dom.show(copy); }
    else { dom.hide(command); dom.hide(copy); }
  }

  /* 무엇이 바뀌는지 사람이 볼 수 있는 링크 (서버가 줄 때만 — GitHub·GitLab 형태). */
  function showCompare(url) {
    if (!compare) { return; }
    if (url) { compare.href = url; dom.show(compare); }
    else { compare.href = ''; dom.hide(compare); }
  }

  /* 어느 버튼을 보이나 — 상태 하나에서 화면 하나가 나오게 **한 곳**에서 정한다. */
  function actions(which) {
    if (runBtn) { if (which === 'run') { dom.show(runBtn); } else { dom.hide(runBtn); } }
    if (okBtn) { if (which === 'confirm') { dom.show(okBtn); } else { dom.hide(okBtn); } }
    if (noBtn) { if (which === 'confirm') { dom.show(noBtn); } else { dom.hide(noBtn); } }
  }

  function lock(on) {
    if (button) { button.disabled = !!on; }
    if (runBtn) { runBtn.disabled = !!on; }
    if (okBtn) { okBtn.disabled = !!on; }
  }

  function short(sha) {
    return sha ? String(sha).slice(0, 12) : '알 수 없음';
  }

  function commandText() {
    return (last && last.command) || FALLBACK_COMMAND;
  }

  function hintOf(err) {
    var hint = err && err.payload && err.payload.hint;
    return hint ? ' (' + hint + ')' : '';
  }

  /* 함께 쓰는 git 라이브러리(gitwire)가 밀렸으면 그것도 말해 준다.

     ⚠️ 이게 없으면 "앱은 최신인데 갱신할 것이 있다"가 설명 없는 화면이 된다 —
     앱 커밋이 그대로라 `installed → remote` 가 **같은 값**으로 보인다. 무엇이
     왜 갱신되는지 사용자가 알아야 한다. 서버가 주는 `deps` 가 그 원천이다. */
  function depsText(list) {
    var out = [];
    var i;
    var dep;
    for (i = 0; i < (list || []).length; i += 1) {
      dep = list[i] || {};
      if (dep.behind) {
        out.push(dep.name + ' ' + short(dep.installed) + ' → ' + short(dep.remote));
      }
    }
    return out.join(' · ');
  }

  function render(data) {
    last = data || {};
    if (!last.behind) {
      phase = 'idle';
      showCommand('');
      showCompare('');
      actions('');
      say('최신이다. (설치본 ' + short(last.installed) + ')');
      return;
    }
    phase = 'asked';
    var deps = depsText(last.deps);
    var head;
    if (last.self_behind === false) {
      /* 앱은 최신이고 라이브러리만 밀린 경우 — 커밋 두 개를 나란히 보여주면
         같은 값이라 오히려 혼란스럽다. 무엇이 밀렸는지만 말한다. */
      head = '앱은 최신이지만 함께 쓰는 라이브러리가 밀렸다';
    } else {
      head = '새 버전이 있다 — ' + short(last.installed) + ' → ' + short(last.remote);
    }
    if (deps) { head += ' (' + deps + ')'; }
    say(head + '. 「지금 갱신」을 누르면 멈추고 · 갈아치우고 · 다시 뜬다. ' +
        '터미널에서 직접 하려면 아래 명령이다.', 'new');
    showCommand(commandText());
    showCompare(last.compare);
    actions('run');
  }

  /* ⭐ 사용자가 누를 때만 나간다. 이 함수 밖에서는 아무도 이걸 부르지 않는다. */
  function check() {
    if (busy) { return; }
    busy = true;
    lock(true);
    say('원격을 확인하는 중…');
    showCommand('');
    showCompare('');
    actions('');
    return api('/api/update/check', { method: 'POST' })
      .then(function (data) { render(data || {}); })
      ['catch'](function (err) {
        /* 조용히 실패하지 않는다 — 서버가 함께 준 힌트까지 보여준다. */
        phase = 'idle';
        say('확인하지 못했다 — ' + errText(err) + hintOf(err), 'bad');
      })
      .then(function () {
        busy = false;
        lock(false);
      });
  }

  /* 「지금 갱신」 — 아직 아무것도 하지 않는다. **무엇이 바뀌는지 보여주고 묻는다.** */
  function ask() {
    if (phase !== 'asked' || busy || !last) { return; }
    phase = 'confirm';
    var deps = depsText(last.deps);
    var what = last.self_behind === false
      ? '정말 갱신한다 — 함께 쓰는 라이브러리를 올린다'
      : '정말 갱신한다 — ' + short(last.installed) + ' → ' + short(last.remote);
    say(what + (deps ? ' (' + deps + ')' : '') + '. ' +
        '앱이 잠깐 멈추고 새 버전으로 다시 뜬다 (보통 수십 초). ' +
        '이 화면은 기다렸다가 알아서 새 화면으로 간다. 보내던 말이 있으면 먼저 끝내라.',
        'new');
    actions('confirm');
  }

  function cancel() {
    if (phase !== 'confirm') { return; }
    render(last);
  }

  /* 「갱신한다」 — 여기서 처음으로 상태가 바뀐다. */
  function run() {
    if (phase !== 'confirm' || busy) { return; }
    busy = true;
    lock(true);
    actions('');
    say('갱신을 시작한다…', 'new');
    var options = { method: 'POST', headers: {} };
    options.headers[GUARD_HEADER] = GUARD_VALUE;
    return api('/api/update/run', options).then(function (data) {
      data = data || {};
      if (!data.started) {
        /* **바뀔 게 없다** — 서버가 아무것도 띄우지 않았다. 그렇게 말하고 끝. */
        busy = false;
        lock(false);
        render(data.check || { behind: false, installed: last && last.installed });
        return;
      }
      beforePid = (data.serving && data.serving.pid) || 0;
      runLog = (data.run && data.run.log) || '';
      last = data.check || last;
      phase = 'running';
      tries = 0;
      lost = false;
      showCommand(commandText());
      showCompare(last && last.compare);
      say('갱신 중이다 — 멈추고 · 갈아치우고 · 다시 띄운다. 이 화면은 기다린다. ' +
          '새 버전이 답하면 알아서 새 화면으로 간다.', 'new');
      schedule();
    })['catch'](function (err) {
      /* 못 시작한 경우다 (거절 · 이미 돌고 있다 · 대장에 없다 · 출처 불명…).
         서버가 준 사유와 힌트를 그대로 드러내고, 칠 명령을 남긴다. */
      busy = false;
      lock(false);
      phase = last && last.behind ? 'asked' : 'idle';
      say('갱신을 시작하지 못했다 — ' + errText(err) + hintOf(err), 'bad');
      showCommand(commandText());
      actions(phase === 'asked' ? 'run' : '');
    });
  }

  function schedule() {
    if (timer) { win.clearTimeout(timer); }
    timer = win.setTimeout(function () { timer = 0; poll(); }, POLL_MS);
  }

  /* ⭐ 서버를 잃는 구간. 여기서 요청 실패는 **오류가 아니라 예정된 상태**다. */
  function poll() {
    if (phase !== 'running') { return; }
    tries += 1;
    return api('/api/version').then(
      function (seen) {
        if (phase !== 'running') { return; }
        var pid = (seen && seen.pid) || 0;
        if (pid && beforePid && pid !== beforePid) { return arrived(seen); }
        /* pid 를 못 받은 경우의 폴백 — 한 번 죽었다가 다시 답하기 시작했으면
           그것을 도착으로 본다 (모르는 것을 안다고 말하지 않되, 멈추지도 않는다). */
        if (!beforePid && lost) { return arrived(seen); }
        return waiting('아직 옛 서버가 답한다 — 멈추기를 기다린다');
      },
      function () {
        if (phase !== 'running') { return; }
        lost = true;
        return waiting('서버가 멈췄다 — 새 버전이 뜨기를 기다린다');
      }
    );
  }

  function waiting(text) {
    if (tries >= GIVE_UP_TRIES) { return giveUp(); }
    say(text + ' (' + tries + '번 확인했다)', 'new');
    schedule();
  }

  function arrived(seen) {
    phase = 'done';
    busy = false;
    say('새 버전이 떴다 — 화면을 다시 불러온다.', 'new');
    /* 자원 URL 에 내용 도장이 박혀 있고 셸은 캐시하지 않으므로, 그냥 다시
       불러오면 새 JS·CSS 가 내려온다 (강력 새로고침 불필요). */
    try {
      win.location.reload();
    } catch (err) {
      say('새 버전이 떴다 — 화면을 직접 새로고침해라.', 'new');
    }
  }

  /* ⭐ 안 돌아오면 포기하고 **사람에게 알린다.** 무한 대기 금지. */
  function giveUp() {
    phase = 'stuck';
    busy = false;
    lock(false);
    actions('');
    say('새 버전이 ' + GIVE_UP_TRIES + '번 확인하는 동안 답하지 않았다 — ' +
        '기다리기를 그만둔다. ' +
        (lost ? '서버가 멈춘 뒤 돌아오지 않았다. ' : '서버가 멈추지도 않았다. ') +
        '아래 명령을 터미널에서 실행하면 무엇이 걸렸는지 말해 주고, 실패했으면 ' +
        '되돌리는 방법까지 보여준다.' +
        (runLog ? ' 갱신 기록: ' + runLog : ''), 'bad');
    showCommand(commandText());
  }

  function copyCommand() {
    var text = command ? command.textContent : '';
    if (!text) { return; }
    var clip = env.win && env.win.navigator && env.win.navigator.clipboard;
    if (!clip || !clip.writeText) {
      say('이 브라우저에서는 복사할 수 없다 — 위 명령을 직접 골라 복사한다.', 'bad');
      return;
    }
    return clip.writeText(text).then(
      function () { say('복사했다. 터미널에 붙여 넣으면 된다.'); },
      function () { say('복사하지 못했다 — 위 명령을 직접 골라 복사한다.', 'bad'); }
    );
  }

  function mount() {
    dom.on(button, 'click', function () { check(); });
    dom.on(copy, 'click', function () { copyCommand(); });
    dom.on(runBtn, 'click', function () { ask(); });
    dom.on(okBtn, 'click', function () { run(); });
    dom.on(noBtn, 'click', function () { cancel(); });
  }

  return {
    mount: mount,
    check: check,
    copyCommand: copyCommand,
    ask: ask,
    cancel: cancel,
    run: run,
    poll: poll,
    phase: function () { return phase; }
  };
}

/*
 * 갱신 알림 — `#update-bar` 와 그 안의 것들 **만** 소유한다.
 *
 * ⭐ 여기서 하는 일은 딱 하나다: **"새 것이 있나"를 물어보고, 있으면 칠 명령을
 * 보여준다.** 앱이 자기를 갱신하지 않는다.
 *
 * 왜 버튼으로 갱신을 실행하지 않나
 * --------------------------------
 * 이 앱은 루프백 전용 · **인증 없음**이 설계다. 여기에 "누르면 앱을 죽이고
 * 패키지를 갈아치우는" 엔드포인트를 두면, 브라우저로 아무 웹페이지나 열어 둔
 * 상태에서 그 페이지가 우리 서버에 요청 하나를 보내(폼 전송만으로도 된다) 앱을
 * 재설치·재기동시킬 수 있다. 인증이 없으니 막을 방법도 없다. 얻는 것이 "터미널을
 * 안 여는 편의" 하나인데 내주는 것이 그것이라 하지 않았다.
 *
 * 그리고 **CLI 가 정본**이다 (`python -m gitwire_chat update`). 앱이 아예 안 뜨는
 * 상태에서도 갱신·복구가 되어야 하는데, 그 경로가 버튼이면 성립하지 않는다.
 * 여기 있는 것은 그 정본을 **가리키는 안내**다.
 *
 * 왜 주기적으로 원격을 보지 않나
 * -----------------------------
 * 확인은 `git ls-remote` 한 번 = 실제 네트워크 왕복이다. 그걸 폴링으로 돌리면
 * 로컬 앱이 사용자가 모르는 사이에 외부 호스트로 나가는 동작이 하나 생긴다.
 * 이 앱의 다른 어떤 것도 그렇게 하지 않는다(방 폴링은 사용자가 등록한 레포만
 * 본다). 그래서 **사용자가 누를 때만** 본다. 누르기 전에는 이 모듈이 네트워크를
 * 전혀 쓰지 않는다.
 */

import { errText } from './dom.js';

export function createUpdate(env) {
  var dom = env.dom;
  var api = env.api;

  var button = dom.$('check-update');
  var note = dom.$('update-note');
  var command = dom.$('update-cmd');
  var copy = dom.$('copy-update-cmd');
  var busy = false;

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

  function render(data) {
    if (!data.behind) {
      showCommand('');
      say('최신이다. (설치본 ' + short(data.installed) + ')');
      return;
    }
    say('새 버전이 있다 — ' + short(data.installed) + ' → ' + short(data.remote) +
        '. 아래 명령을 터미널에서 실행하면 멈추고 · 갈아치우고 · 다시 뜬다.', 'new');
    showCommand(data.command || 'python -m gitwire_chat update');
  }

  function short(sha) {
    return sha ? String(sha).slice(0, 12) : '알 수 없음';
  }

  /* ⭐ 사용자가 누를 때만 나간다. 이 함수 밖에서는 아무도 이걸 부르지 않는다. */
  function check() {
    if (busy) { return; }
    busy = true;
    if (button) { button.disabled = true; }
    say('원격을 확인하는 중…');
    showCommand('');
    return api('/api/update/check', { method: 'POST' })
      .then(function (data) { render(data || {}); })
      ['catch'](function (err) {
        /* 조용히 실패하지 않는다 — 서버가 함께 준 힌트까지 보여준다. */
        var hint = err && err.payload && err.payload.hint;
        say('확인하지 못했다 — ' + errText(err) + (hint ? ' (' + hint + ')' : ''), 'bad');
      })
      .then(function () {
        busy = false;
        if (button) { button.disabled = false; }
      });
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
  }

  return { mount: mount, check: check, copyCommand: copyCommand };
}

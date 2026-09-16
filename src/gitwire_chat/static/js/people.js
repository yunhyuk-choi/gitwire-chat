/*
 * 사람을 보여 주는 두 자리 — 오른쪽 **참여자 서랍**(`#people…`)과 **작성자 정보
 * 카드**(`#user-card…`)를 소유한다. 여는 길은 **방 이름 클릭 하나**이고, 그
 * 노드는 방 목록 모듈 것이라 신호(`people:toggle`)로만 만난다.
 *
 * ⭐ 둘을 한 모듈에 둔 이유: **같은 값의 두 크기**다. 서랍은 전체를 훑고, 카드는
 * 그 중 한 명을 말한다. 같은 모델(`reads.js` 의 커서 지도)에서 같은 규칙으로
 * 뽑으므로, 갈라 두면 "서랍과 카드가 다른 말을 한다"가 생길 자리가 생긴다.
 *
 * ⭐ **모델을 소유하지 않는다.** 상태·커서의 주인은 `reads.js` 하나이고(서버
 * 스냅샷 그대로), 여기는 그것을 읽어 그릴 뿐이다. 그래서 갱신 경로가 하나다 —
 * 폴링이든 SSE 든 내 POST 응답이든 전부 `reads:changed` 로 도착한다.
 *
 * ⚠️ 이 모듈이 **메시지 노드를 만지지 않는다.** 카드는 화면에 **한 장**뿐이고
 * 위치만 옮겨 다닌다. 메시지마다 카드를 달면 그 방의 메시지 수만큼 헛노드가
 * 생기고, 누군가의 상태가 바뀔 때 N 장을 다시 칠해야 한다 — 읽음 카운트를
 * 메시지에 저장하지 않는 것과 같은 이유다.
 *
 * ⚠️ 줄마다 점을 찍지 않는 것도 같은 판단이다 (`log`·`tty` 는 줄마다, `ide` 는
 * 묶음마다 작성자가 나온다). 상태는 자주 바뀌는데 그 표시를 N 벌 두면 무효화가
 * N 개로 불어난다. 거짓말이라서가 아니라 **중복**이라서 안 넣는다.
 */

import { participantOf, cursorTime } from './reads.js';
import { statusLabel, DEFAULT_STATUS, normalizeStatus } from './userstatus.js';
import { timeLabel } from './dom.js';

export function createPeople(env) {
  var dom = env.dom;
  var bus = env.bus;

  var el = {
    drawer: dom.$('people'),
    list: dom.$('people-list'),
    empty: dom.$('people-empty'),
    close: dom.$('people-close'),
    title: dom.$('people-title'),
    card: dom.$('user-card'),
    cardName: dom.$('user-card-name'),
    cardStatus: dom.$('user-card-status'),
    cardRead: dom.$('user-card-read')
  };

  /* 지금 카드가 누구 것인가 (같은 이름을 다시 누르면 닫기 — 터치용). */
  var cardFor = '';

  var EMPTY = { me: '', participants: [] };

  function model() {
    var mod = env.reads ? env.reads() : null;
    var got = mod && mod.model ? mod.model() : null;
    return got || EMPTY;
  }

  /* 참가자 하나 → 사람이 읽는 두 조각. 상태는 **모르는 값이면 기본값**으로
     떨어진다 (`normalizeStatus`) — 나보다 새 버전이 쓴 네 번째 상태일 수 있다. */
  function statusOf(p) {
    return normalizeStatus(p && p.status ? p.status : DEFAULT_STATUS);
  }

  /* 마지막으로 읽은 시각. 커서가 없으면 빈 문자열 — **없는 것을 지어내지 않는다.**
     (커서가 없다 = 이 방에서 아직 아무것도 읽지 않았다.) */
  function readLabel(p) {
    var when = cursorTime(p && p.cursor);
    return when ? timeLabel(when) + ' 까지 읽음' : '아직 읽은 표시가 없다';
  }

  /* ------------------------------------------------------------ 서랍 */

  function paint() {
    var data = model();
    var people = data.participants || [];
    /* 사람 수는 **서랍 제목**에 얹는다. 머리에 숫자 버튼을 따로 두면 서랍을 여는
       길이 둘이 되고(같은 일을 하는 두 진입점), 그건 사용자가 매번 "뭐가 다르지"를
       판단해야 하는 비용이다. 숫자는 서랍을 연 사람에게만 필요하다. */
    if (el.title) {
      dom.setText(el.title, people.length ? '참여자 ' + people.length : '참여자');
    }
    if (!el.list) { return; }
    /* ⭐ **닫혀 있으면 그리지 않는다.** 이 함수는 남의 커서가 움직일 때마다
       불리는데(내 스크롤 한 번에도 불린다), 화면에 없는 목록을 그때마다 다시
       만들면 사람 수만큼의 노드를 계속 버리고 만든다. 열 때 한 번 그리면 된다 —
       가상 스크롤이 창 밖 메시지를 DOM 에 두지 않는 것과 같은 판단이다. */
    if (el.drawer && el.drawer.hidden) { return; }
    /* 참여자 목록은 대화가 아니다 — 짧고, 바뀔 때만 통째로 다시 그린다
       (방 목록과 같은 규율. append-only 규율은 **메시지**에 대한 것이다). */
    el.list.replaceChildren();
    for (var i = 0; i < people.length; i++) {
      var p = people[i];
      var id = statusOf(p);
      var li = dom.make('li', 'person');
      li.setAttribute('data-status', id);
      var dot = dom.make('span', 'status-dot', '●');
      dot.setAttribute('aria-hidden', 'true');
      li.appendChild(dot);
      var name = dom.make('span',
        p.key === data.me ? 'person-name me' : 'person-name',
        p.person || p.key);
      li.appendChild(name);
      li.appendChild(dom.make('span', 'person-status', statusLabel(id)));
      /* ⭐ 읽은 자리도 함께 보여 준다 — **같은 파일에서 오는 값**이라 배관이 늘지
         않고, 상태만으로는 "자리 비움인데 방금 읽었다"를 알 수 없다. */
      li.appendChild(dom.make('span', 'person-read', readLabel(p)));
      el.list.appendChild(li);
    }
    if (people.length) { dom.hide(el.empty); } else { dom.show(el.empty); }
  }

  function openDrawer(on) {
    if (!el.drawer) { return; }
    if (on) { dom.show(el.drawer); paint(); } else { dom.hide(el.drawer); }
    /* 열면 초점을 서랍 안으로 옮긴다 — 키보드로 들어왔으면 그 다음 키가 서랍에
       닿아야 한다(그리고 Esc·닫기 버튼이 나오는 길이다). */
    if (on && el.close && el.close.focus) { el.close.focus(); }
  }

  function toggleDrawer() {
    if (!el.drawer) { return; }
    openDrawer(el.drawer.hidden);
  }

  /* ------------------------------------------------------------ 카드 */

  /* 이 말을 쓴 사람의 참가자 파일. 짝짓기 규칙은 읽음 카운트와 **같은 것**이다
     (봉투에는 설치본 식별자만 있다 — `reads.js` 의 `participantOf`).

     ⚠️ 아직 봉투가 없는 낙관적 항목(`~pending/…`)은 `sender` 가 비어 있다.
     그건 정의상 내 것이라 내 파일을 쓴다. */
  function ownerOf(data, msg) {
    var found = participantOf(data.participants, msg && msg.sender);
    if (found) { return found; }
    if (msg && msg.mine && data.me) {
      for (var i = 0; i < data.participants.length; i++) {
        if (data.participants[i].key === data.me) { return data.participants[i]; }
      }
    }
    return null;
  }

  /* 카드를 이름 **바로 아래**에 놓는다. 창을 넘지 않게만 민다.
     ⚠️ 위치를 픽셀로 계산하는 유일한 자리다 — 카드가 `fixed` 라 화면 좌표를 쓴다
     (대화 영역은 스크롤 상자라 그 안에 두면 잘리거나 함께 스크롤된다). */
  function place(at) {
    if (!el.card || !at || !at.getBoundingClientRect) { return; }
    var box = at.getBoundingClientRect();
    var width = (env.win && env.win.innerWidth) || 0;
    var left = box.left;
    if (width) { left = Math.max(4, Math.min(left, width - 244)); }
    el.card.style.left = Math.round(left) + 'px';
    el.card.style.top = Math.round(box.bottom + 4) + 'px';
  }

  function showCard(msg, at) {
    if (!el.card || !msg) { return false; }
    var data = model();
    var owner = ownerOf(data, msg);
    var id = owner ? statusOf(owner) : '';
    el.card.setAttribute('data-status', id || 'unknown');
    dom.setText(el.cardName, msg.author || (owner ? owner.person : '') || '?');
    /* 참가자 파일이 없는 사람은 **모른다고 말한다.** 기본값으로 칠하면 이 방에
       아직 들어온 적 없는 사람이 `활동 중` 으로 보인다. */
    dom.setText(el.cardStatus, owner ? statusLabel(id) : '상태를 모른다');
    dom.setText(el.cardRead, owner ? readLabel(owner) : '');
    place(at);
    dom.show(el.card);
    cardFor = msg.id || '';
    return true;
  }

  function hideCard() {
    cardFor = '';
    dom.hide(el.card);
  }

  function mount() {
    openDrawer(false);
    hideCard();
    paint();

    dom.on(el.close, 'click', function () { openDrawer(false); });
    /* ⭐ **유일한 진입점** — 방 이름을 눌렀다는 신호다. 그 노드의 주인이 방 목록
       모듈이라 여기서 직접 만지지 않는다 (남의 노드를 만지지 않는다). */
    bus.on('people:toggle', toggleDrawer);

    /* 모델이 바뀌면 다시 그린다 — 내 POST 응답·폴링·SSE 가 전부 이 하나로 온다. */
    bus.on('reads:changed', function () {
      paint();
      /* 카드가 떠 있으면 그 안의 상태도 같이 낡는다. 위치는 그대로 두고 값만
         다시 넣는 것이 정확하지만, 카드는 **한 장**이라 그냥 닫는다 — 호버를
         유지한 채 상태가 바뀌는 순간은 드물고, 닫히면 다시 올리면 된다. */
      if (cardFor) { hideCard(); }
    });
    bus.on('room:switch', function () {
      hideCard();
      paint();
    });

    /* 작성자 이름에 포인터가 올라왔다 (`message-node.js` 가 네 배치 **공통**으로
       배선한다 — 배치별 코드가 없다). 같은 이름을 다시 누르면 닫힌다(터치). */
    bus.on('people:card', function (e) {
      if (!e || !e.message) { return; }
      if (e.show === false) { hideCard(); return; }
      if (e.toggle && cardFor === e.message.id) { hideCard(); return; }
      showCard(e.message, e.at);
    });

    dom.on(dom.doc, 'keydown', function (e) {
      if (e && e.key === 'Escape') { hideCard(); openDrawer(false); }
    });
  }

  return {
    mount: mount,
    paint: paint,
    open: function (on) { openDrawer(on !== false); },
    isOpen: function () { return !!(el.drawer && !el.drawer.hidden); },
    card: function () {
      return el.card && !el.card.hidden
        ? { name: el.cardName.textContent, status: el.cardStatus.textContent,
          read: el.cardRead.textContent, of: cardFor }
        : null;
    }
  };
}

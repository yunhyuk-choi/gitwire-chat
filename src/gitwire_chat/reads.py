"""읽음 표시 — **커서 하나**로 두 가지 사실을 만든다.

두 기능이고 성격이 다르다 (섞으면 둘 다 이상해진다)
--------------------------------------------------
| | ① 방 목록의 **내가 안 읽은 개수** | ② 방 안의 **남이 안 읽은 수** |
|---|---|---|
| 누구 것 | 나 | 남 (참가자 전체) |
| 발행 | **없다** — 내 컴퓨터에만 있으면 된다 | 필요하다 (남이 알아야 한다) |
| 저장 | 채널 디렉토리의 로컬 커서 (`cursors/read.json`) | 예약 경로 `participants/<키>.json` |
| 비용 | git 0회 (로컬 파일 + 캐시된 나열) | 폴 주기마다 캐시된 나열 1회 |

⭐ 카운트를 **메시지마다 저장하지 않는다**
----------------------------------------
사람당 커서 하나만 두고 카운트를 **파생**시킨다::

    메시지 M(작성자 A) 의 카운트 = |{ p ∈ 참가자, p ≠ A, cursor(p) < M }|

* 상태가 **O(사람 수)** 다 — 메시지가 몇만이어도 늘어나지 않는다.
* 누군가의 커서가 전진하면 **그 아래 메시지들의 카운트가 한꺼번에 줄어든다.**
  저장값이면 하나하나 갱신해야 하고, 그 갱신이 곧 append-only 매체에 쓰는
  일이라 개수만큼 커밋이 생긴다.
* 그래서 이 파일에는 **판정에 필요한 값**(커서·참가자 집합)만 있고, 카운트라는
  숫자는 어디에도 저장되지 않는다. 화면에서 매번 계산한다 (`static/js/reads.js`).

⚠️ 발행은 **레코드가 아니다.** append-only 로 적으면 그 사람의 *현재* 커서를
알기 위해 마지막 read 레코드까지 과거로 스캔해야 한다 — 2주 안 읽은 사람이면
2주치다. keyset 페이징으로 없앤 전량 스캔이 그대로 되살아난다. 그래서 기반의
**참가자 상태 예약 경로**(`gitwire.state`)에 쓴다: 파일 하나 = 사람 하나, 덮어쓴다.

식별자 = `git user.email` (**사람** 단위)
----------------------------------------
설치본 식별자(`installation_id`)를 쓰면 한 사람이 노트북·데스크탑을 쓸 때
**참가자 2명**으로 세어져 카운트가 0 이 되지 않는다. 그래서 사람 단위 키를 쓴다 —
기반이 이미 `git config user.email` 을 안다 (`gitwire.git_email`).

같은 사람의 두 기기는 **같은 파일**을 공유하므로 값이 자연히 `max` 로 합쳐진다
(쓰기 전에 저장된 값을 읽어 `max` 를 취한다 — 아래 `publish`). 경로가 겹치는
그 한 경우의 push 경합은 기반이 해소한다 (gitwire README 「참가자별 가변 상태」).

커서는 **단조 증가**한다
-----------------------
뒤로 가면 카운트가 **늘어나** 사람 눈에 고장으로 보인다. 그래서 로컬·발행 양쪽
모두 항상 `max()` 다. 되돌리는 API 를 두지 않는다.

⭐ 커서는 **실제 봉투 ID 만** 받는다 (`gitwire.is_record_id`)
-----------------------------------------------------------
단조 증가에는 함정이 하나 있다 — **오염된 값이 최대값이면 되돌릴 수 없다.**
실측된 사고: 화면의 낙관적 임시 ID(`~pending/000004`)가 커서로 저장돼 원격까지
올라갔다. `~`(0x7E) 가 `records/`(0x72…) 보다 사전식으로 뒤라 그 값은 **모든**
실제 ID 보다 크고, 커서가 항상 `max()` 이므로 그 뒤로는 어떤 실제 ID 도 커서를
전진시키지 못했다. 결과는 조용한 전면 고장이었다:

* 내 뱃지 — `rid <= cursor` 가 모든 레코드에 참이라 **안 읽은 개수가 영구히 0**.
* 방 안 카운트 — `cursor(p) < M` 이 모든 참가자에게 거짓이라 **영구히 0**
  (= 아무 숫자도 화면에 뜨지 않는다).

그래서 이 모듈은 커서를 **두 방향에서** 지킨다:

| 방향 | 규율 |
|---|---|
| **쓰기** (내가 전진) | 형식을 검증하고 아니면 **거부한다**(`InvalidCursor`). 조용히 저장하지 않는다 — HTTP 400 으로 화면에 드러난다 |
| **읽기** (저장된 값·남의 값) | 형식이 아닌 값은 **커서 없음으로 취급한다**(`sane_cursor`). 이미 원격에 올라간 오염 값을 사람 손으로 고치지 않아도 다음 순간부터 카운트가 정상으로 돌아온다 |

읽기 쪽을 "없음"으로 떨구는 이유: 안 읽음이 **과다**로 보이는 것은 사람이 알아채고
스크롤하면 사라진다. 반대(조용히 다 읽음)는 아무도 못 알아챈다.

유령 참가자는 그냥 센다
----------------------
설치했지만 앱을 안 켜는 사람의 커서는 움직이지 않고, 그러면 카운트가 영구히
≥1 로 남는다. **그게 사실이므로 특별 처리하지 않는다.** 오래 안 움직인 커서를
분모에서 빼면 조용히 "다 읽음"이 되는데 그게 더 나쁘다.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable

import gitwire

log = logging.getLogger(__name__)

#: 발행되는 값(`participants/<키>.json` 의 `value`)의 스키마.
#: 기반은 이 안을 해석하지 않는다 — 레코드 payload 와 같은 경계다.
CURSOR_KIND = "read-cursor"
CURSOR_SCHEMA = 1

#: **내** 읽은 위치용 로컬 커서의 소비자 이름. 폴러가 쓰는 `chat` 과 다른 이름을
#: 쓴다 — 기반이 소비자별로 커서를 나눠 주므로(`gitwire.CursorStore`) 새 저장
#: 규약을 만들 필요가 없다. 이 커서는 **발행되지 않는다.**
LOCAL_CONSUMER = "read"

#: 뱃지에 그대로 싣기엔 큰 수. 화면 표기는 브라우저가 정한다 (여기서는 세기만).
MAX_BADGE = 999


#: 사람 식별자를 **명시적으로** 지정하는 환경변수. 기반의 `GITWIRE_SENDER` 와 같은
#: 성격이다 — git 신원을 넣을 수 없는 환경(컨테이너)과, 한 머신에서 서로 다른
#: 사람으로 두 인스턴스를 띄워야 하는 경우(테스트·공용 PC)를 위한 문이다.
PERSON_ENV = "GITWIRE_CHAT_PERSON"


class InvalidCursor(ValueError):
    """커서로 받을 수 없는 값이다 (실제 봉투 ID 가 아니다).

    **쓰기 경로에서만** 던진다. 읽기는 관대하게 — `sane_cursor` 가 떨군다.
    """


def sane_cursor(cursor: Any, *, where: str = "") -> str:
    """읽어 들인 커서를 **믿을 수 있는 값으로만** 좁힌다. 아니면 빈 문자열.

    형식 판정은 ID 의 주인인 기반이 한다 (`gitwire.is_record_id`) — 여기서
    `records/` 접두를 손으로 세면 두 곳이 어긋난다.

    ⚠️ 조용히 넘기지 않는다 — 떨굴 때마다 로그를 남긴다. 이 값은 원격에 올라가
    있을 수 있고(다른 참가자·과거 버전의 나), 그 사실을 사람이 알아야 한다.
    """
    value = str(cursor or "").strip()
    if not value:
        return ""
    if gitwire.is_record_id(value):
        return value
    log.warning(
        "커서가 실제 봉투 ID 가 아니다 — 커서 없음으로 취급한다%s: %r",
        f" ({where})" if where else "",
        value,
    )
    return ""


def person_id(home=None, *, runner=None) -> str:
    """이 **사람**의 식별자. `git config user.email` 이 정본이다.

    우선순위: `GITWIRE_CHAT_PERSON` → `git config user.email` → 설치본 식별자.

    마지막으로 떨어지면 조용히 넘어가지 않는다 — 로그를 남긴다. 그 경우 같은
    사람의 두 기기가 두 참가자로 세어지고 카운트가 0 이 되지 않는다(정직한
    열화이며, 고치는 방법은 git 신원을 넣거나 위 환경변수를 주는 것).
    """
    explicit = (os.environ.get(PERSON_ENV) or "").strip()
    if explicit:
        return explicit
    email = ""
    try:
        email = gitwire.git_email()
    except Exception:  # noqa: BLE001 — git 이 없을 수도 있다
        email = ""
    email = (email or "").strip()
    if email:
        return email
    fallback = gitwire.installation_id(home, runner=runner) if home else ""
    log.warning(
        "git user.email 을 읽을 수 없다 — 읽음 표시가 **사람**이 아니라 설치본 "
        "단위로 세어진다 (`git config --global user.email` 을 넣으면 해소된다)"
    )
    return fallback or "anon"


@dataclass(frozen=True)
class ReadCursor:
    """참가자 한 명의 읽은 위치 (발행되는 값)."""

    person: str
    """원본 식별자 (보통 이메일). 파일명 슬러그는 `key` 다."""

    key: str
    cursor: str = ""
    """마지막으로 읽은 메시지 ID (= gitwire 레코드 ID). 빈 문자열 = 아직 없음."""

    senders: tuple[str, ...] = ()
    """이 사람이 쓰는 봉투 `sender` 목록 (= 설치본들).

    카운트 공식의 `p ≠ A`(작성자 제외)를 판정하는 유일한 근거다. 봉투에는 설치본
    식별자만 있고 사람 키가 없으므로, 각자 자기 파일에 자기 설치본을 적어 둔다 —
    **자기 것만 쓰므로** 이 목록도 단일 쓰기자 규율 안에 있다.
    """

    updated_at: str = ""

    def to_json(self) -> dict:
        return {
            "person": self.person,
            "key": self.key,
            "cursor": self.cursor,
            "senders": list(self.senders),
            "updated_at": self.updated_at,
        }


def build_value(cursor: str, senders: Iterable[str]) -> dict:
    """발행할 `value` 를 만든다 (스키마 버전을 항상 싣는다)."""
    return {
        "kind": CURSOR_KIND,
        "v": CURSOR_SCHEMA,
        "cursor": cursor or "",
        "senders": sorted({s for s in senders if s}),
    }


def _state_label(state: Any) -> str:
    """로그에 쓸 참가자 식별 문자열 (실패해도 절대 던지지 않는다).

    ⚠️ 기반의 `gitwire.state_key`(신원 → 파일명 슬러그)와 **다른 것**이다 —
    여기 것은 사람이 읽을 라벨뿐이라 이름을 헷갈리지 않게 갈라 둔다.
    """
    return str(getattr(state, "identity", "") or getattr(state, "key", "") or "?")


def parse_state(state: Any) -> ReadCursor | None:
    """기반의 `ParticipantState` → `ReadCursor`. **절대 예외를 던지지 않는다.**

    남이 쓴 파일이고, 우리보다 새 버전이 쓴 것일 수 있다. 모르는 종류는 그냥
    건너뛴다 — 한 사람의 파일 때문에 방 전체의 읽음 표시가 죽지 않게.
    """
    value = getattr(state, "value", None)
    if not isinstance(value, dict):
        return None
    if str(value.get("kind") or CURSOR_KIND) != CURSOR_KIND:
        return None
    # ⭐ 남이 쓴 커서도 **형식이 맞아야** 커서다. 오염된 값(과거 버전이 올린
    # `~pending/…`)을 그대로 쓰면 그 사람은 "모든 메시지를 읽은 사람"이 되어
    # 카운트에서 조용히 사라진다 — 없음으로 떨궈 안 읽은 것으로 센다.
    cursor = sane_cursor(value.get("cursor"), where=f"참가자 {_state_label(state)}")
    senders = value.get("senders")
    if not isinstance(senders, list):
        senders = []
    when = getattr(state, "updated_at", None)
    return ReadCursor(
        person=str(getattr(state, "identity", "") or getattr(state, "key", "")),
        key=str(getattr(state, "key", "")),
        cursor=str(cursor or ""),
        senders=tuple(sorted({str(s) for s in senders if s})),
        updated_at=when.isoformat().replace("+00:00", "Z") if when else "",
    )


@dataclass
class ReadView:
    """방 하나의 읽음 상태 스냅샷 — API·SSE·테스트가 **같은 값**을 쓴다."""

    me: str = ""
    """내 참가자 키 (파일명 슬러그)."""

    person: str = ""
    """내 원본 식별자."""

    cursor: str = ""
    """내 로컬 커서 (= 내가 읽은 위치). 발행값이 아니라 **로컬**이 정본이다."""

    unread: int = 0
    first_unread: str | None = None
    participants: list[ReadCursor] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "me": self.me,
            "person": self.person,
            "cursor": self.cursor,
            "unread": self.unread,
            "first_unread": self.first_unread,
            "participants": [p.to_json() for p in self.participants],
        }

    def fingerprint(self) -> tuple:
        """"바뀌었나"의 판정 키. 같은 값을 되풀이해 밀지 않기 위한 것."""
        return (
            self.cursor,
            self.unread,
            self.first_unread,
            tuple(sorted((p.key, p.cursor) for p in self.participants)),
        )


class ReadTracker:
    """방 하나의 읽음 커서 — 로컬(내 것)과 발행(남이 보는 것)을 함께 쥔다.

    **채널을 주입받는다** (`RoomManager` 가 준다). 그래서 이 클래스는 방·HTTP·
    SSE 를 모르고, 아는 것은 "커서를 어디에 어떻게 쓰나" 하나다.
    """

    def __init__(self, channel: Any, person: str, *, seed_latest: bool = True) -> None:
        self.channel = channel
        self.person = person
        self.key = gitwire.state_key(person)
        self._lock = threading.RLock()
        self._seed_latest = seed_latest
        # 로컬 커서는 기반이 이미 주는 표면이다 — 소비자 이름만 다르게 열면 된다.
        self._store = gitwire.CursorStore(channel.dir, LOCAL_CONSUMER)

    # ------------------------------------------------------------- 내 커서

    def local(self) -> str:
        """내가 읽은 위치 (로컬 커서). 없으면 빈 문자열.

        ⭐ 저장된 값이 실제 봉투 ID 가 아니면 **없음으로 취급한다.** 오염된 커서를
        디스크에서 읽어 올 때가 유일한 복구 지점이다 — 사람이 파일을 고치지 않아도
        이 순간부터 뱃지가 다시 맞는다. (파일 자체의 정정은 `ensure_local` 이 방을
        열 때 한 번 한다.)
        """
        return sane_cursor(self._store.load().watermark, where="로컬 커서")

    def _save_local(self, cursor: str) -> None:
        cur = self._store.load()
        cur.watermark = cursor
        cur.started = True
        self._store.save(cur)

    def ensure_local(self) -> str:
        """로컬 커서가 없으면 만든다 — **지금까지는 읽은 것으로 본다.**

        갓 합류한 방(또는 이 기능이 생기기 전부터 쓰던 방)의 과거 대화 수천 건이
        '안 읽음'으로 뜨면 뱃지가 쓸모없어진다. 구독이 백로그를 건너뛰는 것과
        같은 판단이다 (`gitwire.Channel.skip_to_now`).
        """
        with self._lock:
            cur = self._store.load()
            # ⚠️ 판정은 `started` **하나**다. 예전에는 `watermark` 가 비어 있으면
            # 다시 씨앗을 심었는데, 그러면 **빈 방에서 처음 연 사람**(커서가
            # 정당하게 빈 상태)이 방을 다시 열 때마다 "지금까지 다 읽음"으로
            # 재설정된다 — 그 사이 도착한 남의 메시지가 안 읽음으로 세어지지
            # 않고, 읽음 발행도 "이미 읽었다"며 나가지 않는다 (실측된 사고).
            if cur.started:
                good = sane_cursor(cur.watermark, where="로컬 커서")
                if good != (cur.watermark or ""):
                    # ⭐ 오염된 값을 **디스크에서도** 지운다. 읽을 때마다 떨구기만
                    # 하면 경고가 영원히 반복되고, 무엇보다 그 값이 남아 있는 동안
                    # 다른 소비자가 그것을 그대로 읽을 수 있다. `started` 는 그대로
                    # 둔다 — 이 방은 이미 시작한 방이라 "지금까지 다 읽음"으로
                    # 재설정하면 안 읽은 말이 조용히 사라진다.
                    self._save_local(good)
                return good
            latest = ""
            if self._seed_latest:
                ids = self.channel.record_ids(limit=1, fresh=False)
                latest = ids[-1] if ids else ""
            self._save_local(latest)
            return latest

    def mark(self, message_id: str) -> bool:
        """"여기까지 읽었다" — 로컬 커서를 **단조 증가**로 전진시킨다.

        돌려주는 값은 "실제로 전진했나"다. 안 움직였으면 발행할 것도 없다
        (같은 값을 되풀이해 push 하지 않는 근거).
        """
        message_id = (message_id or "").strip()
        if not message_id:
            return False
        # ⭐ **실제 봉투 ID 만** 커서가 된다. 조용히 저장하지 않고 거부한다 —
        # 여기가 오염이 들어오는 유일한 문이었다 (모듈 도크 「커서 형식」).
        if not gitwire.is_record_id(message_id):
            raise InvalidCursor(
                f"읽음 커서는 실제 봉투 ID 여야 한다 (받은 값: {message_id!r})"
            )
        with self._lock:
            now = self.local()
            if now and message_id <= now:
                return False              # 뒤로 가지 않는다 (항상 max)
            self._save_local(message_id)
            return True

    # ----------------------------------------------------------- 참가자 집합

    def participants(self, *, fresh: bool = False) -> dict[str, ReadCursor]:
        """참가자 집합 = **커서 파일 집합** (그래서 분모를 알 수 있다)."""
        try:
            states = self.channel.read_states(fresh=fresh)
        except Exception as exc:  # noqa: BLE001 — 읽음 표시가 대화를 막지 않는다
            log.debug("참가자 상태를 읽지 못했다: %s", exc)
            return {}
        out: dict[str, ReadCursor] = {}
        for key, state in states.items():
            got = parse_state(state)
            if got is not None:
                out[key] = got
        return out

    def ensure_participant(self) -> bool:
        """⭐ **방을 열 때** 내 커서 파일이 없으면 만든다. 만들었으면 True.

        판정은 로컬 stat 한 번이고(`Channel.state_exists` — git 0개), 없을 때만
        쓴다. 설치 스크립트에 넣지 않는 이유: 나중에 추가한 방을 놓친다.
        """
        with self._lock:
            try:
                if self.channel.state_exists(self.key):
                    return False
            except Exception as exc:  # noqa: BLE001
                log.debug("커서 파일 확인 실패: %s", exc)
                return False
            self.publish(force=True)
            return True

    def publish(self, *, force: bool = False) -> bool:
        """내 커서를 발행한다 (best-effort). 실제로 썼으면 True.

        ⭐ **밀어내기는 아웃박스가 한다** — 여기서는 파일만 쓴다(기반이 디스크에
        즉시 쓰고, 커밋·push 는 대기 중인 메시지와 **한 커밋**으로 나간다). 읽음
        발행이 메시지 전송을 막지 않는 근거이고, 배칭·백그라운드 push 를 두 벌
        만들지 않는 이유이기도 하다.

        쓰기 전에 저장된 값을 읽어 `max` 를 취한다 — 같은 사람의 다른 기기가 더
        앞서 있으면 그 값을 되돌리지 않기 위해서다 (같은 경로를 공유한다).
        """
        with self._lock:
            mine = self.local()
            stored = None
            try:
                stored = parse_state(self.channel.read_state(self.key, fresh=False))
            except Exception as exc:  # noqa: BLE001
                log.debug("내 커서 파일을 읽지 못했다: %s", exc)
            sender = str(getattr(self.channel, "sender", "") or "")
            senders = set(stored.senders) if stored else set()
            # 두 값 모두 이미 위생을 통과했다 (`local` · `parse_state`) — 그래서
            # `max` 가 오염된 값을 최대값으로 집어 올릴 수 없다. 저장된 값이 오염돼
            # 있었다면 `stored.cursor` 가 "" 이므로 내 실제 커서가 그것을 덮어쓴다
            # (= 원격의 오염이 다음 발행에 저절로 정정된다).
            candidates = [c for c in (mine, stored.cursor if stored else "") if c]
            cursor = max(candidates) if candidates else ""
            if stored is not None and not force:
                if stored.cursor == cursor and sender in senders:
                    return False          # 바뀔 것이 없다 — push 를 만들지 않는다
            senders.add(sender)
            try:
                self.channel.write_state(
                    self.key, build_value(cursor, senders), identity=self.person
                )
            except Exception as exc:  # noqa: BLE001 — 조용히 넘기지 않는다
                log.warning("읽음 커서를 발행하지 못했다 (다음에 다시 시도한다): %s", exc)
                return False
            return True

    # -------------------------------------------------------------- 스냅샷

    def unread(self, *, fresh: bool = False) -> tuple[int, str | None]:
        """(내가 안 읽은 개수, 그 첫 메시지 ID). 방 목록 뱃지의 값이다.

        ⚠️ **git 왕복이 없고 payload blob 도 열지 않는다** (`fresh=False` +
        레코드 **ID 나열**). 그 나열은 커밋 sha 로 캐시되므로 같은 상태를
        되풀이해 세면 **git 호출이 0회**다 — 방 목록이 상태마다 다시 그려지고
        SSE 로도 밀리기 때문에, 이 성질이 없으면 유휴 비용이 방 개수만큼 는다.

        ⚠️ 참가자 커서는 여기서 읽지 않는다. 뱃지는 **내 것**이고, 남의 커서는
        방 안 카운트에만 쓰인다 (`view`).
        """
        cursor = self.local()
        try:
            ids = self.channel.record_ids(fresh=fresh)
        except Exception as exc:  # noqa: BLE001
            log.debug("레코드 나열 실패: %s", exc)
            return 0, None
        count = 0
        first: str | None = None
        for rid in ids:
            if cursor and rid <= cursor:
                continue
            if first is None:
                first = rid
            count += 1
        return count, first

    def view(self, *, fresh: bool = False) -> ReadView:
        """방 하나의 읽음 상태 — 내 안 읽은 개수 + 참가자 커서 전부.

        방 **안**을 그리는 데 필요한 전부다 (뱃지만 필요하면 `unread`).
        """
        count, first = self.unread(fresh=fresh)
        return ReadView(
            me=self.key,
            person=self.person,
            cursor=self.local(),
            unread=count,
            first_unread=first,
            participants=sorted(
                self.participants(fresh=fresh).values(), key=lambda p: p.key
            ),
        )

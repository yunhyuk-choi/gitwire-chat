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

⭐ 그래서 **내가 보낼 때 커서를 발행할 이유가 없다**
------------------------------------------------
위 공식이 작성자를 분모에서 빼므로(`p ≠ A`) **내 커서 값은 내 메시지의 카운트에
아무 영향이 없다.** 아카이브 합의도 커서를 보지 않는다 (`consensus_day` 는
`archived` 워터마크만 본다). 남은 이유는 뱃지 하나였는데 — 안 읽은 개수를 세는
루프가 작성자를 안 가려서 내가 보낸 말이 내 뱃지에 "1 안 읽음"으로 떴다 — 그것은
**커서가 아니라 그 루프**에서 고칠 일이다 (`ReadTracker.unread` 가 이제
`record_sender` 로 내 레코드를 뺀다).

그 하나를 고치기 전까지 값을 치른 것은 왕복이었다: 전송마다 참가자 상태 파일이
다시 쓰였고, 그것이 **전송당 커밋·push 를 하나 더** 만들었다 (실측: 메시지
커밋 뒤 2초쯤 뒤 「참가자 상태 1건」 커밋이 예외 없이 따라왔고, 그 두 번째 push
가 다음 전송의 레코드 스탬프를 439ms 늦췄다). 그래서 **보내기 경로에는 커서 발행
트리거가 없다** (`rooms._after_push`).

커서가 전진하고 발행되는 자리는 하나로 남는다 — **내가 아직 읽지 않은 *남의*
메시지를 보이는 탭에서 실제로 봤을 때** (`static/js/reads.js` 의 `visible()` +
작성자 판정, `static/js/timeline.js` 가 창 안에서 *남의* 최대 ID 만 알린다).
"새로운"은 도착 시각이 아니라 **내가 아직 안 읽음**이라는 뜻이다 — 이미 있던
남의 과거 메시지를 뒤늦게 처음 읽는 경우도 그 자리에 포함된다.

⚠️ 발행은 **레코드가 아니다.** append-only 로 적으면 그 사람의 *현재* 커서를
알기 위해 마지막 read 레코드까지 과거로 스캔해야 한다 — 2주 안 읽은 사람이면
2주치다. keyset 페이징으로 없앤 전량 스캔이 그대로 되살아난다. 그래서 기반의
**참가자 상태 예약 경로**(`gitwire.state`)에 쓴다: 파일 하나 = 사람 하나, 덮어쓴다.

⭐ 같은 파일에 **가용 상태**를 얹는다 (새 파일을 만들지 않는다)
--------------------------------------------------------------
`활동 중`·`자리 비움`·`방해 금지` — 전부 "지금 메시지를 읽을 수 있나"라는 **능력**
축이고, 사람이 선언하는 값이다 (관찰이 아니다). 그래서:

* **하트비트가 없다.** 값이 바뀌는 계기는 이벤트뿐이다 — 메시지 전송 · 커서 전진
  (= 새 메시지를 읽음) · 창 닫기 · 본인 선택. 주기적으로 쓰면 그 자체가 커밋이고,
  아무 일도 없는 방이 하루 종일 자라난다.
* **커서와 같은 파일**에 산다. 쓰기자가 같고(본인), 읽는 쪽이 이미 그 파일 집합을
  나열하고 있다 — 파일을 하나 더 만들면 나열이 한 번 더 늘 뿐이다.
* **바뀔 게 없으면 여전히 안 쓴다** (`publish` 의 가드). 상태가 바뀐 것도 "바뀜"에
  포함시키되, 커서·상태가 둘 다 그대로면 파일을 만지지 않는다.

옛 버전과 **양방향으로** 호환된다: 옛 클라이언트는 모르는 키를 무시하므로
(`parse_state`) 새 파일에서 커서를 그대로 읽고, 새 클라이언트는 `status` 가 없는
파일을 기본값(`활동 중`)으로 읽는다.

⭐ 같은 파일에 **아카이빙 확인응답**도 얹는다 (`archived`)
----------------------------------------------------------
`archived` = "이 **UTC 날짜**까지는 그 날의 레코드를 내 **로컬 아카이브**로 다
옮겼다". 레코드 삭제가 이 값들의 합의로만 일어난다 (`archive.py`).

왜 같은 파일인가 — 판정에 필요한 것이 이미 여기 다 있다:

* 쓰기자가 같다 (**본인 하나** — 경로당 단일 쓰기자 규율 안이다).
* 읽는 쪽이 **이미 이 파일 집합을 폴 주기마다 나열한다.** 그래서 합의 판정이
  공짜다 — 배관도, 왕복도 늘지 않는다.
* 휴면 판정에 쓰는 `updated_at` 도 같은 봉투에 이미 있다 (기반이 매 쓰기에 찍는다).

새 파일을 만들면 이 세 가지를 전부 한 번 더 증명해야 한다.

⚠️ 값은 **단조 증가**다 (커서와 같은 규율) — 되돌리는 API 를 두지 않는다. 뒤로
가면 "옮겼다고 했던 날짜를 이제는 안 옮겼다"가 되어, 그 사이에 그 말을 믿고
레코드를 지운 참가자와 사실이 어긋난다.

⚠️ 옛 파일에는 이 키가 **없다**(= 응답 없음 = 합의 미달). 그 참가자가 앱을 올리면
그때부터 응답이 붙는다. 그동안 삭제가 미뤄지는 것은 **의도된 안전측 실패**다.

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
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import gitwire

log = logging.getLogger(__name__)

#: 발행되는 값(`participants/<키>.json` 의 `value`)의 스키마.
#: 기반은 이 안을 해석하지 않는다 — 레코드 payload 와 같은 경계다.
CURSOR_KIND = "read-cursor"
CURSOR_SCHEMA = 1

#: ⭐ **사용자 가용 상태** — 같은 파일에 얹은 두 번째 사실.
#:
#: "지금 보고 있나"(인스타식 관찰)가 아니라 **"지금 메시지를 읽을 수 있는 상태인가"**
#: 를 사람이 선언하는 값이다. 그래서 하트비트도 자동 만료도 없다 — 값이 바뀌는
#: 계기는 *이벤트*뿐이다 (전송·커서 전진·창 닫기·본인 선택).
#:
#: ⭐ **새 파일을 만들지 않는다.** 커서와 같은 `participants/<키>.json` 의 `value`
#: 에 키 하나로 얹는다. 그 파일은 **경로당 단일 쓰기자**(본인)라 충돌이 구조적으로
#: 생기지 않는데, 파일을 하나 더 만들면 그 성질을 한 번 더 증명해야 하고 읽는 쪽도
#: 나열을 한 번 더 해야 한다.
#:
#: ⚠️ 저장값은 **ASCII 열쇠**다 (화면 문구가 아니다). 화면에 보이는 한국어 이름은
#: 브라우저가 갖는다 (`static/js/userstatus.js` 의 `STATUSES`) — 문구를 파일에
#: 적으면 그것이 곧 스키마가 되어, 문구를 다듬는 일이 원격 데이터 이전이 된다.
STATUS_ACTIVE = "active"
STATUS_AWAY = "away"
STATUS_DND = "dnd"
STATUSES = (STATUS_ACTIVE, STATUS_AWAY, STATUS_DND)

#: 기본값·초기값. `status` 키가 **없는 옛 파일**도 여기로 읽힌다 (양방향 호환의
#: 한쪽 — 반대쪽은 `parse_state` 가 모르는 키를 무시하는 성질이 이미 갖고 있다).
DEFAULT_STATUS = STATUS_ACTIVE

#: **내** 읽은 위치용 로컬 커서의 소비자 이름. 폴러가 쓰는 `chat` 과 다른 이름을
#: 쓴다 — 기반이 소비자별로 커서를 나눠 주므로(`gitwire.CursorStore`) 새 저장
#: 규약을 만들 필요가 없다. 이 커서는 **발행되지 않는다.**
LOCAL_CONSUMER = "read"

#: 뱃지에 그대로 싣기엔 큰 수. 화면 표기는 브라우저가 정한다 (여기서는 세기만).
MAX_BADGE = 999

#: ⭐ **아카이빙 확인응답** — 같은 파일에 얹은 세 번째 사실 (모듈 도크 참조).
#: "이 UTC 날짜까지는 로컬 아카이브로 다 옮겼다". 빈 문자열 = 응답 없음.
ARCHIVED_KEY = "archived"

#: 휴면으로 보는 기간(일). 이 기간 동안 `updated_at` 이 움직이지 않은 참가자는
#: **합의에서 제외한다.**
#:
#: 근거: 한 명이 앱을 안 켜면 삭제가 영원히 일어나지 않는다. 퇴사자면 영구히다.
#: 7일은 "휴가 한 주는 기다려 주고, 그 이상은 기다리지 않는다"는 선이다. 제외해도
#: **데이터를 버리지 않는다** — 그 사람이 돌아와 삭제를 pull 하면 히스토리에서
#: 자기 로컬 아카이브를 복구한다 (`gitwire.Channel.recover_archive`).
DORMANT_DAYS = 7.0


#: 사람 식별자를 **명시적으로** 지정하는 환경변수. 기반의 `GITWIRE_SENDER` 와 같은
#: 성격이다 — git 신원을 넣을 수 없는 환경(컨테이너)과, 한 머신에서 서로 다른
#: 사람으로 두 인스턴스를 띄워야 하는 경우(테스트·공용 PC)를 위한 문이다.
PERSON_ENV = "GITWIRE_CHAT_PERSON"


# ⚠️ 커서 형식 판정은 기반이 준다. 그것이 없는 구버전 gitwire 와 섞이면 **조용히**
# 나빠진다 — `local()` 에서 AttributeError 가 나고, 그것을 삼키는 넓은 except 들이
# 뱃지를 0 으로 만든다(고치려던 그 증상과 똑같은 화면). 그래서 여기서 **크게**
# 실패시킨다: 앱이 뜨지 않고, 무엇을 해야 하는지가 메시지에 있다.
if not hasattr(gitwire, "is_record_id") or not hasattr(
    gitwire.records, "slug_sender"
):  # pragma: no cover — 버전 불일치 방어
    raise ImportError(
        "기반(gitwire)이 너무 낮다 — 읽음 커서 형식 판정(`gitwire.is_record_id`) "
        "또는 발신자 슬러그 규칙(`gitwire.records.slug_sender`)이 없다. "
        "`python -m gitwire_chat update` 로 함께 올려라."
    )


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


def record_sender(record_id: Any) -> str:
    """레코드 ID 에 **박혀 있는** 발신자 슬러그. ID 가 아니면 빈 문자열.

    ⭐ 왜 ID 에서 꺼내는가 — 뱃지(`unread`)가 "이 레코드는 내가 쓴 것인가"를 알아야
    하는데, 그 판정을 **레코드 나열만으로** 해야 한다. 봉투(payload blob)를 열면
    뱃지 하나가 방 안의 메시지 수만큼 blob 을 여는 일이 되고, 방 목록이 상태마다
    다시 그려지므로(SSE) 그 비용이 유휴 상태에서도 계속 나간다. 다행히 발신자는
    이미 ID 안에 있다 — 기반이 `make_record_id` 에서 `<타임스탬프>-<발신자>-<난수>`
    로 박아 넣는다. 그래서 **새 왕복이 하나도 없다.**

    ⚠️ 꺼낸 값은 **슬러그**다 (원본 식별자가 아니다). 기반이 파일명에 안전한
    형태로 바꿔 넣기 때문이다(`gitwire.records.slug_sender` — '-' 제거·40자 상한).
    그래서 참가자 파일의 `senders`(원본)와 비교할 때는 **양쪽을 같은 규칙으로**
    통과시켜야 한다 (`_slug_all`) — 규칙의 주인은 기반이고, 여기서 손으로 다시
    쓰지 않는다.

    형식 판정도 ID 의 주인이 한다 (`gitwire.is_record_id`) — 통과한 값은 칸이
    정확히 셋이지만, 판정과 쪼개기가 어긋나는 날을 대비해 개수를 확인한다.
    """
    if not gitwire.is_record_id(record_id):
        return ""
    name = str(record_id).rsplit("/", 1)[-1]
    parts = name[: -len(".json")].split("-")
    if len(parts) != 3:  # pragma: no cover — 형식 판정이 이미 보장한다
        return ""
    return parts[1]


def _slug_all(senders: Iterable[str]) -> set[str]:
    """봉투 `sender` 집합을 **ID 에 박히는 형태**로 맞춘다 (빈 값은 버린다).

    비교의 한쪽(`record_sender`)이 이미 슬러그이므로 다른 쪽도 같은 규칙을 통과해야
    한다. 규칙은 기반이 소유한다 — 여기서 '-' 를 지우고 40자로 자르는 코드를 다시
    쓰면 상한이 바뀌는 날 두 곳이 어긋나고, 그때 증상은 "내 말이 내 뱃지에 뜬다"로
    조용히 돌아온다.
    """
    return {gitwire.records.slug_sender(s) for s in senders if s}


def sane_day(value: Any) -> str:
    """읽어 들인 날짜 워터마크를 **형식이 맞는 값으로만** 좁힌다. 아니면 빈 문자열.

    형식 판정은 날짜의 주인인 기반이 한다 (`gitwire.is_day`) — 커서와 같은 규율이고
    이유도 같다. 오염된 값이 **미래**를 가리키면 그 참가자가 "다 옮겼다"고 말하는
    셈이 되어 아직 아무도 안 옮긴 날짜가 지워질 수 있다. 그래서 읽기에서 떨군다
    (= 응답 없음 = 합의 미달 = **안 지운다**). 조용히 넘기지 않고 로그를 남긴다.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if gitwire.is_day(text):
        return text
    log.warning("아카이빙 확인응답이 날짜 형식이 아니다 — 없음으로 취급한다: %r", text)
    return ""


def sane_status(value: Any) -> str:
    """읽어 들인 상태를 **아는 값으로만** 좁힌다. 아니면 기본값(`활동 중`).

    ⚠️ 커서(`sane_cursor`)와 달리 **경고하지 않는다.** 모르는 값의 가장 그럴듯한
    출처는 *나보다 새 버전이 쓴 네 번째 상태*이고, 그건 결함이 아니라 버전 차이다.
    상태를 모르면 "받을 수 있다"로 보는 쪽이 안전하다 — 반대(조용히 자리 비움)는
    상대가 "저 사람은 못 받는다"고 오해하게 만든다.
    """
    text = str(value or "").strip()
    if text in STATUSES:
        return text
    if text:
        log.debug("모르는 가용 상태다 — 기본값으로 본다: %r", text)
    return DEFAULT_STATUS


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

    status: str = DEFAULT_STATUS
    """이 사람이 **선언한** 가용 상태 (`STATUSES`). 없던 파일이면 기본값이다.

    커서와 같은 파일에 있지만 **다른 축**이다 — 커서는 "어디까지 읽었나"(사실),
    이것은 "지금 읽을 수 있나"(선언)다. 한 파일에 둔 이유는 쓰기자가 같고
    (본인), 읽는 쪽이 이미 그 파일을 나열하고 있기 때문이다.
    """

    archived: str = ""
    """⭐ **아카이빙 확인응답** — "이 UTC 날짜까지는 로컬 아카이브로 다 옮겼다".

    빈 문자열 = 응답 없음(= 합의 미달). 단조 증가 값이다 (모듈 도크 참조).
    """

    updated_at: str = ""
    """기반이 이 파일을 쓸 때 찍은 시각. **휴면 판정의 유일한 입력**이다
    (`is_dormant`) — 이미 봉투에 있으므로 판정이 공짜다."""

    def to_json(self) -> dict:
        return {
            "person": self.person,
            "key": self.key,
            "cursor": self.cursor,
            "senders": list(self.senders),
            "status": self.status,
            "archived": self.archived,
            "updated_at": self.updated_at,
        }


def build_value(
    cursor: str,
    senders: Iterable[str],
    status: str = DEFAULT_STATUS,
    archived: str = "",
) -> dict:
    """발행할 `value` 를 만든다 (스키마 버전을 항상 싣는다).

    ⭐ `status` 와 `archived` 는 **키 하나씩으로 얹힌다** — 스키마 버전을 올리지
    않는다. 옛 파서는 모르는 키를 그냥 무시하므로(`parse_state`) 버전을 올리면 옛
    클라이언트가 "내가 모르는 버전"이라며 커서까지 버릴 위험만 생긴다.

    ⚠️ `archived` 가 빈 문자열이면 **키를 아예 넣지 않는다.** 그래야 이 기능이
    붙기 전과 파일이 바이트로 같고(= 쓸 이유 없는 push 가 생기지 않고), "응답
    없음"이 *키 없음*과 *빈 값* 두 형태로 갈리지 않는다.
    """
    value = {
        "kind": CURSOR_KIND,
        "v": CURSOR_SCHEMA,
        "cursor": cursor or "",
        "senders": sorted({s for s in senders if s}),
        "status": sane_status(status),
    }
    day = sane_day(archived)
    if day:
        value[ARCHIVED_KEY] = day
    return value


def _state_label(state: Any) -> str:
    """로그에 쓸 참가자 식별 문자열 (실패해도 절대 던지지 않는다).

    ⚠️ 기반의 `gitwire.state_key`(신원 → 파일명 슬러그)와 **다른 것**이다 —
    여기 것은 사람이 읽을 라벨뿐이라 이름을 헷갈리지 않게 갈라 둔다.
    """
    return str(getattr(state, "identity", "") or getattr(state, "key", "") or "?")


def state_senders(state: Any) -> tuple[str, ...]:
    """참가자 상태 봉투에서 **설치본 목록만** 꺼낸다 (커서는 보지 않는다).

    ⭐ 왜 `parse_state` 를 쓰지 않는가 — 그것은 커서 위생(`sane_cursor`)을 함께
    돌리고, 오염된 값을 만나면 **경고를 남긴다.** 뱃지 경로(`ReadTracker.unread`
    → `my_senders`)는 방 목록이 다시 그려질 때마다 도는 자리라, 거기서 커서를
    보면 내 파일이 한 번 오염된 동안 같은 경고가 로그를 채운다 (오염 경고를 줄이려
    한 변경이 새 경고 원천을 만드는 셈이다). 필요한 것은 목록 하나뿐이다.

    ⚠️ 그래도 **목록이 어디 있나**는 여기 한 곳에만 적는다 — `parse_state` 도 이
    함수를 쓴다. 두 벌이 되면 스키마가 움직이는 날 한 벌이 낡는다.

    절대 던지지 않는다 (남이 쓴 파일이고, 우리보다 새 버전일 수 있다).
    """
    value = getattr(state, "value", None)
    if not isinstance(value, dict):
        return ()
    if str(value.get("kind") or CURSOR_KIND) != CURSOR_KIND:
        return ()
    senders = value.get("senders")
    if not isinstance(senders, list):
        return ()
    return tuple(sorted({str(s) for s in senders if s}))


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
    when = getattr(state, "updated_at", None)
    return ReadCursor(
        person=str(getattr(state, "identity", "") or getattr(state, "key", "")),
        key=str(getattr(state, "key", "")),
        cursor=str(cursor or ""),
        senders=state_senders(state),
        # ⭐ 호환의 한쪽 방향: `status` 가 **없는 옛 파일**은 기본값(`활동 중`)으로
        # 읽힌다. 상태를 모르는 사람을 "자리 비움"으로 칠하면, 아직 갱신하지 않은
        # 동료가 전부 조용히 자리를 비운 것처럼 보인다.
        status=sane_status(value.get("status")),
        # ⭐ 옛 파일에는 이 키가 없다 → "" = 응답 없음 = 합의 미달 (= 안 지운다).
        archived=sane_day(value.get(ARCHIVED_KEY)),
        updated_at=when.isoformat().replace("+00:00", "Z") if when else "",
    )


def is_dormant(
    who: ReadCursor, now: datetime, *, dormant_days: float = DORMANT_DAYS
) -> bool:
    """이 참가자를 **합의에서 제외**해도 되는가 (근거는 `DORMANT_DAYS`).

    판정 입력은 `updated_at` 하나다 — 봉투에 이미 있으므로 왕복이 없다.

    ⚠️ 읽을 수 없는 `updated_at`(없거나 형식이 깨짐)은 **휴면이 아니다.** 모르는
    것을 "없는 사람"으로 치면 조용히 남의 레코드를 지우는 쪽으로 기운다 — 판정
    불가는 언제나 *안 지우는* 쪽으로 떨어져야 한다.
    """
    raw = (who.updated_at or "").strip()
    if not raw:
        return False
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (now - when) > timedelta(days=max(0.0, float(dormant_days)))


def consensus_day(
    people: dict,
    *,
    now: datetime,
    dormant_days: float = DORMANT_DAYS,
) -> str:
    """⭐ **전원이 옮겼다고 말한 가장 이른 날짜** (= 여기까지는 지워도 된다).

    휴면 참가자는 분모에서 빠진다 (`is_dormant`). 응답이 없는 참가자(옛 파일·아직
    한 번도 배치를 돌리지 않은 사람)는 `""` 이므로 **합의를 막는다** — 그것이
    안전측 기본값이다.

    참가자가 아무도 없으면(파일 집합이 빈 새 방) `""` — 지울 근거가 없다.
    """
    days = [
        w.archived
        for w in people.values()
        if not is_dormant(w, now, dormant_days=dormant_days)
    ]
    if not days:
        return ""
    return min(days)


@dataclass
class ReadView:
    """방 하나의 읽음 상태 스냅샷 — API·SSE·테스트가 **같은 값**을 쓴다."""

    me: str = ""
    """내 참가자 키 (파일명 슬러그)."""

    person: str = ""
    """내 원본 식별자."""

    cursor: str = ""
    """내 로컬 커서 (= 내가 읽은 위치). 발행값이 아니라 **로컬**이 정본이다."""

    status: str = DEFAULT_STATUS
    """내가 **발행한** 가용 상태. 화면의 드롭다운이 무엇을 고르고 있든, 원격에
    실제로 올라간 값은 이것이다 (그 둘이 어긋나면 사람이 볼 수 있어야 한다)."""

    unread: int = 0
    first_unread: str | None = None
    participants: list[ReadCursor] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "me": self.me,
            "person": self.person,
            "cursor": self.cursor,
            "status": self.status,
            "unread": self.unread,
            "first_unread": self.first_unread,
            "participants": [p.to_json() for p in self.participants],
        }

    def fingerprint(self) -> tuple:
        """"바뀌었나"의 판정 키. 같은 값을 되풀이해 밀지 않기 위한 것.

        ⚠️ **상태도 여기 들어간다.** 안 넣으면 남이 상태만 바꿨을 때 지문이 같아
        SSE 가 나가지 않고, 참여자 목록이 조용히 낡는다 (커서만 세던 시절의 잔재가
        그대로 새 기능의 침묵이 된다).
        """
        return (
            self.cursor,
            self.unread,
            self.first_unread,
            tuple(sorted((p.key, p.cursor, p.status) for p in self.participants)),
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

    def publish(
        self,
        *,
        force: bool = False,
        status: str | None = None,
        archived: str | None = None,
    ) -> bool:
        """내 커서를 발행한다 (best-effort). 실제로 썼으면 True.

        ⭐ **밀어내기는 아웃박스가 한다** — 여기서는 파일만 쓴다(기반이 예약 경로
        파일을 즉시 쓰고, 커밋·push 는 다음 배칭 창에 실린다). 읽음 발행이 메시지
        전송을 막지 않는 근거이고, 배칭·백그라운드 push 를 두 벌 만들지 않는
        이유이기도 하다.

        ⚠️ 내가 보낸 말로 커서를 올리는 경우, 그 커서 발행은 메시지와 **같은
        커밋이 아니다** — 커서 값(= 레코드 ID)이 메시지가 push 되는 순간에야
        생기기 때문이다 (`rooms._after_push`). 순서가 뒤집힐 위험은 그래서 오히려
        사라졌다: 커서가 가리키는 레코드는 **이미 원격에 있다.**

        쓰기 전에 저장된 값을 읽어 `max` 를 취한다 — 같은 사람의 다른 기기가 더
        앞서 있으면 그 값을 되돌리지 않기 위해서다 (같은 경로를 공유한다).

        ⭐ `status` 를 주면 그 값으로 **바꾼다**, 안 주면 저장된 값을 **그대로
        유지한다.** 상태는 커서와 달리 단조 증가가 아니라 왕복하는 값이라(자리를
        비웠다 돌아온다) `max` 같은 합치기 규칙이 없다 — 마지막으로 선언한 것이
        곧 현재다. 같은 사람의 두 기기가 다투면 나중 선언이 이긴다 (둘 다 그 사람
        본인이라 그게 맞다).

        ⭐ `archived`(아카이빙 확인응답)는 **커서와 같은 규율**이다 — 주면 저장된
        값과 `max` 를 취하고, 안 주면 그대로 유지한다. 절대 뒤로 가지 않는다
        (모듈 도크 「아카이빙 확인응답」).

        ⚠️⚠️ **호출 순서가 데이터 안전의 전부다.** 이 발행은 아카이브 파일을
        **디스크에 확실히 쓴 뒤에만** 불려야 한다 (`archive.py` 가 그 순서를
        지킨다). 반대로 하면 "옮겼다"고 말해 놓고 죽었을 때 남들이 레코드를 지우고
        **그 사람만 잃는다.**
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
            # 확인응답도 단조 증가 — 저장된 값과 `max`. 안 주면 그대로 유지한다.
            acks = [
                d
                for d in (sane_day(archived), stored.archived if stored else "")
                if d
            ]
            ack = max(acks) if acks else ""
            # 상태는 **주면 바꾸고 안 주면 그대로**다. 저장된 값이 없으면 기본값.
            want = sane_status(status) if status is not None else (
                stored.status if stored else DEFAULT_STATUS
            )
            if stored is not None and not force:
                # ⚠️ 이 가드가 레포가 커지지 않는 이유다. **상태가 바뀐 것도
                # "바뀜"에 포함**시키되(안 그러면 상태 변경이 영원히 안 나간다),
                # 커서·상태가 둘 다 그대로면 여전히 아무것도 쓰지 않는다.
                if (stored.cursor == cursor and sender in senders
                        and stored.status == want and stored.archived == ack):
                    return False          # 바뀔 것이 없다 — push 를 만들지 않는다
            senders.add(sender)
            try:
                self.channel.write_state(
                    self.key,
                    build_value(cursor, senders, want, ack),
                    identity=self.person,
                )
            except Exception as exc:  # noqa: BLE001 — 조용히 넘기지 않는다
                log.warning("읽음 커서를 발행하지 못했다 (다음에 다시 시도한다): %s", exc)
                return False
            return True

    # -------------------------------------------------------------- 스냅샷

    def my_senders(self) -> set[str]:
        """내 봉투 `sender` 들 — **레코드 ID 에 박히는 형태**(슬러그)로.

        ⭐ 근거는 **하나**다: 내 참가자 파일의 `senders`(= 내 설치본 목록, 이
        모듈의 `ReadCursor.senders`). 카운트 공식의 `p ≠ A` 를 판정하는 그 값을
        뱃지도 그대로 쓴다 — 작성자 판정 규칙을 두 벌 만들면 "카운트에서는
        작성자인데 뱃지에서는 남"인 상태가 생긴다 (`static/js/reads.js` 의 같은
        경고).

        여기에 **지금 이 설치본**(`channel.sender`)을 더한다. 방을 막 열어 아직
        파일이 없을 때(또는 이 기기가 아직 그 목록에 못 들어갔을 때)도 내 말이
        내 뱃지에 뜨지 않게 하기 위해서다 — `publish` 가 그 값을 목록에 넣는
        주체이므로 둘은 같은 사실의 이른 쪽·늦은 쪽이다.

        ⚠️ 비용: 참가자 상태 **내 파일 하나**를 로컬에서 읽는다. 커밋 sha 로
        캐시된 나열(`ls-tree`) + 작업 사본 파일 읽기이므로 **git 왕복이 0회**다
        (기반 `_blob_bytes` — 파일 + sha1 대조, subprocess 없음). 읽지 못하면 이
        설치본 하나로 떨어진다 — 뱃지가 조금 과다해질 수 있고(내 다른 기기의 말이
        세어진다) 그 방향이 안전측이다.

        ⚠️ **커서는 보지 않는다** (`state_senders`, 그 도크). 이 자리는 방 목록이
        다시 그려질 때마다 도므로, 여기서 커서 위생을 돌리면 내 파일이 한 번
        오염된 동안 같은 경고가 로그를 채운다.
        """
        raw = {str(getattr(self.channel, "sender", "") or "")}
        try:
            raw |= set(state_senders(self.channel.read_state(self.key, fresh=False)))
        except Exception as exc:  # noqa: BLE001 — 뱃지가 방 목록을 죽이지 않는다
            log.debug("내 설치본 목록을 읽지 못했다: %s", exc)
        return _slug_all(raw)

    def unread(
        self, *, fresh: bool = False, senders: set[str] | None = None
    ) -> tuple[int, str | None]:
        """(내가 안 읽은 개수, 그 첫 메시지 ID). 방 목록 뱃지의 값이다.

        ⭐ **내 레코드는 세지 않는다.** 뱃지는 "내가 아직 안 읽은 *남의* 말이
        몇이냐"이고, 내가 보낸 말은 그 정의에 들어가지 않는다. 판정은 레코드 ID 에
        박힌 발신자(`record_sender`)와 내 설치본 목록(`my_senders`)으로 한다 —
        카운트 공식의 `p ≠ A` 와 **같은 근거**다.

        ⚠️ 그래서 이 값은 **내 커서를 전진시키지 않고도** 맞는다. 예전에는 반대로
        했다 — 보낼 때 내 커서를 내 메시지로 올려(`rooms._after_push`) 뱃지를
        0 으로 만들었는데, 그 전진은 (a) 카운트 공식에 아무 영향이 없고(작성자는
        분모에서 빠진다) (b) 아카이브 합의도 커서를 안 보며(`consensus_day` 는
        `archived` 만 본다) (c) 그러면서 **전송마다 참가자 상태 파일을 다시 써
        커밋·push 를 하나 더 만들었다.** 뱃지가 커서를 안 읽으면 그 왕복이 사라진다.

        ⚠️ **git 왕복이 없고 payload blob 도 열지 않는다** (`fresh=False` +
        레코드 **ID 나열**). 그 나열은 커밋 sha 로 캐시되므로 같은 상태를
        되풀이해 세면 **git 호출이 0회**다 — 방 목록이 상태마다 다시 그려지고
        SSE 로도 밀리기 때문에, 이 성질이 없으면 유휴 비용이 방 개수만큼 는다.
        작성자 판정도 그 성질을 깨지 않는다: 발신자는 **ID 안에** 있고, 내 설치본
        목록은 참가자 상태 파일 하나를 로컬에서 읽는 것이다 (`my_senders`).

        ⚠️ 남의 커서는 여기서 읽지 않는다. 뱃지는 **내 것**이고, 남의 커서는
        방 안 카운트에만 쓰인다 (`view`). `senders` 인자는 이미 참가자 집합을
        읽은 호출자(`view`)가 같은 사실을 두 번 읽지 않도록 넘겨 주는 자리다.
        """
        cursor = self.local()
        mine = self.my_senders() if senders is None else senders
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
            # ⭐ 내가 쓴 말은 내 안 읽음이 아니다 (위 도크). 발신자를 모르는 ID
            # (형식이 아닌 값)는 남의 것으로 센다 — 과다는 눈에 보이고 사라지지만
            # 조용한 0 은 아무도 못 알아챈다 (이 모듈의 일관된 안전측 방향).
            if mine and record_sender(rid) in mine:
                continue
            if first is None:
                first = rid
            count += 1
        return count, first

    def view(self, *, fresh: bool = False) -> ReadView:
        """방 하나의 읽음 상태 — 내 안 읽은 개수 + 참가자 커서 전부.

        방 **안**을 그리는 데 필요한 전부다 (뱃지만 필요하면 `unread`).
        """
        people = self.participants(fresh=fresh)
        # 내 상태는 **내 파일**에 있다. 이미 나열한 것에서 꺼낸다 — 따로 한 번 더
        # 읽으면 같은 사실을 두 경로로 얻게 되고, 그 둘이 어긋날 자리가 생긴다.
        mine = people.get(self.key)
        # ⭐ 작성자 판정용 설치본 목록도 **그 나열에서** 꺼낸다 (같은 이유다 —
        # `unread` 가 스스로 읽으면 같은 파일을 두 번 읽는다).
        count, first = self.unread(
            fresh=fresh,
            senders=_slug_all(
                {str(getattr(self.channel, "sender", "") or "")}
                | (set(mine.senders) if mine else set())
            ),
        )
        return ReadView(
            me=self.key,
            person=self.person,
            cursor=self.local(),
            status=mine.status if mine else DEFAULT_STATUS,
            unread=count,
            first_unread=first,
            participants=sorted(people.values(), key=lambda p: p.key),
        )

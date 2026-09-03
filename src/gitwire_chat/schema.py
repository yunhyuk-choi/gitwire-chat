"""메시지 스키마 — payload 안에 무엇을 담고, 무엇을 봉투에 맡기나.

gitwire 는 **전송 계층**이고 payload 를 해석하지 않는다. 그러니 "무엇을
payload 에 넣을지"는 전적으로 이 앱의 결정이다. 원칙은 하나다:

    **봉투가 이미 더 잘 아는 것을 payload 에 중복해서 담지 않는다.**

봉투(gitwire ``Record``)가 담당하는 것
--------------------------------------
* ``Record.id``   — 채널 안에서 유일하고 **정렬 가능한** 식별자.
  → 그대로 **메시지 ID** 로 쓴다. 새 UUID 를 만들지 않는다.
  → 브라우저 DOM 의 키이자 **중복 수신 방어(멱등)** 의 기준이다.
    gitwire 는 크래시 시점에 따라 같은 레코드를 재전달할 수 있다고
    명시한다 — 그래서 소비자 쪽 키가 반드시 필요하고, 봉투 ID 가
    이미 그 역할에 완벽하다.
  → ``reply_to`` 도 이 ID 를 가리킨다.
* ``Record.timestamp`` — **공통 시계(git 호스트 Date 헤더 기준)** 로 찍힌 시각.
  → 표시 시각으로 이걸 쓴다. payload 에 우리 로컬 시계로 시각을 또 넣으면
    참가자 간 시계 차이(실측 2.1초)만큼 순서가 뒤집히는, 더 나쁜 값이 된다.
* ``Record.sender`` — **어느 참가자 프로세스가 발행했나**(전송 수준 식별자,
  IP 주소에 가깝다).
  → 표시 이름으로 쓰지 **않는다.** 다만 "내가 보낸 것인가"(로컬 에코 판정)와
    같은 기계 판단에는 쓴다.

payload(이 앱의 스키마)가 담당하는 것
-------------------------------------
전송 계층이 **알아서는 안 되는** 것, 즉 사람이 읽는 대화 내용이다::

    {"kind": "msg", "v": 1, "author": "최윤혁",
     "text": "안녕", "reply_to": "records/2026.../....json" | 생략}

* ``kind`` — 레코드 종류. v1 은 ``msg`` 뿐이지만, 나중에 ``system``·``edit``
  같은 종류가 섞여도 **모르는 kind 는 조용히 무시**하고 앱이 죽지 않게 하려면
  첫 버전부터 있어야 한다(append-only 라 과거 레코드를 고칠 수 없다).
* ``v`` — 스키마 버전. 같은 이유.
* ``author`` — 사람이 읽는 표시 이름. 봉투 ``sender`` 는 프로세스 식별자라
  이 자리를 대신할 수 없다(한 사람이 노트북·데스크탑 두 프로세스를 쓰면
  ``sender`` 가 둘이다).
* ``text`` — 본문.
* ``reply_to`` — 답장 대상 메시지 ID(= 봉투 ID). 없으면 키 자체를 생략한다.

⚠️ append-only 매체라 **읽기는 언제나 관대하게(lenient)** 한다. 필드가 빠졌거나
타입이 다른 레코드가 섞여도 예외를 던지지 않고 최선의 해석을 돌려준다 —
과거 레코드는 절대 고칠 수 없기 때문이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

#: payload 스키마 버전
SCHEMA_VERSION = 1

#: v1 이 아는 레코드 종류
KIND_MESSAGE = "msg"

#: 본문 길이 상한. git 레포에 영구히 남으므로 사고성 거대 붙여넣기를 막는다.
MAX_TEXT_LEN = 8000

#: 표시 이름 길이 상한
MAX_AUTHOR_LEN = 64


class InvalidMessage(ValueError):
    """보내려는 메시지가 스키마를 만족하지 못한다 (쓰기 경로에서만 발생)."""


def build_payload(author: str, text: str, reply_to: str | None = None) -> dict:
    """보낼 메시지의 payload 를 만든다.

    **쓰기 경로는 엄격하다** — 잘못된 것을 append-only 매체에 남기지 않는다.
    """
    author = (author or "").strip()
    text = (text or "").strip()
    if not author:
        raise InvalidMessage("표시 이름(author)이 비어 있다")
    if len(author) > MAX_AUTHOR_LEN:
        raise InvalidMessage(f"표시 이름이 너무 길다 (최대 {MAX_AUTHOR_LEN}자)")
    if not text:
        raise InvalidMessage("본문(text)이 비어 있다")
    if len(text) > MAX_TEXT_LEN:
        raise InvalidMessage(f"본문이 너무 길다 (최대 {MAX_TEXT_LEN}자)")

    payload: dict[str, Any] = {
        "kind": KIND_MESSAGE,
        "v": SCHEMA_VERSION,
        "author": author,
        "text": text,
    }
    if reply_to:
        payload["reply_to"] = str(reply_to)
    return payload


@dataclass(frozen=True)
class Message:
    """봉투 + payload 를 합쳐 UI 가 그대로 쓰는 한 건."""

    id: str
    """= gitwire 봉투 ID. 정렬 키이자 DOM 키이자 중복 방어 키."""

    author: str
    text: str
    ts: datetime
    """= 봉투 timestamp (공통 시계 기준 UTC)."""

    sender: str
    """= 봉투 sender (참가자 프로세스 식별자). 표시용 아님."""

    kind: str = KIND_MESSAGE
    reply_to: str | None = None
    unknown: bool = False
    """모르는 kind/버전이라 최선으로 해석했다는 표시."""

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "author": self.author,
            "text": self.text,
            "ts": self.ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "sender": self.sender,
            "kind": self.kind,
            "reply_to": self.reply_to,
            "unknown": self.unknown,
        }


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def parse_record(record: Any) -> Message:
    """gitwire ``Record`` → ``Message``. **절대 예외를 던지지 않는다.**

    모르는 형태의 레코드도 화면에 자리를 차지하게 둔다. append-only 매체에서
    "해석 못 하니 죽는다"는 곧 대화 전체가 막히는 것이다.
    """
    payload = getattr(record, "payload", None)
    rid = _as_text(getattr(record, "id", "")) or "?"
    sender = _as_text(getattr(record, "sender", "")) or "?"
    ts = getattr(record, "timestamp", None)
    if not isinstance(ts, datetime):
        ts = datetime.fromtimestamp(0, timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    if not isinstance(payload, dict):
        return Message(
            id=rid,
            author="(알 수 없음)",
            text=_as_text(payload),
            ts=ts,
            sender=sender,
            kind="unknown",
            unknown=True,
        )

    kind = _as_text(payload.get("kind")) or KIND_MESSAGE
    version = payload.get("v", SCHEMA_VERSION)
    unknown = kind != KIND_MESSAGE or version != SCHEMA_VERSION

    author = _as_text(payload.get("author")).strip() or "(이름 없음)"
    text = _as_text(payload.get("text"))
    reply_to = payload.get("reply_to")
    reply_to = _as_text(reply_to).strip() or None

    return Message(
        id=rid,
        author=author[:MAX_AUTHOR_LEN],
        text=text,
        ts=ts,
        sender=sender,
        kind=kind,
        reply_to=reply_to,
        unknown=unknown,
    )

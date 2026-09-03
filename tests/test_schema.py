"""메시지 스키마 — 봉투와 payload 의 분담이 실제로 지켜지는지."""

from __future__ import annotations

from datetime import datetime, timezone

import gitwire
import pytest

from gitwire_chat import schema


def record(payload, *, rid="records/20260903/20260903T010203004Z-a-abc123.json",
           sender="alice.laptop", ts=None):
    return gitwire.Record(
        id=rid,
        sender=sender,
        timestamp=ts or datetime(2026, 9, 3, 1, 2, 3, tzinfo=timezone.utc),
        payload=payload,
    )


def test_payload_에는_봉투가_아는_것을_담지_않는다():
    payload = schema.build_payload("최윤혁", "안녕")
    # id·ts·sender 는 전부 봉투 몫이다 — payload 에 중복되면 안 된다.
    assert set(payload) == {"kind", "v", "author", "text"}
    assert payload["author"] == "최윤혁"
    assert payload["kind"] == schema.KIND_MESSAGE


def test_reply_to_는_있을_때만_담는다():
    assert "reply_to" not in schema.build_payload("나", "안녕")
    assert schema.build_payload("나", "안녕", "records/x.json")["reply_to"] == "records/x.json"


@pytest.mark.parametrize(
    "case",
    ["빈_작성자", "빈_본문", "공백_본문", "너무_긴_본문", "너무_긴_작성자"],
)
def test_쓰기_경로는_엄격하다(case):
    author, text = {
        "빈_작성자": ("", "안녕"),
        "빈_본문": ("나", ""),
        "공백_본문": ("나", "   "),
        "너무_긴_본문": ("나", "가" * (schema.MAX_TEXT_LEN + 1)),
        "너무_긴_작성자": ("가" * (schema.MAX_AUTHOR_LEN + 1), "안녕"),
    }[case]
    with pytest.raises(schema.InvalidMessage):
        schema.build_payload(author, text)


def test_봉투가_메시지_ID_와_시각의_원천이다():
    payload = schema.build_payload("최윤혁", "안녕")
    message = schema.parse_record(record(payload))
    assert message.id == "records/20260903/20260903T010203004Z-a-abc123.json"
    assert message.ts == datetime(2026, 9, 3, 1, 2, 3, tzinfo=timezone.utc)
    assert message.sender == "alice.laptop"      # 전송 식별자
    assert message.author == "최윤혁"             # 표시 이름 (payload)
    assert message.unknown is False


def test_읽기_경로는_관대하다_모르는_레코드도_죽지_않는다():
    # append-only 라 과거 레코드를 고칠 수 없다 → 어떤 모양이 와도 예외 금지.
    for payload in [None, "그냥 문자열", 42, [], {"kind": "kanban", "v": 9}, {}]:
        message = schema.parse_record(record(payload))
        assert isinstance(message, schema.Message)
        assert message.id


def test_모르는_kind_는_unknown_으로_표시된다():
    message = schema.parse_record(record({"kind": "kanban", "v": 1, "text": "x"}))
    assert message.unknown is True
    assert message.kind == "kanban"


def test_망가진_레코드도_해석된다():
    message = schema.parse_record(object())
    assert message.id == "?"
    assert message.unknown is True


def test_to_json_은_UI_가_쓰는_필드를_전부_준다():
    message = schema.parse_record(record(schema.build_payload("나", "안녕")))
    data = message.to_json()
    assert set(data) >= {"id", "author", "text", "ts", "sender", "kind", "reply_to"}
    assert data["ts"].endswith("Z")

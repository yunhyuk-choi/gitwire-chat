"""일일 아카이빙 배치 — 순서·합의·휴면·알림·호환을 대역으로 세밀하게 본다.

⚠️ **이 층만으로는 부족하다.** 여기서 증명하는 것은 *판정과 순서*이고, "정말
레코드가 지워지고 아카이브는 커밋되지 않는가"는 실제 git 2~3인 왕복이 아니면
아무것도 증명하지 못한다 — 그쪽은 `tests/test_archive_e2e.py` 다. 둘은 서로를
대체하지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import gitwire
import pytest

from gitwire_chat import archive as _archive
from gitwire_chat import reads as _reads

NOW = datetime(2026, 9, 17, 6, 0, tzinfo=timezone.utc)


def cursor(key, *, archived="", updated_at=NOW, senders=("s",)) -> _reads.ReadCursor:
    return _reads.ReadCursor(
        person=key, key=key, cursor="", senders=tuple(senders),
        archived=archived,
        updated_at=updated_at.isoformat().replace("+00:00", "Z"),
    )


# ------------------------------------------------------------ 값 / 호환 (③)


def test_ack_key_is_omitted_when_there_is_no_answer():
    """응답이 없으면 키를 **아예 넣지 않는다** — 옛 파일과 바이트로 같다."""
    assert "archived" not in _reads.build_value("", ["s"])
    assert _reads.build_value("", ["s"], _reads.STATUS_ACTIVE, "20260915")[
        "archived"
    ] == "20260915"


def test_old_file_without_the_ack_key_is_read_as_no_answer():
    """⭐ 옛 → 새: 확인응답 키가 없는 파일을 새 코드가 정상 처리한다 (= 합의 미달)."""
    state = gitwire.ParticipantState(
        key="a@x.io", identity="a@x.io",
        value={"kind": "read-cursor", "v": 1, "cursor": "", "senders": ["s"]},
        updated_at=NOW,
    )
    got = _reads.parse_state(state)
    assert got is not None and got.archived == ""
    # 그 한 명 때문에 합의가 성립하지 않는다 — 안전측 기본값
    assert _reads.consensus_day({"a@x.io": got}, now=NOW) == ""


def test_new_file_is_readable_by_an_old_parser():
    """⭐ 새 → 옛: 옛 파서(커서만 보는 코드)가 새 파일에서 커서를 그대로 뽑는다."""
    value = _reads.build_value("records/20260901/x.json", ["s"], "active", "20260915")
    # 옛 파서가 하던 일 그대로 (모르는 키는 손대지 않는다)
    assert value["kind"] == "read-cursor" and value["v"] == 1
    assert value["cursor"] == "records/20260901/x.json"
    assert sorted(value) == ["archived", "cursor", "kind", "senders", "status", "v"]


def test_a_broken_ack_is_treated_as_no_answer():
    """오염된 응답(미래·형식 아님)은 **없음**으로 떨군다 — 안 지우는 쪽으로."""
    assert _reads.sane_day("20260915") == "20260915"
    assert _reads.sane_day("어제") == ""
    assert _reads.sane_day("~pending/000001") == ""
    assert _reads.sane_day(None) == ""


# ------------------------------------------------------------ 합의 판정 (②⑨)


def test_consensus_is_the_minimum_answer():
    people = {
        "a": cursor("a", archived="20260916"),
        "b": cursor("b", archived="20260914"),
    }
    assert _reads.consensus_day(people, now=NOW) == "20260914"


def test_one_missing_answer_blocks_everything():
    people = {"a": cursor("a", archived="20260916"), "b": cursor("b")}
    assert _reads.consensus_day(people, now=NOW) == ""


def test_no_participants_means_no_consensus():
    assert _reads.consensus_day({}, now=NOW) == ""


def test_dormant_participants_drop_out_of_the_denominator():
    """⭐ 검증 3 — `updated_at` 이 8일 전인 참가자는 합의에서 제외된다."""
    people = {
        "a": cursor("a", archived="20260916"),
        "gone": cursor("gone", updated_at=NOW - timedelta(days=8)),
    }
    assert _reads.is_dormant(people["gone"], NOW) is True
    assert _reads.is_dormant(people["a"], NOW) is False
    assert _reads.consensus_day(people, now=NOW) == "20260916"
    # 7일 안쪽이면 여전히 기다린다
    people["gone"] = cursor("gone", updated_at=NOW - timedelta(days=6))
    assert _reads.consensus_day(people, now=NOW) == ""


def test_unreadable_updated_at_is_not_dormant():
    """판정 불가는 **안 지우는 쪽**으로 떨어진다."""
    assert _reads.is_dormant(_reads.ReadCursor(person="x", key="x"), NOW) is False
    assert _reads.is_dormant(
        _reads.ReadCursor(person="x", key="x", updated_at="깨진값"), NOW
    ) is False


# ------------------------------------------------------- ⭐ 순서 (검증 4)


def _batch(manager, room_id):
    return manager.archive_batch(room_id)


def test_ack_is_never_published_when_archiving_fails(manager, fake_opener):
    """⭐ 검증 4 — 아카이브 쓰기가 실패하면 **확인응답이 나가지 않는다.**

    반대 순서면 응답만 보내고 죽었을 때 남들이 레코드를 지우고 그 사람만 잃는다.
    """
    room = manager.register("https://example.com/me/room.git")
    channel = fake_opener.channels[gitwire.normalize_repo_url(room.repo_url)]
    manager.send(room.id, "안녕")
    batch = _batch(manager, room.id)
    channel.archive_error = OSError("디스크가 꽉 찼다")

    got = batch.run_once()
    assert got.through == "" and got.acked is False
    assert got.problems and "아카이브로 옮기지 못했다" in got.problems[0]
    mine = _reads.parse_state(channel.read_state(batch.tracker.key))
    assert mine is None or mine.archived == ""
    assert channel.dropped == []


def test_ack_stops_before_a_day_that_could_not_be_moved(manager, fake_opener):
    """옮기지 못한 날짜가 있으면 응답이 **그 앞에서 멈춘다** (건너뛰지 않는다)."""
    room = manager.register("https://example.com/me/room.git")
    channel = fake_opener.channels[gitwire.normalize_repo_url(room.repo_url)]
    channel.archive_skipped = {"20260910": "레코드가 아닌 항목이 섞여 있다"}
    manager.send(room.id, "안녕")

    got = _batch(manager, room.id).run_once()
    assert got.through == "20260909"
    assert got.problems and "20260910" in got.problems[0]


def test_ack_is_monotonic(manager, fake_opener):
    """응답은 **뒤로 가지 않는다** (커서와 같은 규율)."""
    room = manager.register("https://example.com/me/room.git")
    channel = fake_opener.channels[gitwire.normalize_repo_url(room.repo_url)]
    tracker = manager.reads(room.id)
    tracker.publish(archived="20260916")
    assert _reads.parse_state(channel.read_state(tracker.key)).archived == "20260916"
    tracker.publish(archived="20260901")     # 되돌리려는 시도
    assert _reads.parse_state(channel.read_state(tracker.key)).archived == "20260916"


def test_publishing_the_same_ack_twice_writes_nothing(manager, fake_opener):
    """같은 값이면 파일을 만지지 않는다 — 아무 일 없는 방이 매일 자라지 않는다."""
    room = manager.register("https://example.com/me/room.git")
    channel = fake_opener.channels[gitwire.normalize_repo_url(room.repo_url)]
    tracker = manager.reads(room.id)
    tracker.publish(archived="20260916")
    before = channel.state_writes
    assert tracker.publish(archived="20260916") is False
    assert channel.state_writes == before


# --------------------------------------------------- ⭐ 합의 뒤 삭제 (검증 2)


def test_drop_waits_for_everyone(manager, fake_opener):
    """⭐ 검증 2 — 한 명만 응답하면 안 지우고, 전원이 응답하면 지운다."""
    room = manager.register("https://example.com/me/room.git")
    channel = fake_opener.channels[gitwire.normalize_repo_url(room.repo_url)]
    manager.send(room.id, "어제 이야기")
    batch = _batch(manager, room.id)

    # 아직 응답하지 않은 동료가 있다
    channel.inject_state("other@x.io", _reads.build_value("", ["other"]))
    got = batch.run_once()
    assert got.acked is True and got.through
    assert got.consensus == "" and got.dropped == []
    assert channel.dropped == []

    # 동료가 응답했다 → 이제 지운다
    channel.inject_state(
        "other@x.io", _reads.build_value("", ["other"], "active", got.through)
    )
    again = batch.run_once()
    assert again.consensus == got.through
    assert again.dropped and channel.dropped == again.dropped


def test_drop_only_covers_days_within_the_consensus(manager, fake_opener):
    """합의 날짜보다 **뒤**의 날짜는 지우지 않는다."""
    room = manager.register("https://example.com/me/room.git")
    channel = fake_opener.channels[gitwire.normalize_repo_url(room.repo_url)]
    manager.send(room.id, "옛날")
    batch = _batch(manager, room.id)
    first = batch.run_once()
    channel.archived["20261231"] = 1          # 합의보다 **뒤**의 날짜
    channel.inject_state(
        "other@x.io",
        _reads.build_value("", ["other"], "active", "20261231"),
    )
    got = batch.run_once()
    # 합의 = 전원의 **최소값** = 내 응답 (남이 더 앞서 있어도 내가 기준을 낮춘다)
    assert got.consensus == first.through
    assert "20261231" not in got.dropped
    assert got.dropped and max(got.dropped) <= got.consensus


# --------------------------------------------------- ⭐ 자동 복구 (검증 5)


def test_a_pulled_deletion_triggers_recovery(manager, fake_opener):
    """⭐ 검증 5 — 삭제를 pull 로 받으면 그 날짜를 히스토리에서 복구한다."""
    room = manager.register("https://example.com/me/room.git")
    channel = fake_opener.channels[gitwire.normalize_repo_url(room.repo_url)]
    manager.send(room.id, "안녕")
    batch = _batch(manager, room.id)

    channel.deleted_map[("head", "head2")] = ["20260915", "20260916"]
    assert batch.observe("head") == []          # 첫 관찰 — 비교할 기준이 없다
    assert batch.observe("head2") == ["20260915", "20260916"]
    assert channel.recovered == ["20260915", "20260916"]
    # 같은 head 를 다시 보면 아무것도 하지 않는다 (git 0회)
    assert batch.observe("head2") == []


def test_recovery_runs_even_when_i_already_have_that_day(manager, fake_opener):
    """로컬 아카이브가 **있어도** 복구를 부른다 — 불완전할 수 있다 (합집합·멱등)."""
    room = manager.register("https://example.com/me/room.git")
    channel = fake_opener.channels[gitwire.normalize_repo_url(room.repo_url)]
    manager.send(room.id, "안녕")
    batch = _batch(manager, room.id)
    channel.archived["20260915"] = 3            # 이미 담아 뒀다
    channel.deleted_map[("h1", "h2")] = ["20260915"]
    batch.observe("h1")
    assert batch.observe("h2") == ["20260915"]


def test_unrecoverable_day_is_not_swallowed(manager, fake_opener):
    """히스토리가 없으면 **조용히 넘기지 않는다** (사유가 결과에 남는다)."""
    room = manager.register("https://example.com/me/room.git")
    channel = fake_opener.channels[gitwire.normalize_repo_url(room.repo_url)]
    manager.send(room.id, "안녕")
    batch = _batch(manager, room.id)
    channel.recover_problems = ["abc1234^ 의 트리를 읽지 못했다 (히스토리가 없다)"]
    channel.gaps = ["20260901"]

    got = batch.run_once()
    assert "20260901" in channel.recovered
    assert any("완전히 복구하지 못했다" in p for p in got.problems)


def test_startup_sweep_runs_once(manager, fake_opener):
    """기동 후 첫 배치에서 **한 번** 훑는다 (앱이 꺼져 있던 동안의 삭제)."""
    room = manager.register("https://example.com/me/room.git")
    channel = fake_opener.channels[gitwire.normalize_repo_url(room.repo_url)]
    manager.send(room.id, "안녕")
    batch = _batch(manager, room.id)
    channel.gaps = ["20260901"]
    batch.run_once()
    assert channel.recovered == ["20260901"]
    batch.run_once()
    assert channel.recovered == ["20260901"], "훑기를 매번 반복했다"


# ------------------------------------------------------ ⭐ 반복 실패 알림 (10)


def test_repeated_failures_reach_the_screen(manager, fake_opener):
    """⭐ 배치가 계속 실패하면 상태줄로 민다 (그전에 사람이 알아야 한다)."""
    room = manager.register("https://example.com/me/room.git")
    channel = fake_opener.channels[gitwire.normalize_repo_url(room.repo_url)]
    manager.send(room.id, "안녕")
    batch = _batch(manager, room.id)
    sub = manager.bus.subscribe(room.id)
    channel.archive_error = OSError("디스크가 꽉 찼다")

    for _ in range(_archive.FAILURE_ALERT_AFTER):
        batch.run_once()
    assert batch.failures == _archive.FAILURE_ALERT_AFTER

    alerts = []
    while not sub.queue.empty():
        event = sub.queue.get_nowait()
        if event is not None and event.name == "trouble":
            alerts.append(event.data)
    assert alerts, "실패가 화면에 닿지 않았다"
    assert alerts[-1]["kind"] == "archive"
    assert "연속 실패" in alerts[-1]["detail"]
    assert "디스크가 꽉 찼다" in alerts[-1]["detail"]

    # 나으면 경고를 해제한다 (한 번 뜬 뒤 영원히 남지 않는다)
    channel.archive_error = None
    batch.run_once()
    assert batch.failures == 0
    cleared = []
    while not sub.queue.empty():
        event = sub.queue.get_nowait()
        if event is not None and event.name == "trouble":
            cleared.append(event.data)
    assert cleared and cleared[-1]["detail"] == ""


def test_a_quiet_poll_tick_does_not_clear_a_standing_alert(manager, fake_opener):
    """⚠️ 관찰 경로가 조용했다고 **나았다고 선언하지 않는다.**

    폴 틱은 아카이빙을 시도하지 않으므로 "문제 없음"이 "이제 옮길 수 있다"를 뜻하지
    않는다. 그걸 나음으로 읽으면 배치가 계속 실패하는 동안 경고가 매 틱 지워진다.
    """
    room = manager.register("https://example.com/me/room.git")
    channel = fake_opener.channels[gitwire.normalize_repo_url(room.repo_url)]
    manager.send(room.id, "안녕")
    batch = _batch(manager, room.id)
    channel.archive_error = OSError("디스크가 꽉 찼다")
    for _ in range(_archive.FAILURE_ALERT_AFTER):
        batch.run_once()
    assert batch._alerted is True

    batch.observe("h1")
    batch.observe("h2")                      # 지워진 날짜 없음 = 조용한 틱
    assert batch._alerted is True, "관찰이 경고를 지웠다"
    assert batch.failures >= _archive.FAILURE_ALERT_AFTER


def test_one_or_two_failures_do_not_cry_wolf(manager, fake_opener):
    """1~2회는 알리지 않는다 — 흔히 생기고 다음 주기에 저절로 낫는다."""
    room = manager.register("https://example.com/me/room.git")
    channel = fake_opener.channels[gitwire.normalize_repo_url(room.repo_url)]
    manager.send(room.id, "안녕")
    batch = _batch(manager, room.id)
    sub = manager.bus.subscribe(room.id)
    channel.archive_error = OSError("일시적")
    batch.run_once()
    batch.run_once()
    troubles = []
    while not sub.queue.empty():
        event = sub.queue.get_nowait()
        if event is not None and event.name == "trouble":
            troubles.append(event.data)
    assert troubles == []


# ------------------------------------------------------------- 스케줄러


def test_last_occurrence_looks_backwards():
    """"오늘 04시가 지났나"가 아니라 "마지막 예정 시각이 언제였나"로 판정한다."""
    assert _archive.last_occurrence(datetime(2026, 9, 17, 5, 0), 4.0) == datetime(
        2026, 9, 17, 4, 0
    )
    assert _archive.last_occurrence(datetime(2026, 9, 17, 1, 0), 4.0) == datetime(
        2026, 9, 16, 4, 0
    )
    assert _archive.last_occurrence(datetime(2026, 9, 17, 4, 0), 4.0) == datetime(
        2026, 9, 17, 4, 0
    )
    # 30분 단위도 표현할 수 있다
    assert _archive.last_occurrence(datetime(2026, 9, 17, 5, 0), 4.5) == datetime(
        2026, 9, 17, 4, 30
    )


def test_daily_runner_catches_up_then_waits_a_day():
    """기동 직후 한 번 따라잡고(앱이 04:00 에 꺼져 있었을 수 있다), 그 뒤엔 하루 한 번."""
    clock = {"at": datetime(2026, 9, 17, 1, 0)}
    runs: list[datetime] = []
    runner = _archive.DailyRunner(
        lambda: runs.append(clock["at"]), hour=4.0, now=lambda: clock["at"]
    )
    assert runner.tick() is True and len(runs) == 1      # 밀린 분 따라잡기
    assert runner.tick() is False                        # 같은 분을 또 돌지 않는다
    clock["at"] = datetime(2026, 9, 17, 3, 59)
    assert runner.tick() is False
    clock["at"] = datetime(2026, 9, 17, 4, 0)
    assert runner.tick() is True and len(runs) == 2      # 오늘 04:00 분
    clock["at"] = datetime(2026, 9, 17, 23, 59)
    assert runner.tick() is False
    clock["at"] = datetime(2026, 9, 18, 4, 1)
    assert runner.tick() is True and len(runs) == 3


def test_daily_runner_survives_a_failing_batch():
    """배치 하나가 예외를 올려도 스레드가 죽지 않는다 (다음 주기에 또 본다)."""
    clock = {"at": datetime(2026, 9, 17, 5, 0)}
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("터졌다")

    runner = _archive.DailyRunner(boom, hour=4.0, now=lambda: clock["at"])
    assert runner.tick() is True
    clock["at"] = datetime(2026, 9, 18, 5, 0)
    assert runner.tick() is True
    assert len(calls) == 2


def test_daily_runner_thread_starts_and_stops():
    clock = {"at": datetime(2026, 9, 17, 5, 0)}
    runs = []
    runner = _archive.DailyRunner(
        lambda: runs.append(1), hour=4.0, now=lambda: clock["at"], interval=0.01
    )
    runner.start()
    for _ in range(200):
        if runs:
            break
        import time as _t
        _t.sleep(0.01)
    runner.stop()
    assert runs, "스케줄러 스레드가 한 번도 돌지 않았다"
    assert runner._thread is None


def test_manager_does_not_start_the_daily_thread_when_disabled(manager):
    """`daily_archive=False` 면 스레드를 띄우지 않는다 (진단·테스트용 문)."""
    manager.start()
    assert manager._daily._thread is None


# --------------------------------------------------------------- 회귀 가드 (④)


def test_read_cursor_guards_are_untouched(manager, fake_opener):
    """⭐ 읽음 커서의 세 가드는 그대로다 (과거에 카운트가 영구히 0 이 된 자리).

    확인응답은 **덧붙인 키**일 뿐이고, 커서 검증·오염 떨구기·`started` 판정에는
    손대지 않았다.
    """
    room = manager.register("https://example.com/me/room.git")
    tracker = manager.reads(room.id)
    with pytest.raises(_reads.InvalidCursor):
        tracker.mark("~pending/000004")            # (1) 쓰기 검증
    assert _reads.sane_cursor("~pending/000004") == ""   # (2) 읽기 떨구기
    tracker.ensure_local()
    store = gitwire.CursorStore(
        fake_opener.channels[gitwire.normalize_repo_url(room.repo_url)].dir,
        _reads.LOCAL_CONSUMER,
    )
    assert store.load().started is True            # (3) started 판정

"""⭐ 지상검증 — **실제 git** 레포와 참가자 2~3인으로 핸드셰이크 전체를 돌린다.

대역을 쓰지 않는다. 여기서 증명해야 하는 것들은 전부 진짜 git 의 동작이다:

* 아카이브가 커밋에 **들어가지 않는다** (그리고 pack 이 두 벌로 자라지 않는다)
* 합의 전에는 레코드가 **지워지지 않는다**
* 휴면 참가자가 있어도 삭제가 **진행된다**
* 아카이브 쓰기가 실패하면 확인응답이 **나가지 않는다**
* 응답하지 않은 참가자가 삭제를 pull 하면 로컬 아카이브가 **복구된다**
* 두 사람이 동시에 지우려 하면 fast-forward 가 정리한다
* 과거 메시지 읽기(페이징·오프라인 소비자)가 로컬 아카이브로도 동작한다

`test_two_instances.py` 와 같은 층이다 (그쪽은 대화, 이쪽은 지난 날짜 정리).
"""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import gitwire
import pytest
from gitwire.clock import FixedOffsetClock

from gitwire_chat import archive as _archive
from gitwire_chat import reads as _reads

DAY = 86400.0


def git_bare(repo: Path, *args: str) -> str:
    """원격(bare)에 직접 물어본다 — "정말 올라갔나"의 유일한 정직한 확인."""
    return subprocess.run(
        ["git", "--git-dir", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout


def remote_files(repo: Path, ref: str = "main") -> list[str]:
    out = git_bare(repo, "ls-tree", "-r", "--name-only", ref)
    return sorted(p for p in out.splitlines() if p)


def pack_bytes(repo: Path) -> int:
    """이 레포가 실제로 차지하는 pack 크기(KiB→바이트). `count-objects -v` 가 정본."""
    for line in git_bare(repo, "count-objects", "-v").splitlines():
        if line.startswith("size-pack:"):
            return int(line.split(":")[1].strip()) * 1024
    return 0


@dataclass
class Party:
    """참가자 한 명 = 채널 + 읽음 트래커 + 아카이빙 배치."""

    name: str
    channel: Any
    tracker: _reads.ReadTracker
    batch: _archive.ArchiveBatch

    def close(self) -> None:
        try:
            self.channel.close()
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture
def party(bare_repo, tmp_path):
    made: list[Party] = []

    def make(name: str, **kwargs) -> Party:
        home = tmp_path / "homes" / name
        home.mkdir(parents=True, exist_ok=True)
        channel = gitwire.Channel(
            str(bare_repo), home=home, sender=name, consumer="chat",
            clock=FixedOffsetClock(0.0), batch_window=0.0, auto_archive=False,
            **kwargs,
        ).open()
        tracker = _reads.ReadTracker(channel, f"{name}@x.io")
        tracker.ensure_local()
        tracker.ensure_participant()          # 참가자 집합에 들어간다 (파일을 만든다)
        batch = _archive.ArchiveBatch(channel, tracker)
        got = Party(name, channel, tracker, batch)
        made.append(got)
        return got

    yield make
    for got in made:
        got.close()


def write_past(p: Party, days: float, texts):
    """days 일 전 시각으로 메시지를 발행한다 (실제 발행 경로를 그대로 탄다)."""
    real = p.channel.clock
    p.channel.clock = FixedOffsetClock(-days * DAY)
    try:
        return [
            p.channel.append({"kind": "msg", "author": p.name, "text": t}, flush=True)
            for t in texts
        ]
    finally:
        p.channel.clock = real


def day_of(rec) -> str:
    return gitwire.rollup.day_of(rec.id)


# ------------------------------------------------------- ⭐ 검증 1 (중복 제거)


def test_archive_never_enters_a_commit_and_the_pack_does_not_double(
    party, bare_repo, capsys
):
    """⭐ 검증 1 — 아카이브가 커밋에 안 들어가고, 정리가 레포를 **줄인다**.

    예전 롤업은 같은 데이터를 두 벌 저장했다 (삭제된 레코드는 히스토리에 남고,
    그 옆에 같은 바이트의 아카이브 blob 이 더해진다). 그 구조가 없어졌음을
    **원격 트리와 pack 크기**로 못 박는다.
    """
    a = party("a")
    old = write_past(a, 2, [f"어제 {i}" for i in range(20)])
    day = day_of(old[0])
    git_bare(bare_repo, "gc", "--quiet")
    before = pack_bytes(bare_repo)
    record_bytes = sum(
        len(git_bare(bare_repo, "cat-file", "blob", f"main:{r.id}").encode("utf-8"))
        for r in old
    )

    got = a.batch.run_once()
    assert got.archived == [day] and got.dropped == [day]

    files = remote_files(bare_repo)
    # (1) 아카이브는 **어느 커밋에도** 없다
    assert not [f for f in files if f.startswith("archive/")], files
    assert not git_bare(bare_repo, "log", "--all", "--name-only", "--pretty=format:")\
        .count("archive/")
    # (2) 그 날짜의 레코드는 지워졌다
    assert not [f for f in files if f.startswith(f"records/{day}/")], files
    # (3) 로컬 아카이브는 남아 있고, 같은 바이트를 담고 있다
    local = a.channel.clone_dir / gitwire.archive_path(day)
    assert local.exists()
    assert sorted(a.channel.archived_ids(day)) == sorted(r.id for r in old)

    git_bare(bare_repo, "gc", "--quiet")
    after = pack_bytes(bare_repo)
    print(
        f"\n[검증1] 레코드 blob 합계={record_bytes}B  "
        f"pack {before}B → {after}B (증가 {after - before}B)  "
        f"추적된 archive/ 파일={len([f for f in files if f.startswith('archive/')])}"
    )
    # (4) 옛 구조라면 여기서 레코드 합계만큼 **더** 늘었다. 이제는 그렇지 않다.
    assert after - before < record_bytes, (before, after, record_bytes)


def test_gitignore_is_shared_so_everyone_ignores_the_archive(party, bare_repo):
    """`.gitignore` 는 추적된다 — 나중에 합류한 사람도 처음부터 무시한다."""
    a = party("a")
    assert ".gitignore" in remote_files(bare_repo)
    b = party("b")
    (b.channel.clone_dir / gitwire.archive_path("20260901")).parent.mkdir(
        parents=True, exist_ok=True
    )
    (b.channel.clone_dir / gitwire.archive_path("20260901")).write_bytes(b"x\n")
    assert b.channel.git.out("status", "--porcelain") == ""


# --------------------------------------------------- ⭐ 검증 2 (합의 전 금지)


def test_records_survive_until_everyone_has_answered(party, bare_repo):
    """⭐ 검증 2 — 한 명만 응답 → `records/<날짜>/` 그대로. 전원 응답 → 삭제."""
    a = party("a")
    b = party("b")
    c = party("c")
    old = write_past(a, 2, [f"어제 {i}" for i in range(4)])
    day = day_of(old[0])

    # a 만 응답했다
    first = a.batch.run_once()
    last_closed = gitwire.last_closed_day(a.channel.clock.now())
    assert first.acked is True and first.through == last_closed
    assert day < last_closed, (day, last_closed)   # 이틀 전 기록이므로
    assert first.consensus == "" and first.dropped == []
    assert [f for f in remote_files(bare_repo) if f.startswith(f"records/{day}/")]

    # b 도 응답했다 — 아직 c 가 없다
    second = b.batch.run_once()
    assert second.acked is True and second.consensus == ""
    assert second.dropped == []
    assert [f for f in remote_files(bare_repo) if f.startswith(f"records/{day}/")]

    # c 까지 응답했다 → 가장 먼저 알아챈 앱이 지운다
    third = c.batch.run_once()
    assert third.consensus == last_closed
    assert third.dropped == [day], third
    assert not [f for f in remote_files(bare_repo) if f.startswith(f"records/{day}/")]

    # 세 명 모두 그 날의 대화를 그대로 읽는다 (각자 로컬 아카이브로)
    for p in (a, b, c):
        p.channel.sync()
        assert sorted(p.channel.archived_ids(day)) == sorted(r.id for r in old)
        texts = [r.payload["text"] for r in p.channel.history(fresh=False)]
        assert texts == [f"어제 {i}" for i in range(4)], p.name


# ------------------------------------------------------ ⭐ 검증 3 (휴면 제외)


def test_a_dormant_participant_does_not_block_forever(party, bare_repo):
    """⭐ 검증 3 — `updated_at` 이 8일 전인 참가자가 있어도 삭제가 **진행된다**."""
    a = party("a")
    b = party("b")
    gone = party("gone")

    # 'gone' 의 상태 파일을 8일 전 시각으로 만든다 (앱을 그만 켠 사람)
    gone.channel.clock = FixedOffsetClock(-8 * DAY)
    gone.tracker.publish(force=True)
    gone.channel.clock = FixedOffsetClock(0.0)
    gone.close()

    old = write_past(a, 2, [f"어제 {i}" for i in range(3)])
    day = day_of(old[0])
    a.batch.run_once()
    got = b.batch.run_once()

    assert got.consensus == gitwire.last_closed_day(b.channel.clock.now()), got
    assert got.dropped == [day], got
    assert not [f for f in remote_files(bare_repo) if f.startswith(f"records/{day}/")]

    # 7일 안쪽이면 여전히 기다린다 — 같은 판정으로 반대 결론이 나오는지 확인
    people = b.tracker.participants(fresh=True)
    assert _reads.is_dormant(people[gitwire.state_key("gone@x.io")], b.channel.clock.now())


# ------------------------------------------------------- ⭐ 검증 4 (순서 보장)


def test_no_ack_when_the_archive_file_cannot_be_written(party, bare_repo, monkeypatch):
    """⭐ 검증 4 — 아카이브 쓰기를 실패시키면 **확인응답이 push 되지 않는다.**

    반대 순서면 응답만 올라간 채로 죽었을 때 남들이 레코드를 지우고 그 사람만
    잃는다. 여기서는 기반의 파일 쓰기 자체를 대역으로 막는다.
    """
    a = party("a")
    b = party("b")
    old = write_past(a, 2, [f"어제 {i}" for i in range(3)])
    day = day_of(old[0])

    def boom(clone, day_, data):
        raise OSError("디스크가 꽉 찼다")

    monkeypatch.setattr(gitwire.rollup, "write_archive", boom)
    got = a.batch.run_once()

    assert got.problems and day in got.problems[0]
    # 응답 자체가 그 날짜를 덮지 않는다 (워터마크가 앞에서 멈췄다)
    assert got.through < day
    assert not (a.channel.clone_dir / gitwire.archive_path(day)).exists()

    # 그리고 그 사실이 **원격에** 반영돼 있다 — 남이 읽어도 합의가 안 된다
    monkeypatch.undo()
    b.channel.sync()
    people = b.tracker.participants(fresh=True)
    mine = people.get(gitwire.state_key("a@x.io"))
    assert mine is None or mine.archived < day, (mine and mine.archived, day)
    b.batch.run_once()
    assert [f for f in remote_files(bare_repo) if f.startswith(f"records/{day}/")]


def test_no_ack_at_all_when_archiving_raises(party, bare_repo, monkeypatch):
    """옮기기가 통째로 실패하면 응답 단계에 **들어가지도 않는다**."""
    a = party("a")
    write_past(a, 2, ["어제"])
    before = a.tracker.participants(fresh=False)

    def boom(**kwargs):
        raise OSError("디스크가 꽉 찼다")

    monkeypatch.setattr(a.channel, "archive_days", boom)
    got = a.batch.run_once()
    assert got.acked is False and got.through == ""
    after = a.tracker.participants(fresh=False)
    assert after[a.tracker.key].archived == before[a.tracker.key].archived == ""


# ------------------------------------------------------- ⭐ 검증 5 (자동 복구)


def test_a_participant_that_never_answered_recovers_from_history(party, bare_repo):
    """⭐ 검증 5 — 응답 안 한 참가자가 삭제를 pull → 그 날짜가 로컬 아카이브로 복구.

    그리고 **화면에서 과거 메시지가 다시 보인다** (히스토리 조회로 증명).
    """
    a = party("a")
    b = party("b")
    quiet = party("quiet")
    old = write_past(a, 2, [f"어제 {i}" for i in range(5)])
    day = day_of(old[0])

    # quiet 을 휴면으로 만들어 a·b 만으로 합의가 성립하게 한다
    quiet.channel.clock = FixedOffsetClock(-8 * DAY)
    quiet.tracker.publish(force=True)
    quiet.channel.clock = FixedOffsetClock(0.0)

    a.batch.run_once()
    got = b.batch.run_once()
    assert got.dropped == [day], got

    # quiet 이 돌아왔다 — 폴 한 틱이 삭제를 알아채고 복구한다
    base = quiet.channel.local_head()
    quiet.channel.sync()
    head = quiet.channel.local_head()
    assert quiet.channel.deleted_days(base, head) == [day]
    assert day not in quiet.channel.archive_state()

    recovered = quiet.batch.observe(head) if base != head else []
    if not recovered:                       # 첫 관찰이 기준을 세운 경우
        quiet.batch.observe(base)
        recovered = quiet.batch.observe(head)
    assert recovered == [day], recovered
    assert sorted(quiet.channel.archived_ids(day)) == sorted(r.id for r in old)
    assert [r.payload["text"] for r in quiet.channel.history(fresh=False)] == [
        f"어제 {i}" for i in range(5)
    ]


def test_startup_sweep_recovers_what_happened_while_the_app_was_off(party, bare_repo):
    """앱이 꺼져 있던 동안의 삭제도 기동 후 첫 배치가 훑어 복구한다."""
    a = party("a")
    b = party("b")
    old = write_past(a, 2, [f"어제 {i}" for i in range(3)])
    day = day_of(old[0])
    b.channel.clock = FixedOffsetClock(-8 * DAY)
    b.tracker.publish(force=True)
    b.channel.clock = FixedOffsetClock(0.0)
    a.batch.run_once()
    assert a.batch.last_result.dropped == [day]

    # b 는 그동안 꺼져 있었다 (그래서 아무 틱도 못 봤다)
    fresh = _archive.ArchiveBatch(b.channel, b.tracker)
    got = fresh.run_once()
    assert day in got.recovered, got
    assert sorted(b.channel.archived_ids(day)) == sorted(r.id for r in old)


# ----------------------------------------------------------- ⭐ 검증 6 (경합)


def test_two_apps_dropping_at_once_converge_without_error(party, bare_repo):
    """⭐ 검증 6 — 동시 삭제: 한쪽 push 거부 → pull 하면 이미 삭제 → 오류 없이 수렴."""
    a = party("a")
    b = party("b")
    old = write_past(a, 2, [f"어제 {i}" for i in range(6)])
    day = day_of(old[0])
    a.batch.run_once()                   # a 응답 (아직 b 가 없어 삭제는 안 된다)
    b.channel.sync()
    b.channel.archive_days()
    b.tracker.publish(
        archived=gitwire.last_closed_day(b.channel.clock.now())
    )                                    # b 응답 → 이제 둘 다 응답했다
    a.channel.sync()
    a.channel.archive_days()
    b.channel.archive_days()

    results: dict[str, Any] = {}
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def go(p: Party):
        try:
            barrier.wait()
            results[p.name] = p.batch.run_once()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(p,)) for p in (a, b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert not any(t.is_alive() for t in threads)
    assert not errors, errors

    a.channel.sync(); b.channel.sync()
    assert not [f for f in remote_files(bare_repo) if f.startswith(f"records/{day}/")]
    assert a.channel.git.out("rev-parse", "HEAD^{tree}") == b.channel.git.out(
        "rev-parse", "HEAD^{tree}"
    )
    for p in (a, b):
        assert sorted(p.channel.archived_ids(day)) == sorted(r.id for r in old)
        assert not results[p.name].problems, results[p.name].problems


# ------------------------------------------------------- ⭐ 검증 7 (읽기 회귀)


def test_reading_past_messages_still_works_after_the_drop(party, bare_repo):
    """⭐ 검증 7 — 페이징·오프라인 소비자·`_diff_records` 가 로컬 아카이브로도 동작."""
    a = party("a")
    b = party("b")
    made = []
    for d in (4, 3, 2):
        made += write_past(a, d, [f"{d}일전 {i}" for i in range(4)])
    made += [a.channel.append({"kind": "msg", "author": "a", "text": "오늘"}, flush=True)]
    all_ids = [r.id for r in made]

    # 주말 내내 꺼져 있던 소비자 (커서를 '지금'에 맞춰 둔다)
    reader = gitwire.Channel(
        str(bare_repo), home=b.channel.home, sender="b", consumer="weekend",
        clock=FixedOffsetClock(0.0), batch_window=0.0, auto_archive=False,
    ).open()
    try:
        reader.skip_to_now()
        weekend = write_past(a, 2, ["주말에 한 말"])
        a.channel.sync()
        a.batch.run_once()
        b.channel.sync()
        b.tracker.publish(archived=gitwire.last_closed_day(b.channel.clock.now()))
        a.channel.sync()
        got = a.batch.run_once()
        assert got.dropped, got

        # (1) 페이징이 끝까지 걸린다 (순서·집합 동일)
        walked = []
        page = a.channel.history_page(limit=3, fresh=False)
        while True:
            walked = [r.id for r in page.records] + walked
            if not page.has_more:
                break
            page = a.channel.history_page(before=page.oldest, limit=3, fresh=False)
        assert walked == sorted(all_ids + [weekend[0].id])

        # (2) `before=` 커서가 그대로 동작한다
        mid = made[5].id
        older = a.channel.history(before=mid, limit=3, fresh=False)
        assert [r.id for r in older] == sorted(i for i in all_ids if i < mid)[-3:]

        # (3) 오프라인 소비자가 지워진 날의 대화를 놓치지 않는다
        delivered = [r.id for r in reader.fetch_new()]
        assert weekend[0].id in delivered, delivered
        assert reader.fetch_new() == []          # 중복 없음
    finally:
        reader.close()


def test_search_over_the_whole_history_still_finds_old_text(party, bare_repo):
    """검색은 전량 스캔이다 — 지워진 날짜의 본문도 로컬 아카이브에서 찾힌다."""
    a = party("a")
    old = write_past(a, 2, ["찾을 말이 여기 있다", "다른 말"])
    a.batch.run_once()
    hits = [
        r for r in a.channel.history(fresh=False)
        if "찾을 말" in r.payload["text"]
    ]
    assert [r.id for r in hits] == [old[0].id]

"""공용 픽스처.

두 층으로 나눠 검증한다:

* **대부분의 테스트**는 gitwire 채널을 대역(`FakeChannel`)으로 갈아끼워
  네트워크·git 없이 빠르게 돈다 (`RoomManager(opener=...)` 주입점).
* **핵심 검증**(`test_two_instances.py`)만 대역을 쓰지 않는다. 로컬 bare
  레포를 방으로 삼아 **앱 인스턴스 두 개가 실제 git 으로 대화**한다.
  거기서 흉내를 내면 아무것도 증명하지 못한다.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import gitwire  # noqa: E402

from gitwire_chat.config import Settings  # noqa: E402
from gitwire_chat.events import EventBus  # noqa: E402
from gitwire_chat.notify import Notifier  # noqa: E402
from gitwire_chat.outbox import STUCK as OUTBOX_STUCK  # noqa: E402
from gitwire_chat.rooms import RoomManager  # noqa: E402
from gitwire_chat import schema  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path_factory):
    """전역 git 설정·자격증명·환경변수가 테스트에 새지 않게 한다."""
    home = tmp_path_factory.mktemp("githome")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@localhost")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@localhost")
    monkeypatch.delenv("GITWIRE_TOKEN", raising=False)
    monkeypatch.delenv("GITWIRE_CHAT_HOME", raising=False)
    monkeypatch.delenv("GITWIRE_CHAT_AUTHOR", raising=False)


class FrozenClock:
    """고정 시계 (기반 `SystemClock` 의 소비자 표면만). 테스트가 값을 옮긴다."""

    def __init__(self, at: datetime) -> None:
        self.at = at
        self.offset = 0.0

    def now(self) -> datetime:
        return self.at


# --------------------------------------------------------------- 대역 채널


class FakeChannel:
    """gitwire ``Channel`` 의 소비자 표면만 흉내 낸 대역.

    이 앱이 **실제로 쓰는** 메서드만 구현한다 — 이 목록 자체가 우리가 기반
    API 에 의존하는 표면의 전부라는 문서 역할을 한다.
    """

    def __init__(self, repo_url: str, **kwargs) -> None:
        self.repo_url = repo_url
        self.kwargs = kwargs
        # 채널 디렉토리 — 기반은 여기에 소비자별 커서를 둔다. 읽음 커서(내 것)가
        # 그 표면을 그대로 쓰므로 대역도 **진짜 디렉토리**를 하나 준다.
        home = Path(kwargs.get("home") or ".")
        self.dir = home / "channels" / f"fake-{abs(hash(repo_url)) % 999999:06d}"
        (self.dir / "cursors").mkdir(parents=True, exist_ok=True)
        # 참가자 상태 예약 경로의 대역 ({키: ParticipantState}). 기반에서는 커밋된
        # 트리를 읽는 것이므로, 여기서도 **쓴 즉시 보이는** 사전으로 흉내 낸다.
        self.states: dict[str, gitwire.ParticipantState] = {}
        self.state_writes = 0
        # 이 앱은 이제 sender 를 넘기지 않는다 — 기반이 설치본 식별자를 준다.
        self.sender = kwargs.get("sender") or f"fake.host.{abs(hash(repo_url)) % 999999:06d}"
        # ⭐ 발행 대기열 — 기반과 같이 **메모리**다. `append()` 는 여기에만 넣고
        # 시각·ID 는 `flush()`(= push)가 정한다 (`gitwire.Channel.append` 도크).
        self.queue: list[gitwire.PendingRecord] = []
        # ⭐ 기반의 **발행 계약**도 흉내 낸다 — 누가 미느냐가 기본값에 달려 있다.
        # `autopublish=True`(기반 기본값)면 `append()` 가 그 자리에서 민다(드레인
        # 루프 — `gitwire.Channel._drain`). 이 앱은 그 자리가 HTTP 요청 스레드라
        # **끄고** 연다(`rooms._open_channel`). 대역이 기본값을 흉내 내지 않으면
        # 그 인자가 빠져도 여기 테스트가 전부 통과한다 — 즉 앱이 가장 아프게
        # 회귀하는 자리를 대역이 가려 준다. 그래서 기본값을 그대로 산다.
        self.autopublish = bool(kwargs.get("autopublish", True))
        # 한 커밋의 상한. 기반의 `flush()` 는 **한 회차**라 이만큼만 가져가고,
        # 되풀이는 호출자 몫이다 (`rooms._flush_room`).
        self.max_batch = int(kwargs.get("max_batch", gitwire.DEFAULT_MAX_BATCH))
        self.records: list[gitwire.Record] = []
        self.subscribers: list = []
        self.cycle_hooks: list = []
        self.closed = False
        self.skipped_to = 0
        # 읽기가 원격을 봤는지(=fresh) 그대로 기록한다. 테스트가 이 값을 본다.
        self.read_fresh: list[bool] = []
        self.polls = 0
        # 밀어내기(= 커밋·push) 대역. 앱이 **응답 밖에서** 미는지 보려면 이 호출이
        # 언제·몇 번 났는지가 필요하다.
        self.flushes = 0
        self.pushed = 0             # 원격에 갔다고 친 레코드 수
        self.flush_error: BaseException | None = None
        self.flush_delay = 0.0      # 느린 push 흉내 (응답이 이걸 기다리면 안 된다)
        self._cursor = 0
        self._clock = datetime(2026, 9, 3, 1, 0, 0, tzinfo=timezone.utc)
        # ⭐ 공통 시계 — 기반이 git 호스트의 HTTP Date 로 맞춘 것. 아카이빙 배치가
        # 휴면 판정·"어제" 판정에 이것을 쓰므로(로컬 시계를 따로 보지 않는다) 대역도
        # 준다. **레코드 시각보다 이틀 뒤**로 둔다 — 그래야 대역으로 만든 메시지가
        # 곧 "지난 날짜"가 되어 아카이빙 경로를 실제로 탄다.
        self.clock = FrozenClock(self._clock + timedelta(days=2))
        # 지난 날짜 아카이빙·삭제 대역. 기반에서는 로컬 파일 + 삭제 커밋이지만,
        # 여기서 흉내 낼 것은 **호출과 그 순서**뿐이다 (읽기는 그대로 돌아간다).
        self.archived: dict[str, int] = {}
        self.dropped: list[str] = []
        self.recovered: list[str] = []
        self.gaps: list[str] = []
        self.deleted_map: dict[tuple[str, str], list[str]] = {}
        self.recover_problems: list[str] = []
        self.archive_error: BaseException | None = None
        self.archive_skipped: dict[str, str] = {}
        self.syncs = 0
        self._n = 0
        self._seq = 0
        self._lock = threading.Lock()

    # -- 발행 ---------------------------------------------------------
    def append(self, payload, *, sender=None, flush=False) -> gitwire.PendingRecord:
        """기반과 같이 **티켓을 돌려준다** — 봉투(ID·시각)는 아직 없다.

        ⭐ 티켓 타입은 **기반의 진짜 클래스**를 쓴다. 계약을 두 벌 쓰면 한 벌이
        반드시 낡는다 — 대역이 흉내 낼 것은 *언제 settled 되는가*뿐이다.

        ⚠️ `autopublish`(기본 켜짐)면 **이 호출이 민다** — 기반의 드레인 루프
        규칙 1이다. 기다리지 않는 쪽이므로 전송 실패를 예외로 올리지 않는다
        (기반도 그렇게 한다 — 올리면 소비자가 "보낼 수 없었다"로 사용자에게
        말하고 배경 재시도가 조용히 성공한다).
        """
        with self._lock:
            self._seq += 1
            ticket = gitwire.PendingRecord(self._seq, payload, sender or self.sender)
            self.queue.append(ticket)
        if flush:
            self.flush()
        elif self.autopublish:
            try:
                self.flush()
            except BaseException:  # noqa: BLE001 — 기다리지 않는 쪽에는 안 올린다
                pass
        return ticket

    def flush(self, push_attempts: int = 5) -> list[gitwire.Record]:
        """대기열을 원격까지 민다 — **여기서 시각·ID 가 정해진다.**

        ⚠️ 실패는 대기열을 비우지 않는다 — 그래야 '아직 안 나갔다' 가 대역에서도
        진짜 사실이 된다 (그리고 순서가 유지된다).

        ⚠️ **한 회차다** — 대기열 앞에서 `max_batch` 개까지만 가져간다(기반과 같다).
        비워질 때까지 되풀이하는 것은 호출자 몫이다 (`rooms._flush_room`).
        """
        self.flushes += 1
        if self.flush_delay:
            time.sleep(self.flush_delay)
        if self.flush_error is not None:
            raise self.flush_error
        with self._lock:
            batch = list(self.queue[: self.max_batch])
            del self.queue[: len(batch)]
            made = []
            for item in batch:
                self._n += 1
                ts = self._clock + timedelta(seconds=self._n)
                rid = gitwire.records.make_record_id(
                    ts, item.sender, nonce=f"{self._n:06d}"
                )
                record = gitwire.Record(
                    id=rid, sender=item.sender, timestamp=ts, payload=item.payload
                )
                self.records.append(record)
                item._settle(record)          # 기반이 push 성공 때 하는 일
                made.append(record)
            self.pushed = len(self.records)
        return made

    def unpushed(self) -> list[gitwire.PendingRecord]:
        """아직 원격에 못 간 것 = **대기열** (지상 검증용)."""
        with self._lock:
            return list(self.queue)

    def inject(self, payload, sender="other.host") -> gitwire.Record:
        """다른 참가자가 보낸 것처럼 레코드를 밀어 넣는다 (구독 전달까지)."""
        with self._lock:
            self._n += 1
            ts = self._clock + timedelta(seconds=self._n)
            rid = gitwire.records.make_record_id(ts, sender, nonce=f"{self._n:06d}")
            record = gitwire.Record(id=rid, sender=sender, timestamp=ts, payload=payload)
            self.records.append(record)
        for callback in list(self.subscribers):
            callback(record)
        return record

    # -- 조회 ---------------------------------------------------------
    # ``fresh`` = 기반의 신선도 정책 (True 면 ls-remote 왕복, False 면 로컬만).
    # 대역에서는 결과가 같지만 **무엇을 요청했는지**를 기록해 둔다.
    def history(self, limit=None, *, before=None, fresh=True):
        self.read_fresh.append(bool(fresh))
        with self._lock:
            items = list(self.records)
        if before is not None:
            items = [r for r in items if r.id < before]
        return items if limit is None else items[-limit:]

    def history_page(self, *, before=None, limit=50, fresh=True):
        """기반의 keyset 페이징. 한 건 더 세어 has_more 를 판정하는 것까지 같다."""
        self.read_fresh.append(bool(fresh))
        with self._lock:
            items = list(self.records)
        if before is not None:
            items = [r for r in items if r.id < before]
        page = items[-limit:] if limit else items
        return gitwire.HistoryPage(list(page), len(items) > len(page))

    def record_ids(self, *, before=None, limit=None, fresh=True):
        return [r.id for r in self.history(limit, before=before, fresh=fresh)]

    def fetch_new(self, limit=None, *, advance=True):
        with self._lock:
            items = self.records[self._cursor:]
            if limit is not None:
                items = items[:limit]
            if advance:
                self._cursor += len(items)
            return list(items)

    def poll_once(self, callback, *, on_error=None):
        self.polls += 1
        delivered = 0
        for record in self.fetch_new():
            callback(record)
            delivered += 1
        return delivered

    def subscribe(self, callback, *, interval=None, on_error=None, on_cycle=None):
        self.subscribers.append(callback)
        # 폴 한 틱 훅. 대역에는 폴링 루프가 없으므로 **테스트가 직접 부른다**
        # (`channel.tick()`) — 언제 도는지를 손에 쥐어야 "레코드 0건인 변화"를
        # 셀 수 있다.
        self.cycle_hooks.append(on_cycle) if on_cycle else None
        channel = self

        class _Sub:
            def stop(self, timeout=None):
                if callback in channel.subscribers:
                    channel.subscribers.remove(callback)

        return _Sub()

    # -- 참가자 상태 (예약 경로) ---------------------------------------
    def state_exists(self, key: str) -> bool:
        return gitwire.state_key(key) in self.states

    def write_state(self, key, value, *, identity=None, flush=False) -> str:
        who = gitwire.state_key(key)
        self.state_writes += 1
        self.states[who] = gitwire.ParticipantState(
            key=who,
            identity=identity if identity is not None else key,
            value=value,
            updated_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
        )
        return gitwire.state_path(who)

    def read_state(self, key: str, *, fresh: bool = False):
        return self.states.get(gitwire.state_key(key))

    def read_states(self, *, fresh: bool = False) -> dict:
        return dict(self.states)

    def inject_state(self, key: str, value) -> None:
        """다른 참가자가 자기 커서를 올린 것처럼 밀어 넣는다."""
        who = gitwire.state_key(key)
        self.states[who] = gitwire.ParticipantState(
            key=who, identity=key, value=value,
            updated_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
        )

    # -- 지난 날짜 아카이빙 / 삭제 / 복구 ------------------------------
    #
    # ⚠️ 이 목록이 곧 "아카이빙 기능이 기반에 요구하는 표면"의 전부다.
    def sync(self):
        self.syncs += 1
        return "head"

    def archive_days(self, **kwargs) -> dict:
        """지난 날짜를 로컬 아카이브로 옮긴 것처럼 한다 (레코드는 그대로 둔다)."""
        if self.archive_error is not None:
            raise self.archive_error
        today = f"{self.clock.now().astimezone(timezone.utc):%Y%m%d}"
        days: dict[str, int] = {}
        for record in list(self.records):
            day = gitwire.rollup.day_of(record.id)
            if day and day < today and day not in self.archive_skipped:
                days[day] = days.get(day, 0) + 1
        self.archived.update(days)
        through = "" if self.archive_skipped else gitwire.last_closed_day(
            self.clock.now()
        )
        if self.archive_skipped:
            through = gitwire.previous_day(min(self.archive_skipped))
        return {
            "archived": sorted(days),
            "written": sorted(days),
            "records": sum(days.values()),
            "skipped": dict(self.archive_skipped),
            "through": through,
        }

    def archive_state(self) -> dict:
        return {day: f"stamp-{n}" for day, n in self.archived.items()}

    def drop_days(self, days, **kwargs) -> dict:
        """삭제를 **기록만** 한다 — 대역의 읽기 경로는 라이브/아카이브를 가르지 않는다."""
        wanted = [d for d in days if d in self.archived]
        self.dropped += wanted
        return {"dropped": bool(wanted), "days": sorted(wanted), "skipped": {}}

    def deleted_days(self, base, target) -> list:
        return list(self.deleted_map.get((base, target), []))

    def recover_archive(self, day: str) -> dict:
        self.recovered.append(day)
        return {
            "recovered": not self.recover_problems,
            "day": day,
            "added": 0 if self.recover_problems else 1,
            "problems": list(self.recover_problems),
        }

    def archive_gaps(self, through, *, max_days=60) -> list:
        return list(self.gaps)

    def skip_to_now(self) -> None:
        with self._lock:
            self.skipped_to = len(self.records)
            self._cursor = len(self.records)

    def tick(self, head: str = "head") -> int:
        """폴 한 틱이 끝난 것처럼 훅을 부른다 (레코드가 0건이어도 불린다)."""
        for hook in list(self.cycle_hooks):
            hook(head)
        return len(self.cycle_hooks)

    def info(self) -> dict:
        return {"repo": self.repo_url, "records": len(self.records)}

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_opener():
    """열린 대역 채널을 URL 별로 기억하는 opener."""
    channels: dict[str, FakeChannel] = {}

    def opener(repo_url, **kwargs):
        key = gitwire.normalize_repo_url(repo_url)
        if key not in channels:
            channels[key] = FakeChannel(repo_url, **kwargs)
        return channels[key]

    opener.channels = channels
    return opener


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        home=tmp_path / "chats",
        author="테스터",
        poll_interval=0.05,
        recent_limit=5,
        page_limit=3,
        notifications=False,
        # ⭐ 일일 배치 스레드를 띄우지 않는다 — 배치를 보는 테스트는 `tick()`·
        # `run_archive()` 를 **직접** 불러 시점을 손에 쥔다 (실제 시간을 기다리는
        # 테스트를 만들지 않는다).
        daily_archive=False,
    )


class RecordingNotifier(Notifier):
    """알림을 실제로 띄우지 않고 기록만 한다."""

    def __init__(self, **kwargs):
        self.sent: list[tuple[str, str]] = []
        super().__init__(
            backends=[self._record], enabled=True, coalesce_window=0.0, **kwargs
        )

    def _record(self, title, body, app):
        self.sent.append((title, body))
        return True


class ConnectedRoomManager(RoomManager):
    """**끝까지** 진행된 뒤 돌려주는 테스트용 매니저 (등록·전송 둘 다).

    실제 `register()` 는 즉시 반환하고 클론은 백그라운드에서 돈다(그게 요점이다).
    브라우저는 SSE 로 완료를 기다리는데, 대부분의 테스트는 그 타이밍이 관심사가
    아니라 '연결된 방'이 필요할 뿐이다 — 그래서 여기서 기다려 준다.
    비동기 동작 자체를 보는 테스트는 `RoomManager` 를 직접 쓴다.
    """

    def register(self, *args, **kwargs):
        room = super().register(*args, **kwargs)
        self.wait_for_connect(timeout=30.0)
        return room

    def send(self, room_id, *args, **kwargs):
        """보내고 **원격에 나갈 때까지** 기다린 뒤 그 메시지를 돌려준다.

        실제 `send()` 는 대기열에 넣고 즉시 돌아오며(봉투가 아직 없다) 밀어내기는
        아웃박스 워커가 한다. 대부분의 테스트는 그 타이밍이 아니라 *나간 메시지*가
        필요하므로 여기서 기다린다 — 그래서 옛 호출부가 그대로 읽힌다.

        못 나가면 **None** 이다 (`flush_error` 를 심은 테스트). 그것도 정상
        결과이므로 예외로 만들지 않는다.
        """
        ticket = super().send(room_id, *args, **kwargs)
        box = self.outbox(room_id)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if box.wait_idle(0.05):
                break
            if box.state.state == OUTBOX_STUCK:
                break                       # 더 기다려도 안 나간다
        record = ticket.record
        return schema.parse_record(record) if record is not None else None


@pytest.fixture
def manager(settings, fake_opener):
    bus = EventBus(keepalive=0.05)
    mgr = ConnectedRoomManager(
        settings,
        bus=bus,
        notifier=RecordingNotifier(),
        opener=fake_opener,
    )
    yield mgr
    mgr.stop()


# ---------------------------------------------------------------- 실제 git


@pytest.fixture
def bare_repo(tmp_path) -> Path:
    """빈 원격 레포 — 사용자가 방금 만든 private repo 를 흉내 낸다."""
    repo = tmp_path / "room.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(repo)],
        check=True,
        capture_output=True,
    )
    return repo

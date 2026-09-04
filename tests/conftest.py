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
from gitwire_chat.rooms import RoomManager  # noqa: E402

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


# --------------------------------------------------------------- 대역 채널


class FakeChannel:
    """gitwire ``Channel`` 의 소비자 표면만 흉내 낸 대역.

    이 앱이 **실제로 쓰는** 메서드만 구현한다 — 이 목록 자체가 우리가 기반
    API 에 의존하는 표면의 전부라는 문서 역할을 한다.
    """

    def __init__(self, repo_url: str, **kwargs) -> None:
        self.repo_url = repo_url
        self.kwargs = kwargs
        # 이 앱은 이제 sender 를 넘기지 않는다 — 기반이 설치본 식별자를 준다.
        self.sender = kwargs.get("sender") or f"fake.host.{abs(hash(repo_url)) % 999999:06d}"
        self.records: list[gitwire.Record] = []
        self.subscribers: list = []
        self.closed = False
        self.skipped_to = 0
        # 읽기가 원격을 봤는지(=fresh) 그대로 기록한다. 테스트가 이 값을 본다.
        self.read_fresh: list[bool] = []
        self.polls = 0
        self._cursor = 0
        self._clock = datetime(2026, 9, 3, 1, 0, 0, tzinfo=timezone.utc)
        self._n = 0
        self._lock = threading.Lock()

    # -- 발행 ---------------------------------------------------------
    def append(self, payload, *, sender=None, flush=False) -> gitwire.Record:
        """기반과 같이 **Record 를 돌려준다** (ID 문자열이 아니다)."""
        with self._lock:
            self._n += 1
            ts = self._clock + timedelta(seconds=self._n)
            who = sender or self.sender
            rid = gitwire.records.make_record_id(ts, who, nonce=f"{self._n:06d}")
            record = gitwire.Record(id=rid, sender=who, timestamp=ts, payload=payload)
            self.records.append(record)
        for callback in list(self.subscribers):
            callback(record)
        return record

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

    def subscribe(self, callback, *, interval=None, on_error=None):
        self.subscribers.append(callback)
        channel = self

        class _Sub:
            def stop(self, timeout=None):
                if callback in channel.subscribers:
                    channel.subscribers.remove(callback)

        return _Sub()

    def skip_to_now(self) -> None:
        with self._lock:
            self.skipped_to = len(self.records)
            self._cursor = len(self.records)

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
    """등록이 **끝까지** 진행된 뒤 돌려주는 테스트용 매니저.

    실제 `register()` 는 즉시 반환하고 클론은 백그라운드에서 돈다(그게 요점이다).
    브라우저는 SSE 로 완료를 기다리는데, 대부분의 테스트는 그 타이밍이 관심사가
    아니라 '연결된 방'이 필요할 뿐이다 — 그래서 여기서 기다려 준다.
    비동기 동작 자체를 보는 테스트는 `RoomManager` 를 직접 쓴다.
    """

    def register(self, *args, **kwargs):
        room = super().register(*args, **kwargs)
        self.wait_for_connect(timeout=30.0)
        return room


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

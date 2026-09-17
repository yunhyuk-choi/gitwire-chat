"""일일 아카이빙 배치 — 옮기고, 확인응답하고, **전원이 응답하면** 지운다.

무엇을 푸는가
-------------
지난 날짜의 레코드를 레포에서 덜어내고 싶다. 그런데 아카이브를 **커밋하면서**
레코드를 지우면 같은 데이터가 두 벌 저장된다 — 지워진 레코드는 히스토리에 영구히
남고 그 옆에 같은 바이트의 아카이브 blob 이 더해진다 (실측 pack **+11%**).

그래서 아카이브는 **각자 로컬**에만 두고(`gitwire.rollup` 상단), 레코드 삭제는
**전원이 "나는 그 날짜까지 다 옮겼다"고 말한 뒤에만** 한다. 이 모듈이 그 핸드셰이크
의 소비자 쪽이다.

⭐ 다섯 단계 (순서가 곧 안전이다)
--------------------------------
    1. 옮긴다      channel.archive_days()   → 로컬 파일 (fsync 까지)
    2. 응답한다    tracker.publish(archived=through) → participants/<나>.json
    3. 판정한다    전원(휴면 제외)의 응답 최소값 = 합의 날짜
    4. 지운다      channel.drop_days(...)   → 평범한 커밋 1개 + fast-forward push
    5. 복구한다    남이 지운 날짜가 내 아카이브에 없거나 불완전하면 히스토리에서

⚠️⚠️ **1 → 2 의 순서를 절대 뒤집지 않는다.** 응답을 먼저 보내고 죽으면, 남들이
그것을 믿고 레코드를 지우고 **그 사람만 잃는다.** `archive_days()` 는 파일을
fsync 한 뒤에 돌아오고, `publish()` 는 그 뒤에만 불린다. 1단계가 일부라도
실패하면 워터마크가 그 앞에서 멈추므로(기반이 그렇게 계산한다) 응답도 그만큼만
올라간다.

⭐ 경합은 **fast-forward push 가 정리한다**
-------------------------------------------
두 사람이 동시에 같은 날짜를 지우려 하면 한쪽 push 가 거부된다. 진 쪽은 pull 하면
이미 지워져 있어 할 일이 없어진다. 락도 리더 선출도 없다 (`Channel.drop_days`).

⭐ 배치 **시각**과 "어제"의 **정의**는 다른 것이다
-------------------------------------------------
* 실행 시각 = 각자 **로컬 04:00** (설정 가능). 참가자마다 시간대가 달라도 상관
  없다 — 삭제는 합의 뒤라 시각을 맞출 필요가 없다.
* "어제"의 정의 = **UTC 날짜**. 폴더 이름이 UTC 날짜이기 때문이다. 판정은 전부
  기반이 한다 (`gitwire.last_closed_day`) — 여기서 로컬 달력으로 다시 세지 않는다.

휴면 참가자
----------
`updated_at` 이 7일 이상 움직이지 않은 참가자는 합의에서 **제외한다**
(`reads.DORMANT_DAYS`). 한 명이 앱을 안 켜면 삭제가 영원히 일어나지 않고, 퇴사자면
영구히다. 제외해도 **데이터를 버리지 않는다** — 그 사람이 돌아와 삭제를 pull 하면
5단계가 히스토리에서 자기 아카이브를 복구한다.

배치가 계속 실패하면 화면에 알린다
--------------------------------
옮기기가 반복 실패하는 사람은 응답이 올라가지 않아 **모두의 삭제를 막는다.**
휴면 규칙에 결국 걸리지만 그전에 사람이 알아야 한다 — 그래서 연속 실패가 문턱을
넘으면 상태줄로 민다 (`FAILURE_ALERT_AFTER`).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

import gitwire

from . import reads as _reads

log = logging.getLogger(__name__)

#: 배치를 돌리는 **로컬** 시각 (시). 기본 04:00 — 사람이 자는 시간이고, 그날의
#: 대화가 시작되기 전이다. 설정 가능하다 (`config.Settings.archive_hour`).
DEFAULT_ARCHIVE_HOUR = 4.0

#: 스케줄러가 깨어나 "지금 돌릴 때인가"를 보는 간격(초). 5분이면 절전·시계 변경·
#: 시간대 변경을 따라잡기에 충분하고, 확인 비용은 비교 두 번이다.
TICK_INTERVAL = 300.0

#: 연속 실패가 이만큼 쌓이면 화면에 알린다. 1~2회는 네트워크·경합으로 흔히
#: 생기고 다음 주기에 저절로 낫는다 — 그걸로 사람을 부르면 경고가 무시된다.
FAILURE_ALERT_AFTER = 3

#: 기동 직후 빈 날짜를 찾아 거슬러 올라갈 범위(일). 그보다 오래된 날짜는 히스토리
#: 가 없어 어차피 꺼낼 수 없다 (`Channel.archive_gaps`).
GAP_SCAN_DAYS = 60


@dataclass
class BatchResult:
    """배치 한 번의 결과 — 테스트·진단이 같은 값을 본다."""

    archived: list[str] = field(default_factory=list)
    """이번에 로컬 아카이브로 옮긴(또는 이미 옮겨져 있던) 날짜."""

    through: str = ""
    """발행한 확인응답 워터마크 (빈 문자열 = 응답하지 않았다)."""

    acked: bool = False
    """확인응답 파일을 실제로 **썼는가** (값이 안 바뀌면 쓰지 않는다)."""

    consensus: str = ""
    """전원(휴면 제외)의 응답 최소값. 빈 문자열 = 합의 미달."""

    dropped: list[str] = field(default_factory=list)
    recovered: list[str] = field(default_factory=list)
    skipped: dict = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    """사람이 알아야 하는 것 (옮기지 못한 날짜·복구 불가). 화면 알림의 원천."""

    def to_json(self) -> dict:
        return {
            "archived": list(self.archived),
            "through": self.through,
            "acked": self.acked,
            "consensus": self.consensus,
            "dropped": list(self.dropped),
            "recovered": list(self.recovered),
            "skipped": dict(self.skipped),
            "problems": list(self.problems),
        }


class ArchiveBatch:
    """방 하나의 아카이빙 배치. **방·HTTP·SSE 를 모른다** (주입받는다).

    `RoomManager` 가 채널·읽음 트래커·알림 훅을 준다. 그래서 이 클래스가 아는
    것은 "무엇을 어떤 순서로 하나" 하나뿐이다 (`reads.ReadTracker` 와 같은 모양).
    """

    def __init__(
        self,
        channel: Any,
        tracker: _reads.ReadTracker,
        *,
        now: Callable[[], datetime] | None = None,
        dormant_days: float = _reads.DORMANT_DAYS,
        on_ack: Callable[[], None] | None = None,
        on_alert: Callable[[str], None] | None = None,
        gap_scan_days: int = GAP_SCAN_DAYS,
    ) -> None:
        self.channel = channel
        self.tracker = tracker
        # ⭐ 시각의 원천은 **기반의 공통 시계**다 (git 호스트의 HTTP Date 로 맞춘
        # 것 — `gitwire.clock`). 로컬 시계를 따로 보면 시계가 어긋난 참가자에서
        # 휴면 판정이 엉뚱해진다.
        self._now = now or (lambda: self.channel.clock.now())
        self.dormant_days = float(dormant_days)
        self._on_ack = on_ack
        self._on_alert = on_alert
        self.gap_scan_days = int(gap_scan_days)
        self._lock = threading.RLock()
        self.failures = 0
        """연속 실패 횟수. 문턱을 넘으면 화면에 알린다 (`FAILURE_ALERT_AFTER`)."""
        self.last_error = ""
        self.last_result: BatchResult | None = None
        self._alerted = False
        self._swept = False
        self._head: str | None = None

    # ------------------------------------------------------------- 한 번 돌기

    def run_once(self, *, sync: bool = True) -> BatchResult:
        """⭐ 배치 한 번 — 옮기고 · 응답하고 · 합의되면 지우고 · 빈 날짜를 복구한다.

        예외를 올리지 않는다. 실패는 `problems` 와 `failures` 에 쌓이고, 문턱을
        넘으면 화면 알림으로 나간다 — **조용히 넘기지 않는다.**
        """
        with self._lock:
            result = BatchResult()
            try:
                if sync:
                    # 최신 상태에서 옮긴다 — 늦게 도착한 레코드를 내 아카이브에
                    # 담을 기회를 한 번이라도 더 준다.
                    self.channel.sync()
            except Exception as exc:  # noqa: BLE001
                log.debug("아카이빙 배치: 당겨오기 실패 — 로컬 상태로 계속한다: %s", exc)
            ok = self._archive(result)
            if ok:
                self._ack(result)
            self._sweep(result)
            self._drop(result)
            self._settle(result)
            self.last_result = result
            return result

    def _archive(self, result: BatchResult) -> bool:
        """1단계 — 옮긴다 (로컬 파일, fsync 까지). 성공했으면 True."""
        try:
            got = self.channel.archive_days()
        except Exception as exc:  # noqa: BLE001
            result.problems.append(f"지난 날짜를 아카이브로 옮기지 못했다: {exc}")
            log.warning("아카이빙 실패", exc_info=True)
            return False
        result.archived = list(got.get("archived") or [])
        result.skipped = dict(got.get("skipped") or {})
        result.through = str(got.get("through") or "")
        for day, why in result.skipped.items():
            result.problems.append(f"{day} 을 옮기지 못했다: {why}")
        return True

    def _ack(self, result: BatchResult) -> None:
        """2단계 — 확인응답을 발행한다.

        ⚠️ **1단계 뒤에만** 불린다 (모듈 도크). 값이 안 바뀌면 아무것도 쓰지 않는다
        (`ReadTracker.publish` 의 가드) — 아무 일도 없는 방이 매일 자라지 않는다.
        """
        if not result.through:
            return
        try:
            result.acked = self.tracker.publish(archived=result.through)
        except Exception as exc:  # noqa: BLE001
            result.problems.append(f"확인응답을 발행하지 못했다: {exc}")
            log.warning("확인응답 발행 실패", exc_info=True)
            return
        if result.acked and self._on_ack is not None:
            # 파일은 썼다 — 원격으로 밀어내는 것은 아웃박스의 일이다.
            try:
                self._on_ack()
            except Exception:  # noqa: BLE001
                log.debug("확인응답 밀어내기 실패", exc_info=True)

    def _drop(self, result: BatchResult) -> None:
        """3~4단계 — 합의를 판정하고, 합의된 날짜의 레코드를 지운다."""
        try:
            people = self.tracker.participants(fresh=False)
        except Exception as exc:  # noqa: BLE001
            result.problems.append(f"참가자 응답을 읽지 못했다: {exc}")
            return
        result.consensus = _reads.consensus_day(
            people, now=self._now(), dormant_days=self.dormant_days
        )
        if not result.consensus:
            return
        try:
            days = [d for d in self.channel.archive_state() if d <= result.consensus]
        except Exception as exc:  # noqa: BLE001
            result.problems.append(f"로컬 아카이브를 나열하지 못했다: {exc}")
            return
        if not days:
            return
        try:
            got = self.channel.drop_days(sorted(days))
        except Exception as exc:  # noqa: BLE001
            result.problems.append(f"레코드를 지우지 못했다: {exc}")
            log.warning("레코드 삭제 실패", exc_info=True)
            return
        result.dropped = list(got.get("days") or [])
        for day, why in (got.get("skipped") or {}).items():
            result.problems.append(f"{day} 의 레코드를 지우지 않았다: {why}")

    # ------------------------------------------------------------- 자동 복구

    def _sweep(self, result: BatchResult) -> None:
        """5단계(기동 1회) — 빈 날짜를 찾아 히스토리에서 복구한다.

        내가 자는 동안 남이 지운 날짜가 있을 수 있다. 폴 틱의 `observe()` 가 그
        순간을 잡지만, **앱이 꺼져 있던 동안의 삭제**는 아무도 못 봤다. 그래서
        기동 후 첫 배치에서 한 번 훑는다 (`Channel.archive_gaps` — 나열 1회).
        """
        if self._swept:
            return
        self._swept = True
        try:
            through = gitwire.last_closed_day(self._now())
            gaps = self.channel.archive_gaps(through, max_days=self.gap_scan_days)
        except Exception as exc:  # noqa: BLE001
            result.problems.append(f"빈 날짜를 훑지 못했다: {exc}")
            return
        for day in gaps:
            self._recover(day, result)

    def observe(self, head: str | None) -> list[str]:
        """폴 한 틱 — **레코드 삭제를 pull 로 받았으면** 그 날짜를 복구한다.

        ⭐ 비용이 거의 0 이다: 두 커밋의 날짜 나열은 sha 로 캐시되므로 보통 git
        호출이 **0회**이고, 지워진 날짜가 없으면 그대로 돌아온다.

        ⚠️ 로컬 아카이브가 **있어도** 복구를 부른다. 내가 응답한 *뒤에* 늦게
        도착한 레코드가 있으면 내 아카이브는 불완전하고, 복구는 합집합이라
        멱등하다 (`Channel.recover_archive`) — 완전하면 아무것도 쓰지 않는다.
        """
        with self._lock:
            base, self._head = self._head, head
        if not head or not base or base == head:
            return []
        try:
            days = self.channel.deleted_days(base, head)
        except Exception as exc:  # noqa: BLE001
            log.debug("지워진 날짜를 보지 못했다: %s", exc)
            return []
        if not days:
            return []
        result = BatchResult()
        for day in days:
            self._recover(day, result)
        self._settle(result, count_failure=False)
        return result.recovered

    def _recover(self, day: str, result: BatchResult) -> None:
        try:
            got = self.channel.recover_archive(day)
        except Exception as exc:  # noqa: BLE001
            result.problems.append(f"{day} 을 복구하지 못했다: {exc}")
            return
        if got.get("recovered"):
            result.recovered.append(day)
            log.info("방의 아카이브 %s 를 히스토리에서 복구했다 (%s건)", day, got["added"])
        for why in got.get("problems") or []:
            # ⚠️ 조용히 넘기지 않는다 — 히스토리가 없으면 그 대화는 되찾을 수 없다.
            result.problems.append(f"{day} 을 완전히 복구하지 못했다: {why}")

    # --------------------------------------------------------------- 알림

    def _settle(self, result: BatchResult, *, count_failure: bool = True) -> None:
        """연속 실패를 세고, 문턱을 넘으면 **한 번** 알린다 (나으면 해제한다)."""
        if result.problems:
            self.last_error = result.problems[0]
            if count_failure:
                self.failures += 1
            if self.failures >= FAILURE_ALERT_AFTER and not self._alerted:
                self._alerted = True
                self._alert(
                    f"지난 날짜 정리가 {self.failures}회 연속 실패했다 — "
                    f"{self.last_error} (그동안 이 방의 레코드 삭제가 미뤄진다)"
                )
            return
        self.last_error = ""
        if count_failure:
            self.failures = 0
        if self._alerted:
            self._alerted = False
            self._alert("")          # 상태줄을 비운다 (나았다)

    def _alert(self, detail: str) -> None:
        if self._on_alert is None:
            return
        try:
            self._on_alert(detail)
        except Exception:  # noqa: BLE001
            log.debug("아카이빙 알림 실패", exc_info=True)


# ------------------------------------------------------------------ 스케줄러


def last_occurrence(now: datetime, hour: float) -> datetime:
    """`now` 기준 **가장 최근에 지난** 예정 시각 (로컬 달력).

    "오늘 04:00 이 지났나"가 아니라 "마지막 예정 시각이 언제였나"로 판정한다.
    그래야 새벽 01:00 에 앱을 켜도 *어제 04:00 분*이 아직 안 돌았다는 사실이
    드러나고, 밀린 배치를 따라잡을 수 있다 (앱이 꺼져 있었을 수 있다).
    """
    h = max(0.0, min(23.999, float(hour)))
    today = now.replace(
        hour=int(h), minute=int((h % 1) * 60), second=0, microsecond=0
    )
    return today if now >= today else today - timedelta(days=1)


class DailyRunner:
    """로컬 시각 기준 하루 한 번 `run` 을 부르는 스레드 하나.

    ⭐ 절대 시각까지 한 번에 재우지 않는다 — `TICK_INTERVAL` 마다 깨어나 "마지막
    예정 시각을 이미 처리했나"만 비교한다. 그래서 절전·시계 변경·시간대 변경·
    기동 직후(밀린 분 따라잡기)가 모두 같은 한 가지 규칙으로 풀린다.

    시계와 잠은 **주입 가능하다** — 테스트가 실제 시간을 기다리지 않는다.
    """

    def __init__(
        self,
        run: Callable[[], Any],
        *,
        hour: float = DEFAULT_ARCHIVE_HOUR,
        now: Callable[[], datetime] | None = None,
        interval: float = TICK_INTERVAL,
        name: str = "gitwire-chat-archive",
    ) -> None:
        self._run = run
        self.hour = float(hour)
        self._now = now or (lambda: datetime.now().astimezone())
        self.interval = float(interval)
        self.name = name
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._done_for: datetime | None = None
        self.runs = 0

    def tick(self) -> bool:
        """지금 돌릴 때면 돌린다. 돌렸으면 True (테스트가 직접 부른다)."""
        occurrence = last_occurrence(self._now(), self.hour)
        if self._done_for is not None and self._done_for >= occurrence:
            return False
        self._done_for = occurrence
        self.runs += 1
        try:
            self._run()
        except Exception:  # noqa: BLE001 — 배치 하나가 스레드를 죽이지 않는다
            log.exception("일일 아카이빙 배치 실패 — 다음 주기에 다시 시도한다")
        return True

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                self.tick()
                self._stop.wait(self.interval)

        self._thread = threading.Thread(target=loop, name=self.name, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)
        self._thread = None

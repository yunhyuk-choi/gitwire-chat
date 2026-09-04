"""아웃박스 — **이미 디스크에 있는** 레코드를 원격까지 밀어내는 일 하나만 한다.

왜 있나
------
예전에는 `send()` 가 `channel.append(payload, flush=True)` 를 불렀다. `flush=True`
는 *즉시 커밋 + push* 라서 HTTP 응답이 GitHub push 를 통째로 기다렸다 — 실측
**한 건에 2.7~3.4초**(자격증명 관리자 ~1.3초 + HTTPS 왕복). 사용자가 "보내는 게
느리다"고 느낀 것이 정확히 이 시간이다.

그런데 **내구성은 push 가 주는 것이 아니다.** 기반의 `Channel.append` 도크:
"파일은 즉시 디스크에 쓰이고(내구성), 커밋·push 는 배칭 창 안의 여러 건을 묶어
한 커밋으로 나간다." 즉 `flush=True` 는 이미 안전한 쓰기 위에 **동기 네트워크**를
한 겹 얹는 것뿐이었다. 그래서 그 겹을 여기로 옮긴다.

무엇을 책임지나 (하나다)
----------------------
"아직 원격에 나가지 못한 것을 밀어내고, **그 상태를 하나의 값으로 말한다**."

* 코얼레싱 — 연달아 보낸 여러 건이 push 한 번으로 묶인다.
* 순서 — 방마다 워커 **하나**다. 두 push 가 겹치지 않는다. (레코드 파일 이름이
  고정폭 타임스탬프라 원격 정렬 자체는 커밋을 어떻게 쪼개든 시간순이지만,
  워커가 하나여야 실패·재시도 상태가 하나로 남는다.)
* 재시도 — 실패하면 지수 백오프로 계속 민다. 레코드는 디스크에 그대로 있다.
* **조용한 실패 금지** — 실패도, *너무 오래 안 나가는 것*도 상태로 드러낸다.

무엇을 책임지지 않나
------------------
레코드를 만드는 일(= `channel.append`)은 `rooms.py` 것이고, 상태를 **그리는** 일은
브라우저(`static/js/outbox.js`) 것이다. 여기는 그 사이의 한 값(`OutboxState`)만
소유한다.

상태 세 가지 — 그리고 왜 셋뿐인가
--------------------------------
전송 한 건이 지나는 지점은 넷이다: *보내는 중(HTTP)* → *내 디스크엔 있음* →
*상대에게 나감* → *실패*. 이 중 **아웃박스가 말하는 것은 방 단위 셋**이다:

===========  ==============================================================
`synced`     밀어내지 못한 것이 없다. 화면에 아무것도 띄우지 않는다.
`sending`    디스크에 있고 나가는 중이다. **일부러 화면에 띄우지 않는다** —
             정상 경로이고 보통 3초면 끝난다. 매번 띄우면 사람이 곧 무시하게
             되고, 그러면 진짜 사고도 같이 묻힌다.
`stuck`      나가지 못했다 (push 실패, 또는 `late_after` 초가 지나도록 못 나감).
             **이것만 화면에 뜬다.** 사용자가 오해할 수 있는 유일한 상태이기
             때문이다 — 나머지 둘은 "곧 간다"로 요약해도 틀리지 않는다.
===========  ==============================================================

`late_after` 가 필요한 이유: push 가 *실패*하지 않고 **매달릴** 수도 있다(끊긴
네트워크의 TCP 대기). 실패만 보고 있으면 그 경우가 조용한 실패가 된다.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

#: 방 단위 아웃박스 상태 3종 (모듈 도크의 표 참조).
SYNCED = "synced"
SENDING = "sending"
STUCK = "stuck"

#: 이 시간이 지나도록 못 나가면 실패가 아니어도 `stuck` 으로 드러낸다(초).
#: push 실측이 3초 안팎이므로 그 몇 배 — 정상 경로가 경고를 띄우면 안 된다.
LATE_AFTER = 20.0

#: 실패 후 재시도 간격(초). 지수로 늘리되 상한을 둔다.
RETRY_BASE = 2.0
RETRY_MAX = 60.0


@dataclass(frozen=True)
class OutboxState:
    """방 하나의 "아직 안 나간 것" 상태. **화면·API·로그가 같은 값을 쓴다.**"""

    state: str = SYNCED
    pending: int = 0
    """이 프로세스가 보냈지만 아직 원격 도달을 확인하지 못한 건수.

    ⚠️ 재기동 직후 남아 있던 레코드는 여기에 세어지지 않는다 — 몇 건인지는
    기반만 알고 그걸 물어보는 공개 API 가 없다. 그래도 밀어내기는 한다
    (`kick()`), 실패하면 `stuck` 으로 드러난다. 세지 못하는 것과 놓치는 것은
    다르다.
    """

    detail: str = ""
    """사람이 읽는 사유. `stuck` 일 때만 채워진다."""

    def to_json(self) -> dict:
        return {"state": self.state, "pending": self.pending, "detail": self.detail}


class Outbox:
    """방 하나의 밀어내기 워커. 스레드 하나, 상태 하나.

    `flush` 는 "지금 밀 수 있는 것을 전부 밀고, 실패하면 예외" 인 함수다
    (기반의 `Channel.flush`). 주입받으므로 테스트가 네트워크 없이 돈다.
    """

    def __init__(
        self,
        flush: Callable[[], object],
        *,
        on_state: Callable[[OutboxState], None] | None = None,
        describe: Callable[[BaseException], str] | None = None,
        name: str = "outbox",
        late_after: float = LATE_AFTER,
        retry_base: float = RETRY_BASE,
        retry_max: float = RETRY_MAX,
    ) -> None:
        self._flush = flush
        self._on_state = on_state
        self._describe = describe or (lambda exc: str(exc))
        self._name = name
        self._late_after = float(late_after)
        self._retry_base = float(retry_base)
        self._retry_max = float(retry_max)

        self._lock = threading.Lock()
        self._pending = 0
        self._drain = False          # 건수는 몰라도 한 번 밀어야 한다 (기동 시)
        self._busy = False
        self._trouble = ""           # 비어 있지 않으면 stuck
        self._wake = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._closing = threading.Event()
        self._thread: threading.Thread | None = None
        self._last: tuple | None = None

    # ------------------------------------------------------------- 바깥 표면

    @property
    def state(self) -> OutboxState:
        with self._lock:
            return self._state_locked()

    def add(self, count: int = 1) -> None:
        """레코드 `count` 건이 디스크에 쌓였다 — 밀어내라."""
        with self._lock:
            self._pending += max(1, int(count))
        self._nudge()

    def kick(self) -> None:
        """지금 한 번 밀어라.

        두 곳에서 부른다: **기동 직후**(지난 실행이 남긴 것을 밀어낸다)와
        사용자가 누른 **다시 보내기**. 둘 다 "건수는 모르지만 밀 것이 있을 수
        있다" 이므로 같은 동작이다.
        """
        with self._lock:
            self._drain = True
        self._nudge()

    def close(self, timeout: float = 20.0) -> None:
        """종료 — 남은 것을 **여기서 한 번 더** 밀어내고 스레드를 접는다.

        앱을 정상적으로 끄는 경로에서 굳이 다음 기동까지 미룰 이유가 없다.
        강제 종료(전원 뽑기)는 이 경로를 못 타지만, 그때도 레코드는 디스크에
        남아 있고 다음 기동의 `kick()` 이 밀어낸다 — 그것이 유실 방지의 본체다.
        """
        self._closing.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        with self._lock:
            leftover = self._pending > 0 or self._drain
        if leftover:
            try:
                self._flush()
            except BaseException as exc:  # noqa: BLE001 — 종료를 막지 않는다
                log.warning(
                    "%s: 종료 시 밀어내기 실패 — 레코드는 디스크에 남는다 "
                    "(다음 기동이 밀어낸다): %s", self._name, exc,
                )
            else:
                with self._lock:
                    self._pending = 0
                    self._drain = False

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """밀어낼 것이 없어질 때까지 기다린다 (테스트·종료 경로용)."""
        return self._idle.wait(timeout)

    # ------------------------------------------------------------ 안쪽 동작

    def _state_locked(self) -> OutboxState:
        if self._trouble:
            return OutboxState(STUCK, self._pending, self._trouble)
        if self._pending or self._busy or self._drain:
            return OutboxState(SENDING, self._pending, "")
        return OutboxState(SYNCED, 0, "")

    def _nudge(self) -> None:
        self._idle.clear()
        self._ensure_thread()
        self._wake.set()

    def _ensure_thread(self) -> None:
        if self._closing.is_set():
            return
        thread = self._thread
        if thread is not None and thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name=f"gitwire-chat-{self._name}", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        delay = 0.0
        while not self._closing.is_set():
            # delay==0 → 다음 add/kick 까지 무한 대기. delay>0 → 재시도 시각까지.
            self._wake.wait(timeout=delay or None)
            self._wake.clear()
            if self._closing.is_set():
                return
            if self._attempt():
                delay = 0.0
            else:
                delay = min(max(self._retry_base, delay * 2), self._retry_max)

    def _attempt(self) -> bool:
        """한 번 밀어낸다. 성공(또는 밀 것이 없음)이면 True."""
        with self._lock:
            if self._pending <= 0 and not self._drain:
                self._idle.set()
                self._publish_locked()
                return True
            taking = self._pending
            self._drain = False
            self._busy = True
            self._publish_locked()

        # ⭐ 실패하지 않고 **매달리는** push 도 조용한 실패다. 타이머 하나로 잡는다
        #    (상시 스레드를 하나 더 두지 않는다 — 미는 동안에만 산다).
        late = threading.Timer(self._late_after, self._late)
        late.daemon = True
        late.start()
        try:
            self._flush()
        except BaseException as exc:  # noqa: BLE001 — 워커는 어떤 실패로도 죽지 않는다
            log.warning("%s: 밀어내기 실패 (재시도한다): %s", self._name, exc)
            with self._lock:
                self._busy = False
                self._trouble = self._reason(exc)
                self._publish_locked()
            return False
        finally:
            late.cancel()

        with self._lock:
            self._pending = max(0, self._pending - taking)
            self._busy = False
            self._trouble = ""
            if self._pending <= 0 and not self._drain:
                self._idle.set()
            self._publish_locked()
        return True

    def _late(self) -> None:
        with self._lock:
            if not self._busy:
                return
            self._trouble = (
                f"아직 원격에 나가지 못했다 ({int(self._late_after)}초째 미는 중)"
            )
            self._publish_locked()

    def _reason(self, exc: BaseException) -> str:
        try:
            return self._describe(exc) or str(exc)
        except Exception:  # noqa: BLE001 — 사유를 만들다 죽으면 사유가 사라진다
            log.debug("%s: 사유 해석 실패", self._name, exc_info=True)
            return str(exc)

    def _publish_locked(self) -> None:
        """상태가 **바뀐 순간에만** 알린다 (같은 값을 되풀이해 밀지 않는다)."""
        state = self._state_locked()
        key = (state.state, state.pending, state.detail)
        if key == self._last:
            return
        self._last = key
        if self._on_state is None:
            return
        try:
            self._on_state(state)
        except Exception:  # noqa: BLE001 — 알림이 밀어내기를 막으면 안 된다
            log.debug("%s: 상태 알림 실패", self._name, exc_info=True)

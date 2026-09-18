"""방 관리 — gitwire 채널 위에 얹은 "대화방".

방 하나 = gitwire 채널 하나 = git 레포 하나. 이 모듈이 하는 일은 얇다:

* 방 등록/목록 (`chats/rooms.json`)
* 채널 열기 = **클론** (빈 레포면 gitwire 가 알아서 초기화한다).
  ⭐ 클론은 **백그라운드**에서 돈다 — 등록은 즉시 돌아오고, 진행 상태(`받는 중 /
  완료 / 실패+사유`)를 이벤트 버스로 민다. 클론을 HTTP 요청 안에서 동기로 돌리면
  대화가 쌓인 방이나 느린 네트워크에서 버튼이 수십 초 멈춰 있고 화면에는 아무
  설명이 없다 — 사용자는 앱이 죽은 줄 안다.
* 타임라인 조회 (최근 N + 이전 불러오기) · 검색
* 메시지 전송. ⭐ 전송 응답은 **원격 push 를 기다리지 않는다** — 기반의 대기열에
  넣는 즉시 돌아오고, 시각·ID·커밋·push 는 `outbox.Outbox` 가 백그라운드로 민다.
  그래서 **응답에는 봉투가 없다**(ID 는 push 때 생긴다): 화면의 말풍선은 브라우저의
  낙관적 항목이고, 진짜 레코드는 push 직후 SSE 로 흘러 그 자리를 채운다
  (`send()` · `_after_push()`). 상태를 무엇으로 말하는지는 `outbox` 모듈 도크에.
* 상시 구독 → 새 메시지를 이벤트 버스로, 그리고 조건이 맞으면 OS 알림으로

전송 계층은 **전부 gitwire 에 위탁한다.** 여기에 git 명령이 단 한 줄도 없다.
전송 수준 식별자(``sender``)도 마찬가지다 — 예전에는 이 앱이 ``instance.txt`` 로
직접 난수를 붙였지만, 지금은 gitwire 가 ``<home>/installation.txt`` 에 설치본
식별자를 만들어 준다. **기반이 보장하는 것을 소비자가 다시 풀지 않는다.**

주입 가능성
----------
``opener`` 인자로 채널 생성 함수를 갈아끼울 수 있다(기본값
``gitwire.open_channel``). 덕분에 대부분의 테스트가 네트워크·git 없이 돈다.
"""

from __future__ import annotations

import inspect
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import gitwire

# 이 앱은 기반의 **새 표면**에 의존한다: 설치본 식별자(installation_id),
# keyset 역방향 페이징(Channel.history_page), **로컬 전용 읽기**(`fresh=`),
# 그리고 **참가자 상태 예약 경로**(write_state/read_states — 읽음 커서가 여기
# 산다). 낮은 gitwire 로 돌면 한참 뒤에 엉뚱한 곳에서 터지므로, 여기서 즉시
# 분명하게 실패한다 (조용한 실패 금지).
def _has_local_read_mode() -> bool:
    try:
        return "fresh" in inspect.signature(gitwire.Channel.history_page).parameters
    except (TypeError, ValueError):  # pragma: no cover - 서명을 읽을 수 없는 구현
        return False


def _has_cycle_hook() -> bool:
    """폴 한 틱이 끝났음을 알리는 훅 — 레코드가 아닌 변화(읽음 커서)를 그것으로 안다."""
    try:
        return "on_cycle" in inspect.signature(gitwire.Channel.subscribe).parameters
    except (TypeError, ValueError):  # pragma: no cover
        return False


if (
    not hasattr(gitwire, "installation_id")
    or not hasattr(gitwire.Channel, "history_page")
    or not _has_local_read_mode()
    or not hasattr(gitwire.Channel, "write_state")
    or not hasattr(gitwire, "git_email")
    or not _has_cycle_hook()
    # ⭐ 아카이브는 **로컬 전용**이고 레코드 삭제는 합의 뒤에만 일어난다. 그 표면이
    # 없는 기반(예전의 `rollup()`)과 섞이면 아카이브가 다시 커밋되고 삭제가 합의
    # 없이 일어난다 — 조용히 나빠지는 대표적인 자리라 여기서 크게 실패시킨다.
    or not hasattr(gitwire.Channel, "archive_days")
    or not hasattr(gitwire.Channel, "drop_days")
    or not hasattr(gitwire.Channel, "recover_archive")
    or not hasattr(gitwire, "last_closed_day")
):  # pragma: no cover - 설치 환경 문제
    raise ImportError(
        "gitwire 가 너무 낮다 — 참가자 상태 예약 경로(write_state/read_states), "
        "폴 틱 훅(subscribe(on_cycle=...)), 지난 날짜 아카이빙·삭제 분리"
        "(archive_days/drop_days/recover_archive)를 주는 gitwire 가 필요하다 "
        "(pip install --force-reinstall "
        '"gitwire @ git+https://github.com/yunhyuk-choi/gitwire.git")'
    )

from . import archive as _archive
from . import reads as _reads
from . import schema
from .config import Room, RoomStore, Settings, with_defaults
from .events import EventBus
from .notify import Notifier
from .outbox import Outbox, OutboxState

log = logging.getLogger(__name__)

# ⚠️ 발행 계약을 기반이 소유한다 — 레코드의 시각·ID 는 **원격에 push 되는 순간**에
# 정해지고, `append()` 는 티켓을, `flush()` 는 *나간 레코드*를 준다. 그것이 없는
# 구버전 gitwire 와 섞이면 전송이 **조용히** 어긋난다(응답이 봉투를 실은 줄 알고
# 커서를 옛 방식으로 올리려 하고, `flush()` 반환값이 건수라 `_after_push` 가
# 아무것도 못 받는다). 그래서 여기서 **크게** 실패시킨다 — `reads.py` 의 같은 방어와
# 같은 성격이다.
if not hasattr(gitwire, "PendingRecord"):  # pragma: no cover — 버전 불일치 방어
    raise ImportError(
        "기반(gitwire)이 너무 낮다 — 발행 티켓(`gitwire.PendingRecord`)이 없다. "
        "이 버전은 레코드의 시각·ID 를 push 시점에 정하는 기반을 전제한다. "
        "`python -m gitwire_chat update` 로 함께 올려라."
    )

#: 방마다 기억하는 "이미 본 메시지 ID" 상한. 중복 수신 방어용이라 최근 것만 있으면 된다.
SEEN_LIMIT = 4096


class RoomError(Exception):
    """방 등록·조회 실패 (사용자에게 그대로 보여줘도 되는 메시지)."""


#: 방 연결 상태 — 사용자에게 그대로 보이는 3단계.
CONNECTING = "connecting"   # 받는 중 (클론·초기화)
READY = "ready"             # 완료
FAILED = "failed"           # 실패 (사유가 남는다)


class RoomNotReady(RoomError):
    """아직 받는 중이거나 실패한 방 — 호출자는 상태를 보여주고 기다리게 한다."""


@dataclass(frozen=True)
class RoomStatus:
    """방 하나의 연결 상태. **디스크에 저장하지 않는다** — 재시작하면 다시 연결한다.

    실패해도 방을 목록에서 지우지 않는다. 예전에는 등록이 취소되면서 방이 통째로
    사라져 사용자가 *왜* 안 됐는지 알 수 없었다.
    """

    state: str = CONNECTING
    detail: str = ""      # 사람이 읽는 사유
    code: str = ""        # url / auth / notfound / network / init / error
    hint: str = ""        # 다음에 무엇을 하면 되는지

    def to_json(self) -> dict:
        return {
            "state": self.state, "detail": self.detail,
            "code": self.code, "hint": self.hint,
        }


def _reason(text: str) -> str:
    """git 출력에서 **사람이 볼 한 줄**만 뽑는다.

    gitwire 의 GitError 는 실행한 명령 전체와 여러 줄 stderr 를 담는다. 그건
    로그에는 좋지만 화면에 그대로 뿌리면 사용자가 읽지 않는다. `fatal:` 줄이
    있으면 그것이 사유이고, 없으면 마지막 의미 있는 줄을 쓴다.
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    fatal = [line for line in lines if line.lower().startswith("fatal:")]
    picked = fatal[0][len("fatal:"):].strip() if fatal else (lines[-1] if lines else "")
    return picked[:200]


def classify(exc: BaseException, room: "Room | None" = None) -> RoomStatus:
    """클론 실패를 **구분해서** 사람이 다음 행동을 알 수 있는 사유로 바꾼다.

    ⚠️ gitwire 는 모든 git 출력에서 자격증명을 레닥션한다. 그 문자열을 그대로
    쓰되, 토큰 **이름**만 언급하고 값은 어디에도 넣지 않는다.
    """
    text = str(exc)
    low = text.lower()
    var = (room.token_env if room and room.token_env else "GITWIRE_TOKEN")
    token_hint = (
        f"비공개 레포라면 토큰이 필요하다. 환경변수 {var} 에 토큰을 넣고 앱을 "
        f"다시 띄워라 (PowerShell: $env:{var}=\"...\" / bash: export {var}=...). "
        "방 등록 시 '토큰 환경변수' 칸에 다른 이름을 넣을 수도 있다."
    )
    if isinstance(exc, gitwire.AuthError) or "authentication failed" in low or (
        "could not read username" in low or "could not read password" in low
        or "terminal prompts disabled" in low or "invalid username or password" in low
    ):
        return RoomStatus(FAILED, "인증에 실패했다 (토큰이 없거나 권한이 없다)",
                          "auth", token_hint)
    if ("could not resolve host" in low or "failed to connect" in low
            or "timed out" in low or "network is unreachable" in low
            or "connection refused" in low or "ssl certificate" in low):
        return RoomStatus(FAILED, "네트워크에 연결하지 못했다", "network",
                          "연결을 확인하고 다시 시도하라.")
    if ("not found" in low or "does not exist" in low
            or "repository not found" in low):
        return RoomStatus(
            FAILED, "그 주소의 레포를 찾지 못했다", "notfound",
            "주소에 오타가 없는지 확인하라. 비공개 레포는 토큰이 없으면 "
            "'없는 레포'처럼 보인다 — " + token_hint,
        )
    # git 이 "레포가 아니다 / 읽을 수 없다" 라고 할 때. 로컬 경로 오타·잘못된
    # 주소가 여기로 온다 (git 은 원인을 특정해 주지 않는다).
    if ("does not appear to be a git repository" in low
            or "could not read from remote repository" in low
            or "unable to access" in low or "no such file or directory" in low
            or "repository" in low and "invalid" in low):
        return RoomStatus(
            FAILED, "레포 주소를 열 수 없다 — " + (_reason(text) or "주소를 확인하라"),
            "url",
            "https://호스트/소유자/레포.git 형태인지, 오타가 없는지 확인하라. "
            "비공개 레포라면 접근 권한도 필요하다 — " + token_hint,
        )
    if isinstance(exc, gitwire.ChannelInitError):
        if "빈 레포" in text:
            # 쓰고 있는 코드 레포 주소를 넣은 경우. gitwire 가 아무것도 쓰지 않고
            # 막아 준다 — 사용자에게는 "새 레포를 만들라"가 답이다.
            return RoomStatus(
                FAILED, "이 레포에는 이미 내용이 있다 (채팅 방은 빈 레포에만 만든다)",
                "notempty",
                "빈 레포 주소를 넣어라. 없으면 방 등록 폼의 "
                "「레포가 아직 없다 — 만들기 거들기」로 새로 만들 수 있다.",
            )
        return RoomStatus(FAILED, "방 규약을 심지 못했다", "init",
                          "그 레포에 쓰기 권한이 있는지 확인하라.")
    return RoomStatus(FAILED, _reason(text) or "알 수 없는 오류", "error", "")


@dataclass(frozen=True)
class MessagePage:
    """타임라인 한 쪽 + "더 있는가".

    ``has_more`` 를 추측(``len(items) == limit``)하지 않고 **기반에서 그대로
    받아** 싣는다. 추측은 마지막 쪽 크기가 우연히 limit 과 같을 때 틀리고,
    그러면 브라우저가 헛요청을 한 번 더 보낸다.
    """

    messages: list[schema.Message] = field(default_factory=list)
    has_more: bool = False

    @property
    def oldest(self) -> str | None:
        return self.messages[0].id if self.messages else None

    def __len__(self) -> int:
        return len(self.messages)

    def __iter__(self):
        return iter(self.messages)


def room_id_for(repo_url: str) -> str:
    """레포 URL → 방 ID. gitwire 의 정규화·해시 규약을 그대로 쓴다.

    같은 레포를 https/ssh, .git 유무로 다르게 적어도 같은 방이 된다.
    """
    return gitwire.layout.channel_key(repo_url)


def _display_name(repo_url: str) -> str:
    norm = gitwire.normalize_repo_url(repo_url)
    return norm.rstrip("/").rsplit("/", 1)[-1] or norm


class _Seen:
    """최근 본 메시지 ID 집합 (bounded). 재전달·로컬 에코 중복을 막는다."""

    def __init__(self, limit: int = SEEN_LIMIT) -> None:
        self._ids: "OrderedDict[str, None]" = OrderedDict()
        self._limit = limit
        self._lock = threading.Lock()

    def add(self, key: str) -> bool:
        """처음 보는 것이면 True. 이미 본 것이면 False."""
        with self._lock:
            if key in self._ids:
                return False
            self._ids[key] = None
            while len(self._ids) > self._limit:
                self._ids.popitem(last=False)
            return True

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._ids


class RoomManager:
    """등록된 방 전체를 열고, 폴링하고, 이벤트를 뿌린다."""

    def __init__(
        self,
        settings: Settings,
        *,
        bus: EventBus | None = None,
        notifier: Notifier | None = None,
        store: RoomStore | None = None,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self.bus = bus or EventBus()
        self.notifier = notifier or Notifier(enabled=settings.notifications)
        self.store = store or RoomStore(settings.rooms_path)
        self._opener = opener or gitwire.open_channel

        self._lock = threading.RLock()
        self._rooms: "OrderedDict[str, Room]" = OrderedDict()
        self._channels: dict[str, Any] = {}
        self._subs: dict[str, Any] = {}
        self._seen: dict[str, _Seen] = {}
        self._status: dict[str, RoomStatus] = {}
        # 방당 밀어내기 워커 하나 (`outbox.py`). 전송 응답이 push 를 기다리지
        # 않게 하는 장치이자, "아직 안 나갔다"를 말하는 단일 원천이다.
        self._outboxes: dict[str, Outbox] = {}
        # 방당 읽음 커서 하나 (`reads.ReadTracker`). 내 로컬 커서와 발행값을
        # 함께 쥔다 — 방·HTTP·SSE 를 모르는 물건이라 여기서 만들어 준다.
        self._reads: dict[str, _reads.ReadTracker] = {}
        # 방당 아카이빙 배치 하나 (`archive.ArchiveBatch`). 옮기기·확인응답·합의
        # 판정·삭제·복구를 쥔다. 스케줄러는 **전체에 하나**다 (아래 `_daily`).
        self._archives: dict[str, _archive.ArchiveBatch] = {}
        # 마지막으로 **밀어 보낸** 읽음 스냅샷의 지문. 같은 값을 되풀이해 밀지
        # 않는 근거다 (아웃박스의 `_publish_locked` 와 같은 규율).
        self._read_prints: dict[str, tuple] = {}
        # 방 하나의 클론이 **동시에 두 번** 시작되지 않게 한다 (같은 디렉토리다).
        self._connecting: dict[str, threading.Lock] = {}
        self._workers: dict[str, threading.Thread] = {}
        # 화면을 막지 않는 '지금 당기기' 스레드 (방당 최대 1개 — `refresh_async`).
        self._refreshers: dict[str, threading.Thread] = {}
        self._started = False
        # ⭐ 일일 배치 스레드는 **하나**다 (방마다 하나씩 두지 않는다). 하는 일이
        # 하루 한 번이고 방 수만큼 스레드를 쌓을 이유가 없다.
        self._daily = _archive.DailyRunner(
            self._run_archives,
            hour=settings.archive_hour,
            name="gitwire-chat-archive",
        )
        self.instance = gitwire.installation_id(settings.home)
        """이 설치의 전송 수준 식별자. **gitwire 가 만들고 영속시킨다** — 같은
        머신의 두 인스턴스가 갈리고 재시작해도 유지된다. 표시 이름이 아니다
        (그건 payload 의 ``author``)."""

        self.person = _reads.person_id(settings.home)
        """이 **사람**의 식별자 (`git user.email`). 읽음 표시의 참가자 키다.

        ⚠️ `instance` 와 다르다 — 그쪽은 설치본(노트북·데스크탑이 각각)이고
        이쪽은 사람이다. 읽음 카운트를 설치본 단위로 세면 한 사람이 기기를 둘
        쓸 때 카운트가 0 이 되지 않는다 (`reads` 모듈 도크)."""

        for room in self.store.load():
            self._rooms[room.id] = with_defaults(room, settings)

    # --------------------------------------------------------------- 조회

    @property
    def home(self) -> Path:
        return self.settings.home

    def rooms(self) -> list[Room]:
        with self._lock:
            return list(self._rooms.values())

    def get(self, room_id: str) -> Room:
        with self._lock:
            room = self._rooms.get(room_id)
        if room is None:
            raise RoomError(f"등록되지 않은 방이다: {room_id}")
        return room

    def status(self, room_id: str) -> RoomStatus:
        with self._lock:
            return self._status.get(room_id, RoomStatus(CONNECTING))

    def rooms_payload(self) -> list[dict]:
        """방 목록 + 연결 상태. **API 와 SSE 가 같은 값을 쓴다** (단일 원천)."""
        with self._lock:
            rooms = list(self._rooms.values())
            status = dict(self._status)
        return [
            {**room.to_json(),
             "status": status.get(room.id, RoomStatus(CONNECTING)).to_json(),
             # 아웃박스는 **변할 때** 자기 이벤트로 따로 흐른다. 여기에도 싣는 것은
             # *최초 그리기* 때문이다 — 방을 막 열었을 때 이미 stuck 이면 다음
             # 변화를 기다리지 말고 바로 보여야 한다. 값의 원천은 한 함수다.
             "outbox": self.outbox_state(room.id).to_json(),
             # ⭐ 내가 안 읽은 개수. **발행이 없고 git 왕복도 없다** — 로컬 커서 +
             # 캐시된 레코드 나열뿐이다 (`reads` 모듈 도크). 방 목록은 짧고 바뀔
             # 때만 다시 그리므로 값과 함께 실어 보내면 배관이 늘지 않는다.
             "unread": self.unread(room.id)}
            for room in rooms
        ]

    def _publish_rooms(self) -> None:
        self.bus.publish(None, "rooms", {"rooms": self.rooms_payload()})

    def _set_status(self, room_id: str, status: RoomStatus) -> None:
        with self._lock:
            if room_id not in self._rooms:
                return
            self._status[room_id] = status
        self._publish_rooms()

    # ---------------------------------------------------------- 채널 열기

    def _credential(self, room: Room):
        """토큰은 **환경변수 이름**만 저장하고 값은 프로세스 안에서만 읽는다."""
        var = room.token_env or "GITWIRE_TOKEN"
        try:
            return gitwire.TokenCredential.from_env(var)
        except Exception:  # noqa: BLE001 — 값이 없으면 자격증명 없이 (공개·로컬 레포)
            return None

    def channel(self, room_id: str):
        """방의 gitwire 채널. 없으면 연다 (= 클론. 빈 레포면 gitwire 가 초기화).

        ⚠️ **오래 걸린다.** HTTP 요청 스레드에서 부르지 마라 — `_connect()` 가
        백그라운드에서 부르고, 요청 경로는 `_ready_channel()` 로 상태만 본다.
        """
        with self._lock:
            channel = self._channels.get(room_id)
            if channel is not None:
                return channel
            room = self.get(room_id)
            # 같은 방을 두 스레드가 동시에 클론하면 같은 디렉토리를 함께 만진다.
            gate = self._connecting.setdefault(room_id, threading.Lock())
        with gate:
            with self._lock:
                channel = self._channels.get(room_id)
            if channel is not None:
                return channel
            return self._open_channel(room)

    def _open_channel(self, room: Room):
        room_id = room.id
        kwargs: dict[str, Any] = {
            "home": self.home,
            "consumer": "chat",
            # sender 를 넘기지 않는다 — 같은 home 이므로 gitwire 가 이 앱과
            # 똑같은 설치본 식별자(= self.instance)를 쓴다.
            "name": room.name or None,
            "poll_interval": room.poll_interval or self.settings.poll_interval,
            # ⭐ **부르는 쪽이 밀지 않는다.** 기반의 기본값(`autopublish=True`)은
            # 드레인 루프이고, 그 규칙 1이 "아무도 밀고 있지 않으면 *부르는 쪽*이
            # 그 자리에서 민다" 다 (`gitwire.Channel._drain` 도크). 여기서 부르는
            # 쪽은 **HTTP 요청 스레드**(`send()`)다 — 켜 두면 `POST /messages` 가
            # 다시 push 를 통째로 기다린다. 실측(로컬 bare 원격, 5회):
            # **1048~2317ms(중앙값 1790ms)** → 끈 뒤 **0.5~1.2ms**. 예전에 뗀
            # `flush=True` 가 붙이던 그 시간이 기본값으로 돌아오는 자리다.
            #
            # 느린 것만이 아니다. 켜 두면 `append()` 가 자기 레코드를 이미 밀어
            # 버려서 아웃박스의 `flush()` 가 *나간 레코드*를 받지 못하고, 그러면
            # `_after_push()` 가 아예 돌지 않는다 → **읽음 커서가 전진하지 않고**
            # (실측: 내가 보낸 5건 중 3건이 내 뱃지에 안읽음으로 남았다) 남의
            # 화면에서 내가 *내 말을 안 읽은 사람*으로 세어진다(발행 커서 = "").
            #
            # ⚠️ 끄면 **아무도 대신 밀지 않는다** — 기반의 배경 재시도
            # (`_arm_retry`)도 `autopublish=False` 면 뜨지 않는다. 밀어내기·재시도·
            # 종료 시 마지막 flush 는 전부 `Outbox` 것이다 (`_flush_room`).
            "autopublish": False,
        }
        credential = self._credential(room)
        if credential is not None:
            kwargs["credential"] = credential
        # 자격증명 캐시는 **옵트인**이다 (기본 0 = 끔). 무엇을 사고 무엇을 파는지는
        # config.Settings.credential_cache 와 gitwire README 「자격증명 조회 비용」.
        if self.settings.credential_cache > 0:
            kwargs["credential_helpers"] = gitwire.credential_cache(
                self.settings.credential_cache
            )
        kwargs.update(self.settings.extra.get("channel_kwargs", {}))
        try:
            channel = self._opener(room.repo_url, **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise RoomError(f"방을 열 수 없다: {exc}") from exc
        with self._lock:
            existing = self._channels.get(room_id)
            if existing is not None:
                return existing
            self._channels[room_id] = channel
            self._seen.setdefault(room_id, _Seen())
        return channel

    def _ready_channel(self, room_id: str):
        """요청 경로용 — 아직 안 된 방이면 **기다리지 않고** 상태를 알린다.

        아무도 연결을 시작하지 않았으면 여기서 시작한다(그리고 곧바로 상태를
        돌려준다). 그래서 재시작 직후 방을 열어도 저절로 붙는다.
        """
        with self._lock:
            channel = self._channels.get(room_id)
        if channel is not None:
            return channel
        self.get(room_id)                       # 등록 여부 먼저 (없으면 RoomError)
        status = self.status(room_id)
        if status.state == FAILED:
            raise RoomNotReady(status.detail or "방을 열지 못했다")
        with self._lock:
            worker = self._workers.get(room_id)
            idle = worker is None or not worker.is_alive()
        if idle:
            self._connect_async(room_id)
        raise RoomNotReady("방을 받는 중이다 — 잠시 뒤 다시 보인다")

    # --------------------------------------------------------- 연결(클론)

    def _connect(self, room_id: str) -> bool:
        """클론·초기화를 **여기서** 한다. 오래 걸리므로 백그라운드에서만 부른다."""
        try:
            room = self.get(room_id)
        except RoomError:
            return False
        self._set_status(room_id, RoomStatus(CONNECTING))
        try:
            self.channel(room_id)
        except Exception as exc:  # noqa: BLE001
            cause = exc.__cause__ or exc
            status = classify(cause, room)
            log.warning("방 %s 연결 실패 [%s]: %s", room_id, status.code, status.detail)
            self._set_status(room_id, status)
            return False
        self._set_status(room_id, RoomStatus(READY))
        # ⚠️ 예전에는 여기서 아웃박스를 한 번 깨워 "지난 실행이 남긴 미푸시
        # 레코드"를 밀어냈다. 그런 것이 더는 존재하지 않는다 — 대기열은 메모리이고
        # 프로세스가 죽으면 사라진다 (`send()` 도크). 남은 것이 없는데 깨우면
        # 붙을 때마다 무의미한 push 왕복이 한 번 생긴다.
        if self._started:
            self._start_room(room_id)
            self._publish_rooms()       # 구독까지 붙은 상태를 한 번 더 알린다
        return True

    def _connect_async(self, room_id: str) -> threading.Thread:
        thread = threading.Thread(
            target=self._connect, args=(room_id,),
            name=f"gitwire-chat-connect-{room_id[:8]}", daemon=True,
        )
        with self._lock:
            running = self._workers.get(room_id)
            if running is not None and running.is_alive():
                return running          # 이미 붙는 중이다 (스레드를 쌓지 않는다)
            self._workers[room_id] = thread
        thread.start()
        return thread

    def wait_for_connect(self, timeout: float = 30.0) -> None:
        """진행 중인 연결이 끝나기를 기다린다 (테스트·종료 경로용)."""
        with self._lock:
            workers = list(self._workers.values())
        for thread in workers:
            thread.join(timeout)

    def connect(self, room_id: str) -> RoomStatus:
        """연결(클론)을 시작한다. 이미 붙는 중이면 그대로 둔다. 재시도도 이것이다."""
        self.get(room_id)
        self._connect_async(room_id)
        return RoomStatus(CONNECTING)

    #: 사용자가 "재시도"를 누르는 경로 — 하는 일은 같다.
    reconnect = connect

    # --------------------------------------------------------------- 등록

    def register(
        self,
        repo_url: str,
        *,
        name: str = "",
        author: str = "",
        token_env: str = "",
        poll_interval: float | None = None,
    ) -> Room:
        """레포 주소를 방으로 등록한다. 빈 레포면 gitwire 가 규약을 심는다."""
        repo_url = (repo_url or "").strip()
        if not repo_url:
            raise RoomError("레포 주소가 비어 있다")
        rid = room_id_for(repo_url)
        with self._lock:
            if rid in self._rooms:
                return self._rooms[rid]

        room = with_defaults(
            Room(
                id=rid,
                repo_url=repo_url,
                name=(name or "").strip() or _display_name(repo_url),
                author=(author or "").strip(),
                token_env=(token_env or "").strip(),
                poll_interval=poll_interval or self.settings.poll_interval,
            ),
            self.settings,
        )
        with self._lock:
            self._rooms[rid] = room
            self._status[rid] = RoomStatus(CONNECTING)
        self._persist()
        # ⭐ 클론은 백그라운드로. 등록은 **즉시** 돌아오고 방은 '받는 중' 으로 뜬다.
        # 실패해도 방을 지우지 않는다 — 사유를 화면에 남기고 재시도하게 한다.
        self._publish_rooms()
        self._connect_async(rid)
        return room

    def unregister(self, room_id: str) -> None:
        """방 목록에서 뺀다. 클론은 지우지 않는다 (기록은 파일이 곧 원본이다)."""
        with self._lock:
            self._rooms.pop(room_id, None)
            sub = self._subs.pop(room_id, None)
            channel = self._channels.pop(room_id, None)
            box = self._outboxes.pop(room_id, None)
            self._seen.pop(room_id, None)
            self._archives.pop(room_id, None)
            self._status.pop(room_id, None)
            self._connecting.pop(room_id, None)
            self._workers.pop(room_id, None)
        if box is not None:
            # 목록에서 빼도 **아직 안 나간 말은 밀어내고** 접는다.
            try:
                box.close()
            except Exception:  # noqa: BLE001
                log.debug("아웃박스 정리 실패", exc_info=True)
        if sub is not None:
            try:
                sub.stop()
            except Exception:  # noqa: BLE001
                log.debug("구독 정리 실패", exc_info=True)
        if channel is not None:
            try:
                channel.close()
            except Exception:  # noqa: BLE001
                log.debug("채널 정리 실패", exc_info=True)
        self._persist()
        self._publish_rooms()

    def _persist(self) -> None:
        try:
            self.store.save(self.rooms())
        except OSError as exc:
            log.warning("방 목록 저장 실패: %s", exc)

    # ----------------------------------------------------------- 타임라인

    def _messages(self, room_id: str, limit: int | None = None) -> list[schema.Message]:
        """전량(또는 최근 N건) — 검색처럼 정말 전부 훑어야 하는 곳 전용.

        읽기는 **로컬 클론만** 본다 (`fresh=False`) — 신선도는 폴러가 맡는다.
        `page()` 의 주석 참조.
        """
        channel = self._ready_channel(room_id)
        try:
            records = channel.history(limit, fresh=False)
        except Exception as exc:  # noqa: BLE001
            raise RoomError(f"기록을 읽을 수 없다: {exc}") from exc
        return [schema.parse_record(r) for r in records]

    def page(
        self, room_id: str, *, before: str | None = None, limit: int | None = None
    ) -> MessagePage:
        """타임라인 한 쪽. ``before`` 가 없으면 최신 쪽.

        기반의 keyset 페이징(``history_page``)에 그대로 얹는다 — **요청한 만큼의
        레코드만 열린다.** 예전에는 '이전 불러오기'가 매번 대화 전체를 읽었다.

        ⭐ **읽기는 원격을 보지 않는다** (``fresh=False``).

        예전에는 이 경로가 매 호출 ``sync()`` 를 타서 ``git ls-remote`` 왕복이
        한 번씩 붙었다. 실측(이 머신·GitHub private repo): ``ls-remote`` **1.3초**,
        같은 데이터를 로컬 클론에서 읽기 **40~110ms**. 그래서 ``GET
        /api/rooms/<id>/messages`` 한 번이 1.4~2.9초였다.

        그럴 이유가 없다 — 레코드는 이미 로컬 클론에 있고, 신선도는 **폴러**
        (``subscribe``, 기본 15초)가 이미 맡고 있다. 특히 ``before`` 가 있는
        '위로 거슬러 올라가기'는 이미 받은 커밋 안에서 뒤로 가는 것이라 원격을
        확인할 이유가 **아예** 없다.

        그럼 "지금 최신을 봐야 하는" 순간(방을 막 열었다)은? 화면을 막아서
        해결하지 않는다 — **로컬로 먼저 그리고**, 최신 쪽을 요청받은 김에
        ``refresh_async()`` 로 한 번 당긴다. 새 것이 있으면 이미 있는 SSE
        배관으로 뒤따라 붙는다.
        """
        channel = self._ready_channel(room_id)
        size = limit or (
            self.settings.page_limit if before else self.settings.recent_limit
        )
        try:
            page = channel.history_page(before=before, limit=size, fresh=False)
        except Exception as exc:  # noqa: BLE001
            raise RoomError(f"기록을 읽을 수 없다: {exc}") from exc
        if before is None:
            # 최신 쪽을 보고 있다 = 신선도가 의미 있는 유일한 순간. 막지 않는다.
            self.refresh_async(room_id)
            # ⭐ **방을 열었다** — 내 커서 파일이 없으면 여기서 만든다 (참가자
            # 집합 = 커서 파일 집합. `enter_room` 도크).
            try:
                self.enter_room(room_id)
            except Exception:  # noqa: BLE001 — 읽음 표시가 대화를 막지 않는다
                log.debug("방 %s 읽음 커서 준비 실패", room_id, exc_info=True)
        return MessagePage(
            [schema.parse_record(r) for r in page.records], bool(page.has_more)
        )

    def timeline(self, room_id: str, limit: int | None = None) -> MessagePage:
        """최근 N건 (시간순) + 더 있는지."""
        return self.page(room_id, limit=limit)

    def older(
        self, room_id: str, before_id: str, limit: int | None = None
    ) -> MessagePage:
        """``before_id`` 보다 앞선 메시지 최대 N건 (위로 스크롤하면 이어 붙일 것)."""
        return self.page(room_id, before=before_id, limit=limit)

    def search(
        self, room_id: str, query: str, limit: int = 50
    ) -> list[schema.Message]:
        """서버에서 **레코드를 뒤진다** — DOM 에 없는 과거 메시지도 찾는다.

        ⚠️ 이건 여전히 전량 스캔이다. 역방향 페이징과 달리 "본문으로 찾기"는
        레코드를 열어봐야 하는데 기반에 인덱스가 없다 (README 「정직한 한계」).
        다만 **로컬 클론만** 뒤진다 — 원격 왕복은 없다.
        """
        query = (query or "").strip()
        if not query:
            return []
        needle = query.casefold()
        hits = [
            m
            for m in self._messages(room_id, None)
            if needle in m.text.casefold() or needle in m.author.casefold()
        ]
        return hits[-limit:]

    # ------------------------------------------------------------- 보내기

    def send(
        self,
        room_id: str,
        text: str,
        *,
        author: str = "",
        reply_to: str | None = None,
    ) -> Any:
        """메시지를 **대기열에 넣는다.** 반환값은 기반의 티켓(`PendingRecord`).

        ⭐ **여기서는 봉투가 없다.** 기반은 레코드의 시각·ID 를 *원격에 push 되는
        순간*에 정한다 (`gitwire.Channel.append` 도크 — 오프라인에서 쓴 말이 며칠
        뒤 과거 날짜 ID 로 도착해 "전원 읽음"으로 보이던 결함 때문이다). 그래서
        이 호출은 ID 도, 시각도, `schema.Message` 도 만들 수 없다.

        바뀌는 것은 **에코를 그리는 시점**이다:

        * 화면의 말풍선은 브라우저가 **즉시** 띄운다 — 봉투 없는 낙관적 항목
          (`~pending/` 임시 ID, `static/js/composer.js`)이고 *전송 중*으로 그려진다.
        * push 가 성공하면 그때 `_after_push()` 가 진짜 봉투로 SSE 를 흘리고,
          브라우저가 임시 항목을 그 레코드로 갈아끼운다.

        ⚠️ 그래서 응답이 "보냈다"가 아니라 **"받아 뒀다"** 다. 아직 안 나간 것을
        "보냈다"고 표시하면, 프로세스가 죽어 대기열이 사라졌을 때 사용자가 이미
        본 말이 조용히 없어진다. 그 구분이 `~pending/` 임시 ID 의 존재 이유다.
        """
        room = self.get(room_id)
        payload = schema.build_payload(
            author or room.author or self.settings.author, text, reply_to
        )
        channel = self._ready_channel(room_id)
        try:
            ticket = channel.append(payload)
        except Exception as exc:  # noqa: BLE001
            raise RoomError(f"메시지를 보낼 수 없다: {exc}") from exc
        # 대기열에 든 **뒤에** 센다. 순서가 반대면 append 가 실패한 건까지 세어
        # "안 나간 것이 있다"고 거짓말한다.
        self.outbox(room_id).add()
        return ticket

    def _flush_room(self, room_id: str) -> list:
        """아웃박스 워커가 부르는 밀어내기. **나간 레코드를 여기서 받는다.**

        기반의 `flush()` 가 *방금 원격에 착지한 레코드*를 돌려주므로, "내가 보낸
        말이 실제로 나갔다"를 알 수 있는 자리가 여기 하나다. 그래서 읽음 커서
        전진과 로컬 에코도 여기서 한다 (`_after_push`).

        ⭐ **비워질 때까지 돈다.** 기반의 `flush()` 는 *한 회차*다 — 대기열 앞에서
        `max_batch` 개까지만 가져가고, 되풀이하는 것은 호출자의 일이라고 명시한다
        (`gitwire.Channel.flush` 도크: "`autopublish=False` 인 호출자는 상한을
        넘긴 잔여가 남을 수 있음을 알아야 한다"). 한 번만 부르면 그 잔여를 **아무도**
        밀지 않는다 — 기반의 배경 재시도는 `autopublish=False` 에서 뜨지 않고,
        아웃박스는 성공한 회차를 보고 `pending` 을 0 으로 내려 `synced` 로 쉰다.
        즉 "보냈는데 안 나갔고 아무도 안 나갔다고 말하지 않는" 자리가 된다.
        그래서 상한에 딱 찬 회차 뒤에는 한 번 더 민다 (드레인 루프와 같은 모양).
        """
        channel = self._ready_channel(room_id)
        limit = getattr(channel, "max_batch", None)
        made: list = []
        while True:
            batch = list(channel.flush())
            if batch:
                self._after_push(room_id, batch)
                made += batch
            # 상한을 못 채웠으면 대기열이 비었다 = 더 돌 이유가 없다. 상한을 모르는
            # 기반(대역 등)이면 한 회차로 끝낸다 — 모르는 값으로 무한히 돌지 않는다.
            if not limit or len(batch) < limit:
                return made

    def _after_push(self, room_id: str, records: list) -> None:
        """레코드가 원격에 **착지했다** — 커서를 올리고 화면에 흘린다.

        ⭐ 내가 보낸 말은 내가 읽은 말이다. 커서를 그 자리까지 올려야
        (1) 내 뱃지가 내 말 때문에 늘어나지 않고 (2) 남의 화면에서 **내가** 그
        메시지를 안 읽은 사람으로 세어지지 않는다 (카운트 공식의 `p ≠ A` 가
        여기서 자연히 성립한다).

        ⚠️ 전진은 **push 된 뒤**여야 한다. 예전에는 발행 즉시 올렸는데, 그때는
        커서 값(= 레코드 ID)이 이미 정해져 있었기 때문이다. 이제 ID 는 push 때
        생기므로 그 전에 올릴 값 자체가 없다 — 그리고 그게 더 안전하다: 커서가
        *아직 원격에 없는* 레코드를 가리킬 여지가 사라진다.

        커서 발행(참가자 상태 파일)은 이 커밋에 못 실리므로 **다음** 커밋으로
        나간다. 순서가 뒤집힐 위험은 없다 — 가리키는 레코드가 이미 원격에 있다.
        """
        try:
            tracker = self.reads(room_id)
            tracker.mark(records[-1].id)      # ID 는 정렬 가능 — 마지막이 최대다
            # ⭐ **전송 = 활동 중.** 말을 보낸 사람이 "메시지를 읽을 수 없는 상태"
            # 일 수는 없다. 그래서 커서가 움직였는지와 무관하게 발행을 시도한다 —
            # 바뀔 것이 없으면 `publish` 의 가드가 아무것도 쓰지 않는다.
            tracker.publish(status=_reads.STATUS_ACTIVE)
            self._nudge_outbox(room_id)       # 발행 파일을 다음 커밋으로 밀어낸다
        except Exception:  # noqa: BLE001 — 읽음 표시가 전달을 막지 않는다
            log.debug("방 %s push 후 읽음 커서 전진 실패", room_id, exc_info=True)
        for record in records:
            self._deliver(room_id, schema.parse_record(record), own=True)

    # ---------------------------------------------------------------- 읽음

    def reads(self, room_id: str) -> _reads.ReadTracker:
        """방의 읽음 커서. 없으면 만든다 (채널이 준비돼 있어야 한다)."""
        with self._lock:
            tracker = self._reads.get(room_id)
            if tracker is not None:
                return tracker
        channel = self._ready_channel(room_id)      # 락 밖에서 (오래 걸릴 수 있다)
        with self._lock:
            tracker = self._reads.get(room_id)
            if tracker is not None:
                return tracker
            tracker = _reads.ReadTracker(channel, self.person)
            self._reads[room_id] = tracker
        return tracker

    def unread(self, room_id: str) -> int:
        """내가 안 읽은 개수. **아직 안 붙은 방은 0** 이다 (모르는 것을 세지 않는다).

        ⚠️ 여기서 채널을 열지 않는다 — 방 목록을 그리는 경로라 클론을 시작하거나
        원격을 보면 안 된다. 이미 열려 있는 방만 센다.
        """
        with self._lock:
            if room_id not in self._channels:
                return 0
        try:
            # 뱃지는 **내 것**이라 남의 커서를 읽지 않는다 (`ReadTracker.unread`).
            return self.reads(room_id).unread()[0]
        except (RoomError, RoomNotReady):
            return 0
        except Exception:  # noqa: BLE001 — 뱃지 하나가 방 목록을 죽이지 않는다
            log.debug("방 %s 안 읽은 개수 계산 실패", room_id, exc_info=True)
            return 0

    def read_view(self, room_id: str) -> _reads.ReadView:
        """방 하나의 읽음 스냅샷 (API·SSE 가 같은 값을 쓴다)."""
        return self.reads(room_id).view()

    def enter_room(self, room_id: str) -> _reads.ReadView:
        """⭐ **방을 열 때** 하는 일 — 내 커서 파일이 없으면 만든다.

        참가자 집합 = 커서 파일 집합이므로, 파일이 없으면 나는 분모에 들어가지
        않는다(= 남의 화면에서 내가 안 읽었다는 사실이 보이지 않는다). 판정은
        로컬 stat 한 번이고 없을 때만 쓴다. 설치 스크립트가 아니라 여기 있는
        이유: 나중에 추가한 방을 놓치지 않기 위해서다.
        """
        tracker = self.reads(room_id)
        tracker.ensure_local()
        if tracker.ensure_participant():
            # 발행은 best-effort 다 — 아웃박스가 배칭·백그라운드로 민다.
            self._nudge_outbox(room_id)
        return tracker.view()

    def mark_read(
        self, room_id: str, message_id: str, *, status: str | None = None
    ) -> _reads.ReadView:
        """"여기까지 읽었다" (+ 가용 상태) — 발행은 아웃박스에 맡긴다.

        ⭐ **내 쪽은 낙관적이다**: 로컬 커서는 즉시 움직이고(그래서 뱃지가 바로
        줄어든다), 남에게 알리는 push 는 뒤에서 나간다. 커서도 상태도 안 바뀌었으면
        발행도 하지 않는다 — 같은 값을 되풀이해 push 하지 않는다(`publish` 가드).

        ⭐ **커서와 상태는 다른 축이다.** 커서가 안 움직여도 상태만 바뀔 수 있고
        (창을 닫았다 · 직접 골랐다), 그 반대도 된다. 그래서 둘 중 하나라도 왔으면
        발행을 시도한다.

        ⭐ `status` 가 없는데 **커서가 전진했다면 `활동 중`** 이다 — 새 메시지를
        읽었다는 것이 곧 "지금 받을 수 있다"는 선언이다 (그래서 `방해 금지`도 이
        규칙으로 풀린다: 읽으러 들어왔다 = 받겠다는 뜻. Teams 관례와 **의도적으로**
        다르다). 화면이 같은 규칙을 이미 적용해 값을 실어 보내므로 이것은 그
        규칙의 서버 쪽 바닥이다 (화면이 아닌 호출자·옛 클라이언트도 덮는다).

        ⭐ 실제 봉투 ID 가 아니면 `reads.InvalidCursor` 가 올라간다 (여기서 삼키지
        않는다 — HTTP 400 이 되어 화면에 드러난다).
        """
        tracker = self.reads(room_id)
        moved = tracker.mark(message_id)
        want = status
        if want is None and moved:
            want = _reads.STATUS_ACTIVE
        published = tracker.publish(status=want)
        if published:
            self._nudge_outbox(room_id)
        if moved or published:
            # 뱃지(방 목록)와 방 안 카운트가 같은 사실의 두 표현이라 함께 알린다.
            self._publish_reads(room_id, tracker.view())
            self._publish_rooms()
        return tracker.view()

    def _nudge_outbox(self, room_id: str) -> None:
        """디스크에 쓴 것을 밀어내라고 아웃박스를 깨운다 (건수는 세지 않는다).

        ⚠️ `add()` 가 아니라 `kick()` 이다 — 아웃박스의 `pending` 은 **메시지**
        건수이고, 읽음 커서는 메시지가 아니다. 그 숫자를 오염시키면 "아직 못
        나간 말이 있다"가 거짓말이 된다.
        """
        try:
            self.outbox(room_id).kick()
        except (RoomError, RoomNotReady) as exc:
            log.debug("방 %s 읽음 발행 밀어내기 실패: %s", room_id, exc)

    def _publish_reads(self, room_id: str, view: _reads.ReadView) -> None:
        """읽음 스냅샷을 SSE 로 민다 — **바뀐 순간에만.**"""
        print_ = view.fingerprint()
        with self._lock:
            if self._read_prints.get(room_id) == print_:
                return
            self._read_prints[room_id] = print_
        self.bus.publish(room_id, "reads", {"room": room_id, **view.to_json()})

    def _reads_tick(self, room_id: str) -> None:
        """폴 한 틱이 끝났다 — 남의 커서가 움직였는지 보고, 움직였으면 알린다.

        ⭐ 이 훅이 필요한 이유: 읽음 커서는 **레코드가 아니다.** 상대가 읽기만
        하면 새 메시지가 0건인 커밋이 올라오므로 구독 콜백은 한 번도 불리지
        않는다. 기반이 틱을 알려 주지 않으면 소비자가 원격을 보는 스레드를 하나
        더 두게 된다 (`gitwire.Channel.subscribe(on_cycle=...)`).

        비용: 캐시된 나열뿐이라 **변화가 없으면 git 호출이 0회**다.
        """
        try:
            with self._lock:
                if room_id not in self._channels:
                    return
            view = self.reads(room_id).view()
        except (RoomError, RoomNotReady):
            return
        except Exception:  # noqa: BLE001
            log.debug("방 %s 읽음 갱신 실패", room_id, exc_info=True)
            return
        with self._lock:
            changed = self._read_prints.get(room_id) != view.fingerprint()
        if not changed:
            return
        self._publish_reads(room_id, view)
        self._publish_rooms()               # 뱃지도 같은 사실의 표현이다

    def _archive_tick(self, room_id: str, head: str | None) -> None:
        """폴 한 틱 — 지워진 날짜를 관찰하고 필요하면 복구한다.

        비용: 두 커밋의 날짜 나열은 sha 로 캐시되므로 **변화가 없으면 git 0회**다
        (`Channel.deleted_days`). 복구할 것이 없으면 그대로 돌아온다.
        """
        try:
            with self._lock:
                if room_id not in self._channels:
                    return
            batch = self.archive_batch(room_id)
        except (RoomError, RoomNotReady):
            return
        except Exception:  # noqa: BLE001
            log.debug("방 %s 아카이빙 배치를 만들지 못했다", room_id, exc_info=True)
            return
        try:
            recovered = batch.observe(head)
        except Exception:  # noqa: BLE001 — 복구 실패가 폴링을 죽이지 않는다
            log.debug("방 %s 아카이브 복구 실패", room_id, exc_info=True)
            return
        if recovered:
            # ⚠️ SSE 로 밀지 않는다. 복구된 것은 **위쪽의 과거 메시지**이고, 지금
            # 화면에 보이는 것은 하나도 바뀌지 않는다 — 위로 스크롤하거나 방을
            # 다시 열 때 평범한 페이징 경로가 그것을 가져온다. 여기서 `reads`
            # 이벤트를 흘리면(커서 지도를 싣는 배관이다) 뜻이 다른 신호가 되고,
            # 새 이벤트를 만들면 화면 배관이 하나 더 늘어난다.
            log.info(
                "방 %s 의 지워진 날짜를 로컬 아카이브로 복구했다: %s",
                room_id, ", ".join(recovered),
            )

    # ----------------------------------------------------------- 아카이빙 배치

    def archive_batch(self, room_id: str) -> _archive.ArchiveBatch:
        """방의 아카이빙 배치 (없으면 만든다). 채널이 준비돼 있어야 한다."""
        with self._lock:
            batch = self._archives.get(room_id)
            if batch is not None:
                return batch
        channel = self._ready_channel(room_id)      # 락 밖에서 (오래 걸릴 수 있다)
        tracker = self.reads(room_id)
        with self._lock:
            batch = self._archives.get(room_id)
            if batch is not None:
                return batch
            batch = _archive.ArchiveBatch(
                channel,
                tracker,
                dormant_days=self.settings.dormant_days,
                # 확인응답 파일을 썼다 → 아웃박스가 다음 커밋으로 밀어낸다.
                # (읽음 커서 발행과 **같은 배관**이다 — 두 벌 만들지 않는다.)
                on_ack=lambda _rid=room_id: self._nudge_outbox(_rid),
                on_alert=lambda detail, _rid=room_id: self._publish_archive_alert(
                    _rid, detail
                ),
            )
            self._archives[room_id] = batch
        return batch

    def run_archive(self, room_id: str) -> _archive.BatchResult:
        """방 하나의 배치를 지금 한 번 돌린다 (스케줄러·테스트·진단이 부른다)."""
        return self.archive_batch(room_id).run_once()

    def _run_archives(self) -> None:
        """일일 배치 — 등록된 방 전부. 한 방이 실패해도 나머지는 돈다."""
        for room in self.rooms():
            with self._lock:
                ready = room.id in self._channels
            if not ready:
                continue        # 아직 안 붙은 방 — 붙고 나서 다음 주기에 돈다
            try:
                got = self.run_archive(room.id)
            except (RoomError, RoomNotReady) as exc:
                log.debug("방 %s 아카이빙 배치 건너뜀: %s", room.id, exc)
                continue
            except Exception:  # noqa: BLE001
                log.exception("방 %s 아카이빙 배치 실패", room.id)
                continue
            if got.dropped or got.recovered:
                log.info(
                    "방 %s 지난 날짜 정리 — 삭제 %s / 복구 %s",
                    room.id, got.dropped or "없음", got.recovered or "없음",
                )

    def _publish_archive_alert(self, room_id: str, detail: str) -> None:
        """배치가 반복 실패한다는 사실을 **화면에** 민다 (상태줄).

        ⭐ 왜 알려야 하나: 옮기기가 계속 실패하는 사람은 확인응답이 올라가지 않아
        **모두의 레코드 삭제를 막는다.** 휴면 규칙(7일)에 결국 걸리지만 그전에
        사람이 알아야 고칠 수 있다.

        ⚠️ 폴링 경고와 **같은 이벤트 이름**(`trouble`)을 쓰되 `kind` 로 갈라
        보낸다 — 상태줄은 이미 있는 배관이고, 같은 자리에 문구만 다르게 뜨는 것이
        맞다 (새 배너를 만들면 화면 요소가 하나 더 늘고 둘 다 낡는다).
        """
        self.bus.publish(
            room_id, "trouble", {"room": room_id, "detail": detail, "kind": "archive"}
        )

    # -------------------------------------------------------------- 아웃박스

    def outbox(self, room_id: str) -> Outbox:
        """방의 아웃박스 (없으면 만든다). 방당 워커 **하나** — 순서가 여기서 난다."""
        with self._lock:
            box = self._outboxes.get(room_id)
            if box is not None:
                return box
        channel = self._ready_channel(room_id)      # 락 밖에서 (오래 걸릴 수 있다)
        with self._lock:
            box = self._outboxes.get(room_id)
            if box is not None:
                return box
            box = Outbox(
                lambda _rid=room_id: self._flush_room(_rid),
                on_state=lambda state, _rid=room_id: self._publish_outbox(_rid, state),
                describe=lambda exc, _rid=room_id: self._push_reason(_rid, exc),
                name=f"outbox-{room_id[:8]}",
            )
            self._outboxes[room_id] = box
        return box

    def outbox_state(self, room_id: str) -> OutboxState:
        """지금 상태. 아직 아웃박스가 없으면 '밀 것이 없다'가 정답이다."""
        with self._lock:
            box = self._outboxes.get(room_id)
        return box.state if box is not None else OutboxState()

    def flush_outbox(self, room_id: str) -> OutboxState:
        """사용자가 누른 '다시 보내기' — 지금 한 번 밀어라."""
        self.outbox(room_id).kick()
        return self.outbox_state(room_id)

    def _push_reason(self, room_id: str, exc: BaseException) -> str:
        """push 실패를 사람이 읽는 한 줄로. 클론 실패와 **같은 분류기**를 쓴다.

        인증 만료·네트워크 끊김·거부는 붙을 때나 밀 때나 같은 사고다 — 사유를
        두 벌 쓰면 하나는 반드시 낡는다.
        """
        room = self._rooms.get(room_id)
        return classify(exc, room).detail

    def _publish_outbox(self, room_id: str, state: OutboxState) -> None:
        self.bus.publish(room_id, "outbox", {"room": room_id, **state.to_json()})

    # -------------------------------------------------------------- 구독

    def _deliver(self, room_id: str, message: schema.Message, *, own: bool) -> bool:
        """새 메시지 1건을 브라우저(SSE)와 OS 알림으로 흘린다.

        **멱등하다.** gitwire 는 크래시 시점에 따라 재전달할 수 있고, 로컬
        에코와 구독 전달은 어차피 겹친다. 봉투 ID 로 한 번만 통과시킨다.
        """
        seen = self._seen.setdefault(room_id, _Seen())
        if not seen.add(message.id):
            return False
        self.bus.publish(room_id, "message", self.message_json(room_id, message))
        if not own and self.bus.viewers(room_id) == 0:
            try:
                room = self.get(room_id)
                self.notifier.notify_message(
                    room.name or room_id, message.author, message.text
                )
            except Exception:  # noqa: BLE001 — 알림 실패가 수신을 막으면 안 된다
                log.debug("알림 실패", exc_info=True)
        return True

    def _own_sender(self, room_id: str) -> str:
        """이 앱이 그 방에 **발행할 때 쓰는** 봉투 sender.

        채널이 열려 있으면 그 값이 정본이다(방마다 다를 수 있다 — `channel_kwargs`
        로 갈아끼울 수 있는 표면이다). 아직 안 열렸으면 설치본 식별자로 답한다 —
        같은 home 이므로 gitwire 가 채널에 줄 값이 바로 그것이다.
        """
        channel = self._channels.get(room_id)
        return getattr(channel, "sender", "") if channel else self.instance

    def is_mine(self, room_id: str, message: schema.Message) -> bool:
        """이 메시지가 **이 설치본에서 나갔나** — 봉투만 보고 판정한다.

        ⭐ "내 것"의 정의는 이 함수 **하나뿐**이다. 화면의 좌우 배치도, 로컬
        에코 판정(알림을 띄울지)도 전부 여기를 지난다. 예전에는 좌우 배치가
        *보내는 순간의 특례*로만 세워져서, 새로고침하면 내가 쓴 말이 전부
        남의 것으로 넘어갔다 — 봉투에 답이 있는데 아무도 비교를 안 했다.

        ⚠️ 판정 대상은 **사람이 아니라 설치본**이다. 같은 사람이 다른 머신에서
        보낸 말은 여기서 '남의 것'이다 (봉투 `sender` 는 참가자 프로세스
        식별자다 — `schema` 모듈 도크). 사람 단위로 묶으려면 신원을 증명할
        수단이 있어야 하는데 봉투에 서명이 없다. 그래서 묶지 않는다.
        """
        return bool(message.sender) and message.sender == self._own_sender(room_id)

    def message_json(self, room_id: str, message: schema.Message) -> dict:
        """메시지 1건의 JSON. **서버가 직렬화할 때 `mine` 을 넣는다.**

        여기가 유일한 직렬화 지점이라, 최초 로드·위로 페이징·검색·SSE·전송
        응답이 전부 같은 판정을 지난다. 브라우저에 맡기면 자기 sender 를
        내려보내는 배관이 하나 더 늘고, 그 배관이 빠진 경로가 곧 버그가 된다.
        """
        return {**message.to_json(), "mine": self.is_mine(room_id, message)}

    def messages_json(
        self, room_id: str, messages: Iterable[schema.Message]
    ) -> list[dict]:
        return [self.message_json(room_id, m) for m in messages]

    def on_record(self, room_id: str, record: Any) -> bool:
        """구독 콜백 (테스트에서 직접 부를 수 있게 공개해 둔다)."""
        message = schema.parse_record(record)
        return self._deliver(room_id, message, own=self.is_mine(room_id, message))

    def _start_room(self, room_id: str) -> None:
        with self._lock:
            if room_id in self._subs:
                return
        try:
            channel = self.channel(room_id)
        except RoomError as exc:
            log.warning("방 %s 를 열 수 없어 구독을 건너뛴다: %s", room_id, exc)
            return
        # 이미 화면에 있는 과거 메시지로 알림 폭탄이 터지지 않게, 지금까지의
        # 레코드는 '처리됨'으로 표시하고 **이후 것만** 구독으로 받는다.
        try:
            channel.skip_to_now()
        except Exception:  # noqa: BLE001
            log.debug("백로그 건너뛰기 실패 — 그대로 진행한다", exc_info=True)

        def callback(record, _rid=room_id):
            try:
                self.on_record(_rid, record)
            except Exception:  # noqa: BLE001
                log.exception("레코드 처리 실패")

        def on_error(_record, exc, _rid=room_id):
            log.warning("방 %s 폴링 오류: %s", _rid, exc)
            self.bus.publish(_rid, "trouble", {"room": _rid, "detail": str(exc)})

        def on_cycle(_head, _rid=room_id):
            # 읽음 커서는 레코드가 아니라 폴링 콜백이 불리지 않는다 — 틱마다
            # 남의 커서를 보고 **바뀐 순간에만** 화면에 알린다 (`_reads_tick`).
            self._reads_tick(_rid)
            # ⭐ 같은 틱에서 **레코드 삭제를 받았는지**도 본다. 남이 지운 날짜가
            # 내 로컬 아카이브에 없거나 불완전하면 히스토리에서 채운다 — 그렇지
            # 않으면 그 날의 대화가 내 화면에서 조용히 사라진다.
            self._archive_tick(_rid, _head)

        try:
            sub = channel.subscribe(callback, on_error=on_error, on_cycle=on_cycle)
        except Exception as exc:  # noqa: BLE001
            log.warning("방 %s 구독 실패: %s", room_id, exc)
            return
        with self._lock:
            self._subs[room_id] = sub

    def start(self) -> None:
        """등록된 모든 방을 연결·구독한다. 한 방이 실패해도 나머지는 돈다.

        연결은 **백그라운드**다 — 방이 여럿이면 예전에는 앱 기동이 클론 N개를
        순서대로 기다렸고, 그동안 서버가 화면조차 못 줬다.
        """
        self._started = True
        for room in self.rooms():
            with self._lock:
                ready = room.id in self._channels
            if ready:
                self._start_room(room.id)
            else:
                self._connect_async(room.id)
        # ⭐ 일일 아카이빙 배치. 기동 직후 한 번 돌아 **밀린 분을 따라잡는다**
        # (앱이 04:00 에 꺼져 있었을 수 있다 — `archive.DailyRunner`).
        if self.settings.daily_archive:
            self._daily.start()

    def stop(self) -> None:
        self._started = False
        self._daily.stop()                   # 배치를 먼저 세운다 (채널을 쓴다)
        self.wait_for_connect(timeout=5.0)   # 진행 중인 클론을 먼저 정리한다
        with self._lock:
            refreshers = list(self._refreshers.values())
            self._refreshers.clear()
        for thread in refreshers:            # 채널을 닫기 전에 당기기를 거둔다
            thread.join(timeout=5.0)
        with self._lock:
            boxes = list(self._outboxes.values())
            self._outboxes.clear()
        # ⭐ 채널을 닫기 **전에** 밀어낸다 — 정상 종료에서 대기열을 버릴 이유가
        # 없다. 강제 종료는 이 경로를 못 타고, 그때는 아직 안 나간 것이 **사라진다**
        # (의도된 성질 — `send()` 도크). 그래서 이 한 번이 마지막 기회다.
        for box in boxes:
            try:
                box.close()
            except Exception:  # noqa: BLE001
                log.debug("아웃박스 정리 실패", exc_info=True)
        with self._lock:
            subs = list(self._subs.values())
            self._subs.clear()
            channels = list(self._channels.values())
            self._channels.clear()
            self._reads.clear()
            self._archives.clear()
            self._read_prints.clear()
        for sub in subs:
            try:
                sub.stop()
            except Exception:  # noqa: BLE001
                log.debug("구독 정리 실패", exc_info=True)
        for channel in channels:
            try:
                channel.close()
            except Exception:  # noqa: BLE001
                log.debug("채널 정리 실패", exc_info=True)
        self.notifier.close()
        self.bus.close_all()

    def refresh_async(self, room_id: str) -> threading.Thread | None:
        """폴 주기를 기다리지 않고 **백그라운드로** 한 번 당긴다 (화면을 막지 않는다).

        읽기 경로에서 원격 왕복을 뗐으므로(`page()` 참조), 방을 지금 막 열었다면
        마지막 폴 이후의 몇 초가 비어 있을 수 있다. 그 순간을 **기다림으로**
        메우지 않는다 — 로컬로 먼저 그린 뒤 여기서 당기고, 새 레코드는 이미 있는
        SSE 배관(`_deliver`)으로 뒤따라 붙는다.

        방당 최대 1개. 사용자가 방을 빠르게 오가도 `ls-remote` 스레드를 쌓지 않는다.
        """
        if not self._started:
            return None                 # 구독도 안 붙은 상태 — 당길 곳이 없다
        with self._lock:
            if room_id not in self._rooms:
                return None
            running = self._refreshers.get(room_id)
            if running is not None and running.is_alive():
                return running          # 이미 당기는 중이다
            thread = threading.Thread(
                target=self._refresh, args=(room_id,),
                name=f"gitwire-chat-refresh-{room_id[:8]}", daemon=True,
            )
            self._refreshers[room_id] = thread
        thread.start()
        return thread

    def _refresh(self, room_id: str) -> None:
        """`refresh_async` 의 몸통. 실패해도 조용히 넘긴다 — 폴러가 다음 주기에 또 본다."""
        try:
            self.poll_now(room_id)
        except (RoomError, RoomNotReady) as exc:
            log.debug("방 %s 즉시 당기기 실패: %s", room_id, exc)
        except Exception:  # noqa: BLE001
            log.debug("방 %s 즉시 당기기 실패", room_id, exc_info=True)

    def poll_now(self, room_id: str) -> int:
        """폴 주기를 기다리지 않고 즉시 한 번 당겨온다 (수동 새로고침).

        ⚠️ 원격 왕복(`ls-remote`)이 들어 있다 — 실측 1.3초. HTTP 요청 스레드에서
        직접 부르는 곳은 사용자가 **명시적으로** 누른 `POST .../refresh` 뿐이고,
        타임라인 조회는 `refresh_async()` 로 비동기로 부른다.
        """
        channel = self._ready_channel(room_id)
        delivered = 0

        def callback(record):
            nonlocal delivered
            if self.on_record(room_id, record):
                delivered += 1

        try:
            channel.poll_once(callback)
        except Exception as exc:  # noqa: BLE001
            raise RoomError(f"새로고침 실패: {exc}") from exc
        # 사람이 누른 새로고침도 폴 한 틱이다 — 남의 커서가 움직였으면 알린다.
        self._reads_tick(room_id)
        return delivered

    def info(self, room_id: str) -> dict:
        status = self.status(room_id).to_json()
        outbox = self.outbox_state(room_id).to_json()
        try:
            channel = self._ready_channel(room_id)
        except RoomNotReady as exc:
            return {"status": status, "outbox": outbox, "error": str(exc)}
        try:
            return {"status": status, "outbox": outbox, **channel.info()}
        except Exception as exc:  # noqa: BLE001
            return {"status": status, "outbox": outbox, "error": str(exc)}

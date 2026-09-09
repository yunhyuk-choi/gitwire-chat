"""⭐ 핵심 검증 — 앱 인스턴스 **두 개**가 로컬 bare 레포를 방으로 삼아
실제 git 으로 대화한다.

여기서는 아무것도 흉내 내지 않는다. gitwire 대역이 없고, git 은 진짜 git 이며,
"원격"은 진짜 bare 레포다. 이유는 단순하다 — clone·push·fetch·rebase·커서
영속은 대역으로 흉내내면 **아무것도 증명하지 못한다.**

이 테스트가 실제로 증명하는 것:

1. A 의 HTTP POST 한 번이 **git 레포 안의 파일**이 된다 (bare 레포를 직접 열어
   레코드 파일 존재를 확인한다 — 앱의 자기 보고가 아니라 지상 검증이다).
2. B 는 A 의 프로세스와 아무 연결도 없는데(공유 메모리·소켓 없음, 홈 디렉토리도
   따로다) 자기 폴링만으로 그 메시지를 얻는다.
3. 그 메시지가 B 의 **SSE 스트림**을 타고 브라우저까지 간다 — 즉 "A 가 보낸
   메시지가 B 화면에 뜬다"의 서버측 전 구간이 이어져 있다.
4. 반대 방향(B → A)도 성립한다.
5. 두 인스턴스는 상대 메시지를 **자기 에코로 오인하지 않는다.**
6. 보고 있는 탭이 없으면 **OS 알림 경로**를 탄다.

⚠️ **전송 응답은 더 이상 push 를 기다리지 않는다** (`gitwire_chat.outbox`). 그래서
"원격에 도달했나"는 POST 가 돌아온 시점의 사실이 아니라 *조금 뒤*의 사실이다 —
아래 검증은 전부 그 지점을 `_wait` 로 기다린다. 기다리는 것과 흉내 내는 것은
다르다: 여기서 보는 것은 여전히 **bare 레포 안의 진짜 파일**이다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from gitwire_chat import reads as reads_mod
from gitwire_chat.app import create_app
from gitwire_chat.config import Settings
from gitwire_chat.events import EventBus
from gitwire_chat.rooms import RoomManager

from conftest import RecordingNotifier

#: 로컬 git 왕복 + 폴 주기를 감안한 여유. 실제로는 훨씬 빨리 끝난다.
DEADLINE = 60.0
POLL = 1.0


class Instance:
    """한 참가자의 앱 한 벌 (자기 home, 자기 클론, 자기 커서, 자기 서버).

    `person` — 이 인스턴스가 **어느 사람인가** (읽음 표시의 참가자 키). 실제로는
    `git config user.email` 에서 오지만, 한 머신에서 서로 다른 사람으로 두
    인스턴스를 띄우려면 명시할 수밖에 없다 (`reads.PERSON_ENV`). 기반이
    `GITWIRE_SENDER` 로 같은 문을 열어 둔 것과 같은 성격이다.
    """

    def __init__(self, home: Path, author: str, person: str | None = None) -> None:
        self.person = person
        self.settings = Settings(
            home=home,
            author=author,
            poll_interval=POLL,
            recent_limit=50,
            page_limit=50,
            notifications=False,
        )
        self.notifier = RecordingNotifier()
        # 사람 식별자는 매니저를 만들 때 한 번 정해진다 — 그 순간에만 환경을
        # 바꿔 두고 되돌린다 (전역을 오염시키지 않는다).
        previous = os.environ.get(reads_mod.PERSON_ENV)
        if person:
            os.environ[reads_mod.PERSON_ENV] = person
        try:
            self.manager = RoomManager(
                self.settings,
                bus=EventBus(keepalive=0.2),
                notifier=self.notifier,
            )
        finally:
            if previous is None:
                os.environ.pop(reads_mod.PERSON_ENV, None)
            else:
                os.environ[reads_mod.PERSON_ENV] = previous
        self.app = create_app(self.settings, self.manager, start=False)
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        self.room_id: str | None = None

    def join(self, repo_url: str, name: str) -> str:
        """방 등록 → **즉시** 201. 클론은 백그라운드이므로 여기서 기다려 준다.

        (브라우저는 기다리지 않고 '받는 중' 을 그리다가 SSE 로 완료를 받는다.)
        """
        res = self.client.post(
            "/api/rooms", json={"repo_url": repo_url, "name": name}
        )
        assert res.status_code == 201, res.get_data(as_text=True)
        body = res.get_json()["room"]
        assert body["status"]["state"] == "connecting"
        self.room_id = body["id"]
        self.manager.wait_for_connect()
        assert self.manager.status(self.room_id).state == "ready", (
            self.manager.status(self.room_id).detail
        )
        return self.room_id

    def say(self, text: str) -> dict:
        res = self.client.post(
            f"/api/rooms/{self.room_id}/messages", json={"text": text}
        )
        assert res.status_code == 201, res.get_data(as_text=True)
        return res.get_json()["message"]

    def settle(self, timeout: float = DEADLINE) -> None:
        """아직 원격에 못 간 것이 없어질 때까지 기다린다.

        전송이 비동기가 된 뒤 "지금 원격에 있나"를 묻는 검증은 이 지점 뒤에
        서야 한다. 앱의 자기 보고를 믿는 것이 아니라, *언제 봐도 되는지*를
        아는 것이다 — 실제 확인은 여전히 bare 레포를 직접 연다.
        """
        for room in self.manager.rooms():
            self.manager.outbox(room.id).wait_idle(timeout)

    def timeline(self) -> list[dict]:
        res = self.client.get(f"/api/rooms/{self.room_id}/messages")
        assert res.status_code == 200, res.get_data(as_text=True)
        return res.get_json()["messages"]

    def close(self) -> None:
        self.manager.stop()


def _git_bare(bare: Path, *args: str) -> str:
    """bare 레포에 직접 git 을 건다.

    ``--git-dir`` 로 명시하는 이유: 어떤 환경은 ``safe.bareRepository=explicit``
    이라 cwd 로만 가리키면 git 이 거부한다 (이 머신이 실제로 그렇다).
    """
    result = subprocess.run(
        ["git", f"--git-dir={bare}", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _records_in_bare(bare: Path) -> list[str]:
    """bare 레포를 **직접** 열어 레코드 파일 목록을 본다 (지상 검증)."""
    out = _git_bare(bare, "ls-tree", "-r", "--name-only", "main")
    return [line for line in out.splitlines() if line.startswith("records/")]


def _record_payload(bare: Path, path: str) -> dict:
    return json.loads(_git_bare(bare, "show", f"main:{path}"))


def _wait(predicate, deadline: float = DEADLINE, step: float = 0.2):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return None


def _timeline_after_poll(inst: "Instance") -> list[dict]:
    """폴 한 번 + 타임라인 (상대가 올린 것이 내려왔는지 볼 때)."""
    inst.manager.poll_now(inst.room_id)
    return inst.timeline()


@pytest.fixture
def pair(tmp_path, bare_repo):
    a = Instance(tmp_path / "A" / "chats", "앨리스", person="alice@example.com")
    b = Instance(tmp_path / "B" / "chats", "밥", person="bob@example.com")
    try:
        yield a, b, str(bare_repo)
    finally:
        a.close()
        b.close()


def test_두_인스턴스가_실제_git_으로_대화한다(pair, bare_repo):
    a, b, repo_url = pair

    # (1) A 가 빈 레포를 방으로 등록한다 → gitwire 가 규약을 심는다.
    room_a = a.join(repo_url, "우리 방")
    # (2) B 가 같은 주소로 합류한다 — 같은 레포면 같은 방 ID 여야 한다.
    room_b = b.join(repo_url, "우리 방")
    assert room_a == room_b

    # 두 인스턴스는 서로 다른 전송 식별자를 갖는다 (같은 머신이어도).
    assert a.manager.instance != b.manager.instance

    # (3) B 가 구독을 시작하고 SSE 스트림을 연다 (= 브라우저 탭이 열렸다).
    b.manager.start()
    stream = b.client.get(
        f"/api/rooms/{room_b}/stream?client=tabB", buffered=False
    )
    chunks = stream.response
    assert next(chunks).decode("utf-8").startswith("retry:")
    assert "event: hello" in next(chunks).decode("utf-8")

    # (4) A 가 말한다. 이 한 번의 HTTP POST 가 git commit + push 가 된다.
    said = a.say("안녕 B, 나 A야")

    # (5) 지상 검증 — 앱의 보고가 아니라 **bare 레포 안의 파일**을 직접 본다.
    #     ⚠️ '레코드가 하나라도 있나'로 기다리면 안 된다 — 방 규약을 심을 때
    #     들어간 `records/.gitkeep` 이 그 조건을 이미 만족시킨다(그래서 push 를
    #     기다리지 않게 된 순간 이 검증이 헛통과했다). **그 레코드**를 기다린다.
    paths = _wait(lambda: said["id"] in _records_in_bare(bare_repo)) and         _records_in_bare(bare_repo)
    assert paths, "레코드가 원격 레포에 도달하지 않았다"
    assert said["id"] in paths
    envelope = _record_payload(bare_repo, said["id"])
    assert envelope["payload"] == {
        "kind": "msg", "v": 1, "author": "앨리스", "text": "안녕 B, 나 A야",
    }
    assert envelope["sender"] == a.manager.instance   # 봉투는 전송 식별자를 싣고
    assert "author" not in envelope                    # 표시 이름은 payload 안에만

    # (6) ⭐ B 의 SSE 스트림으로 그 메시지가 온다 — A 프로세스와는 아무 연결이 없다.
    received = None
    end = time.monotonic() + DEADLINE
    while time.monotonic() < end and received is None:
        chunk = next(chunks).decode("utf-8")
        if chunk.startswith("event: message"):
            received = json.loads(chunk.split("data: ", 1)[1])
    assert received is not None, "B 의 SSE 로 A 의 메시지가 오지 않았다"
    assert received["text"] == "안녕 B, 나 A야"
    assert received["author"] == "앨리스"
    assert received["id"] == said["id"]                # 같은 봉투 ID = 같은 메시지
    stream.close()

    # (7) 반대 방향 — B 가 답하고 A 가 받는다.
    replied = b.say("어 A, 잘 지냈어?")

    def poll_and_find():
        a.manager.poll_now(room_a)
        return [m for m in a.timeline() if m["text"] == "어 A, 잘 지냈어?"]

    got = _wait(poll_and_find)
    assert got, "A 가 B 의 답을 받지 못했다"
    assert got[0]["id"] == replied["id"]
    assert got[0]["author"] == "밥"

    # (8) 두 사람의 타임라인이 같은 대화로 수렴한다 (파일이 곧 기록이다).
    a_texts = [m["text"] for m in a.timeline()]
    b.manager.poll_now(room_b)
    b_texts = [m["text"] for m in b.timeline()]
    assert a_texts == b_texts == ["안녕 B, 나 A야", "어 A, 잘 지냈어?"]


def test_보는_탭이_없으면_OS_알림_경로를_탄다(pair):
    a, b, repo_url = pair
    room = a.join(repo_url, "우리 방")
    b.join(repo_url, "우리 방")
    b.manager.start()          # 구독은 돌지만 SSE 구독자(브라우저)는 없다

    a.say("자니?")

    hit = _wait(lambda: b.notifier.sent)
    assert hit, "탭이 없는데 OS 알림 경로를 타지 않았다"
    title, body = hit[-1]
    assert "우리 방" in title
    assert "앨리스: 자니?" == body

    # A 자신은 자기가 보낸 것으로 알림받지 않는다.
    assert a.notifier.sent == []


def test_상대_메시지를_자기_에코로_오인하지_않는다(pair):
    """같은 머신에서 띄운 두 인스턴스도 서로를 남으로 본다."""
    a, b, repo_url = pair
    a.join(repo_url, "우리 방")
    b.join(repo_url, "우리 방")

    a.say("나야")
    delivered = _wait(lambda: b.manager.poll_now(b.room_id) or None)
    assert delivered == 1

    # B 입장에서 A 의 메시지는 '남의 것' 이므로 알림 대상이다.
    assert b.notifier.sent and "앨리스: 나야" in b.notifier.sent[-1][1]


def test_읽기에서_원격을_뗀_뒤에도_폴러가_새_메시지를_가져온다(pair, bare_repo):
    """⭐ 조회는 로컬 클론만 읽는다 — 그럼 새 메시지는 누가 가져오나.

    답은 **구독(폴러)** 이다. 그것을 증명하려면 다른 경로를 전부 막아야 한다:

    * '지금 당기기'(`refresh_async`)를 **꺼 버린다** — 방을 열 때 도는 그 경로가
      남아 있으면 폴러가 죽어 있어도 이 테스트가 통과해 버린다.
    * 그리고 `poll_now()` 를 손으로 부르지 않는다.

    남는 것은 백그라운드 구독 스레드 하나뿐이고, 그것만으로 A 의 말이 B 의
    **로컬 읽기**에 나타나야 한다.
    """
    a, b, repo_url = pair
    a.join(repo_url, "우리 방")
    room_b = b.join(repo_url, "우리 방")

    # 조회 경로가 정말 로컬만 읽는지 그 자리에서 기록한다 (기반 호출을 가로챈다).
    channel = b.manager.channel(room_b)
    original_page = channel.history_page
    fresh_flags: list[bool] = []

    def watched(*, before=None, limit=50, fresh=True):
        fresh_flags.append(bool(fresh))
        return original_page(before=before, limit=limit, fresh=fresh)

    channel.history_page = watched
    # '방을 열 때 한 번 당기기'를 막는다 — 폴러 말고는 아무것도 남기지 않는다.
    b.manager.refresh_async = lambda room_id: None

    b.manager.start()                     # 유일하게 남은 신선도 경로
    assert b.timeline() == []
    assert fresh_flags == [False], "조회가 원격을 봤다"

    said = a.say("폴러야 이거 가져와")

    got = _wait(lambda: [m for m in b.timeline() if m["id"] == said["id"]])
    assert got, "폴러가 새 메시지를 가져오지 못했다"
    assert got[0]["text"] == "폴러야 이거 가져와"
    assert got[0]["author"] == "앨리스"
    # 그동안 조회는 **한 번도** 원격을 보지 않았다.
    assert fresh_flags == [False] * len(fresh_flags)


def test_재시작해도_커서와_방_목록이_이어진다(tmp_path, bare_repo):
    """일회성/재기동 소비자여도 중복·유실이 없다 — gitwire 커서가 디스크에 있다."""
    first = Instance(tmp_path / "A" / "chats", "앨리스")
    first.join(str(bare_repo), "우리 방")
    first.say("첫 마디")
    first.close()

    restarted = Instance(tmp_path / "A" / "chats", "앨리스")
    try:
        assert [r.repo_url for r in restarted.manager.rooms()] == [str(bare_repo)]
        # 방 목록이 디스크에서 그대로 복원된다 — 다시 등록할 필요가 없다.
        restarted.room_id = restarted.manager.rooms()[0].id
        # 재시작 후 연결(클론은 이미 디스크에 있다)도 백그라운드다.
        restarted.manager.connect(restarted.room_id)
        restarted.manager.wait_for_connect()
        assert restarted.manager.status(restarted.room_id).state == "ready"
        # 전송 식별자도 그대로 — 재시작해도 '내 메시지' 판정이 유지된다.
        assert restarted.manager.instance == first.manager.instance
        assert [m["text"] for m in restarted.timeline()] == ["첫 마디"]
    finally:
        restarted.close()


def test_내_것과_남의_것이_봉투로_갈린다(pair):
    """⭐ 두 설치본이 **같은 레코드를 서로 반대로** 판정한다.

    이게 `mine` 의 전부다 — '내 것'은 레코드의 속성이 아니라 **읽는 설치본과의
    관계**다. 그래서 A 가 쓴 말은 A 화면에서 오른쪽(내 것), B 화면에서 왼쪽이다.

    회귀의 초점: A 가 **다시 읽었을 때**(= 새로고침) 판정이 살아 있는가.
    예전에는 전송 직후의 화면 특례로만 참이었고, 다시 읽으면 전부 남의 것이
    됐다 — 봉투에 답이 있는데 아무도 비교하지 않았기 때문이다.
    """
    a, b, repo_url = pair
    room = a.join(repo_url, "우리 방")
    assert b.join(repo_url, "우리 방") == room
    assert a.manager.instance != b.manager.instance

    said = a.say("이건 A 가 쓴 말")
    assert said["mine"] is True, "전송 응답부터 내 것이 아니다"

    # (1) A 가 다시 읽는다 — 새로고침이 하는 그 요청이다.
    #     ⚠️ 조회는 **커밋된 것**을 읽는다(기반의 읽기 API 가 커밋 기준이다).
    #     커밋이 아웃박스로 넘어갔으므로 여기는 '보낸 직후'가 아니라 '한 바퀴 뒤'다.
    a.settle()
    reread = [m for m in a.timeline() if m["id"] == said["id"]]
    assert reread and reread[0]["mine"] is True, "다시 읽으니 내 것이 아니게 됐다"

    # (2) B 가 같은 레코드를 받아온다 — 같은 봉투, 반대 판정.
    def b_sees():
        b.manager.poll_now(b.room_id)
        return [m for m in b.timeline() if m["id"] == said["id"]]

    got = _wait(b_sees)
    assert got, "B 가 A 의 메시지를 받지 못했다"
    assert got[0]["sender"] == said["sender"]      # 봉투는 같고
    assert got[0]["mine"] is False                 # 판정만 반대다

    # (3) 반대 방향도 대칭이다.
    replied = b.say("이건 B 가 쓴 말")
    assert replied["mine"] is True

    def a_sees():
        a.manager.poll_now(a.room_id)
        return [m for m in a.timeline() if m["id"] == replied["id"]]

    back = _wait(a_sees)
    assert back and back[0]["mine"] is False

    # (4) 검색도 같은 문을 지난다 (조회 경로마다 다른 답이 나오면 안 된다).
    for inst, mine_of_a in ((a, True), (b, False)):
        hits = inst.client.get(
            f"/api/rooms/{inst.room_id}/search?q=A 가 쓴 말"
        ).get_json()["messages"]
        assert [m["mine"] for m in hits] == [mine_of_a]


# ═══════════════════════════════════════════════════════════════════════════
# ⭐ 읽음 표시 — 여기서만 증명되는 것들
#
# 대역으로는 아무것도 증명하지 못하는 부분이 정확히 셋이다:
#   1. 커서가 **진짜 파일**이 되어 원격에 올라가고, 상대가 자기 폴링으로 받는다.
#   2. 두 사람이 **같은 순간** 읽어도 충돌이 없다 (경로가 다르므로).
#   3. 카운트가 **파생값**이다 — 커서 하나가 전진하면 그 아래 전부가 줄어든다.
#
# 그리고 카운트 공식은 **화면 것**이므로(`static/js/reads.js`) 여기서 파이썬으로
# 다시 적지 않는다 — 진짜 그 모듈을 node 로 불러 쓴다 (`tests/js/count-unread.mjs`).
# ═══════════════════════════════════════════════════════════════════════════

COUNT_SCRIPT = Path(__file__).resolve().parent / "js" / "count-unread.mjs"
node = shutil.which("node")
needs_node = pytest.mark.skipif(node is None, reason="node 가 없다 — 카운트 공식을 부를 수 없다")


def _counts(view: dict, messages: list[dict]) -> list[int]:
    """⭐ **화면이 쓰는 그 공식**으로 카운트를 계산한다 (파이썬에 베끼지 않는다)."""
    payload = json.dumps({
        "participants": view["participants"],
        "messages": [{"id": m["id"], "sender": m["sender"]} for m in messages],
    })
    proc = subprocess.run(
        [node, str(COUNT_SCRIPT)], input=payload, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _cursors_in_bare(bare: Path) -> dict[str, str]:
    """bare 레포를 **직접** 열어 참가자별 커서를 읽는다 (지상 검증).

    앱의 보고가 아니라 원격 레포 안의 파일이 근거다.
    """
    listing = _git_bare(bare, "ls-tree", "-r", "--name-only", "main").splitlines()
    out: dict[str, str] = {}
    for path in listing:
        if not path.startswith("participants/") or not path.endswith(".json"):
            continue
        envelope = json.loads(_git_bare(bare, "show", f"main:{path}"))
        out[envelope["key"]] = envelope["value"].get("cursor", "")
    return out


def _open_room(inst: "Instance") -> dict:
    """브라우저가 방을 열 때 하는 두 호출 (타임라인 + 읽음 스냅샷)."""
    inst.timeline()                     # 이 호출이 내 커서 파일을 만든다
    res = inst.client.get(f"/api/rooms/{inst.room_id}/reads")
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()


def _reads(inst: "Instance") -> dict:
    """폴 한 번 + 읽음 스냅샷 (상대의 커서가 내 폴링으로 들어온다)."""
    inst.manager.poll_now(inst.room_id)
    return inst.client.get(f"/api/rooms/{inst.room_id}/reads").get_json()


def _mark(inst: "Instance", message_id: str) -> dict:
    """브라우저가 "여기까지 읽었다"고 알리는 그 POST."""
    res = inst.client.post(
        f"/api/rooms/{inst.room_id}/reads", json={"cursor": message_id}
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()


@needs_node
def test_읽음_카운트가_실제_git_왕복으로_1에서_0이_된다(pair, bare_repo):
    """⭐ A 가 보낸 말의 카운트 1 → B 가 읽고 push → A 의 폴링 후 0.

    실제 파일·실제 push 다. 중간에 흉내내는 것이 하나도 없다.
    """
    a, b, repo_url = pair
    room = a.join(repo_url, "우리 방")
    assert b.join(repo_url, "우리 방") == room
    a.manager.start()
    b.manager.start()

    # (1) 둘 다 방을 연다 — 참가자 집합 = **커서 파일 집합**이므로, 여기서 각자
    #     자기 파일이 생긴다. (설치 스크립트가 아니라 방을 열 때다.)
    _open_room(a)
    _open_room(b)
    a.settle()
    b.settle()

    # 지상 검증 — 원격 레포에 커서 파일이 **둘** 있다.
    cursors = _wait(lambda: (
        _cursors_in_bare(bare_repo)
        if len(_cursors_in_bare(bare_repo)) == 2 else None
    ))
    assert cursors is not None, _cursors_in_bare(bare_repo)
    assert set(cursors) == {a.manager.person, b.manager.person}, cursors

    # (2) A 가 말한다.
    said = a.say("이거 읽었니?")
    a.settle()

    # (3) B 가 자기 폴링으로 그 말을 받는다 (A 프로세스와 아무 연결이 없다).
    got = _wait(lambda: [
        m for m in _timeline_after_poll(b) if m["id"] == said["id"]
    ])
    assert got, "B 가 A 의 말을 받지 못했다"

    # (4) ⭐ A 쪽 카운트 = **1** (B 가 아직 안 읽었다). A 는 자기가 보낸 말을
    #     이미 읽은 것으로 세지 않는다 (`p ≠ A`).
    view = _wait(lambda: (
        _reads(a) if len(_reads(a)["participants"]) == 2 else None
    ))
    assert view is not None, "A 가 B 의 커서 파일을 보지 못했다"
    assert _counts(view, [said]) == [1], view["participants"]

    # (5) B 가 읽는다 — 브라우저가 하는 그 POST 하나. 발행은 아웃박스가 민다.
    _mark(b, said["id"])
    b.settle()

    # 지상 검증 — 원격 레포의 B 커서가 그 메시지까지 올라갔다.
    assert _wait(lambda: _cursors_in_bare(bare_repo).get(b.manager.person) == said["id"]), \
        _cursors_in_bare(bare_repo)

    # (6) ⭐ A 의 폴링 후 카운트 = **0**. 지연은 구조적이다 (상대 push → 내 폴링).
    zero = _wait(lambda: _counts(_reads(a), [said]) == [0])
    assert zero, f"카운트가 0 으로 줄지 않았다: {_reads(a)['participants']}"

    # 그리고 그 사이 아무 메시지도 잃지 않았다 (읽음 발행이 대화를 밀어내지 않는다).
    assert [m["text"] for m in a.timeline()] == ["이거 읽었니?"]
    assert a.manager.outbox_state(room).state == "synced"


@needs_node
def test_커서_하나가_여러_칸_전진하면_아래_전부의_카운트가_줄어든다(pair, bare_repo):
    """⭐ 카운트가 **파생값**이라는 증명 — 저장값이면 이렇게 되지 않는다.

    A 가 4건을 보낸 뒤 B 가 **한 번** 커서를 맨 아래로 옮긴다. 그 한 번의 push 로
    네 메시지의 카운트가 전부 줄어든다 (메시지마다 저장된 숫자를 갱신한 것이
    아니다 — 원격에 오간 것은 커서 파일 하나뿐이다).
    """
    a, b, repo_url = pair
    room = a.join(repo_url, "우리 방")
    b.join(repo_url, "우리 방")
    a.manager.start()
    b.manager.start()
    _open_room(a)
    _open_room(b)
    a.settle()
    b.settle()

    said = [a.say(f"{i}번째") for i in range(4)]
    a.settle()

    view = _wait(lambda: (
        _reads(a) if len(_reads(a)["participants"]) == 2 else None
    ))
    assert view is not None
    assert _counts(view, said) == [1, 1, 1, 1], view["participants"]

    # ⭐ **한 번**의 커서 이동 (그리고 원격에 오가는 것은 커서 파일 하나다).
    commits_before = int(_git_bare(bare_repo, "rev-list", "--count", "main").strip())
    _mark(b, said[-1]["id"])
    b.settle()
    assert _wait(lambda: _cursors_in_bare(bare_repo).get(b.manager.person) == said[-1]["id"])
    commits_after = int(_git_bare(bare_repo, "rev-list", "--count", "main").strip())
    assert commits_after - commits_before == 1, "커서 전진에 커밋이 여러 개 생겼다"

    dropped = _wait(lambda: _counts(_reads(a), said) == [0, 0, 0, 0])
    assert dropped, f"아래 메시지 전부가 줄지 않았다: {_counts(_reads(a), said)}"


@needs_node
def test_두_사람이_같은_순간_읽어도_충돌이_없다(tmp_path, bare_repo):
    """⭐ 동시 읽기 — 둘 다 반영되고 충돌이 0 이다.

    근거는 **경로마다 쓰는 사람이 한 명**이라는 성질이다 (`gitwire.state`).
    공유 파일 하나에 전원의 커서를 담으면 여기서 매번 충돌한다.

    ⚠️ 읽는 쪽을 **B·C 두 사람**으로 세운다. 커서는 자기가 보낸 말까지 자동으로
    전진하므로(그게 옳다), 말한 사람 A 는 이미 맨 아래에 있어 "전진"이 없다 —
    그러면 동시 쓰기가 한 건뿐이라 아무것도 증명되지 않는다.
    """
    a = Instance(tmp_path / "A" / "chats", "앨리스", person="alice@example.com")
    b = Instance(tmp_path / "B" / "chats", "밥", person="bob@example.com")
    c = Instance(tmp_path / "C" / "chats", "캐럴", person="carol@example.com")
    repo_url = str(bare_repo)
    try:
        room = a.join(repo_url, "우리 방")
        b.join(repo_url, "우리 방")
        c.join(repo_url, "우리 방")
        for inst in (a, b, c):
            inst.manager.start()
            _open_room(inst)               # 대화가 없는 지금 = 커서가 비어 있다
            inst.settle()

        said = a.say("둘이 같이 읽어라")
        a.settle()
        for inst in (b, c):
            assert _wait(lambda inst=inst: [
                m for m in _timeline_after_poll(inst) if m["id"] == said["id"]
            ]), f"{inst.settings.author} 가 A 의 말을 받지 못했다"

        # ⭐ 같은 순간에 각자 "여기까지 읽었다"를 올린다 (서로 다른 경로다).
        errors: list[BaseException] = []
        gate = threading.Barrier(2)

        def read_now(inst):
            try:
                gate.wait(timeout=30)
                _mark(inst, said["id"])
                inst.settle()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=read_now, args=(x,)) for x in (b, c)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=DEADLINE)

        assert not errors, errors
        # 둘 다 원격에 반영됐다 (한쪽이 다른 쪽을 밀어내지 않았다).
        landed = _wait(lambda: (
            _cursors_in_bare(bare_repo)
            if _cursors_in_bare(bare_repo).get("bob@example.com") == said["id"]
            and _cursors_in_bare(bare_repo).get("carol@example.com") == said["id"]
            else None
        ))
        assert landed is not None, _cursors_in_bare(bare_repo)
        assert set(landed) == {
            "alice@example.com", "bob@example.com", "carol@example.com"
        }, landed
        # 충돌 표식이 남지 않았다 — 아웃박스가 stuck 이 아니고 대화가 그대로다.
        for inst in (a, b, c):
            state = inst.manager.outbox_state(inst.room_id)
            assert state.state == "synced", (inst.settings.author, state.detail)
        # 그리고 A 쪽에서 카운트가 0 이 된다 (둘 다 읽었으므로).
        assert _wait(lambda: _counts(_reads(a), [said]) == [0]),             _reads(a)["participants"]
    finally:
        c.close()
        b.close()
        a.close()


def test_유령_참가자의_커서는_계속_세어진다(pair, bare_repo):
    """설치했지만 앱을 안 켜는 사람 — 카운트가 영구히 ≥1 이다. **그게 사실이다.**

    분모에서 빼지 않는다. 조용히 "다 읽음"이 되는 것이 더 나쁘다.
    """
    a, b, repo_url = pair
    room = a.join(repo_url, "우리 방")
    b.join(repo_url, "우리 방")
    a.manager.start()
    b.manager.start()
    _open_room(a)
    _open_room(b)
    a.settle()
    b.settle()
    # B 는 이후 아무것도 하지 않는다 (앱을 켜 두고 방을 안 본다).

    said = a.say("아무도 안 읽는다")
    a.settle()

    view = _wait(lambda: (
        _reads(a) if len(_reads(a)["participants"]) == 2 else None
    ))
    assert view is not None
    ghost = [p for p in view["participants"] if p["key"] == b.manager.person][0]
    assert ghost["cursor"] < said["id"], ghost
    # 그리고 그 사람이 분모에 그대로 남아 있다.
    assert b.manager.person in {p["key"] for p in view["participants"]}


#: 실제 방의 `participants/*.json` 에 **정말로 저장돼 있던** 오염 값. 화면의
#: 낙관적 항목의 임시 ID 가 커서로 채택돼 원격까지 올라갔다.
DIRTY_CURSOR = "~pending/000004"


@needs_node
def test_원격에_올라간_오염_커서를_만나도_카운트가_동작한다(pair, bare_repo):
    """⭐ **복구 경로** — 실제 git 으로, 실제 오염 값으로.

    구버전이 올린 `~pending/…` 이 원격 `participants/` 에 남아 있는 상태다.
    그 값은 사전식으로 모든 실제 ID 보다 크므로 그대로 쓰면 그 사람은 "다 읽은
    사람"이 되어 카운트가 **조용히 0** 이 된다 (실측된 고장).

    ⚠️ 사용자 방 파일을 손으로 고치지 않아도 되어야 한다 — 여기서 고치는 것은
    **코드**이고, 이 테스트는 오염이 남아 있는 채로 카운트가 사는지를 본다.
    """
    a, b, repo_url = pair
    room = a.join(repo_url, "우리 방")
    b.join(repo_url, "우리 방")
    a.manager.start()
    b.manager.start()
    _open_room(a)
    _open_room(b)
    a.settle()
    b.settle()

    # (1) B 가 **구버전처럼** 오염된 커서를 발행한다 (기반 API 를 직접 쓴다 —
    #     새 코드의 쓰기 경로는 이제 이 값을 거부하므로 앱으로는 만들 수 없다).
    channel = b.manager.reads(b.room_id).channel
    channel.write_state(
        b.manager.reads(b.room_id).key,
        reads_mod.build_value(DIRTY_CURSOR, [channel.sender]),
        identity=b.manager.person,
    )
    b.manager.poll_now(b.room_id)
    b.settle()

    # 지상 검증 — 원격 레포 안에 그 오염 값이 **정말로** 있다.
    landed = _wait(lambda: (
        _cursors_in_bare(bare_repo)
        if _cursors_in_bare(bare_repo).get(b.manager.person) == DIRTY_CURSOR
        else None
    ))
    assert landed is not None, _cursors_in_bare(bare_repo)

    # (2) A 가 말한다. B 의 커서는 오염된 값 그대로다.
    said = a.say("오염된 커서 옆에서도 세어져야 한다")
    a.settle()

    view = _wait(lambda: (
        _reads(a) if len(_reads(a)["participants"]) == 2 else None
    ))
    assert view is not None, "A 가 B 의 커서 파일을 보지 못했다"
    seen = {p["key"]: p["cursor"] for p in view["participants"]}
    assert seen[b.manager.person] == "", f"오염 값을 커서로 들고 있다: {seen}"

    # ⭐ (3) 카운트가 **1** 이다 — 조용히 0 이 되지 않았다.
    assert _wait(lambda: _counts(_reads(a), [said]) == [1]),         f"오염된 커서 때문에 카운트가 사라졌다: {_reads(a)['participants']}"

    # (4) 그리고 B 가 실제로 읽으면 정상적으로 0 이 된다 (오염이 굳지 않았다 —
    #     단조 증가가 `~pending` 을 최대값으로 붙들고 있으면 여기서 막힌다).
    got = _wait(lambda: [
        m for m in _timeline_after_poll(b) if m["id"] == said["id"]
    ])
    assert got, "B 가 A 의 말을 받지 못했다"
    _mark(b, said["id"])
    b.settle()
    assert _wait(lambda: _cursors_in_bare(bare_repo).get(b.manager.person) == said["id"]),         _cursors_in_bare(bare_repo)
    assert _wait(lambda: _counts(_reads(a), [said]) == [0]),         f"실제 ID 로 되돌아오지 못했다: {_reads(a)['participants']}"


def test_임시_ID_는_실제_git_경로에서도_커서가_되지_않는다(pair, bare_repo):
    """⭐ 쓰기 문 — 임시 ID 는 400 이고 **원격에 아무것도 남지 않는다.**"""
    a, b, repo_url = pair
    a.join(repo_url, "우리 방")
    b.join(repo_url, "우리 방")
    a.manager.start()
    b.manager.start()
    _open_room(a)
    _open_room(b)
    a.settle()
    b.settle()

    said = a.say("이 말의 카운트가 살아 있어야 한다")
    a.settle()
    assert _wait(lambda: [
        m for m in _timeline_after_poll(b) if m["id"] == said["id"]
    ])

    res = b.client.post(
        f"/api/rooms/{b.room_id}/reads", json={"cursor": DIRTY_CURSOR}
    )
    assert res.status_code == 400, res.get_data(as_text=True)
    b.settle()
    # 지상 검증 — 원격 레포의 B 커서에 그 값이 없다.
    assert _cursors_in_bare(bare_repo).get(b.manager.person) != DIRTY_CURSOR,         _cursors_in_bare(bare_repo)


def test_한_사람_두_기기는_한_파일을_공유한다(tmp_path, bare_repo):
    """같은 사람(같은 이메일)의 노트북·데스크탑 — 참가자는 **한 명**이다.

    설치본 식별자로 세면 여기서 참가자 2명이 되어 카운트가 0 이 되지 않는다.
    그리고 두 기기가 같은 경로를 쓰므로 push 경합이 나는데, 그것이 대화를 막지
    않는다는 것까지 함께 본다 (기반의 rebase 충돌 규약).
    """
    laptop = Instance(tmp_path / "L" / "chats", "최윤혁", person="same@example.com")
    desktop = Instance(tmp_path / "D" / "chats", "최윤혁", person="same@example.com")
    try:
        room = laptop.join(str(bare_repo), "내 방")
        desktop.join(str(bare_repo), "내 방")
        laptop.manager.start()
        desktop.manager.start()
        _open_room(laptop)
        _open_room(desktop)
        laptop.settle()
        desktop.settle()

        said = laptop.say("두 기기에서 본다")
        laptop.settle()
        assert _wait(lambda: [
            m for m in _timeline_after_poll(desktop) if m["id"] == said["id"]
        ])

        # 데스크탑에서 읽는다 → 참가자 파일은 여전히 **하나**다.
        _mark(desktop, said["id"])
        desktop.settle()
        cursors = _wait(lambda: (
            _cursors_in_bare(bare_repo)
            if _cursors_in_bare(bare_repo).get("same@example.com") == said["id"]
            else None
        ))
        assert cursors is not None, _cursors_in_bare(bare_repo)
        assert list(cursors) == ["same@example.com"], cursors

        # 그리고 두 기기 모두 계속 말할 수 있다 (경합이 대화를 막지 않았다).
        again = desktop.say("여기서도 보낸다")
        desktop.settle()
        assert _wait(lambda: [
            m for m in _timeline_after_poll(laptop) if m["id"] == again["id"]
        ])
    finally:
        desktop.close()
        laptop.close()

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
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from gitwire_chat.app import create_app
from gitwire_chat.config import Settings
from gitwire_chat.events import EventBus
from gitwire_chat.rooms import RoomManager

from conftest import RecordingNotifier

#: 로컬 git 왕복 + 폴 주기를 감안한 여유. 실제로는 훨씬 빨리 끝난다.
DEADLINE = 60.0
POLL = 1.0


class Instance:
    """한 참가자의 앱 한 벌 (자기 home, 자기 클론, 자기 커서, 자기 서버)."""

    def __init__(self, home: Path, author: str) -> None:
        self.settings = Settings(
            home=home,
            author=author,
            poll_interval=POLL,
            recent_limit=50,
            page_limit=50,
            notifications=False,
        )
        self.notifier = RecordingNotifier()
        self.manager = RoomManager(
            self.settings,
            bus=EventBus(keepalive=0.2),
            notifier=self.notifier,
        )
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


@pytest.fixture
def pair(tmp_path, bare_repo):
    a = Instance(tmp_path / "A" / "chats", "앨리스")
    b = Instance(tmp_path / "B" / "chats", "밥")
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
    paths = _wait(lambda: _records_in_bare(bare_repo))
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

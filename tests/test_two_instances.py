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
        res = self.client.post(
            "/api/rooms", json={"repo_url": repo_url, "name": name}
        )
        assert res.status_code == 201, res.get_data(as_text=True)
        self.room_id = res.get_json()["room"]["id"]
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
        # 전송 식별자도 그대로 — 재시작해도 '내 메시지' 판정이 유지된다.
        assert restarted.manager.instance == first.manager.instance
        assert [m["text"] for m in restarted.timeline()] == ["첫 마디"]
    finally:
        restarted.close()

"""읽음 표시 — 커서 하나에서 두 사실이 나온다 (`gitwire_chat/reads.py`).

여기서 못 박는 것:

* **R-1** 방 목록 뱃지(**내가** 안 읽은 개수)는 **발행이 0** 이다. 그리고
  ⭐ **git 호출을 늘리지 않는다** (실제 채널 + 호출 계수기로 확인한다 — 유휴
  폴링을 git 1개로 줄인 것을 되돌리지 않는다는 근거).
* **R-2** 참가자 집합 = **커서 파일 집합.** 방을 처음 열면 내 파일이 생기고,
  이미 있으면 덮어쓰지 않는다.
* **R-3** 커서는 **단조 증가**한다 — 뒤로 보내려 해도 안 간다.
* **R-4** 내가 보낸 말은 내가 읽은 말이다 (남의 화면에서 내가 안 읽은 사람으로
  세어지지 않는다 = 카운트 공식의 `p ≠ A`).
* **R-5** **유령 참가자**는 그냥 센다 — 안 움직이는 커서가 계속 분모에 남는다.
* **R-6** 남의 커서가 움직이면 **폴 한 틱**에 그것을 알아채고 화면에 알린다
  (레코드가 0건인 변화다 — 구독 콜백은 불리지 않는다). 안 움직였으면 안 민다.
* **R-7** 발행은 **아웃박스에 얹힌다** (메시지 건수를 오염시키지 않는다).
* **R-8** ⭐ 커서는 **실제 봉투 ID 만** 받는다 — 화면의 낙관적 임시
  ID(`~pending/…`)를 밀어 넣으면 **거부**하고(400), 이미 오염된 값을 **읽을 때는**
  커서 없음으로 취급해 카운트를 되살린다. 실측된 전면 고장의 재발 방지선이다
  (`reads` 모듈 도크 「커서 형식」).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import gitwire
from gitwire import localrefs
from gitwire.gitcmd import SubprocessGitRunner

from gitwire_chat import reads as reads_mod
from gitwire_chat.app import create_app
from gitwire_chat.config import Settings
from gitwire_chat.events import EventBus
from gitwire_chat.rooms import RoomManager

from conftest import ConnectedRoomManager, RecordingNotifier

REPO = "https://example.invalid/team/room.git"


def _channel(fake_opener):
    return list(fake_opener.channels.values())[0]


def _drain(sub, name: str) -> list[dict]:
    """구독자 큐에서 그 이름의 이벤트만 골라낸다 (큐를 비우면서)."""
    out = []
    while not sub.queue.empty():
        event = sub.queue.get_nowait()
        if event is not None and event.name == name:
            out.append(event.data)
    return out


# ------------------------------------------------------------- 값·공식


def test_사람_식별자는_git_이메일이다(monkeypatch):
    monkeypatch.setattr(gitwire, "git_email", lambda: "yh.choi@example.com")
    assert reads_mod.person_id() == "yh.choi@example.com"
    # 파일명 키는 기반이 깎는다 (경로 탈출 없음).
    assert gitwire.state_key("yh.choi@example.com") == "yh.choi@example.com"


def test_이메일이_없으면_설치본으로_떨어지고_로그를_남긴다(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(gitwire, "git_email", lambda: "")
    with caplog.at_level("WARNING"):
        got = reads_mod.person_id(tmp_path)
    assert got
    assert "user.email" in caplog.text


def test_발행되는_값에_카운트가_없다():
    """⭐ 카운트를 메시지마다 저장하지 않는다 — 커서 하나뿐이다."""
    value = reads_mod.build_value("records/20260908/x.json", ["a.host", "b.host"])
    assert set(value) == {"kind", "v", "cursor", "senders"}
    assert value["cursor"] == "records/20260908/x.json"


def test_모르는_종류의_상태는_건너뛴다():
    class State:
        key = "zz@x.io"
        identity = "zz@x.io"
        value = {"kind": "칸반-보드", "v": 9}
        updated_at = None

    assert reads_mod.parse_state(State()) is None
    assert reads_mod.parse_state(None) is None


# ------------------------- R-8. 커서 형식 (실제 봉투 ID 만)

#: 실제 방(`participants/…json`)에 **정말로 저장돼 있던** 값이다. 화면의 낙관적
#: 항목의 임시 ID 가 커서로 채택돼 원격까지 올라갔다.
DIRTY = "~pending/000004"


def test_오염된_커서는_사전식으로_모든_실제_ID_보다_크다():
    """⭐ 이 성질이 고장을 **되돌릴 수 없게** 만든 원인이다 (그래서 먼저 못 박는다).

    커서는 단조 증가(`max`)이므로, 한 번 이 값이 들어가면 어떤 실제 ID 도
    커서를 전진시키지 못한다 — 안 읽은 개수와 방 안 카운트가 영구히 0 이 된다.
    """
    real = "records/20260909/20260909T001442118Z-a-53032f.json"
    assert DIRTY > real
    assert not gitwire.is_record_id(DIRTY)
    assert gitwire.is_record_id(real)


def test_임시_ID_는_커서로_받지_않는다_거부한다(manager, fake_opener):
    room = manager.register(REPO)
    channel = _channel(fake_opener)
    manager.timeline(room.id)
    recs = [
        channel.inject({"kind": "msg", "v": 1, "author": "밥", "text": f"{i}"})
        for i in range(2)
    ]
    manager.mark_read(room.id, recs[0].id)

    # ⭐ 조용히 저장하지 않는다 — 던진다.
    with pytest.raises(reads_mod.InvalidCursor):
        manager.mark_read(room.id, DIRTY)

    view = manager.read_view(room.id)
    assert view.cursor == recs[0].id, "임시 ID 가 커서를 덮었다"
    assert view.unread == 1


def test_API_는_임시_ID_를_400_으로_거부한다(manager, fake_opener):
    app = create_app(manager.settings, manager, start=False)
    app.config.update(TESTING=True)
    client = app.test_client()
    room = manager.register(REPO)
    channel = _channel(fake_opener)
    manager.timeline(room.id)
    rec = channel.inject({"kind": "msg", "v": 1, "author": "밥", "text": "하나"})

    res = client.post(f"/api/rooms/{room.id}/reads", json={"cursor": DIRTY})
    assert res.status_code == 400, res.get_data(as_text=True)
    assert "봉투" in res.get_json()["error"]

    # 그리고 아무것도 저장되지 않았다 — 뱃지는 그대로 1 이다.
    assert client.get(f"/api/rooms/{room.id}/reads").get_json()["unread"] == 1
    # 실제 ID 는 그대로 받는다 (문을 너무 좁게 닫지 않았다).
    assert client.post(
        f"/api/rooms/{room.id}/reads", json={"cursor": rec.id}
    ).status_code == 200


def test_오염된_로컬_커서는_없음으로_취급되고_파일이_정정된다(manager, fake_opener, caplog):
    """⭐ 복구 경로 — 사람이 파일을 고치지 않아도 뱃지가 되살아난다."""
    room = manager.register(REPO)
    channel = _channel(fake_opener)
    manager.timeline(room.id)
    recs = [
        channel.inject({"kind": "msg", "v": 1, "author": "밥", "text": f"{i}"})
        for i in range(3)
    ]

    # 실제 방과 같은 상태를 만든다 — 오염된 값이 **이미 디스크에** 있다.
    tracker = manager.reads(room.id)
    cur = tracker._store.load()
    cur.watermark = DIRTY
    cur.started = True
    tracker._store.save(cur)

    with caplog.at_level("WARNING"):
        # 오염된 값이면 `rid <= cursor` 가 모든 레코드에 참이라 0 이 됐었다.
        assert manager.read_view(room.id).unread == 3
    assert "봉투 ID" in caplog.text, "조용히 넘어갔다 (로그가 없다)"

    # 방을 다시 열면 **파일 자체가** 정정된다 (경고가 영원히 반복되지 않게).
    manager.enter_room(room.id)
    assert tracker._store.load().watermark == ""
    assert tracker._store.load().started is True, "'지금까지 다 읽음'으로 재설정됐다"

    # 그리고 그 뒤 실제 ID 로 정상 전진한다 (단조 증가가 굳지 않았다).
    assert manager.mark_read(room.id, recs[1].id).cursor == recs[1].id
    assert manager.read_view(room.id).unread == 1


def test_남의_오염된_커서는_안_읽은_것으로_센다(manager, fake_opener):
    """⭐ 조용한 0 보다 과다가 낫다 — 과다는 사람이 알아채고 사라진다."""
    room = manager.register(REPO)
    channel = _channel(fake_opener)
    manager.timeline(room.id)
    channel.inject_state("bob@example.com", reads_mod.build_value(DIRTY, ["bob.host"]))

    view = manager.read_view(room.id)
    bob = [p for p in view.participants if p.key == "bob@example.com"]
    assert bob and bob[0].cursor == "", "오염된 커서를 그대로 들고 있다"


def test_내_발행이_원격의_오염을_덮어쓴다(manager, fake_opener):
    """오염이 내 파일에 올라가 있어도 다음 발행에 저절로 정정된다."""
    room = manager.register(REPO)
    channel = _channel(fake_opener)
    manager.timeline(room.id)
    rec = channel.inject({"kind": "msg", "v": 1, "author": "밥", "text": "하나"})
    channel.inject_state(
        gitwire.state_key(manager.person),
        reads_mod.build_value(DIRTY, [channel.sender]),
    )

    manager.mark_read(room.id, rec.id)
    stored = reads_mod.parse_state(channel.read_state(gitwire.state_key(manager.person)))
    assert stored is not None and stored.cursor == rec.id, "오염된 값이 살아남았다"


def test_형식_판정이_화면과_서버에서_같다():
    """⭐ 판정이 두 곳(파이썬 `gitwire.is_record_id` · JS `reads.js`)에 있으므로
    **같은 표본으로 양쪽을 검증한다.** 어긋나면 한쪽만 막히고 오염이 다시 샌다.
    """
    node_bin = shutil.which("node")
    if node_bin is None:
        pytest.skip("node 가 없다 — 화면 쪽 판정을 부를 수 없다")
    samples = [
        "records/20260909/20260909T001442118Z-yh.choi@interxlab.com.c587c2-53032f.json",
        "records/20260903/20260903T100000123Z-alice-abc123.json",
        "records/20260903/20260903T100000123Z-alice+tag-abc123.json",
        DIRTY,
        "~pending/000001",
        "",
        "?",
        "records/",
        "archive/20260903.jsonl",
        "participants/alice@x.io.json",
        "records/20260904/20260903T100000123Z-a-abc123.json",
        "records/20260903/20260903T100000123Z-a-abc123.txt",
    ]
    script = (
        "import { isMessageId } from "
        f"{json.dumps(_reads_js_url())};"
        "let raw='';process.stdin.on('data',(c)=>raw+=c).on('end',()=>{"
        "process.stdout.write(JSON.stringify(JSON.parse(raw).map(isMessageId)));});"
    )
    proc = subprocess.run(
        [node_bin, "--input-type=module", "-e", script],
        input=json.dumps(samples), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == [gitwire.is_record_id(x) for x in samples]


def _reads_js_url() -> str:
    path = (
        Path(reads_mod.__file__).resolve().parent / "static" / "js" / "reads.js"
    )
    return path.as_uri()


# --------------------------------- R-2. 참가자 집합 = 커서 파일 집합


def test_방을_열면_내_커서_파일이_생긴다(manager, fake_opener):
    room = manager.register(REPO)
    channel = _channel(fake_opener)
    assert channel.state_writes == 0            # 등록만으로는 쓰지 않는다

    manager.timeline(room.id)                   # = 방을 열었다
    assert channel.state_writes == 1
    assert channel.state_exists(manager.person)

    view = manager.read_view(room.id)
    assert [p.key for p in view.participants] == [gitwire.state_key(manager.person)]


def test_이미_있으면_덮어쓰지_않는다(manager, fake_opener):
    room = manager.register(REPO)
    channel = _channel(fake_opener)
    manager.timeline(room.id)
    before = channel.state_writes

    for _ in range(3):
        manager.timeline(room.id)
    assert channel.state_writes == before, "방을 다시 열 때마다 덮어썼다"


def test_남의_커서_파일이_참가자로_들어온다(manager, fake_opener):
    room = manager.register(REPO)
    channel = _channel(fake_opener)
    manager.timeline(room.id)
    channel.inject_state(
        "bob@example.com", reads_mod.build_value("", ["bob.host"])
    )
    view = manager.read_view(room.id)
    assert {p.key for p in view.participants} == {
        gitwire.state_key(manager.person), "bob@example.com"
    }


# ---------------------------------------------- R-1. 뱃지 (발행 0)


def test_뱃지는_발행_없이_동작한다(manager, fake_opener):
    room = manager.register(REPO)
    channel = _channel(fake_opener)
    manager.timeline(room.id)                   # 커서 파일 하나가 생긴다
    writes = channel.state_writes

    for i in range(3):
        channel.inject({"kind": "msg", "v": 1, "author": "밥", "text": f"안녕{i}"})

    payload = {r["id"]: r for r in manager.rooms_payload()}
    assert payload[room.id]["unread"] == 3
    # ⭐ 세는 동작은 **아무것도 발행하지 않는다.**
    assert channel.state_writes == writes


def test_내가_보낸_말은_뱃지를_늘리지_않는다(manager, fake_opener):
    room = manager.register(REPO)
    manager.timeline(room.id)
    manager.send(room.id, "내 말")
    payload = {r["id"]: r for r in manager.rooms_payload()}
    assert payload[room.id]["unread"] == 0


def test_방을_읽으면_뱃지가_줄어든다(manager, fake_opener):
    room = manager.register(REPO)
    channel = _channel(fake_opener)
    manager.timeline(room.id)
    recs = [
        channel.inject({"kind": "msg", "v": 1, "author": "밥", "text": f"{i}"})
        for i in range(4)
    ]
    assert manager.read_view(room.id).unread == 4
    assert manager.read_view(room.id).first_unread == recs[0].id

    view = manager.mark_read(room.id, recs[1].id)
    assert view.unread == 2
    assert view.first_unread == recs[2].id


def test_아직_안_붙은_방은_0_이고_클론을_시작하지_않는다(settings, fake_opener):
    """방 목록을 그리는 경로다 — 여기서 원격을 보거나 클론을 시작하면 안 된다."""
    mgr = RoomManager(
        settings, bus=EventBus(keepalive=0.05),
        notifier=RecordingNotifier(), opener=fake_opener,
    )
    try:
        room = mgr.register(REPO)
        assert mgr.unread(room.id) == 0
        assert not fake_opener.channels, "뱃지를 세려고 채널을 열었다"
    finally:
        mgr.stop()


# ------------------------------------------------- R-3. 단조 증가


def test_커서는_뒤로_가지_않는다(manager, fake_opener):
    room = manager.register(REPO)
    channel = _channel(fake_opener)
    manager.timeline(room.id)
    recs = [
        channel.inject({"kind": "msg", "v": 1, "author": "밥", "text": f"{i}"})
        for i in range(3)
    ]
    manager.mark_read(room.id, recs[2].id)
    assert manager.read_view(room.id).unread == 0

    # 뒤로 보내려 한다 — 안 간다 (카운트가 늘어나면 사람 눈에 고장으로 보인다).
    view = manager.mark_read(room.id, recs[0].id)
    assert view.cursor == recs[2].id
    assert view.unread == 0
    # 빈 값·헛값도 아무 일도 하지 않는다.
    assert manager.mark_read(room.id, "").cursor == recs[2].id


# ---------------------------------- R-4. 내가 보낸 말 = 내가 읽은 말


def test_보내면_내_커서가_그_자리까지_간다(manager, fake_opener):
    room = manager.register(REPO)
    manager.timeline(room.id)
    message = manager.send(room.id, "내 말")

    view = manager.read_view(room.id)
    assert view.cursor == message.id
    mine = [p for p in view.participants if p.key == view.me][0]
    assert mine.cursor == message.id
    # 봉투 sender 가 내 커서 파일에 적혀 있다 — 카운트 공식이 작성자를 가려낼
    # 유일한 근거다 (봉투에는 사람 키가 없다).
    assert _channel(fake_opener).sender in mine.senders


# -------------------------------------------- R-5. 유령 참가자


def test_안_움직이는_커서는_계속_세어진다(manager, fake_opener):
    """설치만 하고 앱을 안 켜는 사람 — 카운트가 영구히 ≥1 이다. **그게 사실이다.**"""
    room = manager.register(REPO)
    channel = _channel(fake_opener)
    manager.timeline(room.id)
    channel.inject_state("ghost@example.com", reads_mod.build_value("", []))

    message = manager.send(room.id, "아무도 안 읽는다")
    view = manager.read_view(room.id)
    ghost = [p for p in view.participants if p.key == "ghost@example.com"][0]
    assert ghost.cursor == ""
    # 그 사람이 분모에 남아 있다 (분모에서 빼는 짓을 하지 않는다).
    assert message.id > ghost.cursor


# ------------------------------------------ R-6. 폴 한 틱이 알아챈다


def test_남의_커서가_움직이면_틱이_알린다(manager, fake_opener):
    """⭐ 읽음은 **레코드가 아니다** — 구독 콜백이 한 번도 불리지 않는 변화다."""
    room = manager.register(REPO)
    channel = _channel(fake_opener)
    # 틱 훅은 **구독을 붙일 때** 달린다 (`_start_room`) — 그 배선까지 함께 본다.
    manager.start()
    manager.timeline(room.id)
    message = manager.send(room.id, "읽어라")
    sub = manager.bus.subscribe(room.id, client="tab")
    assert channel.cycle_hooks, "폴 틱 훅이 구독에 달리지 않았다"

    channel.tick()                              # 아무것도 안 바뀐 틱
    manager._reads_tick(room.id)                # (지문이 같으면 밀지 않는다)
    _drain(sub, "reads")                        # 최초 1회는 지문이 비어 있어 흐른다

    channel.tick()
    assert _drain(sub, "reads") == [], "바뀌지 않았는데 밀었다"

    # 상대가 읽었다 (레코드는 0건 늘었다).
    before = len(channel.records)
    channel.inject_state(
        "bob@example.com", reads_mod.build_value(message.id, ["bob.host"])
    )
    assert len(channel.records) == before
    channel.tick()

    got = _drain(sub, "reads")
    assert got, "남의 커서가 움직였는데 화면에 알리지 않았다"
    cursors = {p["key"]: p["cursor"] for p in got[-1]["participants"]}
    assert cursors["bob@example.com"] == message.id


# ------------------------------------------- R-7. 발행은 아웃박스에 얹힌다


def test_읽음_발행이_메시지_건수를_오염시키지_않는다(manager, fake_opener):
    room = manager.register(REPO)
    channel = _channel(fake_opener)
    manager.timeline(room.id)                   # 커서 파일 생성 → kick
    manager.outbox(room.id).wait_idle(10.0)
    recs = [
        channel.inject({"kind": "msg", "v": 1, "author": "밥", "text": f"{i}"})
        for i in range(2)
    ]
    manager.mark_read(room.id, recs[-1].id)
    state = manager.outbox_state(room.id)
    # 읽음은 메시지가 아니다 — "아직 못 나간 말 N건"에 세어지지 않는다.
    assert state.pending == 0
    manager.outbox(room.id).wait_idle(10.0)
    assert manager.outbox_state(room.id).state == "synced"


# ------------------------------------------------------------- API


def test_API_읽음_스냅샷과_전진(manager, fake_opener):
    app = create_app(manager.settings, manager, start=False)
    app.config.update(TESTING=True)
    client = app.test_client()
    room = manager.register(REPO)
    channel = _channel(fake_opener)
    manager.timeline(room.id)
    recs = [
        channel.inject({"kind": "msg", "v": 1, "author": "밥", "text": f"{i}"})
        for i in range(3)
    ]

    res = client.get(f"/api/rooms/{room.id}/reads")
    assert res.status_code == 200
    body = res.get_json()
    assert body["unread"] == 3
    assert body["first_unread"] == recs[0].id
    assert body["me"] == gitwire.state_key(manager.person)
    # ⭐ 응답에 메시지별 카운트가 없다 (파생값이다 — 화면이 계산한다).
    assert "counts" not in body
    for participant in body["participants"]:
        assert set(participant) == {
            "person", "key", "cursor", "senders", "updated_at"
        }

    res = client.post(f"/api/rooms/{room.id}/reads", json={"cursor": recs[1].id})
    assert res.status_code == 200
    assert res.get_json()["unread"] == 1

    # 목록 응답에도 뱃지가 실려 온다 (배관을 따로 만들지 않는다).
    rooms = client.get("/api/rooms").get_json()["rooms"]
    assert [r["unread"] for r in rooms if r["id"] == room.id] == [1]


def test_API_아직_안_붙은_방은_409(settings, fake_opener):
    mgr = RoomManager(
        settings, bus=EventBus(keepalive=0.05),
        notifier=RecordingNotifier(), opener=fake_opener,
    )
    app = create_app(settings, mgr, start=False)
    app.config.update(TESTING=True)
    client = app.test_client()
    try:
        room = mgr.register(REPO)
        res = client.get(f"/api/rooms/{room.id}/reads")
        assert res.status_code in (200, 409)     # 붙는 속도에 따라 갈린다
        if res.status_code == 409:
            assert "status" in res.get_json()
    finally:
        mgr.stop()


# ---------------------- ⭐ R-1(계속). 실제 채널로 git 호출을 센다


class CountingRunner(SubprocessGitRunner):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def run(self, args, **kwargs):
        self.calls.append(localrefs.subcommand(args) or "?")
        return super().run(args, **kwargs)

    def reset(self) -> None:
        self.calls.clear()


@pytest.fixture
def real_manager(tmp_path, bare_repo):
    """대역 없이 **실제 gitwire 채널**로 도는 매니저 (git 호출을 센다)."""
    runner = CountingRunner()
    settings = Settings(
        home=tmp_path / "chats",
        author="계수기",
        poll_interval=30.0,
        recent_limit=50,
        page_limit=50,
        notifications=False,
        extra={"channel_kwargs": {"runner": runner, "auto_rollup": False}},
    )
    mgr = ConnectedRoomManager(
        settings, bus=EventBus(keepalive=0.2), notifier=RecordingNotifier()
    )
    try:
        yield mgr, runner, str(bare_repo)
    finally:
        mgr.stop()


def test_뱃지를_되풀이해_세도_git_을_부르지_않는다(real_manager):
    """⭐ 유휴 폴링을 git 1개로 줄인 것을 되돌리지 않는다는 근거.

    방 목록은 상태가 바뀔 때마다 다시 그려지고 SSE 로도 밀린다. 그때마다 뱃지를
    세느라 git 이 뜨면 유휴 비용이 방 개수만큼 늘어난다.
    """
    mgr, runner, repo = real_manager
    room = mgr.register(repo)
    mgr.timeline(room.id)
    mgr.outbox(room.id).wait_idle(30.0)
    mgr.rooms_payload()                          # 캐시를 데운다 (첫 나열)
    runner.reset()

    for _ in range(5):
        payload = mgr.rooms_payload()
        assert payload[0]["unread"] == 0
    assert runner.calls == [], f"뱃지가 git 을 불렀다: {runner.calls}"

    # 읽음 스냅샷(방 안 카운트의 근거)도 되풀이 조회에 git 을 쓰지 않는다.
    mgr.read_view(room.id)
    runner.reset()
    for _ in range(5):
        mgr.read_view(room.id)
    assert runner.calls == [], f"읽음 스냅샷이 git 을 불렀다: {runner.calls}"


def test_실제_채널에서_커서_파일이_원격까지_간다(real_manager):
    """대역이 아니라 진짜 push — bare 레포를 직접 열어 확인한다."""
    mgr, _runner, repo = real_manager
    room = mgr.register(repo)
    mgr.timeline(room.id)                        # 방을 열었다 = 내 파일이 생긴다
    mgr.outbox(room.id).wait_idle(60.0)

    listing = subprocess.run(
        ["git", f"--git-dir={repo}", "ls-tree", "-r", "--name-only", "HEAD"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    want = gitwire.state_path(mgr.person)
    assert want in listing.splitlines(), (want, listing)

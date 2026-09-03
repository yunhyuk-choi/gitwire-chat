# gitwire-chat

**중앙 서버 없이 git 레포를 메시지 저장소로 쓰는 로컬-퍼스트 비동기 채팅.**

각 참가자가 자기 컴퓨터에서 이 앱을 띄우고, 공유 private git 레포 하나를 통해
대화한다. 서버 운영이 0이고, 네트워크는 egress(`pull`/`push`)만 쓴다.

```
   내 브라우저 ──HTTP/SSE──▶ 내 로컬 앱 ──push──▶ ┌──────────────┐ ◀──push── 상대 앱
   (localhost)                          ◀──pull── │ private repo │ ──pull──▶
                                                  └──────────────┘
```

전송 계층은 **[gitwire](../gitwire)** 가 전부 담당한다. 이 앱에는 git 명령이
한 줄도 없다. gitwire 는 레코드 `payload` 안을 해석하지 않으므로, "메시지"라는
개념은 오직 여기서 정의된다.

---

## 띄우기

```bash
# 1) 전송 계층 (아직 원격이 없으므로 로컬 경로로 참조한다)
pip install -e ../gitwire

# 2) 이 앱
pip install -e .

# 3) 실행
python -m gitwire_chat
# → http://127.0.0.1:8770
```

옵션:

| 옵션 | 뜻 |
|---|---|
| `--port` / `--host` | 바인드 위치 (기본 `127.0.0.1:8770`) |
| `--home DIR` | 로컬 상태 디렉토리 (기본은 아래 규칙) |
| `--author 이름` | 표시 이름 기본값 |
| `--poll-interval 초` | 폴 주기 (기본 15초) |
| `--no-notify` | OS 알림 끄기 |
| `--open` | 기동 후 브라우저 열기 |

### 방 만들기 / 합류하기

1. GitHub(또는 GitLab 등)에서 **빈 private 레포**를 하나 만든다.
2. 사이드바 `＋` → 레포 주소를 넣는다. 빈 레포면 gitwire 가 방 규약을 심는다.
3. 같이 대화할 사람을 그 레포의 **collaborator** 로 초대한다.
4. 상대도 자기 컴퓨터에서 이 앱을 띄우고 **같은 주소**를 넣으면 같은 방이 된다.

private 레포라면 토큰이 필요하다. 값은 **저장하지 않는다** — 환경변수 이름만
기억한다.

```bash
# Windows PowerShell
$env:GITWIRE_TOKEN = "ghp_..."
# macOS / Linux
export GITWIRE_TOKEN=ghp_...
```

방마다 다른 토큰을 쓰려면 방 등록 시 "토큰 환경변수" 칸에 다른 이름을 넣는다.

---

## 로컬 상태는 어디에 쌓이나

방 클론·커서·방 목록은 전부 한 디렉토리 아래에 모인다.

```
<chat_home>/rooms.json                        방 목록 (이 앱 소유)
<chat_home>/instance.txt                      이 설치의 전송 식별자
<chat_home>/channels/<slug>-<hash>/clone      방 하나의 git 클론
<chat_home>/channels/<slug>-<hash>/cursors    "어디까지 읽었나" (gitwire)
```

`<chat_home>` 은 이 순서로 정해진다:

1. `--home` 또는 `GITWIRE_CHAT_HOME`
2. **소스 체크아웃**으로 돌고 있으면 `<레포 루트>/chats`
3. 그 외(`pip install` 로 설치한 형태 — 앱 디렉토리가 없다)는 OS 데이터 디렉토리
   (`%LOCALAPPDATA%\gitwire-chat`, `~/Library/Application Support/gitwire-chat`,
   `$XDG_DATA_HOME/gitwire-chat`)

> ⚠️ `chats/` 가 **gitignore 되어 있다는 것이 이 배치의 전제다.** 무시되면 부모
> 레포의 `git status` 에 안 보이고 `git clean -fdx` 도
> `Would skip repository ...` 로 클론을 건너뛴다. 무시되지 **않으면** 즉시
> `embedded git repository` 경고와 함께 gitlink 로 스테이징된다.
> `.gitignore` 의 `chats/` 줄을 지우지 마라. 그리고 그 줄에 **인라인 주석을
> 붙이지 마라** — gitignore 에서 `#` 는 줄 첫 칸에서만 주석이라
> `chats/  # 설명` 은 패턴 전체를 무효화한다.

---

## 알림 · 자동 시작

앱 프로세스는 브라우저와 **무관하게** 방을 폴링한다. 그래서 탭이 닫혀 있거나
숨겨져 있어도 새 메시지를 알고, 그때 OS 알림을 띄운다
(Windows 토스트 / macOS `osascript` / Linux `notify-send`, 전부 실패하면 로그만).
탭이 그 방을 **보고 있으면** 알림을 띄우지 않는다.

로그인할 때 자동으로 띄우고 싶다면 (v1 은 자동 등록을 하지 않는다 — 안내만):

* **Windows** — `Win+R` → `shell:startup` → 아래 내용의 `.cmd` 파일을 넣는다.
  ```bat
  pythonw -m gitwire_chat --port 8770
  ```
* **macOS** — `~/Library/LaunchAgents/` 에 `.plist` 를 만들고
  `launchctl load` (`ProgramArguments` 에 `python3 -m gitwire_chat`).
* **Linux (systemd --user)** — `~/.config/systemd/user/gitwire-chat.service`
  를 만들고 `systemctl --user enable --now gitwire-chat`.

---

## 메시지는 어떻게 생겼나

gitwire 봉투와 이 앱의 payload 는 **역할이 겹치지 않게** 나눠 담는다.

| 무엇 | 어디 | 왜 |
|---|---|---|
| 메시지 ID·정렬 키 | 봉투 `id` | 이미 유일하고 정렬 가능하다. 새로 만들 이유가 없다 |
| 표시 시각 | 봉투 `ts` | git 호스트 기준 **공통 시계**. 로컬 시계보다 정확하다 |
| 발행 프로세스 | 봉투 `sender` | 전송 식별자(IP 에 가깝다). 표시용이 아니다 |
| 표시 이름 | payload `author` | 한 사람이 프로세스를 둘 쓸 수 있다 |
| 본문 | payload `text` | |
| 답장 대상 | payload `reply_to` | 봉투 `id` 를 가리킨다 |
| 종류·버전 | payload `kind`·`v` | append-only 라 나중에 고칠 수 없다 |

레포에 실제로 쌓이는 파일 한 건:

```json
{"gitwire":1,"id":"records/20260903/20260903T041210032Z-alice.laptop-f96bb4.json",
 "sender":"alice.laptop","ts":"2026-09-03T04:12:10.032Z",
 "payload":{"kind":"msg","v":1,"author":"앨리스","text":"안녕"}}
```

---

## 개발

```bash
python -m pytest -q
```

핵심 검증은 **앱 인스턴스 두 개가 로컬 bare 레포를 방으로 삼아 실제 git 으로
대화하는 것**이다(`tests/test_two_instances.py`). 대역이 아니라 진짜 git 이다 —
clone·push·커서 영속은 흉내내면 아무것도 증명하지 못한다. 나머지 테스트는
gitwire 채널을 주입 가능한 대역으로 갈아끼워 네트워크 없이 돈다.

프런트엔드는 브라우저 없이 검증한다: `node --check` 로 문법을 보고,
stub DOM(`tests/js/`) 위에서 `app.js` 를 실제로 구동해 **DOM 조작 횟수와 노드
동일성**을 센다 — "전체 리렌더가 없다"를 주장이 아니라 수로 확인한다.

파일은 UTF-8(BOM 없음)·LF 로 쓴다. 코드·주석·UI 문구는 한국어다.

---

## 정직한 한계

이 앱이 **못 하는 것**들이다. 쓰기 전에 반드시 읽어라.

* **실시간이 아니다.** 지연 = 폴 주기다. 기본 15초면 상대가 보낸 말이 최악
  15초 + pull 시간 뒤에 뜬다. 주기를 줄이면 그만큼 호스트에 요청이 늘어난다
  (rate limit 주의). 내가 보낸 말은 로컬 에코로 즉시 보이지만, 그건 화면일 뿐
  상대에게 도착한 시점과는 다르다.
* **진짜 push 알림이 없다.** 서버가 우리를 깨우지 않는다. 전부 폴링이라
  OS 알림도 폴 주기만큼 늦게 뜬다.
* **레포 접근권 = 참여권이다.** 세밀한 권한이 없다. 읽기만 주거나 특정 메시지만
  가리는 것이 불가능하고, **나중에 초대한 사람은 과거 대화를 전부 본다.**
  git 히스토리가 통째로 넘어가기 때문에 "이 시점부터만 보여주기"가 안 된다.
  민감한 대화에는 맞지 않는다.
* **메시지 삭제가 사실상 안 된다.** append-only 다. 지우려면 히스토리 재작성
  (`gitwire compact`)이 필요하고 그건 모든 참가자에게 파괴적이다.
* **순서는 근사치다.** 공통 시계로 ±1초 안쪽까지 맞추지만 전역 전순서를
  보장하지 않는다. 같은 초에 발행된 두 메시지의 상대 순서는 임의다.
* **레포가 계속 커진다.** 메시지마다 커밋이 쌓인다. 아주 활발한 방을 아주 오래
  굴리면 무거워진다.
* **여러 명이 초 단위로 쏟아붓는 용도가 아니다.** 동시 발행이 잦으면 push 경합
  재시도가 늘어난다. 정확성은 유지되지만 느려진다.
* **긴 대화는 최근 N건만 그린다.** 가상 스크롤이 없어서, "이전 불러오기"를
  아주 여러 번 누르면 브라우저가 무거워진다. 그리고 이전 페이지를 가져올 때
  서버가 레코드를 전부 읽는다(gitwire 에 역방향 페이징 API 가 없다) — 대화가
  수천 건을 넘으면 눈에 띄게 느려진다.
* **검색은 매번 전량 스캔이다.** 인덱스가 없다. 위와 같은 이유로 대화가 길수록
  느려진다.
* **이 앱은 인증하지 않는다.** `127.0.0.1` 에만 바인드한다는 전제다. `--host`
  로 외부에 열면 그 포트에 닿는 누구나 당신 이름으로 말할 수 있다.

"""자격증명 **탐색** — 값은 이 모듈 밖으로 나가지 않는다.

왜 이 파일이 생겼나
------------------
기반(gitwire)은 자격증명을 **기동 시 한 번 읽어 메모리에 들고** 쓴다. 못 찾으면
git 이 대화형으로 물어보는데, 이 앱은 ``pythonw`` 로 **창 없이** 돈다
(`winspawn.py` 도크) — 물어볼 곳이 없어 **조용히 멈춘다.** 조용한 실패는 이
프로젝트에서 금지다. 그러니 "지금 자격증명이 있나, 어디 있나"를 앱이 **먼저 알고
화면에 말해야** 한다. 그것이 이 모듈의 존재 이유다.

탐색 순서 (사용자가 정한 순서 — 위에서 처음 걸리는 것이 답)
-----------------------------------------------------------
1. **환경변수** (``GITWIRE_TOKEN``, 방마다 다른 이름 가능). 앱이 이미 읽는
   곳이다 (`app._token_for` · `rooms.RoomManager._credential`).
2. **``git credential fill``** — 대부분 여기서 나온다. OS 자격증명 저장소
   (Windows 자격증명 관리자 / macOS 키체인 / ``credential.helper``) 를 git 이
   자기 규약으로 뒤져 준다. 우리가 저장소별 API 를 알 필요가 없다는 것이 이
   경로를 고른 이유다. 실측 435ms (helper=manager, 값 있음).
3. **레포 주소에 박힌 것** (``https://사용자:토큰@호스트/…``). 권하지 않는
   형태지만(``.git/config`` 에 평문으로 남는다 — `gitwire.credentials` 도크)
   실제로 그렇게 쓰는 사람이 있고, 그 사람에게 "없다"고 말하면 거짓말이다.
4. 없음 → 호출자가 사용자에게 그 사실을 말한다 (조용히 멈추지 않는다).

⚠️ 값을 절대 싣지 않는다 — 이 모듈의 반환값에 토큰이 없다
---------------------------------------------------------
`discover()` 는 **어디서 찾았는지**만 돌려준다. 값은 읽는 즉시 버린다. 그래서
"화면·로그·API 응답에 토큰이 새지 않는다"가 규율이 아니라 **타입에서** 나온다.

``git credential fill`` 이 함께 주는 ``username`` 도 싣지 않는다. 보통은 비밀이
아니지만, GitHub HTTPS 에는 **토큰을 username 자리에 넣는 형태**가 실제로 있어서
(``username=<PAT>`` / ``password=x-oauth-basic``) 그 필드를 화면에 실으면 어떤
사용자에게는 그것이 곧 토큰 유출이 된다. 출처만 말하면 그 위험이 존재하지 않는다.
"""

from __future__ import annotations

import logging
import os
import subprocess
import urllib.parse
from dataclasses import dataclass
from typing import Callable

from . import winspawn

log = logging.getLogger(__name__)

#: 기본 환경변수 이름 (기반·앱이 같이 쓰는 이름).
DEFAULT_ENV = "GITWIRE_TOKEN"

#: ``git credential`` 호출 상한(초). 사람이 버튼을 누르고 기다리는 경로라 짧게
#: 잡는다. helper 가 대화형 창을 띄우려다 막히는 경우까지 여기서 끝난다.
TIMEOUT = 15.0

# ------------------------------------------------------------------ 출처 ID

ENV = "env"            # 환경변수
HELPER = "helper"      # OS 자격증명 저장소 (git credential helper)
URL = "url"            # 레포 주소에 박힘
NONE = "none"          # 없음

# --------------------------------------------------------------- 탐색 결과


@dataclass(frozen=True)
class Discovery:
    """"자격증명이 어디 있나"의 답. ⚠️ **값이 들어 있지 않다** (모듈 도크)."""

    source: str = NONE          # ENV | HELPER | URL | NONE
    env_name: str = DEFAULT_ENV
    host: str = ""
    #: 탐색이 왜 거기서 멈췄는지 (helper 가 없거나 막힌 경우 등). 사람이 읽는 한
    #: 줄이고, git 의 stderr **요약**이다 — 값이 실릴 수 있는 stdout 은 쓰지 않는다.
    detail: str = ""

    @property
    def found(self) -> bool:
        return self.source != NONE

    @property
    def label(self) -> str:
        """화면에 그대로 띄우는 한 줄. **출처만** 말한다."""
        if self.source == ENV:
            return f"환경변수 {self.env_name} 에서 찾았습니다"
        if self.source == HELPER:
            return "OS 자격증명 저장소에서 찾았습니다 (git credential)"
        if self.source == URL:
            return "레포 주소에 토큰이 박혀 있습니다"
        return "토큰을 찾지 못했습니다"

    def to_json(self) -> dict:
        return {
            "source": self.source,
            "found": self.found,
            "label": self.label,
            "env_name": self.env_name,
            "host": self.host,
            "detail": self.detail,
        }


# ------------------------------------------------------------- git 호출 한 겹


def _run_git(args: list[str], stdin: str = "") -> tuple[int, str, str]:
    """``git`` 을 한 번 부른다. 반환 ``(종료코드, stdout, stderr)``.

    ⚠️ **대화형으로 떨어지지 않게** 세 겹으로 막는다. 창 없이 도는 앱에서
    프롬프트가 뜨면 그 프로세스는 영원히 멈춘다:

    1. ``GIT_TERMINAL_PROMPT=0`` — git 이 터미널로 묻지 않고 즉시 실패한다
       (실측: helper 가 없으면 56ms 에 ``fatal: … terminal prompts disabled``).
    2. ``GCM_INTERACTIVE=never`` — git-credential-manager 의 **GUI 창**을 막는다
       (1번은 터미널만 막는다 — GUI helper 는 그 밑으로 새 나간다).
    3. ``GIT_ASKPASS``·``SSH_ASKPASS`` 를 걷어낸다 — 우리 부모 환경에 남아 있는
       askpass 헬퍼가 상속되면 그것이 대신 답해 버려 탐색 결과가 거짓이 된다
       (기반이 자기 git 호출에 심는 헬퍼가 바로 그것이다 —
       `gitwire.credentials._ensure_askpass`).

    그리고 창을 띄우지 않는다 (`winspawn.quiet_kwargs`).
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env.pop("GIT_ASKPASS", None)
    env.pop("SSH_ASKPASS", None)
    try:
        done = subprocess.run(
            ["git", *args],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=TIMEOUT,
            **winspawn.quiet_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return 1, "", f"git {' '.join(args)} 이(가) {TIMEOUT:.0f}초 안에 끝나지 않았다"
    except OSError as exc:
        return 1, "", f"git 을 실행할 수 없다: {exc}"
    return done.returncode, done.stdout or "", done.stderr or ""


def _one_line(text: str) -> str:
    """stderr 에서 사람이 볼 한 줄만. (`rooms._reason` 과 같은 성격.)"""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    fatal = [line for line in lines if line.lower().startswith("fatal:")]
    picked = fatal[0][len("fatal:"):].strip() if fatal else lines[-1]
    return picked[:200]


def _has_password(stdout: str) -> bool:
    """``git credential fill`` 출력에 **비지 않은** ``password`` 가 있나.

    ⚠️ 값을 돌려주지 않는다. ``bool`` 하나가 이 함수의 전부다 — 호출자가 값을
    붙잡을 수 있는 길을 아예 만들지 않는다.
    """
    for line in (stdout or "").splitlines():
        if line.startswith("password=") and line[len("password="):].strip():
            return True
    return False


# ------------------------------------------------------------------ 탐색


def host_of(url: str, fallback: str = "github.com") -> str:
    """레포 주소에서 호스트만. 못 읽으면 ``fallback``."""
    raw = (url or "").strip()
    if not raw:
        return fallback
    parts = urllib.parse.urlsplit(raw if "://" in raw else f"https://{raw}")
    return (parts.hostname or fallback).lower()


def url_has_token(url: str) -> bool:
    """주소에 **완성된** 자격증명이 박혀 있나 (``…//사용자:비밀@호스트/…``).

    ``password`` 가 있어야 참이다. ``https://<토큰>@host/…`` 처럼 한 쪽만 있는
    형태는 git 이 그것을 *username* 으로 보고 **비밀번호를 또 묻는다** — 즉
    자격증명이 완성되지 않았다. 그걸 "있다"로 세면 사용자에게 거짓말이 된다.
    """
    raw = (url or "").strip()
    if "://" not in raw:
        return False                      # 로컬 경로·SSH 축약형엔 박을 자리가 없다
    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError:
        return False
    return bool(parts.password)


def discover(
    *,
    env_name: str = "",
    repo_url: str = "",
    host: str = "",
    runner: Callable[[list[str], str], tuple[int, str, str]] | None = None,
) -> Discovery:
    """자격증명이 **어디 있나**. 값은 돌려주지 않는다 (모듈 도크).

    ``runner`` 를 주면 git 없이도 테스트할 수 있다 (이 앱의 주입 관용구 —
    `rooms.RoomManager(opener=…)` · `api.createApi(fetchImpl)` 와 같다).
    """
    run = runner or _run_git
    var = (env_name or "").strip() or DEFAULT_ENV
    where = (host or "").strip().lower() or host_of(repo_url)

    # 1. 환경변수 — 앱이 이미 읽는 곳.
    if os.environ.get(var, "").strip():
        return Discovery(ENV, env_name=var, host=where)

    # 2. OS 자격증명 저장소. git 이 자기 규약으로 뒤진다.
    detail = ""
    rc, out, err = run(
        ["credential", "fill"], f"protocol=https\nhost={where}\n\n"
    )
    if rc == 0 and _has_password(out):
        return Discovery(HELPER, env_name=var, host=where)
    detail = _one_line(err)

    # 3. 주소에 박힌 것.
    if url_has_token(repo_url):
        return Discovery(URL, env_name=var, host=where)

    # 4. 없음. 왜 거기서 멈췄는지는 남긴다 (조용한 실패 금지).
    return Discovery(NONE, env_name=var, host=where, detail=detail)

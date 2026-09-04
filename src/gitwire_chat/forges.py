"""forge 어댑터 — "레포를 새로 만들기"를 **아는 만큼만** 거든다.

왜 어댑터인가
------------
전송 계층(gitwire)은 GitHub·GitLab·사내 git·bare·로컬 경로를 다 지원하는 **forge
중립** 설계다. 그러니 이 앱도 GitHub 전용이 되면 안 된다. 대신 호스트별로 아는
만큼만 거들고, 모르면 **물러나서 주소 입력만 받는다**(그게 원래 동작이므로 후퇴가
아니다).

호스트별로 어디까지 거드나
--------------------------
| 호스트 | 거드는 정도 |
|---|---|
| github.com | 폼이 **미리 채워진** 링크. 토큰이 있으면 **앱 안에서 바로 생성** |
| gitlab.com | 새 프로젝트 링크만 (프리필 파라미터가 문서화돼 있지 않다) |
| 그 밖 | 거들지 않는다 — 주소를 직접 넣는다 |

GitHub 프리필 파라미터 — 확인한 근거
------------------------------------
GitHub 체인지로그 "Pre-fill form fields when creating a new repo"(2023-04-27)가
`https://github.com/new` 의 쿼리 파라미터를 문서화한다::

    https://github.com/new?owner=octocat&name=new-boilerplate
        &description=A%20new%20boilerplate%20repository&visibility=private
        &template_owner=actions&template_name=boilerplate

즉 `owner`·`name`·`description`·`visibility`(+템플릿용 2개)가 공식 파라미터다.
로그아웃 상태로 그 URL 을 요청하면 302 로 로그인으로 가는데, `return_to` 에
**쿼리가 그대로 보존**되므로 로그인 뒤에도 프리필이 유지된다(실측).

⚠️ 토큰 값은 이 모듈 밖으로 절대 나가지 않는다 — 반환값·예외 메시지·로그 어디에도
싣지 않는다. 저장도 하지 않는다(호출자가 환경변수에서 읽어 넘긴다).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

#: 호스트 → 종류
GITHUB_HOSTS = {"github.com", "www.github.com"}
GITLAB_HOSTS = {"gitlab.com", "www.gitlab.com"}

#: GitHub 레포 이름에 쓸 수 있는 문자 (그 밖은 '-' 로 바꾼다)
_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

GITHUB_API = "https://api.github.com"

#: HTTP 타임아웃(초). 레포 생성은 사람이 버튼을 누르고 기다리는 동작이라 짧게 잡는다.
TIMEOUT = 15.0


class ForgeError(Exception):
    """레포 생성 실패. 메시지는 **그대로 사용자에게 보여줘도 되는** 내용이다."""

    def __init__(self, message: str, *, code: str = "error", hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint


@dataclass(frozen=True)
class Forge:
    """한 호스트에 대해 우리가 할 수 있는 일."""

    kind: str            # "github" | "gitlab" | "unknown"
    host: str
    label: str
    can_prefill: bool    # 폼이 채워진 링크를 만들 수 있나
    can_api: bool        # 토큰으로 앱 안에서 만들 수 있나 (토큰 유무와는 별개)


def detect(host: str = "") -> Forge:
    h = (host or "").strip().lower()
    h = h.split("@")[-1].split("/")[0]
    if h in GITHUB_HOSTS:
        return Forge("github", "github.com", "GitHub", True, True)
    if h in GITLAB_HOSTS:
        return Forge("gitlab", "gitlab.com", "GitLab", False, False)
    return Forge("unknown", h, h or "직접 입력", False, False)


def repo_slug(name: str, fallback: str = "chat-room") -> str:
    """방 이름 → 레포 이름 후보.

    한국어 방 이름(예: "우리 방")은 남는 글자가 없을 수 있다. 그럴 때 억지로
    음역하지 않고 **기본값을 주고 사용자가 고치게** 한다 — 레포 이름은 영구적이라
    앱이 임의로 정하는 것보다 사람이 확인하는 편이 낫다.
    """
    slug = _SLUG_UNSAFE.sub("-", (name or "").strip()).strip("-._")
    slug = re.sub(r"-{2,}", "-", slug)[:100]
    return slug or fallback


def new_repo_link(
    kind: str, name: str, *, owner: str = "", description: str = "", private: bool = True
) -> str:
    """폼이 미리 채워진 '새 레포' 링크. 만들 수 없으면 빈 문자열."""
    if kind == "github":
        params = {"name": name, "visibility": "private" if private else "public"}
        if owner:
            params["owner"] = owner
        if description:
            params["description"] = description
        return "https://github.com/new?" + urllib.parse.urlencode(params)
    if kind == "gitlab":
        # GitLab 은 프리필 파라미터가 문서화돼 있지 않다 — 링크만 준다.
        return "https://gitlab.com/projects/new#blank_project"
    return ""


def clone_url(kind: str, owner: str, name: str) -> str:
    """소유자·이름을 알면 주소는 유도된다 — 사용자가 URL 을 옮겨 적지 않아도 된다."""
    if not owner or not name:
        return ""
    if kind == "github":
        return f"https://github.com/{owner}/{name}.git"
    if kind == "gitlab":
        return f"https://gitlab.com/{owner}/{name}.git"
    return ""


# ------------------------------------------------------------------ GitHub API


def _github_request(token: str, path: str, payload: dict | None = None) -> dict:
    """GitHub REST 호출. 토큰은 **헤더에만** 실린다(URL·로그에 남지 않는다)."""
    url = path if path.startswith("http") else GITHUB_API + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", "gitwire-chat")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as res:
            return json.loads(res.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        message, code, hint = _http_error(exc)
        raise ForgeError(message, code=code, hint=hint) from None
    except urllib.error.URLError as exc:
        raise ForgeError(
            f"GitHub 에 연결하지 못했다: {exc.reason}",
            code="network",
            hint="네트워크·프록시 설정을 확인하라.",
        ) from None


def _http_error(exc: urllib.error.HTTPError) -> tuple:
    """GitHub 응답을 사람이 읽는 사유로. 토큰 값은 어디에도 넣지 않는다."""
    try:
        body = json.loads(exc.read().decode("utf-8") or "{}")
    except Exception:  # noqa: BLE001
        body = {}
    detail = str(body.get("message") or "").strip()
    for item in body.get("errors") or []:
        if isinstance(item, dict) and item.get("message"):
            detail = f"{detail} — {item['message']}" if detail else str(item["message"])
    if exc.code == 401:
        return (
            f"GitHub 인증에 실패했다 ({detail or '401'})",
            "auth",
            "토큰이 만료됐거나 값이 잘못됐다. 환경변수를 다시 설정하고 앱을 재시작하라.",
        )
    if exc.code == 403:
        return (
            f"GitHub 이 거부했다 ({detail or '403'})",
            "scope",
            "토큰 권한(스코프)에 레포 생성이 포함돼야 한다 — classic 은 `repo`, "
            "fine-grained 는 Administration: Read and write.",
        )
    if exc.code == 404:
        return (
            f"대상을 찾을 수 없다 ({detail or '404'})",
            "notfound",
            "소유자(조직) 이름이 맞는지, 그 조직에 만들 권한이 있는지 확인하라.",
        )
    if exc.code == 422:
        return (
            f"만들 수 없는 이름이다 ({detail or '422'})",
            "name",
            "같은 이름의 레포가 이미 있거나 규칙에 맞지 않는다. 다른 이름을 넣어라.",
        )
    return (f"GitHub 오류 {exc.code} ({detail})", "error", "")


def github_login(token: str) -> str:
    """토큰 주인의 로그인 이름. **무엇이 만들어지는지 미리 보여주기 위해** 쓴다."""
    return str(_github_request(token, "/user").get("login") or "")


def create_github_repo(
    token: str, name: str, *, owner: str = "", description: str = "",
    private: bool = True,
) -> dict:
    """레포를 만든다. ⚠️ 계정을 바꾸는 외부 동작이라 **호출자가 사람의 확인을 받는다.**

    반환: `{"full_name", "clone_url", "html_url", "private"}`
    """
    name = repo_slug(name, fallback="")
    if not name:
        raise ForgeError("레포 이름이 비어 있다", code="name")
    payload = {"name": name, "private": bool(private), "auto_init": False}
    if description:
        payload["description"] = description
    me = github_login(token)
    path = "/user/repos" if not owner or owner == me else f"/orgs/{owner}/repos"
    data = _github_request(token, path, payload)
    return {
        "full_name": data.get("full_name") or f"{owner or me}/{name}",
        "clone_url": data.get("clone_url") or clone_url("github", owner or me, name),
        "html_url": data.get("html_url") or "",
        "private": bool(data.get("private", private)),
    }

"""forge 어댑터 — 링크·주소 유도·에러 사유. 네트워크는 대역으로 막는다.

여기서 고정하려는 계약:

* **forge 중립** — 아는 호스트만 거들고, 모르면 물러난다(주소 직접 입력).
* GitHub 프리필 파라미터는 **문서화된 이름**(`name`·`owner`·`visibility`·
  `description`)을 쓴다. 기본은 **비공개**다.
* 실패 사유는 사람이 다음 행동을 알 수 있게 **구분**된다.
* ⚠️ 토큰 **값**은 반환값·예외 어디에도 나타나지 않는다.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse

import pytest

from gitwire_chat import forges

TOKEN = "ghp_ThisIsASecretTokenValue1234567890"


# ----------------------------------------------------------------- 호스트


def test_아는_호스트만_거든다():
    github = forges.detect("github.com")
    assert (github.kind, github.can_prefill, github.can_api) == ("github", True, True)

    gitlab = forges.detect("gitlab.com")
    assert gitlab.kind == "gitlab"
    assert gitlab.can_prefill is False and gitlab.can_api is False

    # 사내 git·모르는 호스트는 거들지 않는다 (후퇴가 아니라 원래 동작이다).
    other = forges.detect("git.example.internal")
    assert (other.kind, other.can_prefill, other.can_api) == ("unknown", False, False)


def test_레포_이름_후보는_파일명처럼_안전하게():
    assert forges.repo_slug("Team Chat 2026!") == "Team-Chat-2026"
    assert forges.repo_slug("a/b\\c") == "a-b-c"
    # 한국어만 있으면 남는 글자가 없다 → 억지로 음역하지 않고 기본값 + 사용자 수정
    assert forges.repo_slug("우리 방") == "chat-room"
    assert forges.repo_slug("") == "chat-room"
    assert len(forges.repo_slug("x" * 200)) <= 100


# ------------------------------------------------------------------ 링크


def test_github_링크는_문서화된_파라미터로_비공개_기본():
    link = forges.new_repo_link(
        "github", "our-room", owner="yunhyuk-choi", description="우리 방"
    )
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)
    assert urllib.parse.urlsplit(link).path == "/new"
    assert query["name"] == ["our-room"]
    assert query["owner"] == ["yunhyuk-choi"]
    assert query["visibility"] == ["private"]      # 기본이 비공개다
    assert query["description"] == ["우리 방"]


def test_gitlab_은_링크만_모르는_곳은_아무것도():
    assert forges.new_repo_link("gitlab", "our-room").startswith(
        "https://gitlab.com/projects/new"
    )
    assert forges.new_repo_link("unknown", "our-room") == ""


def test_주소는_소유자와_이름에서_유도된다():
    """이게 있어야 사용자가 URL 을 손으로 옮겨 적지 않는다."""
    assert forges.clone_url("github", "me", "our-room") == (
        "https://github.com/me/our-room.git"
    )
    assert forges.clone_url("gitlab", "me", "our-room") == (
        "https://gitlab.com/me/our-room.git"
    )
    assert forges.clone_url("unknown", "me", "our-room") == ""
    assert forges.clone_url("github", "", "our-room") == ""


# ------------------------------------------------------------- GitHub API


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _http_error(code: int, body: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.github.com/user/repos", code, "err", {},
        io.BytesIO(json.dumps(body).encode("utf-8")),
    )


def test_레포_생성은_비공개로_만들고_주소를_돌려준다(monkeypatch):
    seen = []

    def fake_urlopen(request, timeout=None):
        seen.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "auth": request.get_header("Authorization"),
                "body": json.loads(request.data) if request.data else None,
            }
        )
        if request.full_url.endswith("/user"):
            return FakeResponse(json.dumps({"login": "me"}).encode("utf-8"))
        return FakeResponse(
            json.dumps(
                {
                    "full_name": "me/our-room",
                    "clone_url": "https://github.com/me/our-room.git",
                    "html_url": "https://github.com/me/our-room",
                    "private": True,
                }
            ).encode("utf-8")
        )

    monkeypatch.setattr(forges.urllib.request, "urlopen", fake_urlopen)
    made = forges.create_github_repo(TOKEN, "our-room", description="우리 방")

    assert made["clone_url"] == "https://github.com/me/our-room.git"
    assert made["private"] is True
    create = seen[-1]
    assert create["url"].endswith("/user/repos") and create["method"] == "POST"
    assert create["body"]["private"] is True      # 기본이 비공개다
    assert create["body"]["name"] == "our-room"
    # 토큰은 **헤더에만** 실린다 (URL·본문에 없다)
    assert TOKEN not in create["url"]
    assert TOKEN not in json.dumps(create["body"])


def test_조직_소유자면_조직_경로로_만든다(monkeypatch):
    seen = []

    def fake_urlopen(request, timeout=None):
        seen.append(request.full_url)
        if request.full_url.endswith("/user"):
            return FakeResponse(json.dumps({"login": "me"}).encode("utf-8"))
        return FakeResponse(json.dumps({"clone_url": "x", "private": True}).encode())

    monkeypatch.setattr(forges.urllib.request, "urlopen", fake_urlopen)
    forges.create_github_repo(TOKEN, "our-room", owner="acme-corp")
    assert seen[-1].endswith("/orgs/acme-corp/repos")


@pytest.mark.parametrize(
    "code, body, expected",
    [
        (401, {"message": "Bad credentials"}, "auth"),
        (403, {"message": "Resource not accessible"}, "scope"),
        (404, {"message": "Not Found"}, "notfound"),
        (422, {"message": "Repository creation failed.",
               "errors": [{"message": "name already exists on this account"}]}, "name"),
    ],
)
def test_실패는_사유별로_구분되고_다음_행동을_알려준다(monkeypatch, code, body, expected):
    def boom(request, timeout=None):
        raise _http_error(code, body)

    monkeypatch.setattr(forges.urllib.request, "urlopen", boom)
    with pytest.raises(forges.ForgeError) as caught:
        forges.create_github_repo(TOKEN, "our-room")
    error = caught.value
    assert error.code == expected
    assert str(error)                       # 사람이 읽는 사유가 있다
    assert TOKEN not in str(error)          # ⚠️ 토큰이 새지 않는다
    assert TOKEN not in error.hint
    if expected in ("auth", "scope", "name", "notfound"):
        assert error.hint, "다음에 무엇을 해야 하는지가 비었다"


def test_네트워크_실패도_사유가_있다(monkeypatch):
    def boom(request, timeout=None):
        raise urllib.error.URLError("getaddrinfo failed")

    monkeypatch.setattr(forges.urllib.request, "urlopen", boom)
    with pytest.raises(forges.ForgeError) as caught:
        forges.github_login(TOKEN)
    assert caught.value.code == "network"
    assert TOKEN not in str(caught.value)

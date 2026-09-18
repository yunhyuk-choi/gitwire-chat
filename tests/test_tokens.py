"""자격증명 탐색·발급 거들기 — 순서 · **값 누출 0** · 저장 왕복.

여기서 고정하려는 계약:

* **탐색 4단계가 그 순서대로** 동작한다 (env → OS 저장소 → 주소 → 없음).
* ⚠️ **값이 밖으로 나가지 않는다** — `Discovery` 에 값을 담을 필드가 아예
  없고, 토큰은 git 의 **stdin** 으로만 간다 (인자·환경변수에 없다 = ``ps`` 에
  안 보인다).
* 탐색은 **실제 git** 왕복으로도 확인한다 — 단, 사용자의 진짜 자격증명 저장소를
  건드리지 않는다 (격리된 ``GIT_CONFIG_GLOBAL`` + ``store --file=<tmp>``).
"""

from __future__ import annotations

import dataclasses
import subprocess
import urllib.parse

import pytest

from gitwire_chat import tokens

#: 이 파일에서 쓰는 가짜 토큰. 어떤 출력에도 나타나면 안 되는 문자열이다.
SECRET = "ghp_ThisIsASecretTokenValue1234567890"


class Runner:
    """``git`` 대역 — **무엇을 어떻게 불렀나**를 그대로 기록한다."""

    def __init__(self, replies=None):
        self.calls: list[tuple[list[str], str]] = []
        self.replies = list(replies or [])

    def __call__(self, args, stdin=""):
        self.calls.append((list(args), stdin))
        if self.replies:
            return self.replies.pop(0)
        return 1, "", "fatal: could not read Username: terminal prompts disabled"


FOUND = (0, f"protocol=https\nhost=github.com\nusername=me\npassword={SECRET}\n", "")
EMPTY = (1, "", "fatal: could not read Username for 'https://github.com': "
              "terminal prompts disabled")


# ------------------------------------------------------------ 탐색 4단계


def test_1단계_환경변수에서_찾으면_git_을_아예_부르지_않는다(monkeypatch):
    monkeypatch.setenv("GITWIRE_TOKEN", SECRET)
    run = Runner()
    got = tokens.discover(runner=run)
    assert got.source == tokens.ENV and got.found is True
    assert "환경변수 GITWIRE_TOKEN" in got.label
    # ⭐ 있는 것을 또 찾지 않는다 — 435ms 짜리 왕복이 헛돌지 않게.
    assert run.calls == []


def test_1단계는_방마다_다른_이름도_본다(monkeypatch):
    monkeypatch.delenv("GITWIRE_TOKEN", raising=False)
    monkeypatch.setenv("TEAM_TOKEN", SECRET)
    got = tokens.discover(env_name="TEAM_TOKEN", runner=Runner())
    assert got.source == tokens.ENV and got.env_name == "TEAM_TOKEN"
    assert "TEAM_TOKEN" in got.label


def test_2단계_OS_자격증명_저장소에서_나온다(monkeypatch):
    monkeypatch.delenv("GITWIRE_TOKEN", raising=False)
    run = Runner([FOUND])
    got = tokens.discover(host="github.com", runner=run)
    assert got.source == tokens.HELPER and got.found is True
    assert "OS 자격증명 저장소" in got.label
    # git credential 규약대로 물어봤나 (호스트를 제대로 넘겼나)
    args, stdin = run.calls[0]
    assert args == ["credential", "fill"]
    assert "protocol=https" in stdin and "host=github.com" in stdin


def test_2단계에서_password_가_비면_찾은_것이_아니다(monkeypatch):
    """helper 가 username 만 주는 경우 — 자격증명이 완성되지 않았다."""
    monkeypatch.delenv("GITWIRE_TOKEN", raising=False)
    half = (0, "protocol=https\nhost=github.com\nusername=me\npassword=\n", "")
    got = tokens.discover(runner=Runner([half]))
    assert got.source == tokens.NONE


def test_3단계_주소에_박힌_것도_찾은_것이다(monkeypatch):
    monkeypatch.delenv("GITWIRE_TOKEN", raising=False)
    got = tokens.discover(
        repo_url=f"https://me:{SECRET}@github.com/me/room.git", runner=Runner([EMPTY])
    )
    assert got.source == tokens.URL and got.found is True
    assert got.host == "github.com"          # 호스트는 주소에서 유도된다


def test_3단계는_비밀번호가_없는_형태를_있다고_하지_않는다(monkeypatch):
    """``https://<토큰>@host/…`` 는 git 이 그걸 username 으로 보고 **또 묻는다.**"""
    monkeypatch.delenv("GITWIRE_TOKEN", raising=False)
    got = tokens.discover(
        repo_url=f"https://{SECRET}@github.com/me/room.git", runner=Runner([EMPTY])
    )
    assert got.source == tokens.NONE


def test_4단계_전부_없으면_왜_멈췄는지가_남는다(monkeypatch):
    monkeypatch.delenv("GITWIRE_TOKEN", raising=False)
    got = tokens.discover(repo_url="https://github.com/me/room.git", runner=Runner([EMPTY]))
    assert got.source == tokens.NONE and got.found is False
    assert got.label == "토큰을 찾지 못했습니다"
    # 조용한 실패 금지 — git 이 준 사유가 한 줄로 남는다 (stderr 만, stdout 은 안 쓴다)
    assert "terminal prompts disabled" in got.detail


def test_탐색_순서는_env_가_저장소보다_먼저다(monkeypatch):
    """둘 다 있으면 env 가 이긴다 — 사용자가 정한 순서."""
    monkeypatch.setenv("GITWIRE_TOKEN", SECRET)
    run = Runner([FOUND])
    assert tokens.discover(runner=run).source == tokens.ENV
    assert run.calls == []


# ------------------------------------------------------- ⚠️ 값 누출 0


def test_탐색_결과에는_값을_담을_필드가_아예_없다(monkeypatch):
    """규율이 아니라 **타입**으로 막는다."""
    names = {f.name for f in dataclasses.fields(tokens.Discovery)}
    assert names == {"source", "env_name", "host", "detail"}
    assert "token" not in names and "password" not in names


@pytest.mark.parametrize("case", ["env", "helper", "url"])
def test_어느_경로로_찾아도_값이_결과에_없다(monkeypatch, case):
    monkeypatch.delenv("GITWIRE_TOKEN", raising=False)
    url = ""
    run = Runner([EMPTY])
    if case == "env":
        monkeypatch.setenv("GITWIRE_TOKEN", SECRET)
        run = Runner()
    elif case == "helper":
        run = Runner([FOUND])
    else:
        url = f"https://me:{SECRET}@github.com/me/room.git"
    got = tokens.discover(repo_url=url, runner=run)
    assert got.found is True, case
    # 결과 객체·라벨·JSON 어디에도 값이 없다
    assert SECRET not in repr(got)
    assert SECRET not in got.label
    assert SECRET not in str(got.to_json())


def test_helper_가_username_에_토큰을_담는_형태도_새지_않는다(monkeypatch):
    """GitHub 에는 ``username=<PAT>`` / ``password=x-oauth-basic`` 형태가 있다.

    그래서 `Discovery` 는 **username 을 싣지 않는다** — 그 필드를 화면에
    실으면 그런 사용자에게는 그것이 곧 토큰 유출이다.
    """
    monkeypatch.delenv("GITWIRE_TOKEN", raising=False)
    weird = (0, f"protocol=https\nhost=github.com\nusername={SECRET}\n"
                "password=x-oauth-basic\n", "")
    got = tokens.discover(runner=Runner([weird]))
    assert got.source == tokens.HELPER
    assert SECRET not in str(got.to_json()) and SECRET not in repr(got)


# ------------------------------------------------- ⭐ 실제 git 왕복 (격리)


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """**내 것이 아닌** 자격증명 저장소. 사용자의 진짜 저장소를 오염시키지 않는다.

    `conftest._isolated_env` 가 이미 ``GIT_CONFIG_GLOBAL`` 을 임시 파일로,
    ``GIT_CONFIG_NOSYSTEM=1`` 로 잡아 준다. 거기에 파일 기반 helper 하나만
    달면 ``git credential approve/fill`` 이 그 파일만 본다.
    """
    store = tmp_path / "creds"
    # ⚠️ **슬래시로** 준다. git config 값 안의 역슬래시는 이스케이프로 읽혀서
    # 윈도우 경로를 그대로 넣으면 helper 가 엉뚱한 파일을 보고 (실측) 아무것도
    # 저장되지 않는다 — 조용히 아무 일도 안 일어나는 쪽의 사고다.
    subprocess.run(
        ["git", "config", "--global", "credential.helper",
         f"store --file={store.as_posix()}"],
        check=True, capture_output=True,
    )
    return store


def test_탐색은_대화형으로_떨어지지_않는다(isolated_store, monkeypatch):
    """⚠️ 창 없이 도는 앱의 급소 — helper 가 답을 못 주면 **즉시** 실패해야 한다.

    ``git credential fill`` 은 기본적으로 터미널로 묻는다. 그 프롬프트가 뜨면
    이 프로세스는 영원히 멈춘다. `tokens._run_git` 의 세 겹 방어가 그것을 막는다
    (여기서 타임아웃 없이 끝나는 것 자체가 증거다).
    """
    monkeypatch.delenv("GITWIRE_TOKEN", raising=False)
    got = tokens.discover(host="nobody.invalid")
    assert got.source == tokens.NONE
    assert got.detail, "왜 못 찾았는지가 남아야 한다"

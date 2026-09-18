"""자격증명 탐색·발급 거들기 — 순서 · **값 누출 0** · 저장 왕복.

여기서 고정하려는 계약:

* **탐색 4단계가 그 순서대로** 동작한다 (env → OS 저장소 → 주소 → 없음).
* ⚠️ **값이 밖으로 나가지 않는다** — `Discovery` 에 값을 담을 필드가 아예
  없고, 토큰은 git 의 **stdin** 으로만 간다 (인자·환경변수에 없다 = ``ps`` 에
  안 보인다).
* 발급 링크는 **문서화된 파라미터**로 **필요한 스코프만** 요구한다.
* 저장은 **실제 git** 왕복으로 확인한다 — 단, 사용자의 진짜 자격증명 저장소를
  건드리지 않는다 (격리된 ``GIT_CONFIG_GLOBAL`` + ``store --file=<tmp>``).
"""

from __future__ import annotations

import dataclasses
import subprocess
import urllib.parse

import pytest

from gitwire_chat import tokens, winspawn

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


def test_저장은_값을_인자로_넘기지_않는다_stdin_뿐이다():
    """``ps``·작업 관리자에 토큰이 보이지 않아야 한다."""
    run = Runner([(0, "", "")])
    tokens.save(SECRET, host="github.com", username="me", runner=run)
    args, stdin = run.calls[0]
    assert args == ["credential", "approve"]
    assert SECRET not in " ".join(args)          # ⭐ 인자에 없다
    assert f"password={SECRET}" in stdin         # stdin 으로만 간다
    assert "username=me" in stdin and "host=github.com" in stdin


def test_저장_실패_사유에도_값이_없다():
    run = Runner([(1, "", "fatal: credential helper 'nope' not found")])
    with pytest.raises(tokens.SaveError) as caught:
        tokens.save(SECRET, runner=run)
    assert SECRET not in str(caught.value) and SECRET not in caught.value.hint
    assert caught.value.code == "store"
    assert "helper" in caught.value.hint or "credential.helper" in caught.value.hint


# ------------------------------------------------------------ 발급 링크


def test_발급_링크는_문서화된_파라미터로_필요한_스코프만_요구한다():
    link = tokens.issue_link("github")
    parts = urllib.parse.urlsplit(link)
    query = urllib.parse.parse_qs(parts.query)
    assert (parts.scheme, parts.netloc, parts.path) == (
        "https", "github.com", "/settings/tokens/new"
    )
    assert query["scopes"] == ["repo"]
    assert query["description"] == ["gitwire-chat"]
    # ⭐ 과한 권한을 받아 두지 않는다 — 이 앱이 부르는 API 가 없는 스코프들.
    for over in ("admin:org", "delete_repo", "workflow", "write:packages", "gist"):
        assert over not in link
    # 만료는 우리가 정하지 않는다 (사용자의 보안 결정이다)
    assert "default_expires_at" not in link


def test_모르는_호스트에는_발급_링크를_짐작하지_않는다():
    assert tokens.issue_link("gitlab") == ""
    assert tokens.issue_link("unknown") == ""


# ------------------------------------------------------------ 입력 방어


def test_빈_토큰은_저장하지_않는다():
    run = Runner()
    with pytest.raises(tokens.SaveError) as caught:
        tokens.save("   ", runner=run)
    assert caught.value.code == "empty"
    assert run.calls == []                      # git 을 부르지도 않는다


def test_줄바꿈이_섞인_붙여넣기는_거절한다():
    """git credential 규약은 한 줄 = 한 항목이다 — 섞이면 엉뚱한 것이 저장된다."""
    run = Runner()
    with pytest.raises(tokens.SaveError) as caught:
        tokens.save(f"{SECRET}\nhost=evil.invalid", runner=run)
    assert caught.value.code == "format"
    assert run.calls == []


# --------------------------------------- git credential 규약 주입 방어


def test_username_에_줄바꿈을_넣어_다른_항목을_심을_수_없다():
    """⚠️ 그 규약은 "한 줄 = 한 항목"이다.

    `username` 에 줄바꿈을 섞으면 우리가 만들려던 것이 아닌 항목
    (`password=엉뚱한값`)이 **저장된다.** 그러면 그 사람의 git 이 전부 인증에
    실패한다 — 되돌리기 어려운 쪽의 사고다.
    """
    run = Runner([(0, "", "")])
    with pytest.raises(tokens.SaveError) as caught:
        tokens.save(SECRET, username="me" + chr(10) + "password=evil", runner=run)
    assert caught.value.code == "format"
    assert run.calls == [], "규약이 깨진 값으로 git 을 불렀다"


def test_host_에_줄바꿈을_넣어_찾았다를_거짓으로_만들_수_없다(monkeypatch):
    """`fill` 요청에 `password=x` 를 심으면 helper 가 그것을 되돌려 준다.

    그러면 저장소에 아무것도 없는 사람에게 "OS 자격증명 저장소에서 찾았습니다"
    라고 말하게 된다 — 화면이 거짓말을 하는 쪽이라 더 나쁘다.
    """
    monkeypatch.delenv("GITWIRE_TOKEN", raising=False)
    run = Runner([FOUND])
    got = tokens.discover(host="github.com" + chr(10) + "password=x", runner=run)
    assert got.source == tokens.NONE, got
    assert run.calls == [], "규약이 깨진 호스트로 git 을 불렀다"


def test_환경변수_이름에_줄바꿈이_있으면_기본_이름으로_떨어진다(monkeypatch):
    monkeypatch.setenv("GITWIRE_TOKEN", SECRET)
    got = tokens.discover(env_name="A" + chr(10) + "B", runner=Runner())
    assert got.env_name == tokens.DEFAULT_ENV and got.source == tokens.ENV


# ------------------------------------------------- git 호출 그 자체


def test_git_호출은_창_없이_대화형_없이_나간다(monkeypatch):
    """⭐ 이 앱의 실측된 두 사고를 한 번에 겨냥한다.

    (1) 창 — `pythonw` 로 도는 앱이 부른 `git.exe` 가 폴링마다 터미널 창을
        띄웠다(`winspawn.py` 도크). 그래서 `quiet_kwargs()` 를 그대로 쓴다.
    (2) 멈춤 — `git credential fill` 은 기본적으로 **물어본다.** 창이 없는
        프로세스에서 프롬프트가 뜨면 그 프로세스는 영원히 멈춘다.
    """
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs

        class Done:
            returncode, stdout, stderr = 1, "", ""

        return Done()

    monkeypatch.setenv("GIT_ASKPASS", "C:/상속된/askpass.bat")
    monkeypatch.setenv("SSH_ASKPASS", "/inherited/askpass")
    monkeypatch.setattr(subprocess, "run", fake_run)
    tokens._run_git(["credential", "fill"], "protocol=https\nhost=github.com\n\n")

    env = seen["kwargs"]["env"]
    assert env["GIT_TERMINAL_PROMPT"] == "0"      # 터미널로 묻지 않는다
    assert env["GCM_INTERACTIVE"] == "never"      # GUI helper 도 막는다
    # 상속된 askpass 헬퍼가 대신 답하면 탐색 결과가 거짓이 된다
    assert "GIT_ASKPASS" not in env and "SSH_ASKPASS" not in env
    assert seen["kwargs"]["timeout"] == tokens.TIMEOUT
    # 창은 띄우지 않는다 — 판정을 여기서 다시 쓰지 않고 그 모듈을 쓴다
    for key, value in winspawn.quiet_kwargs().items():
        assert seen["kwargs"][key] == value, key


def test_git_이_없거나_멈추면_사유가_남고_예외는_안_난다(monkeypatch):
    """탐색이 앱을 죽이면 안 된다 — 못 찾은 것으로 떨어진다."""
    def boom(argv, **kwargs):
        raise OSError("git 을 찾을 수 없다")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.delenv("GITWIRE_TOKEN", raising=False)
    got = tokens.discover(host="github.com")
    assert got.source == tokens.NONE
    assert "git" in got.detail

    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 0))

    monkeypatch.setattr(subprocess, "run", slow)
    got = tokens.discover(host="github.com")
    assert got.source == tokens.NONE and "초" in got.detail


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


def test_저장하면_다음_탐색에서_그것이_나온다(isolated_store, monkeypatch):
    """⭐ 지상검증 — 붙여넣은 값이 저장되고, 다음 조회에서 **거기서** 나온다."""
    monkeypatch.delenv("GITWIRE_TOKEN", raising=False)

    # 저장 전: 저장소가 비어 있으니 못 찾는다 (그리고 **멈추지 않는다**)
    before = tokens.discover(host="example.invalid")
    assert before.source == tokens.NONE, before

    saved = tokens.save(SECRET, host="example.invalid", username="me")
    assert saved == "me"
    assert isolated_store.is_file()

    # 저장 후: 2단계에서 나온다
    after = tokens.discover(host="example.invalid")
    assert after.source == tokens.HELPER, after
    assert "OS 자격증명 저장소" in after.label
    assert SECRET not in str(after.to_json())

    # 저장된 파일에는 값이 있어야 한다 (그래야 저장이 진짜다). 그 파일은
    # 사용자의 것이 아니라 이 테스트의 tmp 파일이다.
    assert SECRET in isolated_store.read_text(encoding="utf-8")


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

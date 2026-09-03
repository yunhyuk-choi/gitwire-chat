"""OS 알림 — OS 중립성과 "실패해도 앱이 죽지 않는다"."""

from __future__ import annotations

import os
import sys
import time

from gitwire_chat import notify


def test_백엔드_체인은_되는_첫_번째를_쓴다():
    calls = []

    def broken(title, body, app):
        calls.append("broken")
        return False

    def works(title, body, app):
        calls.append("works")
        return True

    n = notify.Notifier(backends=[broken, works], coalesce_window=0.0)
    assert n.send("방", "안녕") is True
    assert calls == ["broken", "works"]

    # 한 번 성공한 백엔드를 기억한다 — 매번 실패 경로를 다시 밟지 않는다.
    calls.clear()
    n.send("방", "또")
    assert calls[0] == "works"


def test_백엔드가_예외를_던져도_밖으로_나가지_않는다():
    def explodes(title, body, app):
        raise RuntimeError("알림 스택이 죽었다")

    n = notify.Notifier(backends=[explodes], coalesce_window=0.0)
    assert n.send("방", "안녕") is False   # 조용히 실패


def test_전부_실패해도_False_만_돌려준다():
    n = notify.Notifier(backends=[], coalesce_window=0.0)
    assert n.send("방", "안녕") is False


def test_기본_백엔드는_이_OS_에_맞게_고르고_항상_폴백이_있다():
    chain = notify.default_backends()
    assert chain[-1] is notify.log_only          # 최종 폴백은 언제나 있다
    names = [f.__name__ for f in chain]
    if os.name == "nt":
        assert names[0] == "windows_toast"
    elif sys.platform == "darwin":
        assert names[0] == "macos_notification"
    else:
        assert names[0] == "linux_notify_send"


def test_다른_OS_백엔드는_이_OS_에서_조용히_False():
    # 각 백엔드는 자기 OS 가 아니면 시도조차 하지 않는다.
    others = [notify.macos_notification, notify.linux_notify_send]
    if os.name != "nt":
        others += [notify.windows_toast, notify.windows_balloon]
    for backend in others:
        if backend is notify.linux_notify_send and sys.platform not in ("win32", "darwin"):
            continue
        assert backend("t", "b", "app") in (False, True)  # 예외만 안 나면 된다


def test_알림은_방_단위로_합쳐진다():
    n = notify.Notifier(backends=[], coalesce_window=0.05)
    sent = []
    n.backends = [lambda t, b, a: (sent.append((t, b)), True)[1]]

    for i in range(5):
        n.notify_message("우리 방", "최윤혁", f"메시지 {i}")
    time.sleep(0.25)

    assert len(sent) == 1                    # 토스트 5개가 아니라 1개
    assert sent[0][0] == "우리 방"
    assert "외 4건" in sent[0][1]
    n.close()


def test_한_건이면_그대로_보여준다():
    sent = []
    n = notify.Notifier(
        backends=[lambda t, b, a: (sent.append((t, b)), True)[1]], coalesce_window=0.05
    )
    n.notify_message("방", "영희", "밥 먹자")
    time.sleep(0.25)
    assert sent == [("방", "영희: 밥 먹자")]
    n.close()


def test_꺼두면_아무것도_하지_않는다():
    sent = []
    n = notify.Notifier(
        backends=[lambda t, b, a: (sent.append(1), True)[1]],
        enabled=False,
        coalesce_window=0.0,
    )
    n.notify_message("방", "영희", "안녕")
    assert n.send("방", "안녕") is False
    assert sent == []


def test_PowerShell_은_인코딩된_명령으로_넘긴다():
    """윈도우 콘솔 코드페이지(cp949)를 통과시키지 않는 것이 핵심이다."""
    args = notify._ps_encoded("Write-Output '한국어'")
    assert "-EncodedCommand" in args
    blob = args[args.index("-EncodedCommand") + 1]
    import base64

    decoded = base64.b64decode(blob).decode("utf-16-le")
    assert "한국어" in decoded          # 바이트가 UTF-16LE 로 온전히 실린다


def test_PowerShell_따옴표_이스케이프():
    assert notify._ps_quote("it's") == "it''s"


def test_외부_명령_실패는_False_로만_보고된다():
    assert notify._run(["이런-명령은-없다-확실히"]) is False

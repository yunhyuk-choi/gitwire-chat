"""프로세스를 **창 없이** 띄우기 — Windows 콘솔 판정을 한곳에.

이 앱은 백그라운드 프로세스를 여럿 띄운다: 갱신 CLI(화면 버튼), 갱신이 다시
띄우는 앱, pip, git, 알림 백엔드. Windows 에서 그것들은 전부 **콘솔 앱**이고,
콘솔 앱을 어떻게 띄우느냐가 "사용자 화면에 빈 검은 창이 뜨는가"를 가른다. 그
판정을 모듈마다 다시 쓰면 한 곳이 빠지고, **빠진 그 한 곳이 창을 띄운다** —
실측된 고장이다. 그래서 여기 한 번만 쓴다.

Windows 콘솔의 규칙 (이 모듈의 근거 전부)
-----------------------------------------
``CreateProcess`` 가 콘솔 앱을 띄울 때:

1. 아무 플래그도 안 주면 **부모의 콘솔을 물려받는다.** 부모에게 콘솔이 없으면
   **자식에게 콘솔을 새로 할당하고 그것을 창으로 그린다.**
2. ``DETACHED_PROCESS`` = **콘솔을 아예 주지 않는다.** 자기 창은 안 뜨지만,
   그 자식이 다시 콘솔 앱을 부르면 1번에 걸려 **손자가 창을 띄운다.** 문제를
   한 세대 미루는 플래그다.
3. ``CREATE_NO_WINDOW`` = **창이 없는 콘솔**을 준다. 창이 없으니 안 보이고,
   그 콘솔은 **자식·손자에게 물려진다** → 트리 전체가 조용해진다.

⚠️ 2 와 3 은 **함께 쓸 수 없다.** MSDN: ``CREATE_NO_WINDOW`` 는
``DETACHED_PROCESS``·``CREATE_NEW_CONSOLE`` 과 같이 주면 **무시된다.** 그래서
이 모듈은 ``DETACHED_PROCESS`` 를 쓰지 않는다.

그럼 인터프리터는 ``python`` 인가 ``pythonw`` 인가
--------------------------------------------------
둘 다 "창을 안 띄우는" 수단이지만 **성질이 반대**다:

* ``pythonw.exe`` (GUI 서브시스템) = 콘솔을 **아예 안 갖는다**. 자기 창은 절대
  안 뜬다. 대신 위 2번과 같은 상태가 되어, 그 프로세스가 부르는 ``git``·``pip``
  가 **각자 창을 띄운다.** 실측(2026-09-08): ``pythonw`` 로 띄운 이 앱이 15초
  폴링마다 ``git.exe`` → ``conhost.exe`` → 터미널 창을 하나씩 띄웠다.
* ``python.exe`` + ``CREATE_NO_WINDOW`` = 창 없는 콘솔을 갖고, 그것을 손자까지
  물려준다. **트리 전체가 조용하다.**

그래서 **우리가 플래그를 줄 수 있는 자리에서는 ``python.exe`` 를 고른다**
(`hidden_console_python`). 플래그를 줄 수 없는 자리 — 시작 폴더의 ``.cmd`` 가
띄우는 자동 시작 — 에서만 ``pythonw.exe`` 가 유일한 수단이다
(`console_free_python`, `autostart.py` 가 쓴다).

두 겹으로 막는 이유: 어느 한 겹이 없는 환경이 실제로 있다. ``pythonw.exe`` 가
없는 설치본(임베디드 배포판·일부 스토어 파이썬)이 있고, ``CREATE_NO_WINDOW`` 가
없는 옛 파이썬이 있다. 둘 중 하나만 있어도 창은 뜨지 않는다.

macOS·Linux
-----------
콘솔·창이라는 개념 자체가 없다. 이 모듈의 Windows 분기는 전부 ``os.name ==
"nt"`` 뒤에 있고, POSIX 에서는 예전 그대로 ``start_new_session=True`` 로 세션만
떼어 낸다 (터미널을 닫아도 백그라운드 프로세스가 살아 있게). ``creationflags``
는 아예 넘기지 않는다 — POSIX 에서 0 이 아니면 ``subprocess`` 가 거절한다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: 창 없는 콘솔을 주는 플래그.
#:
#: ⚠️ 폴백 값은 **Win32 상수 그대로**다. ``subprocess`` 는 Windows 에서만 이
#: 이름을 노출하므로, POSIX 에서는 ``getattr`` 이 폴백을 준다. 그 값이 0 이면
#: "POSIX 에서 돌린 테스트가 플래그를 단언할 수 없다"가 되어 회귀를 놓친다
#: (플래그는 아래 ``on_windows()`` 분기 안에서만 쓰이므로 POSIX 실행에는
#: 절대 새지 않는다).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

#: Ctrl+C 가 우리에게서 자식으로 전파되지 않게. 갱신은 우리를 죽이는 프로세스라
#: 우리와 함께 죽으면 갱신이 반쪽에서 멈춘다.
_NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


def on_windows() -> bool:
    """지금 Windows 인가. ``os.name`` 을 **부를 때** 본다 — 테스트 주입점."""
    return os.name == "nt"


# ----------------------------------------------------------- 생성 플래그


def quiet_kwargs() -> dict:
    """**잠깐 돌고 출력을 우리가 받아 읽는** 자식용 (git·pip·알림 백엔드).

    Windows 에서만 ``CREATE_NO_WINDOW``. 파이프·타임아웃과 무관하다 —
    ``capture_output``/``PIPE`` 는 콘솔이 아니라 파이프를 쓴다.
    """
    if not on_windows():
        return {}
    return {"creationflags": _NO_WINDOW}


def detached_kwargs() -> dict:
    """**우리보다 오래 사는** 자식용 (갱신 CLI · 다시 띄우는 앱).

    Windows: 창 없는 콘솔 + 새 프로세스 그룹. ``DETACHED_PROCESS`` 는 쓰지
    않는다 (모듈 도크 — 그걸 주면 ``CREATE_NO_WINDOW`` 가 무시되고, 손자가
    창을 띄운다). 프로세스 수명은 Windows 에서 부모와 묶이지 않으므로
    ``DETACHED_PROCESS`` 없이도 부모가 죽어도 자식은 계속 산다.

    POSIX: 예전 그대로 ``start_new_session=True``.
    """
    if not on_windows():
        return {"start_new_session": True}
    return {"creationflags": _NO_WINDOW | _NEW_GROUP}


# --------------------------------------------------------- 인터프리터 선택


def _sibling(base: str | os.PathLike, name: str) -> Path | None:
    """인터프리터 **옆에** 있는 형제 실행 파일. 실제로 있을 때만 준다.

    ``sys.executable`` 을 기준으로 찾는다 — venv 로 설치한 경우가 흔하고,
    로그인 셸의 ``PATH`` 는 그 venv 를 모른다.
    """
    candidate = Path(base).with_name(name)
    return candidate if candidate.is_file() else None


#: ⚠️ 아래 두 함수는 **호스트 OS 로 갈라지지 않는다** — 파일 이름만 본다. 이유가
#: 있다: 자동 시작은 *다른 OS 대상 미리보기*를 렌더할 수 있어야 하고(리눅스에서
#: Windows 용 ``.cmd`` 를 미리 보여주는 경로가 있다), 그때 판정 대상은 *호스트*가
#: 아니라 *대상*이다. POSIX 인터프리터 이름은 ``pythonw.exe`` 가 아니므로 어차피
#: 첫 분기를 타지 않는다 — 즉 갈라지지 않아도 POSIX 동작은 "받은 것 그대로"다.


def hidden_console_python(explicit: str | None = None) -> tuple[str, bool]:
    """**플래그를 줄 수 있는** 자리용 인터프리터 — ``python.exe`` 를 고른다.

    ``(경로, 확실한가)``. ``확실한가=False`` 면 콘솔 서브시스템 인터프리터를
    못 찾았다는 뜻이다 — 부르는 쪽이 그 사실을 **말해야 한다**(조용한 실패
    금지). 그 경우에도 ``pythonw`` 자신의 창은 뜨지 않지만, 그것이 부르는
    손자(git·pip)가 창을 띄울 수 있다.
    """
    base = explicit or sys.executable
    if Path(base).name.lower() == "pythonw.exe":
        sibling = _sibling(base, "python.exe")
        return (str(sibling), True) if sibling else (base, False)
    return base, True


def console_free_python(explicit: str | None = None) -> tuple[str, bool]:
    """**플래그를 줄 수 없는** 자리용 — 콘솔이 없는 ``pythonw.exe`` 를 고른다.

    자동 시작(`autostart.py`)이 쓴다. 시작 폴더의 ``.cmd`` 가 띄우므로
    ``creationflags`` 를 줄 방법이 없고, 그 자리에서 ``python.exe`` 를 쓰면
    ``cmd`` 의 콘솔에 붙어 창이 남는다.

    *있다고 가정하지 않는다* — 임베디드 배포판이나 일부 스토어 파이썬에는
    ``pythonw.exe`` 가 없다. 실제로 파일을 확인하고, 없으면 ``python.exe`` 로
    폴백하되 ``(경로, False)`` 로 **그 사실을 알린다**.
    """
    base = explicit or sys.executable
    if Path(base).name.lower() == "pythonw.exe":
        return base, True
    sibling = _sibling(base, "pythonw.exe")
    return (str(sibling), True) if sibling else (base, False)

"""``gitwire-chat update …`` 서브커맨드의 CLI 껍데기.

`updater.py` 는 순수 라이브러리로 두고(테스트가 `Report` 만 보면 되게), 인자
파싱과 출력은 여기로 분리한다 — 원칙 1(호출 시점·책임이 다르면 분리).
`_autostart_cli.py` 와 같은 모양이다.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from . import runstate, updater

#: Windows 에서 pip 가 갈아치우다 잠기는 파일 이름 (콘솔 스크립트).
CONSOLE_SCRIPT = "gitwire-chat"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gitwire-chat update",
        description=(
            "최신으로 갱신한다 — 바뀔 게 있나 보고 · 돌고 있는 앱을 멈추고 · "
            "다시 설치하고 · 원래 옵션으로 다시 띄운다"
        ),
        epilog=(
            "먼저 --dry-run 으로 무엇을 멈추고 무엇을 설치하고 어떻게 다시 띄울지 "
            "보고 나서 실행하는 것을 권한다. 바뀔 게 없으면 아무것도 하지 않는다."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="아무것도 멈추거나 설치하지 않고 무엇을 할지만 보여준다",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="설치할 git 주소 (기본: 지금 설치본이 온 곳)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="이 포트의 인스턴스만 멈추고 다시 띄운다 (기본: 대장에 있는 전부)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="최신이어도 그대로 다시 설치한다 (설치본이 깨졌을 때)",
    )
    parser.add_argument(
        "--no-restart",
        action="store_true",
        help="멈추고 설치까지만 한다 — 다시 띄우지 않는다",
    )
    parser.add_argument(
        "--ignore-unmanaged",
        action="store_true",
        help=(
            "대장에 없는 인스턴스가 돌고 있어도 진행한다 "
            "(그 앱은 갱신 뒤 직접 재기동해야 한다)"
        ),
    )
    parser.add_argument(
        "--dir",
        default=os.environ.get(runstate.ENV_DIR) or None,
        help=(
            "돌고 있는 인스턴스 대장 디렉토리 (기본: OS 데이터 디렉토리/run). "
            f"환경변수 {runstate.ENV_DIR} 로도 준다 — 테스트·격리용"
        ),
    )
    parser.add_argument(
        "--up-timeout",
        type=float,
        default=updater.UP_TIMEOUT,
        help=f"다시 띄운 앱이 응답할 때까지 기다리는 시간(초, 기본 {updater.UP_TIMEOUT:.0f})",
    )
    parser.add_argument(
        "--stop-timeout",
        type=float,
        default=updater.STOP_TIMEOUT,
        help=f"멈출 때까지 기다리는 시간(초, 기본 {updater.STOP_TIMEOUT:.0f})",
    )
    return parser


def _reexec_via_module(argv: list[str], *, out) -> int | None:
    """⚠️ Windows 에서 **콘솔 스크립트로 불렸을 때** 자기 자신을 모듈로 다시 부른다.

    ``gitwire-chat update`` 로 부르면 지금 돌고 있는 프로세스가
    ``Scripts\\gitwire-chat.exe`` 그 파일이다. pip 는 재설치할 때 그 파일을 먼저
    지우는데, Windows 는 **실행 중인 파일을 지우지 못한다** — pip 가 "Access is
    denied" 로 실패한다. 사람에게 "다르게 부르세요"를 요구하는 대신 우리가
    ``python -m gitwire_chat update`` 로 갈아타고, 무엇을 왜 했는지 알린다.

    반환값이 None 이면 갈아타지 않았다는 뜻이다(그대로 진행하면 된다).
    """
    if os.name != "nt":
        return None
    name = Path(sys.argv[0] or "").name.lower()
    if not name.startswith(CONSOLE_SCRIPT):
        return None
    command = [sys.executable, "-m", "gitwire_chat", "update", *argv]
    print(
        "※ Windows 에서는 콘솔 스크립트(gitwire-chat.exe)로 갱신할 수 없다 —\n"
        "  pip 가 그 파일을 갈아치우려 할 때 실행 중이면 잠긴다. 그래서 아래로 다시 부른다:\n"
        f"  {' '.join(command)}",
        file=out,
    )
    try:
        return subprocess.run(command).returncode  # noqa: S603 — 우리가 만든 명령
    except OSError as exc:
        print(f"실패: 다시 부르지 못했다 — {exc}", file=out)
        return 1


def run(argv: list[str], *, out=None) -> int:
    """서브커맨드 실행. 0 = 성공."""
    stream = out if out is not None else sys.stdout
    args = build_parser().parse_args(argv)

    reexec = _reexec_via_module(argv, out=stream)
    if reexec is not None:
        return reexec

    def echo(line: str) -> None:
        print(line, file=stream)
        try:
            stream.flush()
        except Exception:  # noqa: BLE001 — 버퍼 없는 스트림
            pass

    report = updater.update(
        url=args.url,
        directory=args.dir,
        dry_run=args.dry_run,
        force=args.force,
        restart=not args.no_restart,
        ignore_unmanaged=args.ignore_unmanaged,
        port=args.port,
        stop_timeout=args.stop_timeout,
        up_timeout=args.up_timeout,
        echo=echo,
    )
    return 0 if report.ok else 1

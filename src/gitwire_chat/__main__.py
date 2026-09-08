"""실행 진입점 — `python -m gitwire_chat`.

OS 중립을 위해 셸 스크립트에 기대지 않는다. 파이썬 하나로 3 OS 를 덮는다.
서브커맨드가 둘 있다 — ``autostart``(로그인 시 자동 시작 등록·해제·상태)와
``update``(최신으로 갱신: 멈추고 · 다시 설치하고 · 원래 옵션으로 다시 띄운다).
인자 없이 부르면 서버를 띄운다(기존 그대로).

서버로 뜰 때는 **자기 자신을 대장에 적는다** (`runstate.py`). 그게 없으면
``update`` 가 "지금 무엇이 어떤 옵션으로 떠 있나"를 알 수 없어서, 갱신 뒤 포트나
``--home`` 을 잃은 다른 앱을 띄우게 된다.

⚠️ **바인드 주소는 루프백 고정이고 바꿀 수 없다.** 이 앱은 인증을 하지 않는데,
그건 결함이 아니라 "내 컴퓨터에서 나만 쓴다"는 설계다. 외부로 여는 스위치를
두면 그 설계 전제가 옵션 하나로 무너지고, 남는 건 경고문뿐이다. 그래서 옵션
자체를 두지 않는다. 밖에서 접근해야 한다면 그건 이 앱이 아니라 앞단(SSH 터널·
리버스 프록시)이 인증과 함께 책임질 일이다.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import webbrowser

from . import runstate
from .app import create_app, installed_version
from .autostart import ServeOptions
from .config import DEFAULT_PORT, load_settings

#: 루프백 고정 — 옵션이 아니다 (모듈 도크 참조).
HOST = "127.0.0.1"

#: 서브커맨드 이름. 이 토큰이 첫 인자로 오면 서버를 띄우지 않고 그쪽으로 넘긴다.
AUTOSTART = "autostart"
UPDATE = "update"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gitwire-chat",
        description="git 레포를 메시지 저장소로 쓰는 로컬-퍼스트 채팅",
        epilog=(
            "서브커맨드: autostart (로그인 시 자동 시작 등록·해제·상태) · "
            "update (최신으로 갱신 — 멈추고 다시 설치하고 다시 띄운다). "
            "자세히는 `gitwire-chat autostart --help` · `gitwire-chat update --help`."
        ),
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="포트")
    parser.add_argument(
        "--home",
        default=None,
        help="로컬 상태 디렉토리 (기본: 소스 체크아웃이면 ./chats, 아니면 OS 데이터 디렉토리)",
    )
    parser.add_argument("--author", default=None, help="표시 이름 기본값")
    parser.add_argument(
        "--poll-interval", type=float, default=None, help="폴 주기(초)"
    )
    parser.add_argument(
        "--no-notify", action="store_true", help="OS 알림을 끈다"
    )
    parser.add_argument(
        "--open", action="store_true", help="기동 후 기본 브라우저를 연다"
    )
    parser.add_argument("--verbose", action="store_true", help="디버그 로그")
    return parser


def _force_utf8_console() -> None:
    """콘솔 출력을 UTF-8 로 고정한다.

    윈도우 콘솔 코드페이지(cp949 등)에 맡기면 한국어 진단 문구가 깨지거나
    이모지에서 죽는다. OS 중립을 위해 파이썬 쪽에서 못 박는다.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — 리다이렉트된 스트림 등
            pass


def serve_argv(args: argparse.Namespace, settings) -> list[str]:
    """이 인스턴스를 **똑같이** 다시 띄우는 인자 목록.

    ⚠️ 파싱된 원문이 아니라 **해석된 값**을 쓴다. ``--home`` 을 안 주고 띄웠다면
    그 값은 ``GITWIRE_CHAT_HOME`` 이나 현재 디렉토리에서 나온 것인데, 갱신은
    다른 셸에서 실행될 수 있어서 그 환경이 같다고 믿을 수 없다. 해석된 절대
    경로를 적어 두면 다시 띄운 앱이 같은 상태 디렉토리를 본다.

    포트·홈·표시 이름·폴 주기·알림은 `autostart.ServeOptions` 가 이미 인자로
    바꿀 줄 안다 — 그 규칙을 여기서 다시 쓰지 않는다(단일 원천).
    """
    options = ServeOptions(
        port=args.port,
        home=str(settings.home),
        author=settings.author,
        poll_interval=settings.poll_interval,
        notifications=settings.notifications,
    )
    argv = options.to_args()
    # 나머지 스위치는 그대로 재현한다 — 원래 쓰던 것과 다른 앱이 뜨지 않게.
    if args.open:
        argv.append("--open")
    if args.verbose:
        argv.append("--verbose")
    return argv


def self_instance(args: argparse.Namespace, settings) -> runstate.Instance:
    """대장에 적을 "지금 이 프로세스" 한 벌."""
    return runstate.Instance(
        port=args.port,
        pid=os.getpid(),
        executable=sys.executable,
        args=tuple(serve_argv(args, settings)),
        home=str(settings.home),
        author=settings.author,
        version=installed_version(),
        started_at=time.time(),
    )


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == AUTOSTART:
        # 서브커맨드는 서버를 띄우지 않는다. 무거운 임포트를 피하려고 여기서 import 한다.
        from ._autostart_cli import run as run_autostart

        return run_autostart(argv[1:])
    if argv and argv[0] == UPDATE:
        from ._update_cli import run as run_update

        return run_update(argv[1:])
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings(
        args.home,
        author=args.author,
        poll_interval=args.poll_interval,
        notifications=not args.no_notify,
    )
    app = create_app(settings)
    url = f"http://{HOST}:{args.port}/"
    print(f"gitwire-chat — {url}", file=sys.stderr)
    print(f"로컬 상태: {settings.home}", file=sys.stderr)
    marker = runstate.record(self_instance(args, settings))
    if marker is not None:
        print(f"인스턴스 대장: {marker}", file=sys.stderr)
    if args.open:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 — 브라우저가 없어도 서버는 돈다
            pass
    try:
        app.run(host=HOST, port=args.port, threaded=True, debug=False)
    except KeyboardInterrupt:
        pass
    finally:
        app.extensions["gitwire_chat"].stop()
        runstate.forget(args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

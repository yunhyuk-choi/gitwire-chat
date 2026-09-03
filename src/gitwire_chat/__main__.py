"""실행 진입점 — `python -m gitwire_chat`.

OS 중립을 위해 셸 스크립트·서비스 등록에 기대지 않는다. 파이썬 하나로 3 OS 를
덮는다. 자동 시작(로그인 시 기동)은 v1 범위 밖이며 README 에 OS 별 안내만 있다.
"""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser

from .app import create_app
from .config import load_settings

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8770


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gitwire-chat",
        description="git 레포를 메시지 저장소로 쓰는 로컬-퍼스트 채팅",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="바인드 주소")
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


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
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
    url = f"http://{args.host}:{args.port}/"
    print(f"gitwire-chat — {url}", file=sys.stderr)
    print(f"로컬 상태: {settings.home}", file=sys.stderr)
    if args.open:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 — 브라우저가 없어도 서버는 돈다
            pass
    try:
        app.run(host=args.host, port=args.port, threaded=True, debug=False)
    except KeyboardInterrupt:
        pass
    finally:
        app.extensions["gitwire_chat"].stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""``gitwire-chat autostart …` 서브커맨드의 CLI 껍데기.

`autostart.py` 는 순수 라이브러리로 두고(테스트가 파일 시스템만 보면 되게),
인자 파싱과 출력은 여기로 분리한다 — 원칙 1(호출 시점·책임이 다르면 분리).
"""

from __future__ import annotations

import argparse
import os
import sys

from . import autostart as _a
from .config import DEFAULT_PORT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gitwire-chat autostart",
        description="로그인할 때 자동으로 뜨게 한다 — 등록 · 해제 · 상태 확인",
        epilog=(
            "먼저 --dry-run 으로 무엇이 어디에 쓰이는지 보고 나서 등록하는 것을 권한다."
        ),
    )
    sub = parser.add_subparsers(dest="action", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--os",
            dest="platform",
            default=_a.host_platform(),
            choices=[*_a.PLATFORMS, "all"],
            help="대상 OS (기본: 지금 이 OS). all 은 --dry-run 미리보기 전용",
        )
        p.add_argument(
            "--dir",
            default=os.environ.get(_a.ENV_DIR) or None,
            help=(
                "등록 파일을 둘 디렉토리 (기본: OS 표준 위치). "
                f"환경변수 {_a.ENV_DIR} 로도 준다 — 테스트·격리용"
            ),
        )
        p.add_argument("--log-file", default=None, help="로그 파일 경로 (기본: OS 관례 위치)")

    def add_serve_options(p: argparse.ArgumentParser) -> None:
        """등록 파일에 **박아 넣을** 서버 옵션 — 등록 시점 값이 그대로 들어간다."""
        p.add_argument("--port", type=int, default=DEFAULT_PORT, help="포트")
        p.add_argument("--home", default=None, help="로컬 상태 디렉토리")
        p.add_argument("--author", default=None, help="표시 이름 기본값")
        p.add_argument("--poll-interval", type=float, default=None, help="폴 주기(초)")
        p.add_argument("--no-notify", action="store_true", help="OS 알림을 끈 채로 띄운다")
        p.add_argument(
            "--python",
            default=None,
            help="쓸 인터프리터 (기본: 지금 이 인터프리터의 절대 경로)",
        )

    install = sub.add_parser("install", help="로그인 시 자동 시작 등록")
    add_common(install)
    add_serve_options(install)
    install.add_argument(
        "--dry-run", action="store_true", help="쓰지 않고 무엇을 쓸지만 보여준다"
    )
    install.add_argument(
        "--force", action="store_true", help="이 앱이 만들지 않은 같은 이름 파일도 덮어쓴다"
    )

    uninstall = sub.add_parser("uninstall", help="자동 시작 해제")
    add_common(uninstall)
    uninstall.add_argument(
        "--dry-run", action="store_true", help="지우지 않고 무엇을 지울지만 보여준다"
    )

    status = sub.add_parser("status", help="지금 등록돼 있는지 · 어디에 · 어떻게")
    add_common(status)
    add_serve_options(status)
    return parser


def _options(args: argparse.Namespace) -> _a.ServeOptions:
    return _a.ServeOptions(
        port=getattr(args, "port", DEFAULT_PORT),
        home=getattr(args, "home", None),
        author=getattr(args, "author", None),
        poll_interval=getattr(args, "poll_interval", None),
        notifications=not getattr(args, "no_notify", False),
    )


def _targets(args: argparse.Namespace) -> list[str]:
    if args.platform == "all":
        return list(_a.PLATFORMS)
    return [args.platform]


def run(argv: list[str], *, out=None) -> int:
    """서브커맨드 실행. 0 = 성공."""
    stream = out if out is not None else sys.stdout
    args = build_parser().parse_args(argv)
    targets = _targets(args)
    if args.platform == "all" and not getattr(args, "dry_run", True):
        print("--os all 은 --dry-run 과 status 에서만 쓸 수 있다.", file=stream)
        return 2

    ok = True
    for index, platform in enumerate(targets):
        backend = _a.make_backend(
            platform,
            _options(args),
            directory=args.dir,
            python=getattr(args, "python", None),
            log_path=args.log_file,
        )
        if args.action == "install":
            report = backend.install(dry_run=args.dry_run, force=args.force)
        elif args.action == "uninstall":
            report = backend.uninstall(dry_run=args.dry_run)
        else:
            report = backend.status()
        if index:
            print("", file=stream)
            print("=" * 70, file=stream)
        for line in report.lines:
            print(line, file=stream)
        ok = ok and report.ok
    return 0 if ok else 1

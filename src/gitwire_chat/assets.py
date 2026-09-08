"""정적 자원 캐시 무효화 — **내용 도장(stamp)을 경로에 박는다.**

무엇을 해결하나
---------------
이 앱은 갱신될 때 파이썬 패키지만 갈아치운다(``pip install --force-reinstall``).
브라우저는 그걸 모른다. ``/static/app.js`` 라는 URL 은 그대로이므로, 캐시에 있던
**옛 JS·CSS** 를 계속 쓴다. 그러면 "업데이트했는데 화면은 그대로" 가 된다 —
사람이 매번 강력 새로고침(Cmd/Ctrl+Shift+R)을 기억해야 하는 절차가 남는다.
갱신을 명령 한 줄로 줄이면 이 함정은 **더 자주** 터진다(새로고침을 잊기 쉬워지므로).

왜 버전 문자열이 아니라 내용 해시인가
------------------------------------
후보였던 ``importlib.metadata.version()`` 은 이 프로젝트에서 **동작하지 않는다.**
배포가 git 커밋으로 나가고 버전 문자열(``0.2.0``)은 그대로 두는 방식이라,
같은 버전으로 열 번을 배포해도 값이 안 바뀐다 — 캐시가 또 안 깨진다.
``git describe`` 류는 아예 못 쓴다(설치본에 체크아웃이 없다).

그래서 **파일 내용 자체**를 도장으로 쓴다. 정적 트리의 모든 파일 내용을
sha256 으로 접어 12자 도장을 만든다. 성질이 정확히 우리가 원하는 것이다:

* 같은 버전으로 재배포해도 파일이 **바뀌었으면** 도장이 바뀐다.
* 파일이 **안 바뀌었으면** 도장이 그대로다(mtime 만 달라진 재설치도 그대로).
* 설치본에서 얻을 수 있다 — 필요한 것은 디스크에 있는 그 파일들뿐이다.

왜 경로 세그먼트인가 (``?v=`` 가 아니라)
---------------------------------------
프런트엔드가 **ES 모듈 그래프**다. ``app.js`` 가 ``./js/boot.js`` 를 부르고 그것이
다시 14개를 부른다. ``app.js?v=abc`` 처럼 질의 문자열만 붙이면 **그 한 파일만**
새로 받고 하위 모듈은 옛 캐시에서 나온다 — 하나라도 옛 파일이 섞이면 조합이
깨져 "업데이트했는데 그대로" 보다 **더 이상한 증상**이 난다.

경로에 박으면 그 문제가 정의상 사라진다::

    /assets/<도장>/app.js   →  './js/boot.js'  →  /assets/<도장>/js/boot.js

상대 import 가 **같은 접두사 안에서** 해석되므로, 도장 하나가 그래프 전체를
한꺼번에 갈아 준다. 벤더 파일(``vendor/**``)도 같은 트리라 자동으로 덮인다.
빌드 단계도, 내용 재작성도, import 맵도 필요 없다.

``index.html`` 은 어떻게 되나
----------------------------
셸 HTML 안에 그 새 URL 이 들어 있으니, **셸이 캐시되면 아무 의미가 없다.**
그래서 ``/`` 응답은 ``no-store`` 로 준다 (`app.create_app`). 첫 페인트 전에 도는
인라인 테마 조각도 그 안에 있으므로 같이 신선해진다.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

#: 도장 길이(16진 문자 수). 충돌 확률은 무시할 수 있고, URL 이 읽을 만하게 짧다.
STAMP_LEN = 12

#: 도장이 붙은 자원의 URL 접두사. Flask 기본 ``/static/`` 과 **겹치지 않게** 따로
#: 둔다 — ``/static/<path:filename>`` 이 ``/static/<도장>/app.js`` 까지 잡아서
#: 어느 규칙이 이기는지가 모호해지는 것을 피한다.
ASSETS_PREFIX = "/assets"

#: 도장이 맞는 자원의 수명(초). 도장이 바뀌면 URL 이 바뀌므로 길게 줘도 된다.
#: 그게 이 방식의 요점이다 — 1년 + ``immutable``.
ASSET_MAX_AGE = 31536000

#: 도장 계산에서 제외할 파일 이름·확장자. 사람이 읽는 문서와 파이썬 캐시는
#: 브라우저가 받는 자원이 아니다.
_SKIP_SUFFIXES = (".md", ".pyc")
_SKIP_DIRS = ("__pycache__",)


def iter_asset_files(root: str | os.PathLike) -> list[Path]:
    """도장에 넣을 파일 목록 — 경로 순으로 **정렬해서** 돌려준다.

    정렬이 없으면 파일 시스템 순서에 따라 같은 트리가 다른 도장을 낸다.
    """
    base = Path(root)
    if not base.is_dir():
        return []
    out = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(base)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() in _SKIP_SUFFIXES:
            continue
        out.append(path)
    return sorted(out, key=lambda p: p.relative_to(base).as_posix())


def compute_stamp(root: str | os.PathLike) -> str:
    """정적 트리의 **내용** 도장. 같은 내용 = 같은 도장 (mtime 무관)."""
    base = Path(root)
    digest = hashlib.sha256()
    for path in iter_asset_files(base):
        rel = path.relative_to(base).as_posix()
        # 경로도 넣는다 — 내용이 같은 두 파일의 **이름이 바뀐 것**도 변경이다.
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:STAMP_LEN]


def _signature(root: str | os.PathLike) -> tuple:
    """"뭔가 달라졌나" 를 stat 만으로 보는 값싼 지문 (내용은 읽지 않는다)."""
    base = Path(root)
    out = []
    for path in iter_asset_files(base):
        try:
            info = path.stat()
        except OSError:                       # 읽는 중에 사라진 파일
            continue
        out.append((path.relative_to(base).as_posix(), info.st_size, info.st_mtime_ns))
    return tuple(out)


class AssetStamper:
    """도장을 들고 있다가, 트리가 달라졌을 때만 다시 계산한다.

    두 성질을 동시에 원해서 이렇게 한다:

    * **정확** — 도장은 내용 해시다. mtime 만 바뀐 재설치는 도장을 안 바꾼다.
    * **값싸다** — 매 요청에 228KB 를 다시 읽지 않는다. stat 지문이 그대로면
      들고 있던 값을 준다.

    소스 체크아웃으로 개발할 때 파일을 고치면 **서버를 안 내려도** 다음 새로고침에
    새 도장이 나온다 — 지문이 달라지기 때문이다.
    """

    def __init__(self, root: str | os.PathLike) -> None:
        self.root = Path(root)
        self._signature: tuple | None = None
        self._stamp = ""

    @property
    def stamp(self) -> str:
        signature = _signature(self.root)
        if signature != self._signature or not self._stamp:
            stamp = compute_stamp(self.root)
            if self._stamp and stamp != self._stamp:
                log.info("정적 자원 도장 %s → %s", self._stamp, stamp)
            self._signature = signature
            self._stamp = stamp
        return self._stamp

    def url(self, filename: str) -> str:
        """템플릿이 부르는 것. ``/assets/<도장>/<파일>``."""
        return f"{ASSETS_PREFIX}/{self.stamp}/{str(filename).lstrip('/')}"

"""콘솔 출력 헬퍼.

`cli.py` 와 `cli_knowledge.py` 가 둘 다 쓴다. 한쪽에 두면 순환 import 가 되므로
양쪽이 의존하는 제3의 모듈로 뺀다.
"""

from __future__ import annotations

import sys


def _p(msg: str = "") -> None:
    """콘솔 출력.

    Windows 콘솔은 기본 코드페이지(예: 한국어 환경의 cp949)로 인코딩하므로
    `print(msg)` 가 이모지 등 코드페이지에 없는 문자에서 UnicodeEncodeError 를
    낼 수 있다. 그 경우 콘솔의 실제 인코딩으로 다시 인코딩하면서 인코딩 불가
    문자는 물음표로 치환해, 출력이 깨지는 대신 알아볼 수 있게 저하시킨다.
    (UTF-8 로 인코딩/디코딩하는 방식은 원래 문자열과 동일한 str 을 돌려주므로
    같은 예외가 다시 발생해 무의미하다.)
    """
    try:
        print(msg)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        print(msg.encode(enc, "replace").decode(enc, "replace"))

"""콘솔 출력 헬퍼.

`cli.py` 와 `cli_knowledge.py` 가 둘 다 쓴다. 한쪽에 두면 순환 import 가 되므로
양쪽이 의존하는 제3의 모듈로 뺀다.
"""

from __future__ import annotations


def _p(msg: str = "") -> None:
    """콘솔 출력. Windows 기본 코드페이지에서 한글이 깨지지 않게 감싼다."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("utf-8", "replace").decode("utf-8", "replace"))

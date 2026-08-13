"""콘솔 출력 헬퍼.

`cli.py` 와 `cli_knowledge.py` 가 둘 다 쓴다. 한쪽에 두면 순환 import 가 되므로
양쪽이 의존하는 제3의 모듈로 뺀다.
"""

from __future__ import annotations

import sys
import unicodedata


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


def display_width(text: str) -> int:
    """이 문자열이 콘솔에서 차지하는 칸 수.

    `len()` 과 다르다 — 한글·전각 문자는 두 칸을 차지한다. 파이썬의 문자열
    서식(`f"{text:<14}"`)은 `len()` 기준이라, 한글 이름이 든 표는 실제 콘솔에서
    열이 어긋난다 (`qatc knowledge` 의 커버리지 표에서 실측). 유니코드가
    East Asian Width 를 W(Wide) 또는 F(Fullwidth) 로 분류한 문자만 2로 센다 —
    A(Ambiguous, 예: `±`)는 폰트에 따라 달라지므로 1로 둔다.
    """
    return sum(
        2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        for ch in text
    )


def pad(text: str, width: int, *, align: str = "left") -> str:
    """`display_width` 기준으로 `width` 칸을 채운다.

    폭을 넘겨도 **자르지 않는다.** 컨텐츠 이름이 잘려 나가면 어느 컨텐츠인지
    알 수 없게 되는데, 그건 열이 어긋나는 것보다 나쁘다.
    """
    fill = " " * max(0, width - display_width(text))
    return fill + text if align == "right" else text + fill

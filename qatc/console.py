"""콘솔 출력 헬퍼.

`cli.py` 와 `cli_knowledge.py` 가 둘 다 쓴다. 한쪽에 두면 순환 import 가 되므로
양쪽이 의존하는 제3의 모듈로 뺀다.
"""

from __future__ import annotations

import sys
import unicodedata


def _p(msg: str = "", *, err: bool = False) -> None:
    """콘솔 출력. `err=True` 면 표준출력 대신 표준오류로 보낸다.

    Windows 콘솔은 기본 코드페이지(예: 한국어 환경의 cp949)로 인코딩하므로
    `print(msg)` 가 이모지 등 코드페이지에 없는 문자에서 UnicodeEncodeError 를
    낼 수 있다. 그 경우 콘솔의 실제 인코딩으로 다시 인코딩하면서 인코딩 불가
    문자는 물음표로 치환해, 출력이 깨지는 대신 알아볼 수 있게 저하시킨다.
    (UTF-8 로 인코딩/디코딩하는 방식은 원래 문자열과 동일한 str 을 돌려주므로
    같은 예외가 다시 발생해 무의미하다.)

    스트림은 **호출 시점에** `sys` 에서 찾는다. 기본 인자로 묶으면 import 시점의
    스트림이 고정되어, 테스트가 `sys.stdout`/`sys.stderr` 를 갈아끼워도 옛
    스트림으로 나간다 — cp949 재현이 통째로 무력해진다.
    """
    stream = sys.stderr if err else sys.stdout
    try:
        print(msg, file=stream)
    except UnicodeEncodeError:
        enc = getattr(stream, "encoding", None) or "utf-8"
        print(msg.encode(enc, "replace").decode(enc, "replace"), file=stream)


def _warn(msg: str = "") -> None:
    """진단 한 줄 — **항상 표준오류로** 나간다.

    명령의 *답*(사람이 읽는 표나 `--json` 페이로드)은 stdout, 환경에 대한
    *진단*(프로파일 건너뜀·검증 생략 경고)은 stderr 로 가른다.

    회귀 배경: `knowledge --json` 이 `resolve_game` 을 타면서 프로파일 진단이
    JSON 앞 stdout 에 붙었고, `json.loads` 는 죽는데 rc 는 0 이었다 —
    호출자(인터뷰 모델)가 성공으로 읽는 최악의 모양이다.

    **`--json` 일 때만 옮기지 않고 항상 옮기는 이유.** 플래그를 조건으로 삼으면
    그 플래그를 `load_profiles` 까지 내려보내야 하고, 앞으로 생길 `--json`
    명령마다 같은 배선을 기억해야 한다 — 잊으면 실패가 조용하다(rc=0 + 깨진
    JSON). 채널로 가르면 배선이 없고, 사람은 터미널에서 두 스트림을 함께 보므로
    잃는 것도 없다. 리다이렉션(`> out.json`)에서도 진단은 화면에 그대로 남는다.

    cp949 보호는 `_p` 한 곳에만 둔다 — 여기서 다시 구현하면 두 벌이 갈라진다.
    """
    _p(msg, err=True)


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

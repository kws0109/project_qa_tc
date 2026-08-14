"""지식 모델의 도메인 타입.

슬롯은 "이 컨텐츠에 대해 알아야 할 항목 하나"다. 상태가 4단계인 것이 핵심이다 —
`EMPTY`(아직 안 물어봄)와 `NA`(해당 없음)를 구분하지 않으면, 사용자가 "이건 재화
안 써요"라고 답해도 도구가 계속 재화를 묻는다.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from enum import Enum

#: 빈칸으로 그려지는 문자의 유니코드 일반 범주.
#:
#: `Cc` 제어문자 · `Cf` 서식문자(제로폭 공백·BOM·제로폭 결합자) ·
#: `Zs`/`Zl`/`Zp` 구분자 · `Mn` 결합 표시(변이 선택자 U+FE0F, 홀로 있는 결합
#: 액센트 U+0301 …).
#:
#: **`Mn` 을 통째로 넣을 수 있는 이유는 :func:`is_blank` 가 `all()` 이기
#: 때문이다.** `가` + U+0301 에는 `가`(범주 `Lo`)가 있으므로 그 문자열은 여전히
#: 뜻을 나른다. `any()` 로 바꾸는 순간 액센트 붙은 정상 텍스트가 거부되기
#: 시작한다 — `test_is_blank_needs_every_character_to_be_blank` 가 그 리팩터링을
#: 깨뜨린다.
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Zs", "Zl", "Zp", "Mn"})

#: 범주는 "뜻이 있는" 쪽인데 화면에는 아무것도 그리지 않는 문자들.
#:
#: 범주만으로는 절대 걸러지지 않는다 — 한글 필러는 `Lo` 로 `가`·`漢` 과 같은
#: 범주이고, 점자 빈칸은 `So` 로 이모지와 같은 범주다. 그런데 한글 필러는
#: 한국어권에서 **빈 이름·간격 맞추기에 쓰는 표준 수단**이라, 한국 게임 QA 를
#: 다루는 이 도구에서는 이국적인 경계값이 아니라 흔한 붙여넣기다.
_INVISIBLE_CHARS = frozenset({
    "\u115f",   # HANGUL CHOSEONG FILLER (Lo)
    "\u1160",   # HANGUL JUNGSEONG FILLER (Lo)
    "\u3164",   # HANGUL FILLER (Lo)
    "\uffa0",   # HALFWIDTH HANGUL FILLER (Lo)
    "\u2800",   # BRAILLE PATTERN BLANK (So)
})


def is_blank(text: str) -> bool:
    """이 문자열에 **뜻을 나르는 문자가 하나도 없는가.**

    `not text.strip()` 으로는 부족하다. `str.strip()` 은 `isspace()` 인 문자만
    지우므로 제로폭 공백(U+200B) · BOM/제로폭 비분할 공백(U+FEFF) · 제로폭
    결합자(U+200D) · C0 제어문자는 **하나도 지우지 않는다.** 실측: `slot set
    ... --status filled --value <U+200B>` 가 `✓ cost = filled` rc=0 으로 통과해
    `tc plan` 이 그 계열을 생성 대상으로 계획했다 — 보이지 않는 문자 하나가
    "근거 없는 TC는 만들어지지 않는다" 를 뚫은 것이다.

    붙여넣기 텍스트의 BOM·제로폭 문자는 실제로 흔하고, 이 검사는 설계상
    **최후 방어선**이라 확률이 아니라 구멍의 유무로 심각도가 정해진다.

    범주만으로도 부족하다 (라운드 1d 실측). `slot set 필러 cost --status filled
    --value <U+3164>` (한글 필러) 는 범주가 `Lo` 라 범주 목록을 그대로 통과해
    rc=0 으로 기록됐고, `tc plan` 이 `재화 부족` 을 생성 대상으로 계획했다.
    그래서 범주 목록 옆에 `_INVISIBLE_CHARS` 코드포인트 목록이 함께 있다.

    **왜 블랙리스트를 유지하는가.** "뜻이 있는 범주" 화이트리스트로 뒤집는 것을
    검토하고 그만뒀다. 뒤집어도 이 구멍은 막히지 않기 때문이다 — 한글 필러는
    `Lo`, 점자 빈칸은 `So` 라 어떤 화이트리스트에도 들어갈 범주고, 결국
    `_INVISIBLE_CHARS` 와 똑같은 예외 목록을 화이트리스트 **안쪽에** 다시 파야
    한다. 남는 차이는 **모르는 문자를 만났을 때의 기본값** 하나뿐인데, 거기서는
    블랙리스트가 낫다: 기본 거부는 정당한 답변을 조용히 버리고 인터뷰를 세우지만
    (사용자는 왜 막혔는지 알 길이 없다), 기본 수용은 최악의 경우 근거 하나가
    헐거워질 뿐이고 그 값은 사람이 읽는 `--value` 로 남는다.

    **이 검사가 일부러 잡지 않는 것** (다음에 넓히는 사람을 위해):

    - `Mc`·`Me` — 자리를 차지하는/둘러싸는 결합 표시. 눈에 보이게 그려진다.
    - `Cs` 서로게이트 · `Co` 사용자 영역 — 게임 아이콘 폰트가 `Co` 를 실제로
      쓰고 그건 화면에 그려진다. `Cn`(미할당)은 두부(tofu)로 보인다.
    - `Lo`·`So` 안의 **아직 모르는** 빈칸 문자. 유니코드가 새 필러를 추가하면
      `_INVISIBLE_CHARS` 에 손으로 넣어야 한다 — 범주가 알려주지 않는다.
    - 보이기는 하는데 뜻이 없는 답변(`...`, `ㅁㅁㅁ`). 문자 종류의 문제가 아니라
      내용의 문제라 이 계층에서 판정하지 않는다.
    """
    return all(
        ch in _INVISIBLE_CHARS or unicodedata.category(ch) in _INVISIBLE_CATEGORIES
        for ch in text
    )


class SlotStatus(str, Enum):
    EMPTY = "empty"       # 아직 안 물어봄
    FILLED = "filled"     # 사용자가 답함
    UNKNOWN = "unknown"   # 사용자가 "모른다"고 답함
    NA = "na"             # 이 컨텐츠엔 해당 없음


#: 슬롯 상태 → 사용자에게 보일 한국어. **표시하는 모든 자리가 이것만 쓴다.**
#:
#: 예전에는 같은 매핑이 `gate.py`(거부 메시지) · `tc plan` · `tc list` ·
#: `tc_excel.py` 에 4벌 있었고 문구가 3가지로 갈렸다 — 한 세션 안에서 `tc plan` 은
#: "슬롯 비어 있음", `tc list` 는 "비어있음", 거부 메시지는 "슬롯이 비어 있음"
#: 이라고 말해 사용자에게는 세 개의 다른 상태처럼 읽혔다. 게다가 넷 다
#: `SlotStatus` 가 하나 늘면 `KeyError` 로 죽었다.
#:
#: 문구는 가장 설명이 필요한 자리(게이트의 거부 메시지)를 표준으로 삼았다.
#: 여기 두는 이유: 상태를 정의한 곳이 그 상태를 뭐라고 부르는지도 정의해야
#: 새 멤버를 추가하는 사람이 라벨을 같이 추가하게 된다.
SLOT_STATUS_LABEL: dict["SlotStatus", str] = {
    SlotStatus.EMPTY: "슬롯이 비어 있음",
    SlotStatus.FILLED: "사용자가 답함",
    SlotStatus.UNKNOWN: "사용자가 모른다고 답함",
    SlotStatus.NA: "해당 없음으로 표시됨",
}


@dataclass(frozen=True)
class SlotSpec:
    """슬롯의 정의. 상태를 갖지 않는 순수 스펙이다."""

    key: str
    prompt_hint: str
    tc_family: str


@dataclass
class Slot:
    """상태와 값을 가진 슬롯."""

    key: str
    prompt_hint: str
    tc_family: str
    status: SlotStatus = SlotStatus.EMPTY
    value: str = ""
    ord: int = 0

    @property
    def is_open(self) -> bool:
        """아직 물어볼 대상인가."""
        return self.status is SlotStatus.EMPTY

    @property
    def is_closed(self) -> bool:
        """다시 묻지 않아도 되는가. UNKNOWN·NA도 닫힌 것으로 본다."""
        return not self.is_open


@dataclass
class Content:
    """인터뷰 단위. 게임의 기능/모듈 하나."""

    name: str
    game: str
    types: list[str] = field(default_factory=list)

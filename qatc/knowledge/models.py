"""지식 모델의 도메인 타입.

슬롯은 "이 컨텐츠에 대해 알아야 할 항목 하나"다. 상태가 4단계인 것이 핵심이다 —
`EMPTY`(아직 안 물어봄)와 `NA`(해당 없음)를 구분하지 않으면, 사용자가 "이건 재화
안 써요"라고 답해도 도구가 계속 재화를 묻는다.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from enum import Enum

#: 뜻을 나르지 않는 문자의 유니코드 일반 범주.
#:
#: `Cc` 제어문자 · `Cf` 서식문자(제로폭 공백·BOM·제로폭 결합자) ·
#: `Zs`/`Zl`/`Zp` 구분자. 이 밖의 범주를 가진 문자는 무엇이든 뜻이 있다고 본다
#: (한글 `Lo`, 숫자 `Nd`, 기호 `Sm` …) — 화이트리스트가 아니라 블랙리스트인
#: 이유는, 모르는 문자가 나왔을 때 "근거 없음"이 아니라 "근거 있음"으로 기우는
#: 편이 사용자의 답변을 잃지 않기 때문이다.
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Zs", "Zl", "Zp"})


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
    """
    return all(unicodedata.category(ch) in _INVISIBLE_CATEGORIES for ch in text)


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

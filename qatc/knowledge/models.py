"""지식 모델의 도메인 타입.

슬롯은 "이 컨텐츠에 대해 알아야 할 항목 하나"다. 상태가 4단계인 것이 핵심이다 —
`EMPTY`(아직 안 물어봄)와 `NA`(해당 없음)를 구분하지 않으면, 사용자가 "이건 재화
안 써요"라고 답해도 도구가 계속 재화를 묻는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SlotStatus(str, Enum):
    EMPTY = "empty"       # 아직 안 물어봄
    FILLED = "filled"     # 사용자가 답함
    UNKNOWN = "unknown"   # 사용자가 "모른다"고 답함
    NA = "na"             # 이 컨텐츠엔 해당 없음


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

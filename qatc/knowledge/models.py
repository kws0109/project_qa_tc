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

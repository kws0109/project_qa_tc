"""TC 계열 게이트 — 이 모듈이 설계 전체의 안전장치다.

인터뷰어가 Claude Code 세션이므로 "빈 슬롯 계열은 만들지 않는다"를 프롬프트로만
부탁하면 지키다 말다 한다. 여기서 **생성 대상 계열을 코드가 계산하고**
(:func:`plan_families`) **대상이 아닌 계열을 코드가 거부해**
(:func:`validate_family`) 프롬프트가 규칙을 어겨도 결과물이 오염되지 않게 한다.

한 계열의 근거 슬롯이 여럿일 수 있다 (`constraints` 와 `편성.정원` 이 둘 다
'경계값'). **하나라도 filled면 그 계열은 생성 대상이다.**
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..models import Priority, TCKind
from .models import SLOT_STATUS_LABEL, Slot, SlotStatus

#: 계열 → (TC 종류, 기본 우선순위).
#: INTERRUPT 를 쓰는 계열이 기본에 없는 이유: 진술에서 통신 끊김·강제 종료가
#: 도출되는 경우가 없다. `slot add` 로 사용자가 추가하면 그때 쓴다.
FAMILY_META: dict[str, tuple[TCKind, Priority]] = {
    "정상 경로": (TCKind.HAPPY_PATH, Priority.HIGH),
    "진입 경로": (TCKind.HAPPY_PATH, Priority.HIGH),
    "결과 검증": (TCKind.HAPPY_PATH, Priority.HIGH),
    "경계값": (TCKind.BOUNDARY, Priority.MEDIUM),
    "재화 부족": (TCKind.EXCEPTION, Priority.MEDIUM),
    "실패 경로": (TCKind.EXCEPTION, Priority.MEDIUM),
    "미해금 접근": (TCKind.EXCEPTION, Priority.LOW),
    "미저장 이탈": (TCKind.REVERSE, Priority.MEDIUM),
    "요소 표시 확인": (TCKind.UIUX, Priority.LOW),
    "중단": (TCKind.INTERRUPT, Priority.MEDIUM),
}


@dataclass(frozen=True)
class FamilyPlan:
    """생성 대상 계열."""

    family: str
    slot_key: str
    prompt_hint: str
    kind: TCKind
    priority: Priority


@dataclass(frozen=True)
class FamilySkip:
    """생성 대상이 아닌 계열과 그 이유."""

    family: str
    slot_key: str
    prompt_hint: str
    status: SlotStatus


def plan_families(slots: Sequence[Slot]) -> tuple[list[FamilyPlan], list[FamilySkip]]:
    """슬롯 상태에서 만들 수 있는 계열과 제외된 계열을 계산한다.

    `tc_family` 가 빈 슬롯(`overview`)은 양쪽 어디에도 넣지 않는다 — 문맥일 뿐
    TC를 만들지 않는다.
    """
    first_filled: dict[str, Slot] = {}
    first_blocked: dict[str, Slot] = {}

    for slot in slots:
        if not slot.tc_family:
            continue
        if slot.status is SlotStatus.FILLED:
            first_filled.setdefault(slot.tc_family, slot)
        else:
            first_blocked.setdefault(slot.tc_family, slot)

    planned: list[FamilyPlan] = []
    for family, slot in first_filled.items():
        kind, priority = FAMILY_META.get(family, (TCKind.HAPPY_PATH, Priority.MEDIUM))
        planned.append(
            FamilyPlan(family, slot.key, slot.prompt_hint, kind, priority)
        )

    skipped = [
        FamilySkip(family, slot.key, slot.prompt_hint, slot.status)
        for family, slot in first_blocked.items()
        if family not in first_filled
    ]
    return planned, skipped


def validate_family(family: str, slots: Sequence[Slot]) -> FamilyPlan:
    """이 계열이 지금 생성 대상인지 검사한다.

    :raises ValueError: 대상이 아니면. 메시지에 막힌 슬롯 키와 다음 조치를 넣는다 —
        "안 됩니다"만 하면 호출자가 무엇을 채워야 할지 모른다.
    """
    planned, skipped = plan_families(slots)
    for p in planned:
        if p.family == family:
            return p

    for s in skipped:
        if s.family == family:
            reason = SLOT_STATUS_LABEL[s.status]
            raise ValueError(
                f"'{family}'은(는) 생성 대상이 아닙니다 "
                f"({s.slot_key} 슬롯: {reason}). "
                f"qatc tc plan 으로 대상 계열을 확인하세요."
            )

    raise ValueError(
        f"알 수 없는 계열: '{family}'. "
        f"qatc tc plan 으로 대상 계열을 확인하세요."
    )

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
from typing import Iterable, Sequence

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


def unregistered_family_reason(family: str) -> str:
    """등록되지 않은 계열을 왜 쓸 수 없는지 — 유효한 이름 목록까지 함께.

    `plan_families` · `validate_family` · `slot add` 가 모두 이 문구를 쓴다.
    셋이 갈라지면 같은 오타에 대해 세 가지 설명이 나온다.
    """
    return (f"등록되지 않은 계열 '{family}' — "
            f"정의된 계열: {', '.join(sorted(FAMILY_META))}")


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

    @property
    def reason(self) -> str:
        """사용자·모델에게 보일 제외 사유. **표시하는 모든 자리가 이것만 쓴다.**

        슬롯 상태가 사유인 경우(대부분)와 계열 이름 자체가 문제인 경우
        (미등록 계열)를 여기서 한 번에 구분한다. 미등록 계열은 슬롯이 FILLED
        여도 제외되므로 `SLOT_STATUS_LABEL[status]`("사용자가 답함")를 쓰면
        "답했는데 왜 안 만들어지지?" 라는 정반대의 설명이 된다.
        """
        if self.family not in FAMILY_META:
            return unregistered_family_reason(self.family)
        return SLOT_STATUS_LABEL[self.status]


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
    unregistered: list[Slot] = []
    for family, slot in first_filled.items():
        # 미등록 계열은 근거가 있어도 계획하지 않는다. 예전에는
        # `FAMILY_META.get(family, (HAPPY_PATH, MEDIUM))` 로 폴백해 오타 하나가
        # 조용히 `정상 경로` TC — 최종 xlsx 에서 가장 신뢰도 높은 칸 — 를 만들어
        # 냈고, 계열 이름은 그 칸에 보이지 않으니 아무도 눈치채지 못했다.
        # 폴백을 없앴으므로 `FAMILY_META[family]` 는 여기서 항상 성공한다.
        if family not in FAMILY_META:
            unregistered.append(slot)
            continue
        kind, priority = FAMILY_META[family]
        planned.append(
            FamilyPlan(family, slot.key, slot.prompt_hint, kind, priority)
        )

    skipped = [
        FamilySkip(family, slot.key, slot.prompt_hint, slot.status)
        for family, slot in first_blocked.items()
        if family not in first_filled
    ]
    skipped.extend(
        FamilySkip(slot.tc_family, slot.key, slot.prompt_hint, slot.status)
        for slot in unregistered
    )
    return planned, skipped


def withdrawn_families(slots: Sequence[Slot], tc_families: Iterable[str]) -> set[str]:
    """TC는 이미 있는데 지금은 근거가 없는 계열.

    인터뷰에서 **정정은 정상 동작이다.** "아 그거 재화 안 써요" 한 마디에
    `cost` 슬롯이 FILLED → NA 로 내려가고, 그 순간 이미 만든 `재화 부족` TC 는
    근거를 잃는다. 그래도 **지우지 않는다** — 사용자가 쌓아온 산출물을 도구가
    조용히 버리면 안 된다. 대신 여기서 계산한 계열을 `tc list` 와 xlsx 가
    `근거 철회됨` 으로 표시해, 같은 문서가 "TC가 있다"와 "TC가 없다"를
    동시에 말하는 상태를 없앤다.
    """
    live = {p.family for p in plan_families(slots)[0]}
    return {f for f in tc_families if f not in live}


def validate_family(family: str, slots: Sequence[Slot]) -> FamilyPlan:
    """이 계열이 지금 생성 대상인지 검사한다.

    :raises ValueError: 대상이 아니면. 메시지에 막힌 슬롯 키와 다음 조치를 넣는다 —
        "안 됩니다"만 하면 호출자가 무엇을 채워야 할지 모른다.
    """
    # 미등록 계열을 먼저 걸러낸다. plan_families 도 같은 것을 막지만, 여기서
    # 다시 보는 이유는 **사유가 다르기 때문**이다 — 아래 skipped 경로를 타면
    # "생성 대상이 아닙니다 (네트워크 슬롯: ...)" 가 되어 슬롯을 고치라고
    # 잘못 안내한다. 슬롯은 멀쩡하고 고칠 것은 계열 이름이다.
    if family not in FAMILY_META:
        raise ValueError(
            f"{unregistered_family_reason(family)}. "
            f"계열 이름을 고쳐 'qatc slot add' 로 다시 등록하거나 "
            f"qatc tc plan 으로 대상 계열을 확인하세요."
        )

    planned, skipped = plan_families(slots)
    for p in planned:
        if p.family == family:
            return p

    for s in skipped:
        if s.family == family:
            reason = s.reason
            raise ValueError(
                f"'{family}'은(는) 생성 대상이 아닙니다 "
                f"({s.slot_key} 슬롯: {reason}). "
                f"qatc tc plan 으로 대상 계열을 확인하세요."
            )

    raise ValueError(
        f"알 수 없는 계열: '{family}'. "
        f"qatc tc plan 으로 대상 계열을 확인하세요."
    )

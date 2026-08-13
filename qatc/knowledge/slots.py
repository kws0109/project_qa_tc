"""슬롯 세트 조립.

각 슬롯이 **어떤 TC 계열을 만드는지**를 정의에 못박는다. 이것이 "설명에 근거가
있는 예외만 만든다"를 코드로 강제하는 방법이다 — 슬롯이 비어 있으면 그 계열은
생성 대상에서 아예 빠진다 (:mod:`qatc.knowledge.gate` 참조).

유형별 슬롯 키에 `유형.` 접두사를 붙이는 이유는 기본 슬롯 키와 절대 충돌하지 않게
하기 위해서다. 기본 슬롯은 모든 컨텐츠에서 같은 의미여야 한다.
"""

from __future__ import annotations

from typing import Sequence

from .models import SlotSpec

#: 모든 컨텐츠에 공통인 슬롯. 순서가 곧 인터뷰의 기본 진행 순서다.
BASE_SLOTS: tuple[SlotSpec, ...] = (
    SlotSpec("overview", "이 컨텐츠가 무엇이고 플레이어가 왜 쓰는가", ""),
    SlotSpec("entry", "어디서 어떻게 들어가는가", "진입 경로"),
    SlotSpec("screen", "무엇이 보이고 무엇을 누를 수 있는가", "요소 표시 확인"),
    SlotSpec("core_action", "이 컨텐츠의 주 동작은 무엇인가", "정상 경로"),
    SlotSpec("result", "성공하면 무엇이 어디에 반영되는가", "결과 검증"),
    SlotSpec("constraints", "정원·상한·중복 제한이 있는가", "경계값"),
    SlotSpec("cost", "무엇을 소모하는가", "재화 부족"),
    SlotSpec("failure", "안 되는 경우와 그때의 피드백은", "실패 경로"),
    SlotSpec("exit", "언제 저장되는가, 취소할 수 있는가", "미저장 이탈"),
    SlotSpec("unlock", "언제부터 쓸 수 있는가", "미해금 접근"),
)

#: 유형별 추가 슬롯. 개괄을 듣고 판정한 유형에 따라 붙는다.
TYPE_SLOTS: dict[str, tuple[SlotSpec, ...]] = {
    "가챠": (
        SlotSpec("가챠.확률공개", "확률이 어디에 표시되는가", "요소 표시 확인"),
        SlotSpec("가챠.천장", "천장이 있는가, 몇 회인가", "경계값"),
        SlotSpec("가챠.픽업", "픽업 규칙은 어떻게 되는가", "결과 검증"),
        SlotSpec("가챠.연차", "10연 등 묶음 뽑기의 차이는", "정상 경로"),
    ),
    "편성": (
        SlotSpec("편성.정원", "몇 명/개까지 넣을 수 있는가", "경계값"),
        SlotSpec("편성.중복", "같은 대상을 두 번 넣을 수 있는가", "경계값"),
        SlotSpec("편성.프리셋", "프리셋을 몇 개 저장할 수 있는가", "경계값"),
        SlotSpec("편성.저장시점", "언제 실제로 적용되는가", "미저장 이탈"),
    ),
    "성장": (
        SlotSpec("성장.재료", "무엇을 재료로 쓰는가", "재화 부족"),
        SlotSpec("성장.성공률", "실패할 수 있는가, 확률은", "실패 경로"),
        SlotSpec("성장.실패손실", "실패하면 무엇을 잃는가", "실패 경로"),
        SlotSpec("성장.최대치", "최대 레벨/단계는", "경계값"),
    ),
    "던전": (
        SlotSpec("던전.구성", "층·웨이브 구성은 어떻게 되는가", "정상 경로"),
        SlotSpec("던전.실패페널티", "실패하면 무엇을 잃는가", "실패 경로"),
        SlotSpec("던전.재도전", "다시 도전할 수 있는가, 조건은", "정상 경로"),
        SlotSpec("던전.제한시간", "제한 시간이 있는가", "경계값"),
    ),
    "상점": (
        SlotSpec("상점.재고", "재고가 한정되는가", "경계값"),
        SlotSpec("상점.갱신", "언제 갱신되는가", "결과 검증"),
        SlotSpec("상점.한도", "구매 한도가 있는가", "경계값"),
        SlotSpec("상점.환불", "환불·되돌리기가 되는가", "미저장 이탈"),
    ),
    "임무": (
        SlotSpec("임무.수락", "수락 조건이 있는가", "미해금 접근"),
        SlotSpec("임무.진행판정", "진행이 어떻게 판정되는가", "결과 검증"),
        SlotSpec("임무.보상수령", "보상을 어떻게 받는가", "정상 경로"),
        SlotSpec("임무.만료", "만료가 있는가", "경계값"),
    ),
}

KNOWN_TYPES: tuple[str, ...] = tuple(TYPE_SLOTS)


def build_slot_set(types: Sequence[str]) -> list[SlotSpec]:
    """기본 슬롯과 유형별 슬롯을 합친다.

    키가 겹치면 **먼저 조립된 것을 남긴다.** 조립 순서가 `기본 → types 인자 순서`
    이므로 기본 슬롯이 항상 이기고, 유형 간에는 앞에 적힌 쪽이 이긴다.

    :raises ValueError: 모르는 유형이 들어오면. 조용히 무시하면 사용자는 자기가
        말한 유형의 슬롯이 왜 없는지 알 수 없다.
    """
    unknown = [t for t in types if t not in TYPE_SLOTS]
    if unknown:
        raise ValueError(
            f"알 수 없는 컨텐츠 유형: {', '.join(unknown)} "
            f"(사용 가능: {', '.join(KNOWN_TYPES)})"
        )

    out: list[SlotSpec] = []
    seen: set[str] = set()
    for spec in (*BASE_SLOTS, *(s for t in types for s in TYPE_SLOTS[t])):
        if spec.key in seen:
            continue
        seen.add(spec.key)
        out.append(spec)
    return out

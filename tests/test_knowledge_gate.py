import pytest

from qatc.knowledge.gate import FAMILY_META, plan_families, validate_family
from qatc.knowledge.models import Slot, SlotStatus
from qatc.knowledge.slots import BASE_SLOTS
from qatc.models import Priority, TCKind


def _slots(**statuses) -> list[Slot]:
    """BASE_SLOTS 를 바탕으로 지정한 키만 상태를 바꾼 슬롯 목록을 만든다."""
    out = []
    for i, spec in enumerate(BASE_SLOTS):
        st = statuses.get(spec.key, SlotStatus.EMPTY)
        out.append(Slot(spec.key, spec.prompt_hint, spec.tc_family, status=st, ord=i))
    return out


def test_every_base_family_has_meta():
    for spec in BASE_SLOTS:
        if spec.tc_family:
            assert spec.tc_family in FAMILY_META, spec.tc_family


def test_no_filled_slots_plans_nothing():
    planned, skipped = plan_families(_slots())
    assert planned == []
    assert {s.family for s in skipped} == {s.tc_family for s in BASE_SLOTS if s.tc_family}


def test_filled_slot_is_planned():
    planned, _ = plan_families(_slots(core_action=SlotStatus.FILLED))
    assert [p.family for p in planned] == ["정상 경로"]
    assert planned[0].slot_key == "core_action"
    assert planned[0].kind is TCKind.HAPPY_PATH
    assert planned[0].priority is Priority.HIGH


def test_empty_slot_family_is_skipped_with_status():
    _, skipped = plan_families(_slots(core_action=SlotStatus.FILLED))
    by_family = {s.family: s for s in skipped}
    assert by_family["재화 부족"].status is SlotStatus.EMPTY
    assert by_family["재화 부족"].slot_key == "cost"


def test_unknown_and_na_slots_are_skipped_not_planned():
    planned, skipped = plan_families(
        _slots(failure=SlotStatus.UNKNOWN, cost=SlotStatus.NA)
    )
    assert planned == []
    by_family = {s.family: s for s in skipped}
    assert by_family["실패 경로"].status is SlotStatus.UNKNOWN
    assert by_family["재화 부족"].status is SlotStatus.NA


def test_overview_never_appears_in_plan_or_skip():
    planned, skipped = plan_families(_slots(overview=SlotStatus.FILLED))
    assert all(p.slot_key != "overview" for p in planned)
    assert all(s.slot_key != "overview" for s in skipped)


def test_validate_family_accepts_planned_family():
    got = validate_family("정상 경로", _slots(core_action=SlotStatus.FILLED))
    assert got.family == "정상 경로"


def test_validate_family_rejects_empty_slot_family():
    with pytest.raises(ValueError) as exc:
        validate_family("재화 부족", _slots(core_action=SlotStatus.FILLED))
    msg = str(exc.value)
    assert "재화 부족" in msg
    assert "cost" in msg
    assert "tc plan" in msg


def test_validate_family_rejects_unknown_family_name():
    with pytest.raises(ValueError, match="알 수 없는 계열"):
        validate_family("없는계열", _slots(core_action=SlotStatus.FILLED))


def test_two_slots_sharing_a_family_plan_once():
    # 편성.정원 과 constraints 가 모두 '경계값' 계열이면 한 번만 계획된다
    slots = _slots(constraints=SlotStatus.FILLED)
    slots.append(Slot("편성.정원", "몇 명까지", "경계값", status=SlotStatus.FILLED, ord=99))
    planned, _ = plan_families(slots)
    assert [p.family for p in planned].count("경계값") == 1


def test_family_planned_if_any_source_slot_is_filled():
    slots = _slots()  # constraints 는 EMPTY
    slots.append(Slot("편성.정원", "몇 명까지", "경계값", status=SlotStatus.FILLED, ord=99))
    planned, skipped = plan_families(slots)
    assert "경계값" in [p.family for p in planned]
    assert "경계값" not in [s.family for s in skipped]

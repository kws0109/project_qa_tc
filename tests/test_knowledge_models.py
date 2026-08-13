from qatc.knowledge.models import (
    SLOT_STATUS_LABEL,
    Content,
    Slot,
    SlotSpec,
    SlotStatus,
)
from qatc.models import TCOrigin


def test_interview_origin_exists():
    assert TCOrigin.INTERVIEW.value == "인터뷰"
    assert TCOrigin("인터뷰") is TCOrigin.INTERVIEW


def test_existing_origin_values_unchanged():
    # export/excel.py 의 색 매핑과 기존 세션 DB가 이 문자열에 의존한다
    assert TCOrigin.RECORDED.value == "기록됨"
    assert TCOrigin.INFERRED.value == "추론됨"
    assert TCOrigin.USER.value == "사용자추가"


def test_empty_slot_is_open():
    s = Slot(key="cost", prompt_hint="무엇을 소모하는가", tc_family="재화 부족")
    assert s.status is SlotStatus.EMPTY
    assert s.is_open is True
    assert s.is_closed is False


def test_filled_unknown_na_are_all_closed():
    for st in (SlotStatus.FILLED, SlotStatus.UNKNOWN, SlotStatus.NA):
        s = Slot(key="cost", prompt_hint="h", tc_family="f", status=st)
        assert s.is_open is False, st
        assert s.is_closed is True, st


def test_slot_specs_with_same_key_differ_by_hint():
    a = SlotSpec(key="cost", prompt_hint="h1", tc_family="f1")
    b = SlotSpec(key="cost", prompt_hint="h2", tc_family="f2")
    assert a.key == b.key
    assert a != b  # 힌트가 다르면 다른 스펙


def test_content_holds_types():
    c = Content(name="파티편성", game="starrail", types=["편성"])
    assert c.types == ["편성"]


def test_every_slot_status_has_a_label():
    """`SlotStatus` 멤버가 늘면 라벨도 같이 늘어야 한다.

    예전에는 이 매핑이 4벌(gate 거부 메시지 · tc plan · tc list · tc_excel)로
    흩어져 있었고, **넷 다 새 멤버에서 `KeyError` 로 죽었다.** 게다가 상태를
    표시하는 자리마다 문구가 달라 한 세션 안에서 같은 상태가 세 이름으로 보였다.
    이제 한 곳이므로, 여기서 실패하면 표시하는 모든 자리가 같이 막힌다 —
    사용자가 export 도중에 KeyError 를 보는 것보다 테스트가 먼저 죽는 게 낫다.
    """
    for status in SlotStatus:
        assert status in SLOT_STATUS_LABEL, status
        assert SLOT_STATUS_LABEL[status].strip(), status


def test_slot_status_labels_are_distinct():
    """상태마다 다른 문구여야 한다 — 사유가 겹치면 거부 메시지가 이유를 못 준다."""
    labels = list(SLOT_STATUS_LABEL.values())
    assert len(set(labels)) == len(labels)

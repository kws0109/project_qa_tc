from qatc.knowledge.models import Content, Slot, SlotSpec, SlotStatus
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

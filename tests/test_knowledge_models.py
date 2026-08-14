import pytest
from conftest import INVISIBLE_IDS, INVISIBLE_VALUES

from qatc.knowledge.models import (
    SLOT_STATUS_LABEL,
    Content,
    Slot,
    SlotSpec,
    SlotStatus,
    is_blank,
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


# --- is_blank — 무엇이 근거가 되는가 (BL1 · 라운드 1d) ---------------------


@pytest.mark.parametrize("value, label", INVISIBLE_VALUES, ids=INVISIBLE_IDS)
def test_is_blank_sees_every_blank_looking_value(value, label):
    """`is_blank` 자체를 목록 전체로 직접 검사한다.

    저장소·CLI 테스트는 이 함수를 **거부 동작을 통해** 확인한다. 여기서는 함수를
    바로 부른다 — 두 호출 지점 중 하나가 사라져도 판정 자체의 계약은 남는다.
    """
    assert is_blank(value) is True, label


def test_is_blank_treats_the_empty_string_as_blank():
    """`slot init ""` 의 이름 가드가 이 값에 의존한다."""
    assert is_blank("") is True


@pytest.mark.parametrize("value, label", [
    ("\uac00\u0301", "한글 '가' + 결합 액센트 (Lo + Mn)"),
    ("e\u0301", "라틴 'e' + 결합 액센트 (Ll + Mn)"),
    ("\u2764\ufe0f", "하트 + 변이 선택자 — 내용이 있는 이모지 (So + Mn)"),
    ("\uac00\u200b", "한글 + 제로폭 공백"),
    ("\u3164\uac00", "한글 필러 + '가'"),
    ("\u28004", "점자 빈칸 + 숫자"),
], ids=["hangul-accent", "latin-accent", "emoji-vs16", "hangul-zwsp",
        "filler-hangul", "braille-digit"])
def test_is_blank_needs_every_character_to_be_blank(value, label):
    """**`all()` 이지 `any()` 가 아니다.** 이 구분이 결합 문자 전체를 지킨다.

    라운드 1d 가 `Mn`(결합 표시) 범주를 통째로 빈 문자로 넣을 수 있었던 유일한
    이유가 이것이다 — `가` + U+0301 에는 `가`(범주 `Lo`)가 있으므로 문자열은
    여전히 뜻을 나른다. 누가 나중에 "빈 문자가 **하나라도** 있으면 거부" 로
    바꾸면 액센트 붙은 라틴 문자·타이어·데바나가리 같은 **정상적인 답변이
    조용히 거부되기 시작한다** (그리고 인터뷰는 사용자가 무엇을 잘못했는지
    모른 채 멈춘다). 그 리팩터링을 여기서 깨뜨린다.

    같은 이유로 `\ufe0f`(변이 선택자)는 홀로 있을 때만 빈 문자다 — 이모지에
    붙어 있으면 그 이모지가 답변의 내용이다.
    """
    assert is_blank(value) is False, label


def test_a_combining_mark_is_blank_alone_and_meaningful_on_a_base_letter():
    """위 대칭을 한 자리에서 못박는다 — 셋이 함께여야 계약이 성립한다."""
    assert is_blank("\u0301") is True          # 홀로 있는 결합 액센트: 뜻이 없다
    assert is_blank("\uac00") is False         # 한글 한 글자: 뜻이 있다
    assert is_blank("\uac00\u0301") is False   # 둘을 붙이면 뜻이 남는다

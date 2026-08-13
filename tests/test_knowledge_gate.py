import pytest

from qatc.knowledge.gate import FAMILY_META, plan_families, validate_family
from qatc.knowledge.models import Slot, SlotStatus
from qatc.knowledge.slots import BASE_SLOTS, TYPE_SLOTS
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


def test_every_type_family_has_meta():
    # 미등록 계열은 plan_families 가 계획하지 않고 skipped 로 보낸다.
    # TYPE_SLOTS 가 FAMILY_META 에 없는 계열을 쓰면 그 유형의 슬롯을 아무리
    # 채워도 TC가 나오지 않으므로, BASE_SLOTS 뿐 아니라 여기도 검사해야 한다.
    for specs in TYPE_SLOTS.values():
        for spec in specs:
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


def test_validate_family_rejects_unknown_status_slot_with_distinct_reason():
    with pytest.raises(ValueError) as exc:
        validate_family(
            "실패 경로",
            _slots(core_action=SlotStatus.FILLED, failure=SlotStatus.UNKNOWN),
        )
    msg = str(exc.value)
    assert "실패 경로" in msg
    assert "failure" in msg
    assert "모른다고 답함" in msg
    assert "해당 없음" not in msg


def test_validate_family_rejects_na_status_slot_with_distinct_reason():
    with pytest.raises(ValueError) as exc:
        validate_family(
            "재화 부족",
            _slots(core_action=SlotStatus.FILLED, cost=SlotStatus.NA),
        )
    msg = str(exc.value)
    assert "재화 부족" in msg
    assert "cost" in msg
    assert "해당 없음" in msg
    assert "모른다고 답함" not in msg


def test_validate_family_rejects_registered_family_with_no_slot():
    """등록은 됐지만 이 컨텐츠에 근거 슬롯이 아예 없는 계열.

    `중단` 은 `FAMILY_META` 에 있으나 어떤 기본·유형 슬롯도 쓰지 않는다
    (`slot add` 로만 생긴다). 미등록 계열과는 다른 상황이라 문구도 다르다.
    """
    with pytest.raises(ValueError, match="알 수 없는 계열"):
        validate_family("중단", _slots(core_action=SlotStatus.FILLED))


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


def test_family_planned_when_filled_slot_comes_before_empty_sibling():
    # constraints(FILLED) 가 먼저, 편성.정원(EMPTY) 가 나중에 나오는 순서.
    # "하나라도 filled면 대상"이 아니라 "마지막 슬롯이 이긴다"로 잘못
    # 구현하면 이 순서에서 깨진다 — 위 두 테스트는 FILLED가 항상 마지막에
    # 오므로 그 버그를 잡지 못한다.
    slots = _slots(constraints=SlotStatus.FILLED)
    slots.append(Slot("편성.정원", "몇 명까지", "경계값", status=SlotStatus.EMPTY, ord=99))
    planned, skipped = plan_families(slots)
    assert "경계값" in [p.family for p in planned]
    assert "경계값" not in [s.family for s in skipped]


def test_first_filled_slot_wins_as_family_representative():
    # constraints 와 편성.정원 이 둘 다 FILLED 일 때, 대표 슬롯은 먼저
    # 채워진(순서상 먼저 나오는) constraints 여야 한다 — setdefault 는
    # "최초 등록"이 이긴다. setdefault 를 평범한 대입(dict[key] = slot)으로
    # 바꾸면 "마지막이 이긴다"가 되어 대표가 편성.정원 으로 뒤바뀐다.
    slots = _slots(constraints=SlotStatus.FILLED)
    slots.append(Slot("편성.정원", "몇 명까지", "경계값", status=SlotStatus.FILLED, ord=99))
    planned, _ = plan_families(slots)
    rep = next(p for p in planned if p.family == "경계값")
    assert rep.slot_key == "constraints"


def _unregistered() -> list[Slot]:
    """등록되지 않은 계열(`중단` 의 오타)을 근거로 가진 슬롯 하나."""
    return [Slot("네트워크", "통신이 끊기면", "중단됨", status=SlotStatus.FILLED, ord=0)]


def test_unregistered_family_is_never_planned():
    """`FAMILY_META` 에 없는 계열은 근거가 있어도 계획하지 않는다.

    예전에는 `FAMILY_META.get(family, (HAPPY_PATH, MEDIUM))` 폴백을 타
    **미등록 계열이 조용히 `정상 경로` TC로 둔갑**했다 — 최종 xlsx 에서 가장
    신뢰도 높은 칸이고, 오타(`중단됨`)는 거기서 보이지 않는다. `slot add` 의
    CLI 검증은 우회 가능하므로(저장소 API 직접 호출, 검증 이전에 들어간 DB)
    정책이 있는 게이트에서 막는다.
    """
    planned, skipped = plan_families(_unregistered())
    assert planned == []
    reasons = {s.family: s.reason for s in skipped}
    assert "중단됨" in reasons
    assert "등록되지 않은 계열" in reasons["중단됨"]
    assert "정상 경로" in reasons["중단됨"]      # 유효한 계열 목록을 알려준다


def test_validate_family_refuses_unregistered_family():
    """`tc add` 도 같은 이유로 거부한다 — `tc plan` 과 어긋나면 안 된다.

    거부 사유가 "근거가 없다"가 아니라 **"계열 이름이 등록돼 있지 않다"** 여야
    한다. 슬롯은 실제로 채워져 있으므로, 막힌 슬롯을 가리키는 문구
    (`생성 대상이 아닙니다 (네트워크 슬롯: ...)`)는 고쳐야 할 곳을 잘못 짚는다.
    """
    with pytest.raises(ValueError) as exc:
        validate_family("중단됨", _unregistered())
    msg = str(exc.value)
    assert "등록되지 않은 계열" in msg
    assert "중단됨" in msg
    assert "정상 경로" in msg                     # 유효한 계열 목록
    assert "생성 대상이 아닙니다" not in msg      # 근거 부족이 아니라 이름 문제다
    assert "tc plan" in msg                       # 다음 조치


def test_registered_interrupt_family_still_reaches_its_meta():
    """`중단` 은 `slot add` 로 도달하라고 FAMILY_META 에 남겨둔 계열이다.

    미등록 계열을 막으면서 이 경로까지 막으면 `slot add` 의 존재 이유가 사라진다.
    """
    slots = [Slot("네트워크", "통신이 끊기면", "중단", status=SlotStatus.FILLED, ord=0)]
    planned, skipped = plan_families(slots)
    assert [p.family for p in planned] == ["중단"]
    assert planned[0].kind is TCKind.INTERRUPT
    assert planned[0].priority is Priority.MEDIUM
    assert skipped == []


def test_first_blocked_slot_wins_as_family_representative():
    """막힌 쪽 대표 슬롯도 "먼저 나온 것이 이긴다" 여야 한다 (뮤테이션 M02).

    위 test_first_filled_slot_wins_as_family_representative 의 거울상이다.
    FILLED 쪽은 봉인돼 있었지만 blocked 쪽은 `first_blocked.setdefault` 를
    평범한 대입으로 바꿔도 109개가 전부 통과했다.

    한 계열에 막힌 슬롯이 둘 이상일 때 이 규칙이 뒤집히면, 사용자에게 보이는
    **슬롯 이름과 사유가 함께** 바뀐다 — `tc plan` 의 제외 목록, `tc list` 의
    미확인 리포트, xlsx 의 `미확인 항목` 시트, 그리고 게이트 거부 메시지가
    "constraints 가 비어 있음" 대신 "편성.정원 이 해당 없음" 이라고 말하게 된다.
    사유가 바뀌면 사용자가 해야 할 다음 행동도 달라진다.
    """
    slots = _slots()  # constraints 는 EMPTY 이고 편성.정원 보다 먼저 나온다
    slots.append(Slot("편성.정원", "몇 명까지", "경계값", status=SlotStatus.NA, ord=99))
    _, skipped = plan_families(slots)

    rep = next(s for s in skipped if s.family == "경계값")
    assert rep.slot_key == "constraints"
    assert rep.status is SlotStatus.EMPTY      # 사유도 첫 슬롯의 것이어야 한다


def test_blocked_representative_reason_reaches_the_refusal_message():
    """대표 슬롯이 바뀌면 거부 메시지의 사유 문구까지 바뀐다는 것을 고정한다."""
    slots = _slots()
    slots.append(Slot("편성.정원", "몇 명까지", "경계값", status=SlotStatus.NA, ord=99))
    with pytest.raises(ValueError) as exc:
        validate_family("경계값", slots)
    msg = str(exc.value)
    assert "constraints" in msg
    assert "슬롯이 비어 있음" in msg
    assert "해당 없음으로 표시됨" not in msg

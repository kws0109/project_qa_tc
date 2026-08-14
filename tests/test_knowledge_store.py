import pytest

from conftest import INVISIBLE_IDS, INVISIBLE_VALUES
from qatc.knowledge.models import SlotStatus
from qatc.knowledge.slots import BASE_SLOTS
from qatc.knowledge.store import KnowledgeStore


@pytest.fixture()
def store(tmp_path):
    with KnowledgeStore(tmp_path / "starrail.db") as s:
        yield s


def test_init_content_creates_base_slots(store):
    store.init_content("파티편성", game="starrail", types=[])
    keys = [s.key for s in store.slots("파티편성")]
    assert keys == [s.key for s in BASE_SLOTS]


def test_init_content_with_type_adds_type_slots(store):
    store.init_content("파티편성", game="starrail", types=["편성"])
    keys = {s.key for s in store.slots("파티편성")}
    assert "편성.정원" in keys
    assert "core_action" in keys


def test_slots_start_empty(store):
    store.init_content("파티편성", game="starrail", types=[])
    assert all(s.status is SlotStatus.EMPTY for s in store.slots("파티편성"))
    assert all(s.value == "" for s in store.slots("파티편성"))


def test_set_slot_persists_value_and_status(store):
    store.init_content("파티편성", game="starrail", types=[])
    store.set_slot("파티편성", "constraints", SlotStatus.FILLED, "최대 4명, 중복 불가")
    got = {s.key: s for s in store.slots("파티편성")}["constraints"]
    assert got.status is SlotStatus.FILLED
    assert got.value == "최대 4명, 중복 불가"


def test_set_slot_unknown_key_raises_with_available_keys(store):
    store.init_content("파티편성", game="starrail", types=[])
    with pytest.raises(KeyError) as exc:
        store.set_slot("파티편성", "없는키", SlotStatus.FILLED, "값")
    assert "core_action" in str(exc.value)


def test_set_slot_on_missing_content_raises(store):
    with pytest.raises(KeyError, match="파티편성"):
        store.set_slot("파티편성", "core_action", SlotStatus.FILLED, "값")


def test_init_content_again_preserves_existing_values(store):
    store.init_content("파티편성", game="starrail", types=[])
    store.set_slot("파티편성", "core_action", SlotStatus.FILLED, "파티를 짠다")
    store.init_content("파티편성", game="starrail", types=["편성"])

    slots = {s.key: s for s in store.slots("파티편성")}
    assert slots["core_action"].value == "파티를 짠다"
    assert slots["core_action"].status is SlotStatus.FILLED
    assert "편성.정원" in slots
    assert slots["편성.정원"].status is SlotStatus.EMPTY


def test_init_content_again_accumulates_types(store):
    store.init_content("워프", game="starrail", types=["가챠"])
    c = store.init_content("워프", game="starrail", types=["상점"])
    assert set(c.types) == {"가챠", "상점"}


def test_add_slot_appends_at_end(store):
    store.init_content("파티편성", game="starrail", types=[])
    before = len(store.slots("파티편성"))
    store.add_slot("파티편성", "네트워크", "통신이 끊기면", "중단")
    after = store.slots("파티편성")
    assert len(after) == before + 1
    assert after[-1].key == "네트워크"
    assert after[-1].tc_family == "중단"


def test_add_slot_duplicate_key_raises(store):
    store.init_content("파티편성", game="starrail", types=[])
    with pytest.raises(KeyError, match="이미 있습니다"):
        store.add_slot("파티편성", "core_action", "힌트", "계열")


def test_list_contents(store):
    store.init_content("파티편성", game="starrail", types=[])
    store.init_content("워프", game="starrail", types=["가챠"])
    assert {c.name for c in store.list_contents()} == {"파티편성", "워프"}


def test_reopen_keeps_data(tmp_path):
    p = tmp_path / "starrail.db"
    with KnowledgeStore(p) as s:
        s.init_content("파티편성", game="starrail", types=[])
        s.set_slot("파티편성", "cost", SlotStatus.NA, "")
    with KnowledgeStore(p) as s:
        got = {x.key: x for x in s.slots("파티편성")}["cost"]
        assert got.status is SlotStatus.NA


def test_set_slot_rejects_filled_with_empty_value(store):
    """FILLED 는 근거다. 빈 근거는 근거가 아니다.

    게이트(`plan_families`)는 `status is FILLED` 만 보고 계열을 계획하므로,
    값이 빈 FILLED 슬롯이 통과하면 "근거 없는 TC" 가 실제로 만들어진다.
    CLI 밖 호출자(게이트와 같은 계층)도 막아야 하므로 저장소에서 검증한다.
    """
    store.init_content("파티편성", game="starrail", types=[])
    with pytest.raises(ValueError) as exc:
        store.set_slot("파티편성", "core_action", SlotStatus.FILLED, "")
    assert "core_action" in str(exc.value)
    # 거부됐으면 상태가 그대로여야 한다
    got = {s.key: s for s in store.slots("파티편성")}["core_action"]
    assert got.status is SlotStatus.EMPTY


def test_set_slot_rejects_filled_with_whitespace_only_value(store):
    """공백만 있는 값도 근거가 아니다 — `--value "  "` 로 우회되면 안 된다."""
    store.init_content("파티편성", game="starrail", types=[])
    with pytest.raises(ValueError):
        store.set_slot("파티편성", "core_action", SlotStatus.FILLED, "   \t ")
    got = {s.key: s for s in store.slots("파티편성")}["core_action"]
    assert got.status is SlotStatus.EMPTY


def test_set_slot_allows_empty_value_for_non_filled_statuses(store):
    """UNKNOWN·NA·EMPTY 는 값이 없는 것이 정상이다.

    "모른다"·"해당 없음" 은 근거가 아니라 근거의 부재를 기록하는 상태다.
    """
    store.init_content("파티편성", game="starrail", types=[])
    for status in (SlotStatus.UNKNOWN, SlotStatus.NA, SlotStatus.EMPTY):
        slot = store.set_slot("파티편성", "core_action", status, "")
        assert slot.status is status


# --- 보이지 않는 문자 (BL1) ----------------------------------------------

@pytest.mark.parametrize("value, label", INVISIBLE_VALUES, ids=INVISIBLE_IDS)
def test_set_slot_rejects_filled_with_invisible_only_value(store, value, label):
    """보이지 않는 문자만 있는 값은 근거가 아니다.

    `str.strip()` 은 `isspace()` 인 문자만 지운다 — 제로폭 공백·BOM·제로폭
    결합자·C0 제어문자는 **하나도 지우지 않는다**. 그래서 라운드 1a 가 막은
    "빈 근거" 구멍이 제로폭 공백 하나로 그대로 다시 열렸다 (실측:
    `slot set ... --status filled --value <U+200B>` → `✓ cost = filled` rc=0,
    이어서 `tc plan` 의 `planned` 에 `재화 부족` 이 등장).

    이 검사는 설계상 **최후 방어선**이라, 입력이 흔한지 여부가 아니라 구멍이
    뚫려 있는지 여부로 심각도가 정해진다. 붙여넣기 텍스트의 BOM·제로폭 문자는
    실제로 흔하다.
    """
    store.init_content("파티편성", game="starrail", types=[])
    with pytest.raises(ValueError) as exc:
        store.set_slot("파티편성", "core_action", SlotStatus.FILLED, value)
    assert "core_action" in str(exc.value), label
    # 거부됐으면 상태가 그대로여야 한다 — 근거로 인정되면 그 계열이 열린다
    got = {s.key: s for s in store.slots("파티편성")}["core_action"]
    assert got.status is SlotStatus.EMPTY, label


@pytest.mark.parametrize("value", ["파티를 짠다", "4", "0", "a", "-", "\u00b1"],
                         ids=["korean", "digit", "zero", "ascii", "hyphen", "plusminus"])
def test_set_slot_accepts_short_but_real_values(store, value):
    """반대쪽 경계 — 짧아도 뜻이 있는 값은 그대로 근거다.

    보이지 않는 문자를 막는다고 `"4"`(정원 4명) 같은 정당한 한 글자 답변까지
    막으면 인터뷰가 진행되지 않는다. 기준은 길이가 아니라 **문자의 종류**다.
    """
    store.init_content("파티편성", game="starrail", types=[])
    slot = store.set_slot("파티편성", "core_action", SlotStatus.FILLED, value)
    assert slot.status is SlotStatus.FILLED
    assert slot.value == value


@pytest.mark.parametrize("value", ["\u200b", "\ufeff", "\x07", "   "],
                         ids=["zwsp", "bom", "bel", "spaces"])
def test_set_slot_still_accepts_invisible_value_for_non_filled_statuses(store, value):
    """UNKNOWN·NA·EMPTY 는 값을 근거로 쓰지 않으므로 검사 대상이 아니다.

    보이지 않는 문자 검사를 상태 구분 없이 걸면 값이 없는 것이 정상인 호출까지
    막혀 인터뷰가 멈춘다.
    """
    store.init_content("파티편성", game="starrail", types=[])
    for status in (SlotStatus.UNKNOWN, SlotStatus.NA, SlotStatus.EMPTY):
        slot = store.set_slot("파티편성", "core_action", status, value)
        assert slot.status is status

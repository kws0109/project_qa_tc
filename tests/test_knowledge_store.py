import pytest

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

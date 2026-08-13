import pytest

from qatc.knowledge.slots import BASE_SLOTS, KNOWN_TYPES, TYPE_SLOTS, build_slot_set


def test_base_slots_have_ten_entries_with_unique_keys():
    keys = [s.key for s in BASE_SLOTS]
    assert len(keys) == 10
    assert len(set(keys)) == 10


def test_base_slots_cover_required_keys():
    keys = {s.key for s in BASE_SLOTS}
    assert keys == {
        "overview", "unlock", "entry", "screen", "core_action",
        "constraints", "cost", "failure", "result", "exit",
    }


def test_overview_produces_no_tc_family():
    overview = next(s for s in BASE_SLOTS if s.key == "overview")
    assert overview.tc_family == ""


def test_every_non_overview_base_slot_has_a_family():
    for s in BASE_SLOTS:
        if s.key == "overview":
            continue
        assert s.tc_family, s.key


def test_no_types_returns_base_only():
    got = build_slot_set([])
    assert [s.key for s in got] == [s.key for s in BASE_SLOTS]


def test_type_slots_are_appended_after_base():
    got = build_slot_set(["편성"])
    assert len(got) > len(BASE_SLOTS)
    assert [s.key for s in got[: len(BASE_SLOTS)]] == [s.key for s in BASE_SLOTS]


def test_duplicate_key_across_types_keeps_the_first():
    # 가챠와 상점이 모두 재화 관련 슬롯을 요구할 때 앞에 적힌 쪽이 이긴다
    got_a = build_slot_set(["가챠", "상점"])
    got_b = build_slot_set(["상점", "가챠"])
    keys_a = [s.key for s in got_a]
    keys_b = [s.key for s in got_b]
    assert len(keys_a) == len(set(keys_a))
    assert len(keys_b) == len(set(keys_b))
    assert set(keys_a) == set(keys_b)


def test_base_slot_wins_over_type_slot_with_same_key():
    # 어떤 유형도 base 키를 덮어쓸 수 없다
    base_hint = {s.key: s.prompt_hint for s in BASE_SLOTS}
    for t in KNOWN_TYPES:
        for s in build_slot_set([t]):
            if s.key in base_hint:
                assert s.prompt_hint == base_hint[s.key], (t, s.key)


def test_unknown_type_raises():
    with pytest.raises(ValueError, match="알 수 없는 컨텐츠 유형"):
        build_slot_set(["로그라이크"])


def test_all_known_types_build_without_error():
    for t in KNOWN_TYPES:
        assert build_slot_set([t])
    assert build_slot_set(list(KNOWN_TYPES))


def test_type_slot_keys_are_prefixed_to_avoid_base_collision():
    for t, specs in TYPE_SLOTS.items():
        for s in specs:
            assert s.key.startswith(f"{t}."), (t, s.key)

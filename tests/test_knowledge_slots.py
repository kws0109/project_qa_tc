import pytest

from qatc.knowledge.slots import BASE_SLOTS, KNOWN_TYPES, SlotSpec, TYPE_SLOTS, build_slot_set


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


def test_first_assembled_slot_wins_on_synthetic_key_collision(monkeypatch):
    # 실제 배포 데이터에는 유형별 키가 전부 "유형." 접두사를 강제로 달고
    # 있어서 base와도, 유형끼리도 진짜 키 충돌이 존재하지 않는다. 그래서
    # test_duplicate_key_across_types_keeps_the_first 와
    # test_base_slot_wins_over_type_slot_with_same_key 는 "먼저 조립된 것이
    # 이긴다" 규칙이 "나중 것이 이긴다"로 뒤집혀도 실패하지 않는다 — 애초에
    # 겹치는 키가 없어 dedup 분기 자체를 타지 않기 때문이다. 여기서는
    # monkeypatch 로 TYPE_SLOTS 에 인위적인 충돌을 주입해 그 분기를 실제로
    # 태워서 검증한다.

    # 유형 vs 유형 충돌: 인자에 먼저 적힌 유형이 이겨야 한다. 양쪽 순서를
    # 모두 확인해야 "이긴다" 판정이 인자 순서를 실제로 따르는지 알 수 있다.
    fake_a = (SlotSpec("충돌.키", "A 유형의 힌트", "정상 경로"),)
    fake_b = (SlotSpec("충돌.키", "B 유형의 힌트", "정상 경로"),)
    monkeypatch.setitem(TYPE_SLOTS, "_fake_a", fake_a)
    monkeypatch.setitem(TYPE_SLOTS, "_fake_b", fake_b)

    won_ab = next(s for s in build_slot_set(["_fake_a", "_fake_b"]) if s.key == "충돌.키")
    assert won_ab.prompt_hint == "A 유형의 힌트"

    won_ba = next(s for s in build_slot_set(["_fake_b", "_fake_a"]) if s.key == "충돌.키")
    assert won_ba.prompt_hint == "B 유형의 힌트"

    # base vs 유형 충돌: base는 항상 먼저 조립되므로 어떤 유형을 넣어도
    # base 쪽 prompt_hint가 살아남아야 한다.
    fake_type = (SlotSpec("core_action", "가짜 유형의 core_action 힌트", "정상 경로"),)
    monkeypatch.setitem(TYPE_SLOTS, "_fake_type", fake_type)

    base_hint = next(s for s in BASE_SLOTS if s.key == "core_action").prompt_hint
    won_base = next(s for s in build_slot_set(["_fake_type"]) if s.key == "core_action")
    assert won_base.prompt_hint == base_hint

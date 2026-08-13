import pytest

from qatc.knowledge.store import KnowledgeStore, testcase_hash
from qatc.models import Priority, TCOrigin


@pytest.fixture()
def store(tmp_path):
    with KnowledgeStore(tmp_path / "starrail.db") as s:
        s.init_content("파티편성", game="starrail", types=[])
        yield s


def test_hash_ignores_id_and_origin(make_tc):
    a = make_tc(id="tc_1", origin=TCOrigin.INTERVIEW)
    b = make_tc(id="tc_2", origin=TCOrigin.INFERRED)
    assert testcase_hash(a) == testcase_hash(b)


def test_hash_changes_with_title(make_tc):
    assert testcase_hash(make_tc(title="가")) != testcase_hash(make_tc(title="나"))


def test_hash_changes_with_expected(make_tc):
    a = make_tc()
    b = make_tc()
    b.expected = ["다른 결과"]
    assert testcase_hash(a) != testcase_hash(b)


def test_add_testcase_assigns_id(make_tc, store):
    got = store.add_testcase("파티편성", "정상 경로", make_tc(), ["core_action"])
    assert got.id.startswith("tc_")


def test_add_testcase_preserves_supplied_id(make_tc, store):
    got = store.add_testcase(
        "파티편성", "정상 경로", make_tc(id="tc_fixed_id"), ["core_action"]
    )
    assert got.id == "tc_fixed_id"
    assert [t.id for t in store.testcases("파티편성")] == ["tc_fixed_id"]


def test_testcases_roundtrip(make_tc, store):
    store.add_testcase("파티편성", "정상 경로", make_tc(title="원본"), ["core_action"])
    got = store.testcases("파티편성")
    assert len(got) == 1
    assert got[0].title == "원본"
    assert got[0].origin is TCOrigin.INTERVIEW
    assert got[0].steps == ["파티 적용을 누른다"]
    assert got[0].priority is Priority.HIGH


def test_testcases_filter_by_family(make_tc, store):
    store.add_testcase("파티편성", "정상 경로", make_tc(title="A"), ["core_action"])
    store.add_testcase("파티편성", "경계값", make_tc(title="B"), ["constraints"])
    assert [t.title for t in store.testcases("파티편성", family="경계값")] == ["B"]


def test_replace_generated_replaces_untouched_case(make_tc, store):
    store.add_testcase("파티편성", "정상 경로", make_tc(title="구버전"), ["core_action"])
    added, kept = store.replace_generated(
        "파티편성", "정상 경로", [make_tc(title="신버전")], ["core_action"]
    )
    assert (added, kept) == (1, 0)
    assert [t.title for t in store.testcases("파티편성")] == ["신버전"]


def test_replace_generated_preserves_user_edited_case(make_tc, store):
    saved = store.add_testcase("파티편성", "정상 경로", make_tc(title="원본"), ["core_action"])
    # 사용자가 손댄 것처럼 본문만 바꿔 다시 저장 (해시는 그대로 두어 불일치를 만든다)
    saved.title = "사람이 고침"
    store.update_testcase_row(saved)

    added, kept = store.replace_generated(
        "파티편성", "정상 경로", [make_tc(title="신버전")], ["core_action"]
    )
    assert kept == 1
    titles = {t.title for t in store.testcases("파티편성")}
    assert "사람이 고침" in titles
    assert "신버전" in titles


def test_replace_generated_preserves_user_origin_case(make_tc, store):
    store.add_testcase(
        "파티편성", "정상 경로", make_tc(title="사람이 추가", origin=TCOrigin.USER), []
    )
    added, kept = store.replace_generated(
        "파티편성", "정상 경로", [make_tc(title="신버전")], ["core_action"]
    )
    assert kept == 1
    assert "사람이 추가" in {t.title for t in store.testcases("파티편성")}


def test_replace_generated_only_touches_named_family(make_tc, store):
    store.add_testcase("파티편성", "경계값", make_tc(title="경계값 것"), ["constraints"])
    store.replace_generated("파티편성", "정상 경로", [make_tc(title="신규")], ["core_action"])
    assert "경계값 것" in {t.title for t in store.testcases("파티편성")}

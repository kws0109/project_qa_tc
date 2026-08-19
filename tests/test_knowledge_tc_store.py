import pytest

from qatc.knowledge.store import KnowledgeStore, testcase_hash
from qatc.models import Priority, TCOrigin


@pytest.fixture()
def store(tmp_path):
    with KnowledgeStore(tmp_path / "starrail.db") as s:
        s.init_content("파티편성", game="starrail", types=[], code="PARTY")
        yield s


def test_hash_ignores_id_and_origin(make_tc):
    a = make_tc(id="tc_1", origin=TCOrigin.INTERVIEW)
    b = make_tc(id="tc_2", origin=TCOrigin.INFERRED)
    assert testcase_hash(a) == testcase_hash(b)


def test_hash_changes_with_title(make_tc):
    assert testcase_hash(make_tc(title="가")) != testcase_hash(make_tc(title="나"))


def test_hash_changes_with_category_sub(make_tc):
    """`title` 이 항상 비므로 소분류가 형제 케이스를 가르는 유일한 이름이다.

    소분류만 고친 편집이 해시에 안 보이면 `replace_generated` 가 다음
    재생성 때 "사람이 안 고쳤다"로 오판해 그 편집을 지운다.
    """
    a = make_tc(category_sub="로그인 기록 없음")
    b = make_tc(category_sub="로그인 기록 있음")
    assert testcase_hash(a) != testcase_hash(b)


def test_hash_changes_with_expected(make_tc):
    a = make_tc()
    b = make_tc()
    b.expected = ["다른 결과"]
    assert testcase_hash(a) != testcase_hash(b)


def test_add_testcase_assigns_id(make_tc, store):
    got = store.add_testcase("파티편성", "정상 경로", make_tc(), ["core_action"])
    assert got.id == "TC_PARTY_001"


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


def test_stored_testcases_come_back_with_their_family(tmp_path, make_tc):
    """`family` 는 `row` JSON 이 아니라 컬럼이 진실이다.

    옛 행에는 `row` 안에 `family` 키가 아예 없다. 컬럼에서 채우면 옛 행도
    그대로 읽히고, 컬럼과 `row` 가 어긋날 여지도 없어진다.
    """
    from qatc.knowledge.store import KnowledgeStore

    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("c", game="g", types=[], code="C1")
        st.add_testcase("c", "경계값", make_tc(title="T"), ["constraints"])
        got = st.testcases("c")
    assert [t.family for t in got] == ["경계값"]


def test_testcases_filter_by_family(make_tc, store):
    store.add_testcase("파티편성", "정상 경로", make_tc(title="A"), ["core_action"])
    store.add_testcase("파티편성", "경계값", make_tc(title="B"), ["constraints"])
    assert [t.title for t in store.testcases("파티편성", family="경계값")] == ["B"]


def test_replace_generated_replaces_untouched_case(make_tc, store):
    store.add_testcase("파티편성", "정상 경로", make_tc(title="구버전"), ["core_action"])
    added, kept, deleted = store.replace_generated(
        "파티편성", "정상 경로", [make_tc(title="신버전")], ["core_action"]
    )
    assert (added, kept, deleted) == (1, 0, 1)
    assert [t.title for t in store.testcases("파티편성")] == ["신버전"]


def test_replace_generated_preserves_user_edited_case(make_tc, store):
    saved = store.add_testcase("파티편성", "정상 경로", make_tc(title="원본"), ["core_action"])
    # 사용자가 손댄 것처럼 본문만 바꿔 다시 저장 (해시는 그대로 두어 불일치를 만든다)
    saved.title = "사람이 고침"
    store.update_testcase_row(saved)

    added, kept, deleted = store.replace_generated(
        "파티편성", "정상 경로", [make_tc(title="신버전")], ["core_action"]
    )
    assert kept == 1
    assert deleted == 0          # 보존한 것은 지운 것에 세지 않는다
    titles = {t.title for t in store.testcases("파티편성")}
    assert "사람이 고침" in titles
    assert "신버전" in titles


def test_replace_generated_preserves_user_origin_case(make_tc, store):
    store.add_testcase(
        "파티편성", "정상 경로", make_tc(title="사람이 추가", origin=TCOrigin.USER), []
    )
    added, kept, deleted = store.replace_generated(
        "파티편성", "정상 경로", [make_tc(title="신버전")], ["core_action"]
    )
    assert kept == 1
    assert deleted == 0
    assert "사람이 추가" in {t.title for t in store.testcases("파티편성")}


def test_replace_generated_reports_how_many_it_deleted(make_tc, store):
    """지운 개수가 **반환값으로 나와야** CLI 가 그 사실을 말할 수 있다 (BL2).

    교체 자체는 의도된 설계다 — 이 메서드의 존재 이유가 "한 계열의 생성분을
    갈아끼우는 것"이고, `generated_hash` 는 그 교체가 사람 손댄 TC를 건드리지
    않게 하려고 있다. 결함은 교체가 **말없이** 일어난다는 것이었다:
    반환이 `(added, kept)` 뿐이라 `cli_knowledge.py` 에는 삭제를 보고할 방법이
    아예 없었고, 실측에서 TC 2건이 `✓ … 1건 저장` rc=0 과 함께 증발했다.
    """
    # 지울 것이 없는 첫 호출은 0 이어야 한다 — CLI 가 이 값으로 ⚠ 경고를 켠다.
    # 예전에는 이 단언이 `..._reports_zero_deleted_on_first_call` 이라는 별도
    # 테스트였는데 **고유하게 잡는 변이가 없었다**: 그것을 죽이는 변이
    # (`deleted` 초기값을 1 로, 반환을 `len(cases)` 로)는 각각 이 테스트를
    # 포함해 7건·6건을 함께 죽였다. 같은 자리에서 0 → 4 를 잇달아 보이면
    # "지운 수" 가 정말 지운 것을 세는지가 오히려 더 잘 드러난다.
    assert store.replace_generated(
        "파티편성", "정상 경로", [make_tc(title="첫 배치")], ["core_action"]
    ) == (1, 0, 0)

    for title in ("A", "B", "C"):
        store.add_testcase("파티편성", "정상 경로", make_tc(title=title), ["core_action"])
    # 다른 계열은 이 호출과 무관하다 — 삭제 수에 섞이면 안 된다
    store.add_testcase("파티편성", "경계값", make_tc(title="경계값 것"), ["constraints"])

    added, kept, deleted = store.replace_generated(
        "파티편성", "정상 경로", [make_tc(title="신규")], ["core_action"]
    )
    assert (added, kept, deleted) == (1, 0, 4)      # 첫 배치 + A · B · C
    assert {t.title for t in store.testcases("파티편성")} == {"신규", "경계값 것"}


def test_replace_generated_only_touches_named_family(make_tc, store):
    store.add_testcase("파티편성", "경계값", make_tc(title="경계값 것"), ["constraints"])
    store.replace_generated("파티편성", "정상 경로", [make_tc(title="신규")], ["core_action"])
    assert "경계값 것" in {t.title for t in store.testcases("파티편성")}


def test_replace_generated_rolls_back_deletes_if_insertion_fails_partway(make_tc, store, monkeypatch):
    """삭제와 삽입은 한 트랜잭션이어야 한다 (Bug A, 원자성).

    Bug A·B 의 거절은 둘 다 어떤 행도 지우기 전에 일어나므로, 그 두 경로
    자체는 이 원자성을 시험하지 못한다 — 이 테스트는 삽입 루프 한가운데서
    나는 (코드 없음·중복 외의) **다른** 예외로 원자성만 따로 겨냥한다.
    실제로 이런 예외가 나면(예: 예상 못 한 버그), 원자성이 없던 예전
    구현은 이미 지운 행만 확정해 계열을 비운 채로 끝났다.
    """
    store.add_testcase("파티편성", "정상 경로", make_tc(category_sub="A"), ["core_action"])
    store.add_testcase("파티편성", "정상 경로", make_tc(category_sub="B"), ["core_action"])

    calls = {"n": 0}
    original = type(store)._insert_testcase

    def flaky(self, content, family, tc, slot_keys):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("삽입 도중의 임의의 실패")
        return original(self, content, family, tc, slot_keys)

    monkeypatch.setattr(type(store), "_insert_testcase", flaky)

    with pytest.raises(RuntimeError):
        store.replace_generated(
            "파티편성", "정상 경로",
            [make_tc(category_sub="C"), make_tc(category_sub="D")],
            ["core_action"],
        )

    # 두 기존 행을 지우는 DELETE 는 실제로 실행됐지만(원자성이 없다면
    # 여기서 사라졌을 것이다), 트랜잭션이 롤백돼 그대로 남아 있어야 한다.
    assert {t.category_sub for t in store.testcases("파티편성")} == {"A", "B"}


def test_replace_generated_rolls_back_deletes_on_keyboard_interrupt(make_tc, store, monkeypatch):
    """`except Exception` 은 `KeyboardInterrupt` 를 놓친다.

    `KeyboardInterrupt` 와 `SystemExit` 은 `Exception` 이 아니라 `BaseException`
    만 상속한다 — 삽입 루프 한가운데서 Ctrl-C 가 들어오면, `except Exception`
    으로 잡는 옛 구현은 그 예외를 그냥 통과시켜 롤백을 건너뛴다. 그러면 이미
    실행된 DELETE 가 `KnowledgeStore.close()` 의 무조건 커밋을 타고 확정되어
    계열이 통째로 비워진 채 남는다. 바로 위 테스트(`RuntimeError`)는
    `Exception` 계열만 겨냥하므로 이 경로를 잡지 못한다 — `except Exception`
    으로 되돌려도 그 테스트는 초록이다.
    """
    store.add_testcase("파티편성", "정상 경로", make_tc(category_sub="A"), ["core_action"])
    store.add_testcase("파티편성", "정상 경로", make_tc(category_sub="B"), ["core_action"])

    calls = {"n": 0}
    original = type(store)._insert_testcase

    def flaky(self, content, family, tc, slot_keys):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt()
        return original(self, content, family, tc, slot_keys)

    monkeypatch.setattr(type(store), "_insert_testcase", flaky)

    with pytest.raises(KeyboardInterrupt):
        store.replace_generated(
            "파티편성", "정상 경로",
            [make_tc(category_sub="C"), make_tc(category_sub="D")],
            ["core_action"],
        )

    # KeyboardInterrupt 여도 DELETE 가 롤백돼 기존 두 행이 살아 있어야 한다.
    assert {t.category_sub for t in store.testcases("파티편성")} == {"A", "B"}

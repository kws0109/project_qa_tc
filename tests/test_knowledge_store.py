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
    """재실행은 기본 슬롯이든 **유형 접두 슬롯이든** 채워진 답을 건드리지 않는다.

    유형 슬롯을 따로 확인하는 이유 — 기본 슬롯(`core_action`)만 보면 "유형
    슬롯의 값만 비운다" 는 회귀를 아무도 보지 못한다. 실측으로 확인했다:
    `init_content` 끝에 `UPDATE slots SET status='empty', value='' WHERE key
    LIKE '%.%'` 한 줄을 넣어도 전체 스위트가 초록이었다. 슬롯 **개수** 를 세는
    검사는 값이 비는 것을 볼 수 없기 때문이다.

    이건 실제로 밟는 경로다 — SKILL.md 1단계는 대화에서 새 유형이 드러날 때마다
    `slot init --types` 를 다시 부르라고 지시하므로, 두 번째·세 번째 호출이 앞서
    받은 답을 지우면 인터뷰가 통째로 되감긴다.
    """
    store.init_content("파티편성", game="starrail", types=[])
    store.set_slot("파티편성", "core_action", SlotStatus.FILLED, "파티를 짠다")
    store.init_content("파티편성", game="starrail", types=["편성"])

    slots = {s.key: s for s in store.slots("파티편성")}
    assert slots["core_action"].value == "파티를 짠다"
    assert slots["core_action"].status is SlotStatus.FILLED
    assert "편성.정원" in slots
    assert slots["편성.정원"].status is SlotStatus.EMPTY

    # 유형 슬롯을 채운 뒤 또 다른 유형을 덧붙인다 — 그 답도 그대로 남아야 한다
    store.set_slot("파티편성", "편성.정원", SlotStatus.FILLED, "최대 4명")
    store.init_content("파티편성", game="starrail", types=["성장"])

    slots = {s.key: s for s in store.slots("파티편성")}
    assert slots["편성.정원"].value == "최대 4명", "재실행이 유형 슬롯의 답을 지웠다"
    assert slots["편성.정원"].status is SlotStatus.FILLED
    assert slots["core_action"].value == "파티를 짠다"
    assert "성장.재료" in slots               # 새 유형은 그래도 붙는다


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

    **`line-separator`(U+2028) 는 이 설명의 예외다.** 목록에서 그것만
    `isspace()` 라 `strip()` 가드도 이미 잡았다 — `is_blank` 를
    `not text.strip()` 로 되돌리면 61건이 깨지는데 이 파라미터는 초록으로
    남는다 (실측). 목록에 둔 이유는 가드가 **통째로** 사라진 경우를 여전히
    잡기 때문이다.

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


@pytest.mark.parametrize("value", ["파티를 짠다", "4", "0", "a", "-", "\u00b1",
                                   "\uac00\u0301", "e\u0301", "\u2764\ufe0f", "\u28004"],
                         ids=["korean", "digit", "zero", "ascii", "hyphen", "plusminus",
                              "hangul-accent", "latin-accent", "emoji-vs16", "braille-digit"])
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


# --- 컨텐츠 코드와 TC ID ---------------------------------------------------


def test_content_code_is_stored_and_returned(tmp_path):
    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("로그인", game="g", types=[], code="LOGIN")
        assert st.content_code("로그인") == "LOGIN"


def test_a_second_init_without_a_code_keeps_the_existing_one(tmp_path):
    """`slot init` 재실행은 유형만 덧붙이는 기존 용법이 그대로 살아야 한다."""
    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("로그인", game="g", types=[], code="LOGIN")
        st.init_content("로그인", game="g", types=["편성"])
        assert st.content_code("로그인") == "LOGIN"


def test_init_content_on_existing_content_leaves_types_untouched_when_the_code_is_taken(tmp_path):
    """기존 컨텐츠에 다른 컨텐츠가 이미 쓰는 코드를 주면, 실패한 명령이
    `types` 확장까지 되돌려야 한다 (부분 커밋 회귀).

    `init_content` 는 기존 컨텐츠 분기에서 `types` UPDATE 를 먼저 실행하고
    코드 중복 검사(`set_content_code`)를 나중에 했다. `KnowledgeStore.close()`
    는 예외와 무관하게 항상 커밋하므로, 코드 검사가 실패해 `init_content` 가
    예외를 던져도 그 앞의 `types` UPDATE 는 이미 실행돼 있어 그대로 커밋된다
    — 실패한 명령(rc!=0)인데 `types` 만 조용히 바뀐 채로 남는다.
    """
    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("로그인보상", game="g", types=[], code="LOGIN")   # LOGIN 선점
        st.init_content("로그인", game="g", types=["가챠"])               # 코드 없음
        before = sorted(st.get_content("로그인").types)

        with pytest.raises(ValueError):
            st.init_content("로그인", game="g", types=["편성"], code="LOGIN")

        after = sorted(st.get_content("로그인").types)
        assert after == before, "실패한 명령인데 types 가 바뀌었습니다 (부분 커밋)"
        assert st.content_code("로그인") == ""


def test_testcase_ids_follow_the_code_and_increase(tmp_path, make_tc):
    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("로그인", game="g", types=[], code="LOGIN")
        for n in range(3):
            tc = make_tc(title=f"T{n}")
            tc.category_sub = f"케이스{n}"
            st.add_testcase("로그인", "경계값", tc, ["constraints"])
        got = sorted(t.id for t in st.testcases("로그인"))
    assert got == ["TC_LOGIN_001", "TC_LOGIN_002", "TC_LOGIN_003"]


def test_a_number_is_never_reused_after_a_delete(tmp_path, make_tc):
    """지워진 번호를 다시 쓰면 버그 리포트가 가리키던 번호가 엉뚱한 TC 를
    가리킨다. 비워 두는 편이 옳다."""
    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("로그인", game="g", types=[], code="LOGIN")
        for n in range(2):
            tc = make_tc(title=f"T{n}")
            tc.category_sub = f"케이스{n}"
            st.add_testcase("로그인", "경계값", tc, ["constraints"])
        st.replace_generated("로그인", "경계값", [], ["constraints"])
        fresh = make_tc(title="새것")
        fresh.category_sub = "새 케이스"
        st.add_testcase("로그인", "경계값", fresh, ["constraints"])
        assert [t.id for t in st.testcases("로그인")] == ["TC_LOGIN_003"]


def test_the_same_case_keeps_its_number_across_a_regeneration(tmp_path, make_tc):
    """같은 `(중분류, 소분류)` 면 같은 TC 로 보고 번호를 물려준다.

    소분류가 케이스 이름이 되면서 이 대조가 가능해졌다. 이것이 없으면 한
    계열을 다시 만들 때마다 그 계열의 모든 ID 가 갈린다.
    """
    def case(text):
        tc = make_tc(title=text)
        tc.category_minor, tc.category_sub = "신규 계정 연동", "비밀번호 불일치"
        return tc

    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("로그인", game="g", types=[], code="LOGIN")
        st.add_testcase("로그인", "경계값", case("처음"), ["constraints"])
        first = st.testcases("로그인")[0].id
        st.replace_generated("로그인", "경계값", [case("다시 쓴 본문")], ["constraints"])
        assert [t.id for t in st.testcases("로그인")] == [first]


def test_adding_a_testcase_without_a_code_is_refused(tmp_path, make_tc):
    """코드가 없으면 ID 를 지어내지 않고 거절한다 — `TC_C01_001` 같은 기계
    이름보다 다음 조치가 적힌 오류가 낫다."""
    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("로그인", game="g", types=[])
        with pytest.raises(KeyError) as e:
            st.add_testcase("로그인", "경계값", make_tc(), ["constraints"])
    assert "--code" in str(e.value.args[0])


def test_replace_generated_without_a_code_refuses_before_deleting_existing_rows(tmp_path, make_tc):
    """코드 없는 컨텐츠에 `replace_generated` 를 다시 부르면, 델리트가 시작
    되기 전에 거절해야 한다 (Bug A).

    위 테스트(`test_adding_a_testcase_without_a_code_is_refused`)는 **빈**
    컨텐츠로 `add_testcase` 를 직접 불러 이 순서를 검증하지 못한다 —
    `replace_generated` 의 삭제 루프를 아예 지나가지 않기 때문이다. 실측
    재현: 마스터 시절 DB는 `_ensure_code_column` 이 `code=''` 로 백필하고,
    거기서 `tc add` 를 다시 부르면 이 브랜치의 `_next_tc_id` 가 삽입 루프
    한가운데서 `KeyError` 를 던졌다 — 그런데 그 시점엔 이미 기존 2건이
    DELETE + COMMIT 으로 지워진 뒤였다(재현: `['tc_old0','tc_old1']` →
    `[]`, 삽입 0건, rc=1). 여기서는 기존 행이 그대로 남아 있어야 한다.
    """
    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("파티편성", game="g", types=[])  # --code 없음
        old0 = make_tc(id="tc_old0", category_sub="케이스0")
        old1 = make_tc(id="tc_old1", category_sub="케이스1")
        st.add_testcase("파티편성", "정상 경로", old0, ["core_action"])
        st.add_testcase("파티편성", "정상 경로", old1, ["core_action"])

        new_case = make_tc(category_sub="새 케이스")  # id 없음 → 코드가 있어야 발급 가능
        with pytest.raises(KeyError) as e:
            st.replace_generated("파티편성", "정상 경로", [new_case], ["core_action"])
        assert "--code" in str(e.value.args[0])

        # 거절됐으니 기존 두 건이 그대로 살아 있어야 한다 — 지워진 뒤에
        # 실패한 것이 아니라, 지우기 전에 실패한 것이다.
        assert sorted(t.id for t in st.testcases("파티편성")) == ["tc_old0", "tc_old1"]


def test_replace_generated_refuses_a_batch_with_duplicate_minor_sub_pairs(tmp_path, make_tc):
    """배치 안에서 (중분류, 소분류) 가 겹치면 조용히 번호를 물려주지 않고
    거절한다 (Bug B).

    겹치는 두 케이스가 기존 행의 id 를 물려받으면(`inherited`), 나중 것이
    `INSERT OR REPLACE` 로 앞의 것을 지운다 — 그런데 반환값은 여전히
    `len(cases)` 라 `tc add` 는 "2건 저장" rc=0 을 찍고 실제로는 1건만 남는다.
    """
    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("파티편성", game="g", types=[], code="PARTY")
        # 이미 있는 행 하나 — 물려줄 id 가 있어야 실제 충돌(덮어쓰기)까지
        # 재현된다. 없어도 거절은 마찬가지로 일어나야 하므로(표에서 구별이
        # 안 되는 문제 자체는 기존 행 유무와 무관), 이 조건 없이도 아래
        # 어서션은 성립한다.
        st.add_testcase("파티편성", "정상 경로",
                         make_tc(category_sub="중복 케이스"), ["core_action"])

        dup_a = make_tc(category_sub="중복 케이스", title="본문A")
        dup_b = make_tc(category_sub="중복 케이스", title="본문B")
        with pytest.raises(ValueError) as e:
            st.replace_generated("파티편성", "정상 경로", [dup_a, dup_b], ["core_action"])
        assert "중복 케이스" in str(e.value)

        # 거절됐으니 원래 있던 한 건이 그대로 남아 있어야 한다.
        assert [t.category_sub for t in st.testcases("파티편성")] == ["중복 케이스"]


def test_replace_generated_accepts_same_sub_under_different_minor_in_one_batch(tmp_path, make_tc):
    """거절 기준은 (중분류, 소분류) **조합**이지 소분류 하나만이 아니다.

    소분류만 보는 (더 좁은) 키로 몰래 좁혀도 위 거절 테스트는 여전히 초록이다
    — 그 테스트는 중분류까지 같은 경우만 배치하기 때문이다. 이 테스트는
    반대쪽 경계를 고정한다: 소분류가 같아도 중분류가 다르면 서로 다른 TC 이므로
    거절되지 않고 **둘 다 저장돼야** 한다.
    """
    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("파티편성", game="g", types=[], code="PARTY")
        a = make_tc(category_sub="입력 오류")
        a.category_minor = "로그인"
        b = make_tc(category_sub="입력 오류")
        b.category_minor = "회원가입"

        added, kept, deleted = st.replace_generated(
            "파티편성", "정상 경로", [a, b], ["core_action"]
        )
        assert added == 2

        pairs = {(t.category_minor, t.category_sub) for t in st.testcases("파티편성")}
        assert pairs == {("로그인", "입력 오류"), ("회원가입", "입력 오류")}


def test_an_old_database_without_the_code_column_still_opens(tmp_path):
    """첫 실사용으로 만들어진 DB 에는 `contents.code` 가 없다."""
    import sqlite3

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE contents (name TEXT PRIMARY KEY, game TEXT NOT NULL,"
                " types TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL)")
    con.execute("INSERT INTO contents VALUES ('로그인','g','[]','2026-01-01')")
    con.commit()
    con.close()

    with KnowledgeStore(path) as st:
        assert st.content_code("로그인") == ""
        st.set_content_code("로그인", "LOGIN")
        assert st.content_code("로그인") == "LOGIN"


def test_set_content_code_refuses_a_code_already_owned_by_another_content(tmp_path):
    """`apply_reclassification` 처럼 `set_content_code` 를 직접 부르는 호출자도
    이 검사를 피해갈 수 없어야 한다 (Bug C).

    실측 재현: `로그인보상`이 먼저 `slot init --code LOGIN` 으로 LOGIN 을
    선점한다 — 그 시점엔 `로그인`이 아직 코드가 없어(`codes_in_use()` 는
    `code != ''` 만 본다) 충돌로 잡히지 않는다. 이후 마이그레이션이
    `set_content_code(로그인, LOGIN)` 을 그대로 밀어붙이면, 두 컨텐츠가 같은
    코드를 갖게 되고 독립된 `tc_seq` 카운터가 같은 `TC_LOGIN_NNN` 을 내놓아
    나중에 저장된 쪽이 먼저 것을 지운다.
    """
    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("로그인보상", game="g", types=[], code="LOGIN")
        st.init_content("로그인", game="g", types=[])
        assert st.content_code("로그인") == ""

        with pytest.raises(ValueError) as e:
            st.set_content_code("로그인", "LOGIN")
        assert "로그인보상" in str(e.value)
        assert st.content_code("로그인") == ""   # 거절됐으니 그대로여야 한다


def test_set_content_code_refuses_a_malformed_code(tmp_path):
    """형식 검사도 `cmd_slot_init` 뿐 아니라 저장소 자신이 한다 (Bug C)."""
    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("로그인", game="g", types=[])
        with pytest.raises(ValueError) as e:
            st.set_content_code("로그인", "log-in")
        assert "영문 대문자" in str(e.value)


def test_set_content_code_refuses_an_unknown_content(tmp_path):
    """존재하지 않는 컨텐츠에는 코드를 달 수 없다 (Bug C)."""
    with KnowledgeStore(tmp_path / "g.db") as st:
        with pytest.raises(KeyError):
            st.set_content_code("없는컨텐츠", "LOGIN")


def test_set_content_code_refuses_changing_an_already_established_code(tmp_path):
    """이미 확정된 코드를 다른 값으로 바꾸는 것은 `set_content_code` 를 직접
    부르는 호출자도 거절당해야 한다.

    `cmd_slot_init` 은 같은 규칙을 CLI 층에서만 봤다(`test_cli_slot.py` 의
    `test_changing_the_code_of_an_existing_content_is_refused`) — 그런데
    `apply_reclassification` 같은 직접 호출자는 그 CLI 검사를 아예 거치지
    않는다. 여기서 막지 않으면 이미 발급된 `TC_<코드>_<번호>` 가 가리키던
    컨텐츠가 조용히 바뀐다.
    """
    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("로그인", game="g", types=[], code="LOGIN")

        with pytest.raises(ValueError) as e:
            st.set_content_code("로그인", "SIGNIN")
        msg = str(e.value)
        assert "로그인" in msg and "LOGIN" in msg and "SIGNIN" in msg
        assert st.content_code("로그인") == "LOGIN"   # 거절됐으니 그대로여야 한다


def test_set_content_code_allows_setting_an_empty_code_even_with_old_shaped_testcases(tmp_path, make_tc):
    """코드가 비어 있는 컨텐츠에 처음 코드를 다는 것은, 코드 없이 발급된 옛
    `tc_<hex>` 모양의 TC 가 이미 있어도 막지 않는다.

    이것이 바로 승인된 로그인 재분류 마이그레이션이 밟는 경로다 — `로그인`
    은 코드 없이(빈 문자열) 옛 TC 23건을 갖고 있다가 `LOGIN` 을 처음 받는다.
    새 guard(이미 확정된 코드를 못 바꾸게 막는 규칙)가 "코드 없음"까지
    "확정된 코드" 로 잘못 취급하면 이 마이그레이션 자체가 막힌다.
    """
    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("로그인", game="g", types=[])
        assert st.content_code("로그인") == ""
        st.add_testcase("로그인", "경계값", make_tc(id="tc_old_shape"), [])

        st.set_content_code("로그인", "LOGIN")   # 거절되면 안 된다

        assert st.content_code("로그인") == "LOGIN"
        assert "tc_old_shape" in {t.id for t in st.testcases("로그인")}


def test_set_content_code_allows_reapplying_the_same_code(tmp_path):
    """같은 코드를 다시 다는 것(멱등 재실행)은 "코드가 바뀐다" 가 아니다 —
    마이그레이션이 끊긴 뒤 다시 돌 때 이 경로를 밟는다."""
    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("로그인", game="g", types=[], code="LOGIN")

        st.set_content_code("로그인", "LOGIN")   # 거절되면 안 된다

        assert st.content_code("로그인") == "LOGIN"


def test_codes_in_use_reports_all_owners_when_a_code_is_already_duplicated(tmp_path):
    """이미 벌어진 중복(이 가드가 생기기 전 데이터 등)을 진단할 때, 소유자를
    하나만 골라 보여주면 나머지 소유자가 안 보인다 — 여기서는 저수준 SQL로
    그 상태를 직접 재현한다(`set_content_code` 는 이제 이 상태를 만들지
    못하게 막으므로).
    """
    import sqlite3

    path = tmp_path / "g.db"
    with KnowledgeStore(path) as st:
        st.init_content("로그인", game="g", types=[])
        st.init_content("로그인보상", game="g", types=[])

    con = sqlite3.connect(path)
    con.execute("UPDATE contents SET code = 'LOGIN' WHERE name IN ('로그인', '로그인보상')")
    con.commit()
    con.close()

    with KnowledgeStore(path) as st:
        owners = st.codes_in_use()
    # `"로그인" in owners["LOGIN"]` 처럼 부분 문자열로 검사하면 안 된다 —
    # "로그인" 은 "로그인보상" 의 접두어라, 소유자가 '로그인보상' 하나뿐인
    # (틀린) 옛 구현도 이 검사를 우연히 통과한다(실측: 이 부분 문자열 검사로
    # 바꾸면 옛 구현으로 되돌려도 이 테스트가 초록이다). 콤마로 나눠 집합으로
    # 비교해야 "두 소유자 모두 보인다" 를 정확히 고정한다.
    assert set(owners["LOGIN"].split(", ")) == {"로그인", "로그인보상"}

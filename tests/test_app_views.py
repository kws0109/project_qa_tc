"""SQLite → JSON. 앱의 왼쪽·오른쪽 패널이 읽는 모양."""

import json

import pytest

from qatc.app.views import ContentNotFound, content_detail, tree
from qatc.config import AppConfig
from qatc.knowledge.models import SlotStatus
from qatc.knowledge.store import KnowledgeStore
from qatc.models import Priority, TCKind, TCOrigin, TestCase


@pytest.fixture()
def cfg(tmp_path):
    """스크래치 지식 루트만 보는 설정."""
    return AppConfig(knowledge_root=str(tmp_path / "k"),
                     profiles_dir=str(tmp_path / "p"))


def _tc(title="TC", family="정상 경로"):
    return TestCase(id="", category_minor=family, family=family, title=title,
                    steps=["1"], expected=["e"],
                    priority=Priority.HIGH, kind=TCKind.HAPPY_PATH,
                    origin=TCOrigin.INTERVIEW)


def _seed(cfg, game="starrail", name="파티편성"):
    with KnowledgeStore(cfg.knowledge_path / f"{game}.db") as st:
        st.init_content(name, game=game, types=["편성"])
        st.set_slot(name, "core_action", SlotStatus.FILLED, "파티를 편성한다")
        st.set_slot(name, "cost", SlotStatus.UNKNOWN)
        st.add_testcase(name, "정상 경로", _tc(), ["core_action"])
    return name


def test_family_is_read_from_the_column_not_from_category_minor(cfg):
    """계열은 `category_minor` 가 아니라 DB 의 `family` 컬럼에서 온다.

    이 둘은 지금 같은 값이지만 곧 갈라진다. 갈라진 뒤에도 트리·철회 판정이
    맞으려면 소비자가 **지금** 옮겨져 있어야 한다. 그래서 두 값이 다른 TC 를
    일부러 만들어, `category_minor` 를 계열로 착각하는 코드를 실패시킨다.
    """
    _seed(cfg)
    with KnowledgeStore(cfg.knowledge_path / "starrail.db") as st:
        tc = _tc(title="비활성 유지", family="경계값")
        tc.category_minor = "신규 계정 연동"      # 계열이 아니라 메뉴 이름
        st.add_testcase("파티편성", "경계값", tc, ["constraints"])

    got = [t for t in st_testcases(cfg) if t.title == "비활성 유지"][0]
    assert got.family == "경계값"
    assert got.category_minor == "신규 계정 연동"


def st_testcases(cfg):
    with KnowledgeStore(cfg.knowledge_path / "starrail.db") as st:
        return st.testcases("파티편성")


def test_the_tree_counts_by_family_not_by_category_minor(cfg):
    """트리의 계열별 TC 개수가 메뉴 이름으로 세어지면 안 된다."""
    _seed(cfg)
    with KnowledgeStore(cfg.knowledge_path / "starrail.db") as st:
        tc = _tc(title="비활성 유지", family="경계값")
        tc.category_minor = "신규 계정 연동"
        st.add_testcase("파티편성", "경계값", tc, ["constraints"])

    fams = {f["family"]: f for f in tree(cfg)["games"][0]["contents"][0]["families"]}
    assert fams["경계값"]["tc_count"] == 1, "계열이 아니라 중분류로 셌습니다"


def test_withdrawal_is_judged_by_family_not_by_category_minor(cfg):
    """근거 철회 판정도 계열 단위다 — 중분류로 판정하면 엉뚱한 TC 가 표시된다."""
    _seed(cfg)
    with KnowledgeStore(cfg.knowledge_path / "starrail.db") as st:
        tc = _tc(title="비활성 유지", family="경계값")
        tc.category_minor = "신규 계정 연동"
        st.add_testcase("파티편성", "경계값", tc, ["constraints"])
        st.set_slot("파티편성", "constraints", SlotStatus.EMPTY)   # 근거를 다시 연다

    detail = content_detail(cfg, "starrail", "파티편성")
    row = [t for t in detail["testcases"] if t["title"] == "비활성 유지"][0]
    assert row["withdrawn"] is True


def test_tree_lists_games_and_contents(cfg):
    _seed(cfg)
    t = tree(cfg)
    assert [g["game"] for g in t["games"]] == ["starrail"]
    c = t["games"][0]["contents"][0]
    assert c["name"] == "파티편성"
    assert c["types"] == ["편성"]


def test_tree_filled_counts_only_filled_slots(cfg):
    """UNKNOWN·NA 는 근거가 아니므로 진척에도 안 들어간다.

    이 구분이 게이트에서는 봉인돼 있는데 사용자가 보는 진척 표시에서
    무너지면, 절반이 '모름' 인 컨텐츠가 완료로 보인다.
    """
    _seed(cfg)   # FILLED 1개 + UNKNOWN 1개
    c = tree(cfg)["games"][0]["contents"][0]
    assert c["filled"] == 1
    assert c["total"] == 14


def test_tree_marks_planned_and_blocked_families(cfg):
    _seed(cfg)
    fams = {f["family"]: f for f in tree(cfg)["games"][0]["contents"][0]["families"]}
    assert fams["정상 경로"]["planned"] is True
    assert fams["정상 경로"]["tc_count"] == 1
    assert fams["재화 부족"]["planned"] is False
    assert fams["재화 부족"]["blocked_by"] == "cost"
    assert "모른다" in fams["재화 부족"]["reason"]


def test_tree_does_not_mark_never_generated_family_as_withdrawn(cfg):
    """`재화 부족`은 TC 가 한 번도 생성된 적이 없다 — 철회와는 다른 상태다.

    `withdrawn_families` 의 둘째 인자를 "TC 가 실제로 있는 계열"이 아니라
    "슬롯이 아는 모든 계열"로 넓히면, 아직 만들어진 적도 없는 계열까지
    "근거 철회됨"으로 보인다. 그러면 사용자는 실제로는 존재하지 않는 TC 가
    철회당했다고 오해한다.
    """
    _seed(cfg)
    fams = {f["family"]: f for f in tree(cfg)["games"][0]["contents"][0]["families"]}
    assert fams["재화 부족"]["tc_count"] == 0
    assert fams["재화 부족"]["withdrawn"] is False


def test_tree_marks_withdrawn_evidence(cfg):
    """근거를 철회해도 TC 는 남는다 — 트리가 그것을 표시해야 한다."""
    name = _seed(cfg)
    with KnowledgeStore(cfg.knowledge_path / "starrail.db") as st:
        st.set_slot(name, "core_action", SlotStatus.NA)
    fams = {f["family"]: f for f in tree(cfg)["games"][0]["contents"][0]["families"]}
    assert fams["정상 경로"]["withdrawn"] is True
    assert fams["정상 경로"]["tc_count"] == 1     # 지우지 않는다


def test_tree_db_mtime_is_per_game(cfg):
    _seed(cfg)
    assert set(tree(cfg)["db_mtime"]) == {"starrail"}


def test_tree_on_empty_root_is_not_an_error(cfg):
    t = tree(cfg)
    assert t["games"] == []


def test_content_detail_carries_slots_and_testcases(cfg):
    name = _seed(cfg)
    d = content_detail(cfg, "starrail", name)
    keys = {s["key"]: s for s in d["slots"]}
    assert keys["core_action"]["status"] == "filled"
    assert keys["core_action"]["value"] == "파티를 편성한다"
    assert len(d["testcases"]) == 1
    assert d["testcases"][0]["family"] == "정상 경로"


def test_content_detail_exposes_slot_keys(cfg):
    """`slot_keys` 는 지금까지 쓰기 전용이었다 — 이 앱이 첫 소비자다."""
    name = _seed(cfg)
    assert content_detail(cfg, "starrail", name)["testcases"][0]["slot_keys"] == ["core_action"]


def test_content_detail_flags_a_human_edited_testcase(cfg):
    """`generated_hash` 와 본문 해시가 다르면 사람이 손댄 것."""
    name = _seed(cfg)
    with KnowledgeStore(cfg.knowledge_path / "starrail.db") as st:
        tc = st.testcases(name)[0]
        tc.title = "사람이 고친 제목"
        st.update_testcase_row(tc)
    d = content_detail(cfg, "starrail", name)
    assert d["testcases"][0]["edited"] is True


def test_content_detail_does_not_flag_an_untouched_testcase(cfg):
    name = _seed(cfg)
    assert content_detail(cfg, "starrail", name)["testcases"][0]["edited"] is False


def test_content_detail_marks_withdrawn_testcases(cfg):
    name = _seed(cfg)
    with KnowledgeStore(cfg.knowledge_path / "starrail.db") as st:
        st.set_slot(name, "core_action", SlotStatus.NA)
    assert content_detail(cfg, "starrail", name)["testcases"][0]["withdrawn"] is True


def test_content_detail_does_not_mark_live_testcase_as_withdrawn(cfg):
    """근거가 살아있는 TC 는 다른 계열이 비어 있어도 철회로 보이면 안 된다.

    `재화 부족`처럼 TC 가 아예 없는 계열이 슬롯 목록에 함께 있어도, 실제로
    존재하는 `정상 경로` TC 의 `withdrawn` 판정에 영향을 주면 안 된다 —
    둘째 인자 범위가 "TC 가 있는 계열"이 아니라 "슬롯이 아는 모든 계열"로
    넓어지는 회귀를 잡는다.
    """
    name = _seed(cfg)
    d = content_detail(cfg, "starrail", name)
    assert d["testcases"][0]["withdrawn"] is False


def test_content_detail_withdraws_a_testcase_whose_family_has_no_slot(cfg):
    """`중단` 은 등록된 계열이지만 이 컨텐츠 유형의 슬롯 어디에도 없다.

    (`qatc/knowledge/gate.py` 의 `FAMILY_META` 주석: 진술에서 통신 끊김·강제
    종료가 도출되는 경우가 없어 기본 슬롯에 없다.) `withdrawn_families` 의
    둘째 인자를 "TC 가 실제로 있는 계열"이 아니라 "슬롯이 아는 모든 계열"로
    넓히면, 슬롯이 아예 모르는 이 계열은 그 집합에 들지도 못해 "철회
    아님"으로 잘못 보인다. 근거가 될 슬롯이 처음부터 없었으므로 이 TC 는
    철회된 것으로 표시돼야 한다 — `content_detail` 자리의 범위 축소가
    `tree()` 자리와 별개로 검증돼야 하는 지점이다.
    """
    name = _seed(cfg)
    with KnowledgeStore(cfg.knowledge_path / "starrail.db") as st:
        st.add_testcase(name, "중단", _tc(title="중단 TC", family="중단"), [])
    d = content_detail(cfg, "starrail", name)
    tc = next(t for t in d["testcases"] if t["family"] == "중단")
    assert tc["withdrawn"] is True


def test_testcase_meta_scopes_to_requested_content(cfg):
    """`WHERE content = ?` 가 빠지면 다른 컨텐츠의 TC 까지 섞여 든다."""
    name = _seed(cfg)
    other = "무기강화"
    with KnowledgeStore(cfg.knowledge_path / "starrail.db") as st:
        st.init_content(other, game="starrail", types=[])
        st.set_slot(other, "core_action", SlotStatus.FILLED, "무기를 강화한다")
        st.add_testcase(other, "정상 경로", _tc(title="다른 컨텐츠 TC"), ["core_action"])
        own_ids = {tc.id for tc in st.testcases(name)}
        other_ids = {tc.id for tc in st.testcases(other)}
        meta = st.testcase_meta(name)
    assert set(meta) == own_ids
    assert not (other_ids & set(meta))


def test_content_detail_on_missing_game_db_does_not_create_it(cfg):
    """DB 가 없는 게임을 조회하면 예외만 던지고 끝나야 한다.

    `KnowledgeStore.open()` 은 연결하면서 `executescript(_SCHEMA)` + `commit()`
    을 실행한다 — 그래서 존재 확인 없이 그냥 열면 없던 `<game>.db` 가
    스키마까지 갖춘 채 새로 생긴다. 이건 앱이 지식 DB 에 쓰지 않는다는
    설계를 뒤엎는 것이다. 예외가 났다는 사실만으로는 아무것도 안 만들어졌다는
    증거가 안 된다 — 파일 존재 여부를 직접 봐야 한다.
    """
    with pytest.raises(ContentNotFound):
        content_detail(cfg, "없는게임", "아무거나")
    assert not (cfg.knowledge_path / "없는게임.db").exists()


def test_content_detail_on_missing_content_says_what_to_do(cfg):
    _seed(cfg)
    with pytest.raises(ContentNotFound) as e:
        content_detail(cfg, "starrail", "없는것")
    msg = str(e.value)
    assert "없는것" in msg
    assert "slot init" in msg          # 다음 조치
    assert "ContentNotFound" not in msg  # 예외 이름을 노출하지 않는다


def test_everything_is_json_serialisable(cfg):
    """Flask 가 직렬화할 수 있어야 한다 — Enum·Path 가 새면 여기서 죽는다."""
    name = _seed(cfg)
    json.dumps(tree(cfg), ensure_ascii=False)
    json.dumps(content_detail(cfg, "starrail", name), ensure_ascii=False)


# --- `game` 은 URL 질의 문자열이다 (최종 리뷰 확정 결함 3) -------------------


def _foreign_db(path):
    """지식 루트 **밖**에 있는 남의 SQLite 파일을 하나 만든다."""
    import sqlite3

    con = sqlite3.connect(path)
    con.execute("CREATE TABLE notes(id INTEGER PRIMARY KEY, body TEXT)")
    con.execute("INSERT INTO notes(body) VALUES ('남의 소중한 메모')")
    con.commit()
    con.close()
    return path.read_bytes()


def _hostile_games(victim):
    """지식 루트를 벗어나려는 `game` 값들. 뒤 이름은 실패 메시지용."""
    stem = str(victim)[: -len(".db")]
    return [
        ("../victimF", "상위 디렉터리"),
        ("..\\victimF", "역슬래시 상위 디렉터리"),
        ("./../victimF", "점 하나를 낀 상위 디렉터리"),
        (stem, "절대 경로 (드라이브 문자)"),
        (stem.replace("\\", "/"), "슬래시로 쓴 절대 경로"),
        ("sub/victimF", "하위 디렉터리"),
        ("starrail\x00", "NUL 을 붙인 이름"),
        ("..", "점 둘"),
        ("", "빈 이름"),
    ]


def test_content_detail_never_touches_a_file_outside_the_knowledge_root(cfg, tmp_path):
    """`game` 을 경로 조각으로 그대로 쓰면 디스크의 아무 SQLite 파일에나 쓴다.

    실측(최종 리뷰): `GET /api/content?game=../../victimF&name=x` 가 **404 를
    돌려주면서** 지식 루트 밖 남의 SQLite 파일을 8192 → 32768 바이트로 키우고
    `contents`·`slots`·`testcases` 테이블을 심었다. 404 라서 아무 일도 없었던
    것처럼 보이는 것이 이 결함의 핵심이다.

    원인은 `KnowledgeStore.open()` 이 연결과 동시에 `executescript(_SCHEMA)`
    + `commit()` 을 돌린다는 것 — **경로를 여는 행위 자체가 쓰기다.** 그래서
    "예외가 났다" 는 아무 증거도 되지 못하고, 남의 파일 바이트를 직접
    비교해야 한다. `pathlib` 의 결합은 오른쪽이 절대 경로면 왼쪽을 통째로
    버리므로 `..` 뿐 아니라 절대 경로 형태도 함께 막혀야 한다.
    """
    _seed(cfg)
    victim = tmp_path / "victimF.db"
    before = _foreign_db(victim)

    for game, label in _hostile_games(victim):
        with pytest.raises(ContentNotFound):
            content_detail(cfg, game, "아무거나")
        assert victim.read_bytes() == before, f"{label}: 남의 파일이 바뀌었다 ({game!r})"


def test_a_hostile_game_name_creates_no_file_anywhere(cfg, tmp_path):
    """거부는 조용해야 한다 — 어디에도 새 파일을 남기지 않는다.

    바이트 비교(위 테스트)는 **이미 있던** 파일만 지킨다. 없던 자리에 유령
    DB 를 새로 만드는 경로는 그것만으로는 보이지 않는다.
    """
    _seed(cfg)
    before = {p for p in tmp_path.rglob("*") if p.is_file()}

    for game, label in _hostile_games(tmp_path / "없던파일.db"):
        with pytest.raises(ContentNotFound):
            content_detail(cfg, game, "아무거나")

    after = {p for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before, f"새로 생긴 파일: {sorted(str(p) for p in after - before)}"


def test_the_rejection_is_korean_and_names_the_next_action(cfg):
    _seed(cfg)
    with pytest.raises(ContentNotFound) as e:
        content_detail(cfg, "../victimF", "아무거나")
    msg = str(e.value)
    assert "ContentNotFound" not in msg
    assert "Traceback" not in msg
    assert "게임" in msg
    assert "왼쪽" in msg or "slot init" in msg      # 다음 조치를 말한다


def test_a_dotfile_named_db_does_not_create_a_ghost_sibling(cfg):
    """`Path(".db").stem == ".db"` — 이름이 `.db` 뿐인 파일이 있으면
    옛 stem 대조를 `game=".db"` 가 통과했다.

    `_db_paths()` 의 `*.db` 글롭은 숨김 파일도 잡으므로, 지식 루트에 `.db`
    라는 이름의 파일이 있을 수 있다. 옛 구현은 `game` 을 실재 stem 집합과
    대조한 뒤 `f"{game}.db"` 로 경로를 다시 조립했는데, `.db` 는 그 왕복을
    통과하지 못한다 — `".db" + ".db"` 는 원본과 다른 이름 `.db.db` 다.
    `KnowledgeStore.open()` 이 열자마자 스키마를 쓰므로, 존재 확인 없이 그
    이름을 열면 유령 DB 파일이 새로 생긴다.
    """
    (cfg.knowledge_path / ".db").write_bytes(b"")
    with pytest.raises(ContentNotFound):
        content_detail(cfg, ".db", "아무거나")
    assert not (cfg.knowledge_path / ".db.db").exists()


def test_a_legitimate_game_name_still_resolves(cfg):
    """봉쇄가 정상 호출자를 막으면 안 된다 — 화면은 `p.stem` 만 나른다."""
    name = _seed(cfg)
    assert content_detail(cfg, "starrail", name)["game"] == "starrail"


def test_tree_db_mtime_carries_the_real_mtime_and_moves_when_the_db_changes(cfg):
    """`db_mtime` 은 장식이 아니다 — 화면의 트리 갱신 판정을 그것이 구동한다.

    `app.js` 는 `db_mtime` 이 이전과 같으면 `renderTree()` 를 건너뛴다.
    `mtimes[p.stem] = 0` 으로 바꿔도 494 가 통과했는데, 그 상태면 `changed`
    가 영원히 거짓이라 첫 로드 뒤로는 트리가 다시는 안 그려진다 — 인터뷰를
    끝내도 왼쪽 패널에 TC 가 나타나지 않는다. 키 집합만 보던 옛 단언은 그
    변이를 못 잡았다(이월 항목 #1).

    값이 실제 mtime 인지, 그리고 DB 가 바뀌면 **움직이는지**를 함께 본다 —
    상수는 첫 단언에, 고정된 값은 둘째 단언에 걸린다.
    """
    name = _seed(cfg)
    db = cfg.knowledge_path / "starrail.db"
    first = tree(cfg)["db_mtime"]["starrail"]
    assert first == db.stat().st_mtime

    with KnowledgeStore(db) as st:
        st.set_slot(name, "cost", SlotStatus.FILLED, "재화를 쓴다")
    second = tree(cfg)["db_mtime"]["starrail"]
    assert second == db.stat().st_mtime
    assert second != first, "DB 가 바뀌었는데 db_mtime 이 그대로다 — 트리가 안 갱신된다"


# --- 분모가 한도처럼 보이지 않게 --------------------------------------------
#
# `8 / 10` 은 10이 고정 상한처럼 읽힌다. 실제로는 유형별 슬롯과 `slot add` 로
# 늘어나고, 첫 실사용에서 사용자가 슬롯을 늘릴 수 있다는 것을 몰랐던 이유의
# 하나가 이 표시였다. 진척 자체는 여전히 filled/total 이다 — 화면이 "이 숫자는
# 늘 수 있다" 를 말할 수 있게 기준선만 함께 실어 준다.


def test_tree_reports_base_total_alongside_total(cfg):
    """유형·추가 슬롯으로 늘어난 만큼을 화면이 구분할 수 있어야 한다."""
    from qatc.knowledge.slots import BASE_SLOTS

    assert len(BASE_SLOTS) == 10        # 아래 수들의 출처

    _seed(cfg)                          # 기본 10 + 유형 편성 4
    c = tree(cfg)["games"][0]["contents"][0]
    assert c["total"] == 14
    assert c["base_total"] == len(BASE_SLOTS)

    # `slot add` 로 하나 더 = total 15, base_total 은 그대로 10.
    with KnowledgeStore(cfg.knowledge_path / "starrail.db") as st:
        st.add_slot("파티편성", "자동로그인", "자동 로그인이 유지되는가", "정상 경로")
    c = tree(cfg)["games"][0]["contents"][0]
    assert c["total"] == 15
    assert c["base_total"] == len(BASE_SLOTS), "추가 슬롯이 기준선까지 밀어 올렸습니다"

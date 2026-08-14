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
    return TestCase(id="", category_minor=family, title=title,
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

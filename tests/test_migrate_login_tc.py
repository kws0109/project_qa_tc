"""승인된 분류를 적용한다. 판단은 하지 않는다."""

import json
from pathlib import Path

import pytest

from qatc.knowledge.models import SlotStatus
from qatc.knowledge.store import KnowledgeStore
from qatc.migrate_login_tc import apply_reclassification

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs" / "superpowers" / "2026-08-16-login-reclassify.json"


@pytest.fixture()
def db(tmp_path):
    """옛 모양의 `로그인` — 코드 없음, 소분류 없음."""
    path = tmp_path / "starrail.db"
    with KnowledgeStore(path) as st:
        st.init_content("로그인", game="starrail", types=[])
        for key in ("entry", "screen", "core_action", "result",
                    "failure", "exit", "constraints"):
            st.set_slot("로그인", key, SlotStatus.FILLED, "사용자 진술")
    return path


def test_the_plan_file_is_the_approved_one():
    """이 파일을 고치면 승인이 무효가 된다 — 승인된 성질을 고정한다."""
    doc = json.loads(PLAN.read_text(encoding="utf-8"))
    assert doc["코드"] == "LOGIN"
    assert len(doc["testcases"]) == 29
    assert sum(len(t["기대결과"]) for t in doc["testcases"]) == 70
    assert max(len(t["기대결과"]) for t in doc["testcases"]) == 6


def test_applying_gives_29_testcases_with_the_new_ids(db):
    apply_reclassification(db, PLAN)
    with KnowledgeStore(db) as st:
        cases = st.testcases("로그인")
        assert st.content_code("로그인") == "LOGIN"
    ids = sorted(t.id for t in cases)
    assert len(ids) == 29
    assert ids[0] == "TC_LOGIN_001" and ids[-1] == "TC_LOGIN_029"


def test_every_row_has_a_hierarchy_and_a_family(db):
    apply_reclassification(db, PLAN)
    with KnowledgeStore(db) as st:
        cases = st.testcases("로그인")
    for t in cases:
        assert t.category_major == "로그인"
        assert t.category_minor and t.category_sub
        assert t.family


def test_nothing_exceeds_the_six_check_ceiling(db):
    apply_reclassification(db, PLAN)
    with KnowledgeStore(db) as st:
        cases = st.testcases("로그인")
    assert max(len(t.expected) for t in cases) <= 6


def test_applying_twice_does_not_double(db):
    """실행 중 끊겼을 때 다시 돌릴 수 있어야 한다."""
    apply_reclassification(db, PLAN)
    apply_reclassification(db, PLAN)
    with KnowledgeStore(db) as st:
        assert len(st.testcases("로그인")) == 29


def test_a_backup_is_written_before_touching_the_db(db):
    """되돌릴 수 없는 편집 전에 원본을 남긴다."""
    apply_reclassification(db, PLAN)
    backups = list(db.parent.glob("starrail.db.bak*"))
    assert backups, "백업 파일이 없습니다"
    assert backups[0].stat().st_size > 0

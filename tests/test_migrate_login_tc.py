"""승인된 분류를 적용한다. 판단은 하지 않는다."""

import json
from pathlib import Path

import pytest

from qatc.knowledge.models import SlotStatus
from qatc.knowledge.store import KnowledgeStore
from qatc.migrate_login_tc import apply_reclassification
from qatc.models import Priority, TCKind, TCOrigin, TestCase

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs" / "superpowers" / "2026-08-16-login-reclassify.json"

#: 실제 `knowledge/starrail.db` 의 `로그인` 23건 중 3건을 옛 모양 그대로 옮겨왔다
#: (읽기 전용으로 확인, 값만 가져오고 그 파일은 건드리지 않았다). `tc_<hex>` id ·
#: 채워진 title · 빈 소분류 · 계열 이름을 담은 category_minor — 이 브랜치 이전의
#: 실제 저장 모양이다. `TC_LOGIN_0NN` 과 겹치지 않는 id 라 `INSERT OR REPLACE`
#: 로는 지워지지 않는다 — `clear_testcases` 가 실제로 지워야만 사라진다.
_OLD_SHAPED_ROWS = [
    ("tc_023a0e9dc6", "진입 경로", "PC 클라이언트 최초 실행 시 로그인 창으로 진입 (로그인 기록 없음)"),
    ("tc_febd2b163e", "진입 경로", "PC 클라이언트 실행 시 기존 로그인 기록이 있으면 게임시작 버튼으로 진입"),
    ("tc_f31272b7b6", "요소 표시 확인", "로그인 창 구성 요소 표시 확인"),
]


@pytest.fixture()
def db(tmp_path):
    """옛 모양의 `로그인` — 코드 없음, 소분류 없음, 옛 행 몇 건이 이미 담겨 있다.

    빈 DB 로는 `clear_testcases` 가 실제로 지우는지 검증할 수 없다 — 옛 ID 가
    하나도 없으면 `add_testcase` 의 `INSERT OR REPLACE` 가 새 29건을 (지우지
    않고 그냥 덮어써도) 같은 결과를 만들어 삭제 경로가 통과한 척한다(M29,
    실측으로 확인). 실제 DB 를 흉내내려면 새 ID 와 겹치지 않는 옛 행이 있어야
    한다.
    """
    path = tmp_path / "starrail.db"
    with KnowledgeStore(path) as st:
        st.init_content("로그인", game="starrail", types=[])
        for key in ("entry", "screen", "core_action", "result",
                    "failure", "exit", "constraints"):
            st.set_slot("로그인", key, SlotStatus.FILLED, "사용자 진술")
        for old_id, family, title in _OLD_SHAPED_ROWS:
            tc = TestCase(
                id=old_id,
                category_major="로그인",
                category_minor=family,      # 옛 모양: 소분류가 없던 시절엔 이 자리에 계열 이름이 있었다
                family=family,
                title=title,
                precondition="옛 사전조건",
                steps=["옛 절차"],
                expected=["옛 기대결과"],
                priority=Priority.HIGH,
                kind=TCKind.HAPPY_PATH,
                origin=TCOrigin.INTERVIEW,
                rationale="옛 근거",
            )
            st.add_testcase("로그인", family, tc, [])
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
    # 개수만 29 인 것으로는 부족하다 — 옛 3건이 살아남은 채로 새 29건이 더해져도
    # 우연히 32건이 아니라 29건처럼 보일 여지는 없지만(29 != 32), *어느* id 가
    # 살아있는지까지 봐야 "지우고 새로 넣었다"와 "옛 것 위에 새 것만 더했다"가
    # 갈린다 — 실패 메시지가 바로 원인을 가리키게 한다.
    old_ids = {row[0] for row in _OLD_SHAPED_ROWS}
    assert not (set(ids) & old_ids), "옛 ID 가 살아남았습니다 — clear_testcases 가 지우지 못했습니다"


def test_applying_reports_how_many_it_removed_and_added(db):
    """`(지운 수, 넣은 수)` 반환값 — 사람이 콘솔에서 읽는 확인 수단이다.

    지금까지 이 반환값 자체를 검증하는 테스트가 없었다. 옛 3건을 지우고
    새 29건을 넣어야 하므로 `(3, 29)` 여야 한다.
    """
    removed, added = apply_reclassification(db, PLAN)
    assert (removed, added) == (len(_OLD_SHAPED_ROWS), 29)


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
    """실행 중 끊겼을 때 다시 돌릴 수 있어야 한다.

    첫 적용은 옛 3건을 지우고 새 29건을 넣는다. 둘째 적용은 (이미 옛 것이
    없으므로) 그 29건을 지우고 같은 29건을 다시 넣는다 — 어느 쪽이든 최종
    개수는 29 여야 한다. 옛 행이 섞인 채로 시작하는 픽스처라야 `clear_testcases`
    가 생략됐을 때 32건(첫 적용에서 옛 3건이 안 지워짐)으로 갈라진다.
    """
    apply_reclassification(db, PLAN)
    apply_reclassification(db, PLAN)
    with KnowledgeStore(db) as st:
        assert len(st.testcases("로그인")) == 29


def test_a_backup_is_written_before_touching_the_db(db):
    """되돌릴 수 없는 편집 전에 원본을 남긴다.

    존재·크기만으로는 부족하다 — `shutil.copy2` 를 삭제·삽입 **뒤**로 옮겨도
    백업 파일은 여전히 생기고 크기도 0보다 크므로 그 어서션들은 그대로
    통과한다(실측: `apply_reclassification` 의 백업 줄을 `with
    KnowledgeStore(...)` 블록 다음으로 옮기면 스위트가 그대로 초록이었다).
    이 테스트가 지키려는 성질은 **순서**이므로, 백업 파일 자체를 열어
    마이그레이션 **전** 상태(옛 3건의 id, 새 `TC_LOGIN_NNN` 은 아직 없음)를
    담고 있는지 확인한다 — 이미 옮겨 써진 백업이라면 옛 id 가 없고 새 id 만
    있을 것이다.
    """
    apply_reclassification(db, PLAN)
    backups = list(db.parent.glob("starrail.db.bak*"))
    assert backups, "백업 파일이 없습니다"
    assert backups[0].stat().st_size > 0

    with KnowledgeStore(backups[0]) as st:
        backed_up_ids = {t.id for t in st.testcases("로그인")}
    old_ids = {row[0] for row in _OLD_SHAPED_ROWS}
    assert backed_up_ids == old_ids, (
        "백업이 마이그레이션 전 원본을 담고 있지 않습니다 — "
        f"기대: {sorted(old_ids)}, 실제: {sorted(backed_up_ids)}"
    )
    assert not any(i.startswith("TC_LOGIN_") for i in backed_up_ids), (
        "백업에 이미 새 ID 가 있습니다 — 편집 뒤에 복사됐다는 뜻입니다"
    )

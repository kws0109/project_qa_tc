"""승인된 로그인 재분류를 지식 DB 에 적용한다.

**이 모듈은 판단하지 않는다.** 어떤 TC 를 나누고 합칠지는 소유자 승인을 거쳐
`docs/superpowers/2026-08-16-login-reclassify.json` 에 확정돼 있고, 여기서는
그것을 그대로 넣는다. 판단을 코드로 옮기면 다시 돌릴 때마다 결과가 흔들린다.

한 번 쓰고 버릴 코드가 아니다 — 실행 중 끊겼을 때 다시 돌릴 수 있어야 하고,
그래서 멱등이다.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .knowledge.gate import FAMILY_META
from .knowledge.store import KnowledgeStore
from .models import Priority, TCOrigin, TestCase

_ORIGIN = {"인터뷰": TCOrigin.INTERVIEW, "추론됨": TCOrigin.INFERRED,
           "사용자": TCOrigin.USER}


def apply_reclassification(db_path: Path | str, plan_path: Path | str) -> tuple[int, int]:
    """분류표를 적용한다. `(지운 수, 넣은 수)`."""
    db_path, plan_path = Path(db_path), Path(plan_path)
    doc = json.loads(plan_path.read_text(encoding="utf-8"))
    content = doc["컨텐츠"]

    # 되돌릴 수 없는 편집이다. 손대기 전에 원본을 복사한다.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    shutil.copy2(db_path, db_path.with_name(db_path.name + f".bak{stamp}"))

    with KnowledgeStore(db_path) as st:
        st.set_content_code(content, doc["코드"])
        removed = st.clear_testcases(content)
        for item in doc["testcases"]:
            kind, _default_priority = FAMILY_META[item["계열"]]
            tc = TestCase(
                id=item["id"],
                category_major=item["대분류"],
                category_minor=item["중분류"],
                category_sub=item["소분류"],
                family=item["계열"],
                precondition=item["사전조건"],
                steps=list(item["절차"]),
                expected=list(item["기대결과"]),
                priority=Priority(item["우선순위"]),
                kind=kind,
                origin=_ORIGIN[item["출처"]],
                rationale=item["근거"],
            )
            st.add_testcase(content, item["계열"], tc, item["근거슬롯"])
        # ID 를 JSON 에서 그대로 가져왔으므로 번호표가 0에 머물러 있다.
        # 맞춰 두지 않으면 다음에 만드는 TC 가 001 을 다시 받는다.
        st.set_tc_seq(content, len(doc["testcases"]))
    return removed, len(doc["testcases"])

"""게임 단위 지식 저장소.

게임 하나당 DB 하나에 컨텐츠가 쌓인다. 게임 단위로 묶는 이유는 **누적** 때문이다 —
유물 장착을 인터뷰할 때 이미 아는 파티 편성 지식을 참고하면 교차 질문이 가능해지고,
같은 것을 두 번 설명하지 않아도 된다.

대화 로그는 저장하지 않는다. Claude Code 트랜스크립트가 그 역할을 하므로 중복이다.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ..models import TCOrigin, TestCase, new_id
from .models import Content, Slot, SlotStatus, is_blank
from .slots import build_slot_set

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contents (
    name       TEXT PRIMARY KEY,
    game       TEXT NOT NULL,
    types      TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS slots (
    content     TEXT NOT NULL,
    key         TEXT NOT NULL,
    prompt_hint TEXT NOT NULL,
    tc_family   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'empty',
    value       TEXT NOT NULL DEFAULT '',
    ord         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (content, key)
);

CREATE TABLE IF NOT EXISTS testcases (
    id             TEXT PRIMARY KEY,
    content        TEXT NOT NULL,
    family         TEXT NOT NULL,
    generated_hash TEXT NOT NULL,
    slot_keys      TEXT NOT NULL DEFAULT '[]',
    row            TEXT NOT NULL
);
"""


#: DB 잠금 안내 (설계 스펙 §7). `timeout=30.0` 은 재시도까지만 담당하고,
#: 그 30초를 다 쓰고 나면 sqlite 는 `database is locked` 만 던진다. 이 CLI 의
#: 호출자는 인터뷰를 진행하는 모델이라, 원인과 다음 조치가 없으면 무엇을 할지
#: 모른다.
DB_LOCKED_HINT = (
    "지식 DB가 잠겨 있습니다 — 다른 qatc 프로세스가 실행 중일 수 있습니다. "
    "그 명령이 끝나기를 기다렸다가 다시 실행하세요."
)


def is_locked_error(exc: sqlite3.OperationalError) -> bool:
    """sqlite 의 잠금 계열 오류인가.

    `sqlite3` 는 잠금 전용 예외 타입을 주지 않고 `OperationalError` 하나로
    묶으므로 메시지로 판별할 수밖에 없다. 잠금이 아닌 `OperationalError`
    (예: `no such table`)까지 잠금으로 뭉개면 진단이 불가능해진다.
    """
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


class KnowledgeStore:
    """게임 하나의 지식 DB."""

    def __init__(self, db_path: Path | str):
        self.path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    # -- 수명주기 ----------------------------------------------------

    def open(self) -> KnowledgeStore:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        return self

    def close(self) -> None:
        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    def __enter__(self) -> KnowledgeStore:
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def game(self) -> str:
        """파일명이 곧 게임 키다 (starrail.db → starrail)."""
        return self.path.stem

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("KnowledgeStore가 열려 있지 않습니다. open()을 먼저 부르세요.")
        return self._conn

    # -- 컨텐츠 ------------------------------------------------------

    def get_content(self, name: str) -> Content | None:
        row = self._db().execute(
            "SELECT name, game, types FROM contents WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        return Content(name=row["name"], game=row["game"], types=json.loads(row["types"]))

    def list_contents(self) -> list[Content]:
        return [
            Content(name=r["name"], game=r["game"], types=json.loads(r["types"]))
            for r in self._db().execute(
                "SELECT name, game, types FROM contents ORDER BY created_at"
            )
        ]

    def init_content(self, name: str, game: str, types: Sequence[str]) -> Content:
        """컨텐츠를 만들거나, 이미 있으면 새 유형의 슬롯만 덧붙인다.

        **기존 슬롯의 값과 상태는 절대 건드리지 않는다.** 재실행이 사용자가 채운
        내용을 지우면 아무도 다시 실행하지 않는다.
        """
        db = self._db()
        existing = self.get_content(name)
        merged = list(dict.fromkeys([*(existing.types if existing else []), *types]))
        specs = build_slot_set(merged)  # 모르는 유형이면 여기서 ValueError

        if existing is None:
            db.execute(
                "INSERT INTO contents (name, game, types, created_at) VALUES (?, ?, ?, ?)",
                (name, game, json.dumps(merged, ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat()),
            )
        else:
            db.execute(
                "UPDATE contents SET types = ? WHERE name = ?",
                (json.dumps(merged, ensure_ascii=False), name),
            )

        have = {r["key"] for r in db.execute(
            "SELECT key FROM slots WHERE content = ?", (name,)
        )}
        next_ord = len(have)
        for spec in specs:
            if spec.key in have:
                continue
            db.execute(
                "INSERT INTO slots (content, key, prompt_hint, tc_family, status, value, ord)"
                " VALUES (?, ?, ?, ?, 'empty', '', ?)",
                (name, spec.key, spec.prompt_hint, spec.tc_family, next_ord),
            )
            next_ord += 1
        db.commit()
        return Content(name=name, game=game, types=merged)

    # -- 슬롯 --------------------------------------------------------

    def slots(self, name: str) -> list[Slot]:
        return [
            Slot(
                key=r["key"],
                prompt_hint=r["prompt_hint"],
                tc_family=r["tc_family"],
                status=SlotStatus(r["status"]),
                value=r["value"],
                ord=r["ord"],
            )
            for r in self._db().execute(
                "SELECT key, prompt_hint, tc_family, status, value, ord"
                " FROM slots WHERE content = ? ORDER BY ord", (name,)
            )
        ]

    def set_slot(self, name: str, key: str, status: SlotStatus, value: str = "") -> Slot:
        """슬롯 값을 기록한다.

        없는 키는 **조용히 무시하지 않는다.** 성공한 척하면 사용자 답변이 증발한
        것처럼 보이고, 인터뷰가 끝날 때까지 아무도 눈치채지 못한다.

        `FILLED` 에 빈 값도 **조용히 받지 않는다.** 게이트(:func:`plan_families`)는
        `status is FILLED` 만 보고 계열을 계획하므로, 값이 빈 FILLED 슬롯은
        "아무 내용도 없는 근거" 로 인정돼 근거 없는 TC를 만든다. CLI 에도 같은
        검사가 있지만 게이트와 같은 계층인 여기서도 막아야 CLI 밖 호출자가
        우회하지 못한다.

        "빈 값" 의 판정은 :func:`is_blank` 가 한다 — `value.strip()` 은 제로폭
        공백·BOM·제어문자를 하나도 지우지 않아 같은 구멍이 다시 열린다.
        """
        if status is SlotStatus.FILLED and is_blank(value):
            raise ValueError(
                f"'{key}' 슬롯을 filled 로 기록하려면 내용이 있는 값이 필요합니다 "
                f"(공백·제로폭 문자·제어문자·한글 필러처럼 빈칸으로 보이는 문자만으로는 근거가 되지 않습니다). "
                f"내용을 모르면 SlotStatus.UNKNOWN, 해당 없으면 SlotStatus.NA 를 쓰세요."
            )
        current = self.slots(name)
        if not current:
            raise KeyError(f"컨텐츠 '{name}'가 없습니다. 먼저 slot init을 실행하세요.")
        by_key = {s.key: s for s in current}
        if key not in by_key:
            raise KeyError(
                f"'{name}'에 '{key}' 슬롯이 없습니다. "
                f"사용 가능한 키: {', '.join(sorted(by_key))}"
            )
        db = self._db()
        db.execute(
            "UPDATE slots SET status = ?, value = ? WHERE content = ? AND key = ?",
            (status.value, value, name, key),
        )
        db.commit()
        slot = by_key[key]
        slot.status = status
        slot.value = value
        return slot

    def add_slot(self, name: str, key: str, prompt_hint: str, tc_family: str) -> Slot:
        """유형 목록에 없던 항목을 추가한다. 커버리지 분모가 늘어난다."""
        current = self.slots(name)
        if not current:
            raise KeyError(f"컨텐츠 '{name}'가 없습니다. 먼저 slot init을 실행하세요.")
        if any(s.key == key for s in current):
            raise KeyError(f"'{name}'에 '{key}' 슬롯이 이미 있습니다.")
        db = self._db()
        db.execute(
            "INSERT INTO slots (content, key, prompt_hint, tc_family, status, value, ord)"
            " VALUES (?, ?, ?, ?, 'empty', '', ?)",
            (name, key, prompt_hint, tc_family, len(current)),
        )
        db.commit()
        return Slot(key=key, prompt_hint=prompt_hint, tc_family=tc_family, ord=len(current))

    # -- 테스트케이스 ------------------------------------------------

    def add_testcase(
        self, content: str, family: str, tc: TestCase, slot_keys: Sequence[str]
    ) -> TestCase:
        """TC를 저장한다. `id` 가 비어 있으면 부여한다."""
        if not tc.id:
            tc.id = new_id("tc")
        self._db().execute(
            "INSERT OR REPLACE INTO testcases"
            " (id, content, family, generated_hash, slot_keys, row)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                tc.id, content, family, testcase_hash(tc),
                json.dumps(list(slot_keys), ensure_ascii=False),
                json.dumps(tc.to_row(), ensure_ascii=False),
            ),
        )
        self._db().commit()
        return tc

    def update_testcase_row(self, tc: TestCase) -> None:
        """본문만 갱신한다 — **테스트 전용 도구다. 프로덕션 호출자는 없다.**

        `generated_hash` 를 일부러 건드리지 않으므로, 이후
        :meth:`replace_generated` 가 그 TC를 '사용자가 고쳤다'로 판정해
        보존한다. 즉 이 메서드의 존재 이유는 **"사람이 xlsx 를 열어 손으로
        고친 TC"** 상태를 테스트가 만들 수 있게 하는 것이다 (현재 사람이
        고친 내용을 DB로 되돌리는 경로는 없다).

        나중에 그 되돌림 경로를 만든다면 이 메서드가 그 출발점이지만,
        그때는 해시 갱신 여부를 다시 판단해야 한다.
        """
        self._db().execute(
            "UPDATE testcases SET row = ? WHERE id = ?",
            (json.dumps(tc.to_row(), ensure_ascii=False), tc.id),
        )
        self._db().commit()

    def testcases(self, content: str, family: str | None = None) -> list[TestCase]:
        sql = "SELECT row FROM testcases WHERE content = ?"
        args: list[str] = [content]
        if family is not None:
            sql += " AND family = ?"
            args.append(family)
        sql += " ORDER BY rowid"
        return [TestCase.from_row(json.loads(r["row"])) for r in self._db().execute(sql, args)]

    def testcase_meta(self, content: str) -> dict[str, tuple[list[str], str]]:
        """이 컨텐츠 TC 들의 `(slot_keys, generated_hash)` 를 id 로 찾는 표.

        두 열을 **한 쿼리로** 가져온다. 앱의 오른쪽 패널이 TC 마다
        "왜 존재하는가"(근거 슬롯)와 "사람이 고쳤는가"(해시 비교)를 둘 다
        보여주는데, TC 당 DB 를 다시 여는 구현은 받아들이지 않는다.

        `slot_keys` 는 `add_testcase` 가 저장해 온 열인데 지금껏 읽는 코드가
        없었다 — 선행 리뷰가 "테스트 전용 아니냐"고 지적한 자리이고,
        이 앱이 첫 소비자다.
        """
        rows = self._db().execute(
            "SELECT id, slot_keys, generated_hash FROM testcases WHERE content = ?",
            (content,),
        ).fetchall()
        return {r["id"]: (json.loads(r["slot_keys"]), r["generated_hash"]) for r in rows}

    def replace_generated(
        self,
        content: str,
        family: str,
        cases: Sequence[TestCase],
        slot_keys: Sequence[str],
    ) -> tuple[int, int, int]:
        """한 계열의 생성분을 갈아끼운다. 사람 손이 닿은 것은 보존한다.

        보존 조건 두 가지 —
        `origin=USER` 이거나, 저장된 본문 해시가 `generated_hash` 와 다른 것.
        후자가 "사용자가 고쳤다"의 판정이다.

        **삭제 수를 함께 돌려준다.** 예전에는 `(added, kept)` 뿐이라 호출자가
        무엇이 사라졌는지 알 방법이 없었고, `tc add` 를 같은 계열에 두 번 부르면
        앞 배치가 `✓ … 저장` rc=0 과 함께 통째로 증발했다 (실측: 4건 → 2건,
        출력에 삭제를 암시하는 글자가 하나도 없음). 교체는 이 메서드의 존재
        이유이므로 막지 않는다 — 대신 **말하게** 만든다.

        `(added, kept)` 순서는 CLI 가 언패킹해서 쓰는 계약이라 그대로 두고
        삭제 수를 뒤에 붙였다.

        :returns: (추가한 수, 보존한 수, 지운 수)
        """
        db = self._db()
        kept = 0
        deleted = 0
        for r in db.execute(
            "SELECT id, generated_hash, row FROM testcases WHERE content = ? AND family = ?",
            (content, family),
        ).fetchall():
            tc = TestCase.from_row(json.loads(r["row"]))
            edited = testcase_hash(tc) != r["generated_hash"]
            if tc.origin is TCOrigin.USER or edited:
                kept += 1
                continue
            db.execute("DELETE FROM testcases WHERE id = ?", (r["id"],))
            deleted += 1
        db.commit()

        for tc in cases:
            self.add_testcase(content, family, tc, slot_keys)
        return len(cases), kept, deleted


def testcase_hash(tc: TestCase) -> str:
    """TC 본문의 해시. `id` 와 `origin` 은 제외한다.

    id는 저장 시 부여되는 것이고 origin은 메타데이터라, 둘 중 하나가 달라졌다고
    "사용자가 고쳤다"로 볼 수 없다. 사람이 실제로 고치는 것은 제목·절차·기대결과다.
    """
    payload = json.dumps(
        {
            "category_major": tc.category_major,
            "category_minor": tc.category_minor,
            "title": tc.title,
            "precondition": tc.precondition,
            "steps": tc.steps,
            "expected": tc.expected,
            "priority": tc.priority.value,
            "kind": tc.kind.value,
            "rationale": tc.rationale,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


#: pytest가 이름이 `test`로 시작하는 이 함수를 테스트로 수집하지 않게 막는다
#: (qatc.models.TestCase.__test__ 와 동일한 이유의 오탐 방지).
testcase_hash.__test__ = False

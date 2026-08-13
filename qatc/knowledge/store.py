"""게임 단위 지식 저장소.

게임 하나당 DB 하나에 컨텐츠가 쌓인다. 게임 단위로 묶는 이유는 **누적** 때문이다 —
유물 장착을 인터뷰할 때 이미 아는 파티 편성 지식을 참고하면 교차 질문이 가능해지고,
같은 것을 두 번 설명하지 않아도 된다.

대화 로그는 저장하지 않는다. Claude Code 트랜스크립트가 그 역할을 하므로 중복이다.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .models import Content, Slot, SlotStatus
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
        """
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

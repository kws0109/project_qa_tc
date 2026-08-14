"""SQLite → JSON. 읽기만 한다."""

from __future__ import annotations

from pathlib import Path

from ..config import AppConfig
from ..knowledge.gate import plan_families, withdrawn_families
from ..knowledge.models import SlotStatus
from ..knowledge.store import KnowledgeStore, testcase_hash


class ContentNotFound(Exception):
    """그 컨텐츠가 없다. 메시지는 완성된 한국어 문장이다."""


def _db_paths(cfg: AppConfig) -> list[Path]:
    return sorted(cfg.knowledge_path.glob("*.db"))


def tree(cfg: AppConfig) -> dict:
    """왼쪽 패널 전체."""
    games, mtimes = [], {}
    for p in _db_paths(cfg):
        mtimes[p.stem] = p.stat().st_mtime
        contents = []
        with KnowledgeStore(p) as st:
            for c in st.list_contents():
                slots = st.slots(c.name)
                cases = st.testcases(c.name)
                planned, skipped = plan_families(slots)
                withdrawn = withdrawn_families(slots, {t.category_minor for t in cases})
                counts: dict[str, int] = {}
                for t in cases:
                    counts[t.category_minor] = counts.get(t.category_minor, 0) + 1
                fams = [
                    {"family": fp.family, "planned": True,
                     "tc_count": counts.get(fp.family, 0),
                     "withdrawn": fp.family in withdrawn}
                    for fp in planned
                ] + [
                    {"family": fs.family, "planned": False,
                     "tc_count": counts.get(fs.family, 0),
                     "withdrawn": fs.family in withdrawn,
                     "blocked_by": fs.slot_key, "reason": fs.reason}
                    for fs in skipped
                ]
                contents.append({
                    "name": c.name,
                    "types": list(c.types),
                    "filled": sum(1 for s in slots if s.status is SlotStatus.FILLED),
                    "total": len(slots),
                    "families": fams,
                })
        games.append({"game": p.stem, "contents": contents})
    return {"db_mtime": mtimes, "games": games}


def content_detail(cfg: AppConfig, game: str, name: str) -> dict:
    """가운데·오른쪽이 함께 쓰는 상세."""
    path = cfg.knowledge_path / f"{game}.db"
    if not path.exists():
        raise ContentNotFound(
            f"'{game}' 지식 DB가 없습니다. "
            f"채팅에서 'qatc slot init <컨텐츠> --game {game}' 으로 먼저 만드세요."
        )
    with KnowledgeStore(path) as st:
        c = st.get_content(name)
        if c is None:
            raise ContentNotFound(
                f"컨텐츠 '{name}'가 없습니다. "
                f"채팅에서 'qatc slot init {name}' 으로 먼저 만드세요."
            )
        slots = st.slots(name)
        cases = st.testcases(name)
        meta = st.testcase_meta(name)          # id -> (slot_keys, generated_hash)
    withdrawn = withdrawn_families(slots, {t.category_minor for t in cases})
    return {
        "name": c.name, "game": game, "types": list(c.types),
        "slots": [
            {"key": s.key, "hint": s.prompt_hint, "family": s.tc_family,
             "status": s.status.value, "value": s.value}
            for s in slots
        ],
        "testcases": [
            {"id": t.id, "family": t.category_minor, "title": t.title,
             "kind": t.kind.value, "priority": t.priority.value,
             "origin": t.origin.value, "precondition": t.precondition,
             "steps": list(t.steps), "expected": list(t.expected),
             "rationale": t.rationale, "slot_keys": meta[t.id][0],
             "edited": testcase_hash(t) != meta[t.id][1],
             "withdrawn": t.category_minor in withdrawn}
            for t in cases
        ],
    }

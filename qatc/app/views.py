"""SQLite → JSON. 읽기만 한다."""

from __future__ import annotations

import re
from pathlib import Path

from ..config import AppConfig
from ..knowledge.gate import plan_families, withdrawn_families
from ..knowledge.models import SlotStatus
from ..knowledge.slots import BASE_SLOTS
from ..knowledge.store import KnowledgeStore, testcase_hash


class ContentNotFound(Exception):
    """그 컨텐츠가 없다. 메시지는 완성된 한국어 문장이다."""


def _db_paths(cfg: AppConfig) -> list[Path]:
    return sorted(cfg.knowledge_path.glob("*.db"))


def resolve_db_path(cfg: AppConfig, game: str) -> Path:
    """`game` 이 가리키는 지식 DB 경로. 지식 루트 밖은 절대 돌려주지 않는다.

    `game` 은 `/api/content?game=…` 의 질의 문자열에서 곧장 온다. 예전에는
    그 값을 `knowledge_path / f"{game}.db"` 로 그대로 이어 붙였는데,
    `pathlib` 의 결합은 오른쪽이 절대 경로면 왼쪽을 통째로 버리고 `..` 도
    그대로 통과시킨다. 그리고 `KnowledgeStore.open()` 은 연결과 동시에
    `executescript(_SCHEMA)` + `commit()` 을 돌리므로 **경로를 여는 행위
    자체가 쓰기다** — 즉 그 한 줄이 "백엔드는 지식 DB 에 쓰지 않는다" 를
    디스크 전체로 넓혀 깨뜨렸다(실측: 지식 루트 밖 SQLite 파일이 8192 →
    32768 바이트로 커지고 qatc 테이블 셋이 심겼다. 라우트는 404 로 답해서
    아무 일도 없었던 것처럼 보였다).

    그래서 이름의 생김새를 흉내 내 검사하는 대신 **실재하는 DB 파일 목록과
    대조한다.** `p.stem` 은 구분자도 `..` 도 드라이브 문자도 담을 수 없으니,
    대조를 통과한 이름은 정의상 지식 루트 바로 아래 파일 하나다 — 새 형태의
    경로 우회가 나와도 이 대조는 자동으로 막는다. 정상 호출자는 영향이
    없다: 화면이 나르는 `game` 은 `/api/tree` 가 바로 그 `p.stem` 으로 만든
    값뿐이다.

    **`stem` 대조에도 왕복하지 않는 이름이 하나 있었다.** `Path(".db").stem`
    은 `".db"` 그대로다 — 앞에 점 하나뿐인 이름은 pathlib 가 확장자로 안
    본다. 지식 루트에 `.db` 라는 이름의 파일이 있으면(`*.db` 글롭이 숨김
    파일도 잡는다) `game=".db"` 가 `stem` 집합 대조는 통과하는데, 그 값을
    다시 `f"{game}.db"` 로 조립하면 `.db.db` 라는 **다른** 파일이 된다 —
    존재 확인 없이 그 경로를 열면 유령 DB 가 새로 생긴다(경로를 여는 행위
    자체가 쓰기라는 사실은 위 단락과 같다). 그래서 `stem` 을 비교하고
    문자열로 다시 짜 맞추는 대신, **조립한 경로 자체가 실재하는 DB 파일
    목록에 있는지**를 직접 본다. 정상 이름은 항상 왕복하므로(`/api/tree`
    가 주는 `game` 은 늘 `p.stem`) 영향이 없고, 왕복하지 않는 이름은 조립한
    경로가 목록에 없어 그대로 거부된다 — 그 경로에 `exists()`/`open()` 을
    한 번도 부르지 않으므로 이 비교 자체는 부작용이 없다.
    """
    paths = _db_paths(cfg)
    candidate = cfg.knowledge_path / f"{game}.db"
    if candidate not in paths:
        raise ContentNotFound(_no_such_game(game, {p.stem for p in paths}))
    return candidate


def _no_such_game(game: str, stems: set[str]) -> str:
    """"그런 게임 DB 는 없다" 를 한국어로, 다음 조치와 함께.

    경로 조각처럼 생긴 이름은 사용자가 직접 친 것이 아니다(화면은 트리가
    준 이름만 나른다) — 그 값을 그대로 되비추면서 "`--game <그 값>` 으로
    만드세요" 라고 하면 사용자를 엉뚱한 명령으로 보낸다. 그래서 그 경우엔
    되비추지 않고 트리에서 고르라고만 말한다.
    """
    if game in _GAME_NAME_FORBIDDEN or _FORBIDDEN_IN_GAME_NAME.search(game):
        return "게임 이름을 알아볼 수 없습니다. 왼쪽 트리에서 게임을 골라 다시 시도하세요."
    return (
        f"'{game}' 지식 DB가 없습니다. "
        f"채팅에서 'qatc slot init <컨텐츠> --game {game}' 으로 먼저 만드세요."
    )


#: 게임 이름에 절대 들어올 수 없는 문자 — 경로 구분자·드라이브 콜론·NUL·제어문자.
_FORBIDDEN_IN_GAME_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
#: 문자만으로는 걸러지지 않는 이름들 (빈 이름과 상대 경로 마디).
_GAME_NAME_FORBIDDEN = {"", ".", ".."}


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
                    # 모든 컨텐츠가 공통으로 받는 기준선. 유형별 슬롯과 나중에
                    # 더한 항목이 이 위로 쌓인다. **화면이 계산하지 않게** 여기서
                    # 실어 보낸다 — 프런트가 기본 슬롯 개수를 따로 알고 있으면
                    # 세트가 바뀔 때 조용히 어긋난다.
                    "base_total": len(BASE_SLOTS),
                    "families": fams,
                })
        games.append({"game": p.stem, "contents": contents})
    return {"db_mtime": mtimes, "games": games}


def content_detail(cfg: AppConfig, game: str, name: str) -> dict:
    """가운데·오른쪽이 함께 쓰는 상세."""
    path = resolve_db_path(cfg, game)
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

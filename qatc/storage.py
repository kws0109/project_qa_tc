"""세션 저장소 — SQLite 메타데이터 + 파일 시스템 프레임.

세션 하나가 폴더 하나다::

    sessions/<session_id>/
        session.db      메타/프레임/이벤트/상태/전이/TC/OCR캐시
        frames/*.jpg    캡처 원본
        masks/*.png     게임별 모션 마스크 (분석 산출물)
        flow.json       분석 결과 스냅샷 (사람이 읽고 diff 하기 좋게)
        export/         xlsx, mermaid

이미지를 DB에 넣지 않는 이유는 두 가지다. 수 GB BLOB이 SQLite를 느리게 만들고,
리뷰 중 스크린샷을 탐색기에서 바로 열어보고 싶기 때문이다.

**동시성**: 레코더(쓰기)와 리뷰 GUI(읽기)가 동시에 열릴 수 있어 WAL 모드를 켠다.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .models import (
    Frame,
    FlowGraph,
    InputEvent,
    ScreenState,
    SessionMeta,
    TestCase,
    Transition,
)

SCHEMA_VERSION = 1

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session (
    id              TEXT PRIMARY KEY,
    profile_name    TEXT NOT NULL,
    game_name       TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    capture_backend TEXT DEFAULT '',
    client_w        INTEGER DEFAULT 0,
    client_h        INTEGER DEFAULT 0,
    note            TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS frames (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    ts          REAL NOT NULL,
    path        TEXT NOT NULL,
    reason      TEXT NOT NULL,
    client_w    INTEGER NOT NULL,
    client_h    INTEGER NOT NULL,
    phash       TEXT DEFAULT '',
    masked_phash TEXT DEFAULT '',
    event_id    TEXT
);
CREATE INDEX IF NOT EXISTS idx_frames_ts ON frames(ts);
CREATE INDEX IF NOT EXISTS idx_frames_event ON frames(event_id);

CREATE TABLE IF NOT EXISTS input_events (
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    ts         REAL NOT NULL,
    kind       TEXT NOT NULL,
    nx         REAL, ny  REAL,
    nx2        REAL, ny2 REAL,
    key        TEXT,
    scroll_dy  INTEGER,
    note       TEXT,
    coords_reliable INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON input_events(ts);

CREATE TABLE IF NOT EXISTS states (
    id        TEXT PRIMARY KEY,
    auto_json TEXT NOT NULL,
    llm_json  TEXT,
    user_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transitions (
    id             TEXT PRIMARY KEY,
    from_state     TEXT NOT NULL,
    to_state       TEXT NOT NULL,
    event_id       TEXT,
    action_desc    TEXT DEFAULT '',
    target_json    TEXT,
    evidence_json  TEXT DEFAULT '[]',
    observed_count INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS step_order (
    seq           INTEGER PRIMARY KEY,
    transition_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS testcases (
    id              TEXT PRIMARY KEY,
    category_major  TEXT, category_minor TEXT,
    title           TEXT, precondition   TEXT,
    steps           TEXT, expected       TEXT,
    priority        TEXT, kind           TEXT, origin TEXT,
    evidence_frames TEXT, state_path     TEXT, edge_ids TEXT,
    rationale       TEXT
);

-- OCR은 느리다. 프레임 해시를 키로 캐시해 재분석 시 다시 돌리지 않는다.
CREATE TABLE IF NOT EXISTS ocr_cache (
    frame_hash  TEXT PRIMARY KEY,
    result_json TEXT NOT NULL
);

-- LLM 호출 원장. 비용 추적과 "무엇을 왜 물어봤는지" 감사에 쓴다.
CREATE TABLE IF NOT EXISTS llm_calls (
    id            TEXT PRIMARY KEY,
    ts            TEXT NOT NULL,
    purpose       TEXT NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read    INTEGER DEFAULT 0,
    cache_write   INTEGER DEFAULT 0,
    cost_usd      REAL DEFAULT 0.0
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SessionStore:
    """세션 하나에 대한 읽기/쓰기 진입점.

    스레드 안전: 레코더는 캡처 스레드와 훅 스레드에서 동시에 쓰기 때문에
    커넥션을 락으로 감싼다. SQLite 자체는 직렬화하지만 커밋 경합을 줄이려면
    애플리케이션 레벨 락이 더 예측 가능하다.
    """

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.frames_dir = self.root / "frames"
        self.masks_dir = self.root / "masks"
        self.export_dir = self.root / "export"
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # -- 수명주기 ----------------------------------------------------

    @classmethod
    def create(cls, sessions_root: Path | str, meta: SessionMeta) -> SessionStore:
        store = cls(Path(sessions_root) / meta.id)
        for d in (store.root, store.frames_dir, store.masks_dir, store.export_dir):
            d.mkdir(parents=True, exist_ok=True)
        store.open()
        store.put_session(meta)
        store.set_meta("schema_version", str(SCHEMA_VERSION))
        return store

    @classmethod
    def open_existing(cls, session_dir: Path | str) -> SessionStore:
        store = cls(session_dir)
        if not (store.root / "session.db").exists():
            raise FileNotFoundError(f"세션 DB가 없습니다: {store.root / 'session.db'}")
        store.open()
        return store

    def open(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self.root / "session.db", check_same_thread=False, timeout=30.0
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.commit()
                self._conn.close()
                self._conn = None

    def __enter__(self) -> SessionStore:
        if self._conn is None:
            self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SessionStore가 열려 있지 않습니다. open()을 먼저 호출하세요.")
        return self._conn

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchall()

    # -- meta / session ---------------------------------------------

    def set_meta(self, key: str, value: str) -> None:
        self._exec("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        rows = self._query("SELECT value FROM meta WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def put_session(self, meta: SessionMeta) -> None:
        d = meta.to_row()
        cols = ",".join(d)
        marks = ",".join("?" * len(d))
        self._exec(f"INSERT OR REPLACE INTO session({cols}) VALUES({marks})", d.values())

    def get_session(self) -> SessionMeta:
        rows = self._query("SELECT * FROM session LIMIT 1")
        if not rows:
            raise LookupError("세션 메타데이터가 없습니다")
        return SessionMeta.from_row(dict(rows[0]))

    def finish_session(self, backend: str = "") -> None:
        if backend:
            self._exec("UPDATE session SET capture_backend=?", (backend,))
        self._exec("UPDATE session SET ended_at=?", (utcnow(),))

    # -- frames ------------------------------------------------------

    def add_frame(self, frame: Frame) -> None:
        d = frame.to_row()
        cols = ",".join(d)
        marks = ",".join("?" * len(d))
        self._exec(f"INSERT OR REPLACE INTO frames({cols}) VALUES({marks})", d.values())

    def add_frames(self, frames: Iterable[Frame]) -> None:
        """배치 삽입. 레코더가 버스트 캡처 4장을 한 번에 넣을 때 쓴다."""
        rows = [f.to_row() for f in frames]
        if not rows:
            return
        cols = ",".join(rows[0])
        marks = ",".join("?" * len(rows[0]))
        with self._lock:
            self.conn.executemany(
                f"INSERT OR REPLACE INTO frames({cols}) VALUES({marks})",
                [tuple(r.values()) for r in rows],
            )
            self.conn.commit()

    def frames(self, *, order: str = "ts") -> list[Frame]:
        return [Frame.from_row(dict(r)) for r in self._query(f"SELECT * FROM frames ORDER BY {order}")]

    def frame(self, frame_id: str) -> Frame | None:
        rows = self._query("SELECT * FROM frames WHERE id=?", (frame_id,))
        return Frame.from_row(dict(rows[0])) if rows else None

    def frames_for_event(self, event_id: str) -> list[Frame]:
        rows = self._query("SELECT * FROM frames WHERE event_id=? ORDER BY ts", (event_id,))
        return [Frame.from_row(dict(r)) for r in rows]

    def update_frame_hashes(self, updates: Iterable[tuple[str, str, str]]) -> None:
        """(frame_id, phash, masked_phash) 배치 갱신. 분석 1단계에서 호출된다."""
        items = list(updates)
        if not items:
            return
        with self._lock:
            self.conn.executemany(
                "UPDATE frames SET phash=?, masked_phash=? WHERE id=?",
                [(p, m, fid) for fid, p, m in items],
            )
            self.conn.commit()

    def frame_path(self, frame: Frame | str) -> Path:
        rel = frame.path if isinstance(frame, Frame) else frame
        return self.root / rel

    # -- input events -----------------------------------------------

    def add_event(self, event: InputEvent) -> None:
        d = event.to_row()
        cols = ",".join(d)
        marks = ",".join("?" * len(d))
        self._exec(f"INSERT OR REPLACE INTO input_events({cols}) VALUES({marks})", d.values())

    def events(self) -> list[InputEvent]:
        return [
            InputEvent.from_row(dict(r))
            for r in self._query("SELECT * FROM input_events ORDER BY ts")
        ]

    def event(self, event_id: str) -> InputEvent | None:
        rows = self._query("SELECT * FROM input_events WHERE id=?", (event_id,))
        return InputEvent.from_row(dict(rows[0])) if rows else None

    # -- flow graph --------------------------------------------------

    def save_graph(self, graph: FlowGraph) -> None:
        """그래프 전체를 원자적으로 교체한다.

        증분 갱신을 하지 않는 이유: 상태 병합이 전이 끝점을 대량으로 바꾸기 때문에
        부분 갱신 로직이 곧바로 정합성 버그의 온상이 된다. 세션 규모(수백 노드)에서는
        통째로 다시 쓰는 편이 빠르고 안전하다.
        """
        with self._lock:
            cur = self.conn
            cur.execute("DELETE FROM states")
            cur.execute("DELETE FROM transitions")
            cur.execute("DELETE FROM step_order")
            for st in graph.states.values():
                d = st.to_row()
                cur.execute(
                    f"INSERT INTO states({','.join(d)}) VALUES({','.join('?' * len(d))})",
                    tuple(d.values()),
                )
            for tr in graph.transitions:
                d = tr.to_row()
                cur.execute(
                    f"INSERT INTO transitions({','.join(d)}) VALUES({','.join('?' * len(d))})",
                    tuple(d.values()),
                )
            cur.executemany(
                "INSERT INTO step_order(seq, transition_id) VALUES(?,?)",
                list(enumerate(graph.step_order)),
            )
            self.conn.commit()
        # 사람이 읽고 git diff 할 수 있는 스냅샷도 함께 남긴다
        (self.root / "flow.json").write_text(
            json.dumps(graph.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def load_graph(self) -> FlowGraph:
        session_id = self.get_session().id
        states = {
            r["id"]: ScreenState.from_row(dict(r)) for r in self._query("SELECT * FROM states")
        }
        transitions = [Transition.from_row(dict(r)) for r in self._query("SELECT * FROM transitions")]
        order = [r["transition_id"] for r in self._query("SELECT * FROM step_order ORDER BY seq")]
        return FlowGraph(session_id=session_id, states=states, transitions=transitions, step_order=order)

    def has_graph(self) -> bool:
        return bool(self._query("SELECT 1 FROM states LIMIT 1"))

    # -- testcases ---------------------------------------------------

    def save_testcases(self, testcases: Iterable[TestCase]) -> None:
        rows = [tc.to_row() for tc in testcases]
        with self._lock:
            self.conn.execute("DELETE FROM testcases")
            if rows:
                cols = ",".join(rows[0])
                marks = ",".join("?" * len(rows[0]))
                self.conn.executemany(
                    f"INSERT INTO testcases({cols}) VALUES({marks})",
                    [tuple(r.values()) for r in rows],
                )
            self.conn.commit()

    def testcases(self) -> list[TestCase]:
        return [TestCase.from_row(dict(r)) for r in self._query("SELECT * FROM testcases")]

    # -- OCR 캐시 ----------------------------------------------------

    def ocr_get(self, frame_hash: str) -> list[dict[str, Any]] | None:
        rows = self._query("SELECT result_json FROM ocr_cache WHERE frame_hash=?", (frame_hash,))
        return json.loads(rows[0]["result_json"]) if rows else None

    def ocr_put(self, frame_hash: str, result: list[dict[str, Any]]) -> None:
        self._exec(
            "INSERT OR REPLACE INTO ocr_cache(frame_hash,result_json) VALUES(?,?)",
            (frame_hash, json.dumps(result, ensure_ascii=False)),
        )

    # -- LLM 원장 ----------------------------------------------------

    def log_llm_call(
        self,
        call_id: str,
        purpose: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read: int = 0,
        cache_write: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        self._exec(
            "INSERT OR REPLACE INTO llm_calls"
            "(id,ts,purpose,model,input_tokens,output_tokens,cache_read,cache_write,cost_usd)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (call_id, utcnow(), purpose, model, input_tokens, output_tokens, cache_read, cache_write, cost_usd),
        )

    def llm_totals(self) -> dict[str, Any]:
        rows = self._query(
            "SELECT COUNT(*) n, SUM(input_tokens) i, SUM(output_tokens) o,"
            " SUM(cache_read) cr, SUM(cost_usd) c FROM llm_calls"
        )
        r = rows[0]
        return {
            "calls": r["n"] or 0,
            "input_tokens": r["i"] or 0,
            "output_tokens": r["o"] or 0,
            "cache_read_tokens": r["cr"] or 0,
            "cost_usd": round(r["c"] or 0.0, 4),
        }


def list_sessions(sessions_root: Path | str) -> list[tuple[Path, SessionMeta]]:
    """세션 폴더를 최근 순으로 나열한다. CLI의 세션 선택에 쓴다."""
    root = Path(sessions_root)
    found: list[tuple[Path, SessionMeta]] = []
    if not root.exists():
        return found
    for d in sorted(root.iterdir(), reverse=True):
        if not (d / "session.db").exists():
            continue
        try:
            with SessionStore.open_existing(d) as store:
                found.append((d, store.get_session()))
        except Exception:  # 손상된 세션은 목록에서 조용히 건너뛴다
            continue
    return found


def iter_frame_paths(store: SessionStore) -> Iterator[tuple[Frame, Path]]:
    for f in store.frames():
        yield f, store.frame_path(f)

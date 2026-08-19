"""게임 단위 지식 저장소.

게임 하나당 DB 하나에 컨텐츠가 쌓인다. 게임 단위로 묶는 이유는 **누적** 때문이다 —
유물 장착을 인터뷰할 때 이미 아는 파티 편성 지식을 참고하면 교차 질문이 가능해지고,
같은 것을 두 번 설명하지 않아도 된다.

대화 로그는 저장하지 않는다. Claude Code 트랜스크립트가 그 역할을 하므로 중복이다.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ..models import TCOrigin, TestCase
from .models import Content, Slot, SlotStatus, is_blank
from .slots import build_slot_set

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contents (
    name       TEXT PRIMARY KEY,
    game       TEXT NOT NULL,
    types      TEXT NOT NULL DEFAULT '[]',
    code       TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS tc_seq (
    content TEXT PRIMARY KEY,
    last    INTEGER NOT NULL DEFAULT 0
);
"""


#: 컨텐츠 코드 형식 — 영문 대문자와 숫자 2~12자 (스펙 §3).
_CODE_PATTERN = re.compile(r"[A-Z0-9]{2,12}")


def is_valid_code_format(code: str) -> bool:
    """컨텐츠 코드 형식을 검사한다. DB 연결이 필요 없다.

    `cmd_slot_init` 이 이 함수로 DB를 열기 전에 미리 걸러낸다 — 형식이 틀린
    코드로 새 게임 DB 파일이 조용히 생기는 것을 막기 위해서다(오타 하나로
    빈 DB가 생기지 않게 하는 이 CLI의 기존 방침과 같다). `KnowledgeStore`
    자신도 `set_content_code`·`init_content` 에서 같은 함수를 쓴다 — 두 곳이
    각자 정규식을 베끼면 한쪽만 느슨해지는 회귀가 생긴다.
    """
    return bool(_CODE_PATTERN.fullmatch(code))


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
        self._ensure_code_column(self._conn)
        return self

    def _ensure_code_column(self, db) -> None:
        """옛 DB 에 `contents.code` 를 보강한다.

        스키마 문의 `IF NOT EXISTS` 는 테이블이 이미 있으면 아무것도 하지
        않으므로, 이 컬럼은 영영 생기지 않는다 — 첫 실사용으로 만들어진 DB 가
        그 경우다.
        """
        names = {r["name"] for r in db.execute("PRAGMA table_info(contents)")}
        if "code" not in names:
            db.execute("ALTER TABLE contents ADD COLUMN code TEXT NOT NULL DEFAULT ''")
            db.commit()

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

    def content_code(self, name: str) -> str:
        row = self._db().execute(
            "SELECT code FROM contents WHERE name = ?", (name,)).fetchone()
        return row["code"] if row else ""

    def _validate_code(self, code: str) -> None:
        """형식만 본다 — 존재·중복은 호출 맥락(새 컨텐츠냐 기존이냐)마다 달라
        여기서 함께 보지 않는다."""
        if not is_valid_code_format(code):
            raise ValueError(
                f"컨텐츠 코드는 영문 대문자와 숫자 2~12자여야 합니다 (예: LOGIN) "
                f"— 받은 값: '{code}'."
            )

    def _conflicting_owner(self, code: str, name: str) -> str | None:
        """`code` 를 이미 `name` 아닌 다른 컨텐츠가 쓰고 있으면 그 이름(들)을 돌려준다.

        `codes_in_use()` 의 값은 한 코드를 여러 컨텐츠가 쓰면 이름을 콤마로
        이어붙인 문자열이다 — 단일 컨텐츠 이름이라 가정하고 등호로 비교하면
        (예: `owner == name`) 중복 코드가 있는 DB 에서 조용히 틀린 답을 얻는다.
        """
        owner = self.codes_in_use().get(code)
        return owner if owner and owner != name else None

    def _ensure_code_available(self, code: str, name: str) -> None:
        owner = self._conflicting_owner(code, name)
        if owner:
            raise ValueError(f"코드 {code}는 이미 '{owner}'가 쓰고 있습니다. 다른 약어를 지정하세요.")

    def set_content_code(self, name: str, code: str) -> None:
        """컨텐츠에 TC ID 접두어를 단다.

        형식·존재·중복·기존코드 네 가지를 **여기서** 검사한다. 예전에는 앞의
        세 가지를 `cmd_slot_init` 만 (그것도 `init_content` 를 부르기 **전에**)
        검사했다. 그런데 `apply_reclassification`(승인된 재분류 마이그레이션)은
        이 메서드를 직접 불러 그 검사를 통째로 우회한다. 실측(Bug C): `로그인보상`이
        먼저 `slot init --code LOGIN` 으로 LOGIN 을 선점해도(그 시점엔 `로그인`이
        아직 코드가 없어 `codes_in_use()` 에 안 잡힌다), 마이그레이션이
        `set_content_code(로그인, LOGIN)` 을 그대로 밀어붙이면 두 컨텐츠가
        같은 코드를 갖게 되고, 독립된 `tc_seq` 카운터가 같은 `TC_LOGIN_NNN`
        을 내놓아 `testcases.id` 가 기본키인 `INSERT OR REPLACE` 가 한쪽을
        지운다. 이 메서드를 유일한 쓰기 경로로 만들면 호출자가 누구든
        (CLI든 마이그레이션이든) 이 검사를 피해갈 수 없다.

        **이미 확정된 코드를 다른 값으로 바꾸는 것도 거절한다.** `cmd_slot_init`
        은 같은 규칙을 CLI 층에서만 봤는데, 이 메서드를 직접 부르는 호출자
        (마이그레이션 포함)는 그 CLI 검사를 거치지 않는다 — 바꾸면 이미 발급된
        `TC_<코드>_<번호>` 가 가리키던 컨텐츠가 바뀌어 인용된 ID 가 죽는다.
        코드가 아직 없는 컨텐츠(빈 문자열)에 처음 코드를 다는 것과, 이미 있는
        코드를 **같은 값으로** 다시 다는 것(마이그레이션 재실행, 멱등성)은
        막지 않는다 — 둘 다 기존 발급 ID 와 어긋나지 않는다.
        """
        self._validate_code(code)
        if self.get_content(name) is None:
            raise KeyError(f"컨텐츠 '{name}'가 없습니다. 먼저 slot init을 실행하세요.")
        current = self.content_code(name)
        if current and current != code:
            raise ValueError(
                f"'{name}'의 코드는 이미 '{current}'로 확정되어 있어 '{code}'로 "
                f"바꿀 수 없습니다 — 이미 발급된 TC ID(TC_{current}_NNN)가 그 "
                f"코드를 가리킵니다. 새 컨텐츠로 분리하거나, 기존 ID 를 그대로 "
                f"쓰려면 코드를 '{current}'로 유지하세요."
            )
        self._ensure_code_available(code, name)
        db = self._db()
        db.execute("UPDATE contents SET code = ? WHERE name = ?", (code, name))
        db.commit()

    def codes_in_use(self) -> dict[str, str]:
        """코드 -> 컨텐츠 이름(들). 중복 판정에 쓴다.

        한 코드를 두 컨텐츠가 갖고 있으면(이 가드가 생기기 전 데이터이거나,
        DB를 직접 고친 경우) 이름 하나만 골라 돌려주는 예전 구현은 나머지
        소유자를 감췄다 — SQL이 어느 행을 나중에 주느냐에 따라 **틀린** 단독
        소유자를 진단자에게 보여준 것이다(실측: Bug C 재현에서 '로그인'이
        진짜 침입자인데 '로그인보상'이 찍혔다). 중복이면 소유자를 전부
        콤마로 나열해, 적어도 "단독 소유가 아니다"는 사실이 드러나게 한다.
        """
        rows = self._db().execute(
            "SELECT code, name FROM contents WHERE code != '' ORDER BY name"
        ).fetchall()
        owners: dict[str, list[str]] = {}
        for r in rows:
            owners.setdefault(r["code"], []).append(r["name"])
        return {code: ", ".join(names) for code, names in owners.items()}

    def init_content(self, name: str, game: str, types: Sequence[str], code: str = "") -> Content:
        """컨텐츠를 만들거나, 이미 있으면 새 유형의 슬롯만 덧붙인다.

        **기존 슬롯의 값과 상태는 절대 건드리지 않는다.** 재실행이 사용자가 채운
        내용을 지우면 아무도 다시 실행하지 않는다.

        `code` 는 TC ID 접두어다. 이미 코드가 있으면 재실행이 덮지 않는다 —
        덮으면 이미 발급된 ID(`TC_<코드>_<번호>`)와 어긋난다. `code` 는 여기서는
        선택값이다 — 컨텐츠를 만드는 것과 TC ID 를 발급하는 것은 다른 결정이라,
        코드 없는 컨텐츠도 만들 수 있어야 한다 (거절은 `_next_tc_id` 쪽 몫이다).

        새 컨텐츠에 `code` 를 함께 주면 **행을 넣기 전에** 형식·중복을 검사한다.
        기존 컨텐츠에 코드를 새로 다는 경우도 마찬가지로 **`UPDATE` 전에**
        검사를 끝낸다. 먼저 쓰고 나중에 검사하면, 검사가 실패해도 그 실패가
        `KnowledgeStore.close()` 의 무조건 커밋(예외와 무관하게 항상 커밋한다)을
        타고 그대로 확정된다 — 실패한 명령(rc!=0)인데 새 컨텐츠는 코드 없는
        유령으로, 기존 컨텐츠는 `types` 만 조용히 바뀐 채로 남는다(실측: 다른
        컨텐츠가 이미 쓰는 코드로 기존 컨텐츠에 `slot init --code` 를 다시
        부르면, 명령은 rc=1 로 실패해도 `types` 확장은 커밋돼 있었다).
        """
        db = self._db()
        existing = self.get_content(name)
        merged = list(dict.fromkeys([*(existing.types if existing else []), *types]))
        specs = build_slot_set(merged)  # 모르는 유형이면 여기서 ValueError

        if existing is None:
            if code:
                self._validate_code(code)
                self._ensure_code_available(code, name)
            db.execute(
                "INSERT INTO contents (name, game, types, code, created_at) VALUES (?, ?, ?, ?, ?)",
                (name, game, json.dumps(merged, ensure_ascii=False), code,
                 datetime.now(timezone.utc).isoformat()),
            )
        else:
            # 코드를 새로 달 것인지부터 정하고, 그럴 것이면 검사까지 아래
            # `UPDATE`(types) 보다 먼저 끝낸다 — 이미 코드가 있으면(재실행)
            # 손대지 않으므로 검사 자체가 필요 없다.
            will_set_code = bool(code) and not self.content_code(name)
            if will_set_code:
                self._validate_code(code)
                self._ensure_code_available(code, name)
            db.execute(
                "UPDATE contents SET types = ? WHERE name = ?",
                (json.dumps(merged, ensure_ascii=False), name),
            )
            if will_set_code:
                db.execute("UPDATE contents SET code = ? WHERE name = ?", (code, name))

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

    def _next_tc_id(self, content: str) -> str:
        """`TC_<코드>_<번호>`. 번호는 컨텐츠 안에서 단조 증가하고 재사용하지 않는다.

        살아 있는 행의 최대값을 쓰지 않는 이유: 전부 지운 뒤 새로 만들면 001
        로 되돌아가, 지워진 TC 를 가리키던 번호가 다른 TC 를 가리키게 된다.
        그래서 마지막 번호를 `tc_seq` 에 따로 기억한다.

        **커밋은 여기서 하지 않는다.** 호출자(`add_testcase`/`replace_generated`)가
        번호표 갱신과 TC 저장(또는 그 앞의 삭제)을 한 트랜잭션으로 묶는다 —
        여기서 커밋하면 `replace_generated` 의 삭제까지 앞당겨 확정시켜, 그
        뒤 삽입이 실패해도 삭제만 살아남는다(Bug A 의 원자성 부분).
        """
        code = self._require_content_code(content)
        db = self._db()
        row = db.execute("SELECT last FROM tc_seq WHERE content = ?", (content,)).fetchone()
        nxt = (row["last"] if row else 0) + 1
        db.execute("INSERT OR REPLACE INTO tc_seq (content, last) VALUES (?, ?)",
                   (content, nxt))
        return f"TC_{code}_{nxt:03d}"

    def add_testcase(
        self, content: str, family: str, tc: TestCase, slot_keys: Sequence[str]
    ) -> TestCase:
        """TC를 저장한다. `id` 가 비어 있으면 부여한다."""
        tc = self._insert_testcase(content, family, tc, slot_keys)
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
        sql = "SELECT row, family FROM testcases WHERE content = ?"
        args: list[str] = [content]
        if family is not None:
            sql += " AND family = ?"
            args.append(family)
        sql += " ORDER BY rowid"
        out = []
        for r in self._db().execute(sql, args):
            tc = TestCase.from_row(json.loads(r["row"]))
            # 컬럼이 진실이다. `row` 안에 `family` 가 있든 없든(옛 행에는 없다)
            # 여기서 덮어써 한 값만 남긴다.
            tc.family = r["family"]
            out.append(tc)
        return out

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

    def clear_testcases(self, content: str) -> int:
        """한 컨텐츠의 TC 를 전부 지운다. 지운 수를 돌려준다.

        마이그레이션 전용이다 — 평소에는 `replace_generated` 가 계열 단위로
        갈아끼우며 사람이 고친 것을 보존한다. 여기서는 승인된 표로 통째로
        갈아엎는 것이 의도다.
        """
        db = self._db()
        n = db.execute("SELECT COUNT(*) AS c FROM testcases WHERE content = ?",
                       (content,)).fetchone()["c"]
        db.execute("DELETE FROM testcases WHERE content = ?", (content,))
        db.commit()
        return n

    def set_tc_seq(self, content: str, last: int) -> None:
        """번호표를 특정 값으로 맞춘다.

        마이그레이션은 ID 를 JSON 에서 그대로 가져오므로 번호표를 건드리지
        않는다. 그대로 두면 다음에 만드는 TC 가 001 을 받아 이미 쓰인 번호와
        부딪힌다.
        """
        db = self._db()
        db.execute("INSERT OR REPLACE INTO tc_seq (content, last) VALUES (?, ?)",
                   (content, last))
        db.commit()

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

        **아무것도 지우기 전에 코드 없음을 거절한다** (Bug A). 예전에는 이
        검사가 `_next_tc_id` 안, 즉 삽입 루프 한가운데에만 있어서, 코드 없는
        컨텐츠에 `tc add` 를 다시 부르면 아래 삭제 루프가 끝나고 커밋까지 된
        **다음에** 이 검사가 실패했다(실측: DELETE → COMMIT → raise → 삽입
        0건 — 사용자에게는 `오류: KeyError: ...` 한 줄과 함께 계열이 조용히
        비워진 결과만 남았다).

        **삭제와 삽입을 한 트랜잭션으로 묶는다** (Bug A 의 원자성 부분, 별도
        마이너 리뷰에서도 같은 비원자성이 지적됐다). 예전에는 삭제 루프 뒤에
        `db.commit()` 이 있어, 그 다음 삽입 루프 도중 아무 예외든 나면 삭제만
        확정된 채 끝났다. 지금은 실패하면 `rollback()` 하고 다시 던진다 —
        `KnowledgeStore.close()` 가 예외와 무관하게 항상 커밋하므로, 여기서
        직접 롤백하지 않으면 그 무조건 커밋이 부분 삭제를 그대로 확정시켜
        버린다.

        **배치 안에서 (중분류, 소분류) 가 겹치면 거절한다** (Bug B). 겹치는
        두 케이스는 기존 행의 id 를 물려받을 때(`inherited`) 똑같은 id 를
        받고, 나중 것이 `INSERT OR REPLACE` 로 앞의 것을 지운다 — 그런데
        반환값은 여전히 `len(cases)` 라 `tc add` 는 "2건 저장" rc=0 을 찍고
        실제로는 1건만 남는다. 조용히 번호를 나눠 주는 대신 거절한다 —
        표에서 두 TC 를 구별할 수 없다는 문제 자체가 기존 행의 유무와
        무관하게 성립하기 때문이다.

        :returns: (추가한 수, 보존한 수, 지운 수)
        """
        self._require_content_code(content)          # Bug A: 지우기 전에 거절

        # Bug B: 배치 안에서 (중분류, 소분류) 가 겹치면 거절한다. 기존 행이
        # 있든 없든 상관없다 — 겹치면 표에서 두 TC 를 구별할 수 없다는 문제
        # 자체는 기존 행의 유무와 무관하게 성립한다.
        seen: dict[tuple[str, str], bool] = {}
        for tc in cases:
            key = (tc.category_minor, tc.category_sub)
            if key in seen:
                raise ValueError(
                    f"'{content}'의 '{family}' 계열에서 '{tc.category_minor} › "
                    f"{tc.category_sub}' 조합이 이 배치에 두 번 있습니다. 같은 "
                    f"(중분류, 소분류) 는 같은 TC 로 보고 번호를 물려주므로, 둘 다 "
                    f"저장하면 나중 것이 앞의 것을 덮어씁니다. 소분류 이름을 "
                    f"구분되게 다시 지어 다시 시도하세요."
                )
            seen[key] = True

        db = self._db()
        kept = 0
        deleted = 0
        try:
            # `ORDER BY id` — 재설계 이전 행은 소분류가 없어 한 계열의 모든
            # 행이 (옛_중분류, "") 하나로 뭉친다. 그런 쌍이 여럿이면 정렬
            # 없이는 어느 id 가 새 배치로 넘어갈지 sqlite 의 반환 순서에
            # 달리게 되어, 인덱스 재구축 등으로 순서가 바뀌면 같은 입력인데도
            # 실행마다 물려받는 번호가 달라진다. `testcases()` 가 같은 이유로
            # `ORDER BY rowid` 를 쓰는 것과 같은 문제다.
            rows = db.execute(
                "SELECT id, generated_hash, row FROM testcases WHERE content = ? AND family = ?"
                " ORDER BY id",
                (content, family),
            ).fetchall()

            # 같은 (중분류, 소분류) 면 같은 TC 로 본다 - 본문을 다시 써도 번호를
            # 물려주기 위해서다. 소분류가 케이스 이름이 되면서 가능해진 대조다.
            inherited: dict[tuple[str, str], str] = {}
            for r in rows:
                old = TestCase.from_row(json.loads(r["row"]))
                inherited[(old.category_minor, old.category_sub)] = r["id"]

            for r in rows:
                tc = TestCase.from_row(json.loads(r["row"]))
                edited = testcase_hash(tc) != r["generated_hash"]
                if tc.origin is TCOrigin.USER or edited:
                    kept += 1
                    # 이 TC 는 아직 살아 있다 — 번호를 새 배치에 물려주면 두 TC 가
                    # 같은 id 를 갖게 된다. 물려줄 후보에서 뺀다.
                    inherited.pop((tc.category_minor, tc.category_sub), None)
                    continue
                db.execute("DELETE FROM testcases WHERE id = ?", (r["id"],))
                deleted += 1

            for tc in cases:
                if not tc.id:
                    tc.id = inherited.get((tc.category_minor, tc.category_sub), "")
                self._insert_testcase(content, family, tc, slot_keys)
        except BaseException:
            # `except Exception` 은 `KeyboardInterrupt`/`SystemExit` 을 놓친다 —
            # 삽입 루프 한가운데서 Ctrl-C 가 들어오면 그 둘은 `BaseException` 만
            # 상속하므로 이 롤백을 타지 않고 그대로 빠져나가, 이미 실행된
            # DELETE 가 `KnowledgeStore.close()` 의 무조건 커밋을 타고 확정된다
            # (계열이 통째로 비워진 채로 남는다). 어떤 방식의 중단이든 롤백해야
            # 원자성이 실제로 지켜진다.
            db.rollback()
            raise
        db.commit()
        return len(cases), kept, deleted

    def _require_content_code(self, content: str) -> str:
        """`content` 의 TC 코드를 돌려준다. 없으면 지어내지 않고 거절한다.

        `_next_tc_id`(개별 저장)와 `replace_generated`(계열 갈아끼우기) 양쪽이
        쓴다. 후자는 **아무것도 지우기 전에** 이 검사를 통과해야 한다.
        """
        code = self.content_code(content)
        if not code:
            raise KeyError(
                f"'{content}'에 컨텐츠 코드가 없어 TC ID를 만들 수 없습니다. "
                # 이 거절은 (replace_generated 호출자라면) 삭제보다 먼저
                # 일어난다 — 그런데 문구가 그 사실을 말하지 않으면, 예전에
                # 이 컨텐츠에서 TC 를 잃어본 사용자는 이번에도 지워졌는지
                # 아닌지를 문구만으로는 알 수 없다.
                f"기존 TC 는 지우지 않았습니다. "
                f"'qatc slot init {content} --code <영문대문자약어>' 로 먼저 정하세요."
            )
        return code

    def _insert_testcase(
        self, content: str, family: str, tc: TestCase, slot_keys: Sequence[str]
    ) -> TestCase:
        """TC 한 건을 쓴다 (커밋 없음).

        `add_testcase`(단건 저장, 즉시 커밋)와 `replace_generated`(삭제+삽입을
        한 트랜잭션으로 묶어야 하는 배치 교체) 양쪽이 이 위에서 각자 트랜잭션
        경계를 정한다 — 여기서 커밋하면 배치 쪽이 원자적일 수 없다.
        """
        if not tc.id:
            tc.id = self._next_tc_id(content)
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
        return tc


def testcase_hash(tc: TestCase) -> str:
    """TC 본문의 해시. `id` 와 `origin` 은 제외한다.

    id는 저장 시 부여되는 것이고 origin은 메타데이터라, 둘 중 하나가 달라졌다고
    "사용자가 고쳤다"로 볼 수 없다. 사람이 실제로 고치는 것은 제목·절차·기대결과다.

    `category_sub` 도 반드시 들어가야 한다 — `title` 이 더 이상 쓰이지 않는
    지금은 그 자리를 소분류가 대신한다. 소분류가 형제 케이스를 구분하는
    유일한 이름인데 해시에서 빠지면, 소분류만 고친 사람의 편집이 안 보여
    `replace_generated` 가 다음 재생성 때 조용히 지워 버린다.
    """
    payload = json.dumps(
        {
            "category_major": tc.category_major,
            "category_minor": tc.category_minor,
            "category_sub": tc.category_sub,
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

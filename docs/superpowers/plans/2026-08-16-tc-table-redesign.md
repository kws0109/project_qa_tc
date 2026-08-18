# TC 테이블 재설계 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** TC 표의 대·중·소분류를 화면·기능 계층으로 바꾸고, 제목과 방법론 컬럼을 없애고, TC ID 를 `TC_LOGIN_001` 형태로 만든다. 기존 `로그인` 23건은 승인된 분류표대로 29건으로 옮긴다.

**Architecture:** 가장 위험한 변경을 맨 앞에 홀로 둔다 — 지금 여덟 자리가 `tc.category_minor` 를 **계열의 대용품**으로 읽는데, 그 칸의 의미가 바뀌면 전부 조용히 틀린다. 그래서 Task 1 이 `family` 를 진짜 필드로 올려 소비자를 옮기고(동작 변화 없음), 그 다음에야 `category_minor` 의 의미를 바꾼다. 마이그레이션의 판단은 이미 끝나 JSON 으로 커밋돼 있으므로 구현은 적용만 한다.

**Tech Stack:** Python 3.11+ · SQLite · openpyxl · Flask · pytest

**Spec:** [../specs/2026-08-16-tc-table-redesign.md](../specs/2026-08-16-tc-table-redesign.md)

## Global Constraints

- Windows 전용. 경로는 `pathlib.Path`.
- 콘솔 출력은 `qatc/console.py` 의 `_p()` / `_p(msg, err=True)`, 경고는 `_warn()`. 맨 `print()` 금지.
- 테스트: `.venv/Scripts/python.exe -m pytest` — `-q` 를 더 붙이면 `-qq` 가 되어 개수 줄이 사라진다.
- **백엔드는 지식 DB 에 쓰지 않는다.** `qatc/app/` 안의 어떤 파일도 지식 DB 쓰기 메서드 이름(`add_testcase` · `set_slot` · `init_content` · `replace_generated` · `add_slot` · `update_testcase_row`)을 담지 않는다 (주석·도크스트링 포함).
- 사용자·화면에 보이는 문자열은 한국어. 오류는 **다음 조치**를 함께 알린다.
- 빈 값 판정은 `qatc/knowledge/models.py` 의 `is_blank` 를 쓴다 — `strip()` 은 제로폭 공백·한글 필러를 하나도 지우지 않는다.
- 작업 트리는 **전부 CRLF** (git 은 `core.autocrlf=true` 로 LF 저장). 편집 후 바이트와 줄바꿈을 각각 확인할 것.
- 커밋 메시지는 한국어. 마지막 줄에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- 시작 테스트 수: **594 passed**.
- **새 테스트는 전부 뮤테이션으로 검증한다.** 구현을 깨뜨려 그 테스트가 실패하는지 확인하고 **에디터로** 복원한다. `git checkout`/`stash`/`reset` 금지.
- 실제 `claude` 턴을 돌리지 않는다.

## 이 계획의 유일한 조용한 실패 경로

`tc.category_minor` 를 계열로 읽는 자리가 여덟 곳이다:

| 파일 | 무엇을 하는가 |
|---|---|
| `qatc/app/views.py` (3곳) | 트리의 계열별 TC 개수, 근거 철회 판정 2곳 |
| `qatc/app/server.py` (1곳) | 엑셀 내보내기 전 철회 판정 |
| `qatc/cli_knowledge.py` (3곳) | `tc list` 의 철회 판정·출력, `export` 의 철회 판정 |
| `qatc/export/tc_excel.py` (2곳) | 행의 철회 표시, 요약의 철회 개수 |

여기에 계열이 아닌 값이 들어가면 **예외도 빈 결과도 아니고 그냥 잘못된 분류가 나온다.** Task 1 이 이것만 처리한다.

---

### Task 1: `family` 를 필드로 올리고 계열 대용 사용을 끝낸다

**Files:**
- Modify: `qatc/models.py`, `qatc/knowledge/store.py`, `qatc/app/views.py`, `qatc/app/server.py`, `qatc/cli_knowledge.py`, `qatc/export/tc_excel.py`
- Modify: `tests/test_app_views.py`, `tests/test_knowledge_tc_store.py`

**Interfaces:**
- Produces: `TestCase.family: str = ""` — 그 TC 가 속한 계열. `KnowledgeStore.testcases()` 가 DB 의 `testcases.family` 컬럼에서 채워 돌려준다.

**동작은 바뀌지 않는다.** 지금 `category_minor` 와 `family` 는 같은 값이라, 이 작업만으로는 사용자가 보는 것이 하나도 안 바뀐다. 그것이 의도다 — 의미가 갈리기 **전에** 소비자를 옮겨 놓는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_app_views.py` 에 추가:

```python
def test_family_is_read_from_the_column_not_from_category_minor(cfg):
    """계열은 `category_minor` 가 아니라 DB 의 `family` 컬럼에서 온다.

    이 둘은 지금 같은 값이지만 곧 갈라진다. 갈라진 뒤에도 트리·철회 판정이
    맞으려면 소비자가 **지금** 옮겨져 있어야 한다. 그래서 두 값이 다른 TC 를
    일부러 만들어, `category_minor` 를 계열로 착각하는 코드를 실패시킨다.
    """
    _seed(cfg)
    with KnowledgeStore(cfg.knowledge_path / "starrail.db") as st:
        tc = _tc(title="비활성 유지", family="경계값")
        tc.category_minor = "신규 계정 연동"      # 계열이 아니라 메뉴 이름
        st.add_testcase("파티편성", "경계값", tc, ["constraints"])

    got = [t for t in st_testcases(cfg) if t.title == "비활성 유지"][0]
    assert got.family == "경계값"
    assert got.category_minor == "신규 계정 연동"


def st_testcases(cfg):
    with KnowledgeStore(cfg.knowledge_path / "starrail.db") as st:
        return st.testcases("파티편성")


def test_the_tree_counts_by_family_not_by_category_minor(cfg):
    """트리의 계열별 TC 개수가 메뉴 이름으로 세어지면 안 된다."""
    _seed(cfg)
    with KnowledgeStore(cfg.knowledge_path / "starrail.db") as st:
        tc = _tc(title="비활성 유지", family="경계값")
        tc.category_minor = "신규 계정 연동"
        st.add_testcase("파티편성", "경계값", tc, ["constraints"])

    fams = {f["family"]: f for f in tree(cfg)["games"][0]["contents"][0]["families"]}
    assert fams["경계값"]["tc_count"] == 1, "계열이 아니라 중분류로 셌습니다"


def test_withdrawal_is_judged_by_family_not_by_category_minor(cfg):
    """근거 철회 판정도 계열 단위다 — 중분류로 판정하면 엉뚱한 TC 가 표시된다."""
    _seed(cfg)
    with KnowledgeStore(cfg.knowledge_path / "starrail.db") as st:
        tc = _tc(title="비활성 유지", family="경계값")
        tc.category_minor = "신규 계정 연동"
        st.add_testcase("파티편성", "경계값", tc, ["constraints"])
        st.set_slot("파티편성", "constraints", SlotStatus.EMPTY)   # 근거를 다시 연다

    detail = content_detail(cfg, "starrail", "파티편성")
    row = [t for t in detail["testcases"] if t["title"] == "비활성 유지"][0]
    assert row["withdrawn"] is True
```

`_tc` 헬퍼가 `family` 인자를 받지 않으면 받도록 넓힌다 (`category_minor=family` 는 그대로 두고 `TestCase` 생성 시 `family=family` 를 함께 넘긴다).

`tests/test_knowledge_tc_store.py` 에 추가:

```python
def test_stored_testcases_come_back_with_their_family(tmp_path, make_tc):
    """`family` 는 `row` JSON 이 아니라 컬럼이 진실이다.

    옛 행에는 `row` 안에 `family` 키가 아예 없다. 컬럼에서 채우면 옛 행도
    그대로 읽히고, 컬럼과 `row` 가 어긋날 여지도 없어진다.
    """
    from qatc.knowledge.store import KnowledgeStore

    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("c", game="g", types=[])
        st.add_testcase("c", "경계값", make_tc(title="T"), ["constraints"])
        got = st.testcases("c")
    assert [t.family for t in got] == ["경계값"]
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app_views.py tests/test_knowledge_tc_store.py`
Expected: 새 4건 FAIL — `AttributeError: 'TestCase' object has no attribute 'family'`

- [ ] **Step 3: 필드를 더하고 저장소가 채우게 한다**

`qatc/models.py` 의 `TestCase` 에 필드를 더한다. **`category_minor` 바로 아래**에 두어 둘의 관계가 눈에 보이게 한다:

```python
    category_major: str = ""
    category_minor: str = ""
    #: 이 TC 가 속한 계열. 게이트(`plan_families`)와 근거 철회 판정이 쓰는 단위다.
    #: `category_minor` 와 값이 같던 시절이 있었지만 그건 우연이었다 — 그쪽은
    #: 이제 화면·메뉴 계층을 담는다. 진실은 `testcases.family` 컬럼이고,
    #: `KnowledgeStore.testcases()` 가 그 값으로 이 필드를 채운다.
    family: str = ""
```

`qatc/knowledge/store.py` 의 `testcases()` 가 컬럼을 함께 읽어 채운다:

```python
    def testcases(self, content: str, family: str | None = None) -> list[TestCase]:
        sql = "SELECT row, family FROM testcases WHERE content = ?"
        params: list = [content]
        if family is not None:
            sql += " AND family = ?"
            params.append(family)
        out = []
        for r in self._db().execute(sql, params):
            tc = TestCase.from_row(json.loads(r["row"]))
            # 컬럼이 진실이다. `row` 안에 `family` 가 있든 없든(옛 행에는 없다)
            # 여기서 덮어써 한 값만 남긴다.
            tc.family = r["family"]
            out.append(tc)
        return out
```

기존 `testcases()` 의 나머지 형태(정렬·필터)는 그대로 둔다.

- [ ] **Step 4: 소비자 여덟 자리를 옮긴다**

`tc.category_minor` 를 **계열로 읽던** 자리만 `tc.family` 로 바꾼다. 표시용으로 쓰던 자리는 건드리지 않는다.

| 파일 | 바꿀 것 |
|---|---|
| `qatc/app/views.py` | `withdrawn_families(slots, {t.category_minor for t in cases})` 2곳 -> `t.family`; `counts[t.category_minor]` -> `counts[t.family]`; `"family": t.category_minor` -> `t.family`; `t.category_minor in withdrawn` -> `t.family in withdrawn` |
| `qatc/app/server.py` | `{tc.category_minor for tc in cases}` -> `tc.family` |
| `qatc/cli_knowledge.py` | `withdrawn_families(...)` 2곳과 `tc.category_minor in withdrawn`, 출력의 `[{tc.category_minor}]` -> 전부 `tc.family` |
| `qatc/export/tc_excel.py` | `tc.category_minor in withdrawn` 2곳 -> `tc.family in withdrawn` |

`tc_excel.py` 의 **행 값** `tc.category_minor` (중분류 칸)는 표시용이므로 **그대로 둔다** — Task 2 에서 다룬다.

- [ ] **Step 5: 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest`
Expected: 594 + 4 = **598 passed**

- [ ] **Step 6: 뮤테이션 검증**

| # | 뮤테이션 | 죽어야 하는 테스트 |
|---|---|---|
| M1 | `tc.family = r["family"]` 제거 | `test_stored_testcases_come_back_with_their_family` |
| M2 | `views.py` 의 개수 집계를 `category_minor` 로 되돌림 | `test_the_tree_counts_by_family_not_by_category_minor` |
| M3 | `views.py` 의 철회 판정을 `category_minor` 로 되돌림 | `test_withdrawal_is_judged_by_family_not_by_category_minor` |

- [ ] **Step 7: 커밋**

```bash
git commit -m "계열을 필드로 올린다 — category_minor 를 대용품으로 쓰던 여덟 자리를 옮긴다"
```

---

### Task 2: 소분류를 더하고 엑셀 컬럼을 바꾼다

**Files:**
- Modify: `qatc/models.py`, `qatc/export/tc_excel.py`
- Modify: `tests/test_tc_excel.py`

**Interfaces:**
- Consumes: `TestCase.family` (Task 1)
- Produces: `TestCase.category_sub: str = ""` — 소분류(케이스 이름)

새 컬럼 구성:

```
TC ID · 대분류 · 중분류 · 소분류 · 사전조건 · 절차 · 기대결과 · 우선순위 · 출처 · 근거 · 근거 상태
```

`제목` 과 `유형` 이 빠지고 `소분류` 가 들어간다. `유형`(정상 경로·경계값…)을 빼는 이유는 **개별 TC 에 방법론을 적지 않기로** 했기 때문이다. `출처`(인터뷰/추론됨)는 방법론이 아니라 출처라 남는다. `요약` 시트의 "유형별" 집계와 `미확인 항목` 시트는 건드리지 않는다 — 거긴 커버리지 이야기다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_tc_excel.py` 에 추가:

```python
def test_the_sheet_has_the_three_level_hierarchy_and_no_title(tmp_path, make_tc):
    """대·중·소가 표에 있고 `제목`·`유형` 칸은 없어야 한다.

    헤더를 통째로 비교한다 — 부분 문자열로 보면 컬럼 순서가 뒤바뀌거나
    하나가 사라져도 통과한다.
    """
    from openpyxl import load_workbook

    tc = make_tc(title="쓰이지 않는 옛 제목")
    tc.category_major, tc.category_minor = "로그인", "신규 계정 연동"
    tc.category_sub, tc.family = "비밀번호 불일치", "경계값"
    out = export_tc_excel("로그인", [tc], [], tmp_path / "o.xlsx", set())
    ws = load_workbook(out)["테스트케이스"]

    header = [c.value for c in ws[1]]
    assert header == ["TC ID", "대분류", "중분류", "소분류", "사전조건", "절차",
                      "기대결과", "우선순위", "출처", "근거", "근거 상태"]
    assert "제목" not in header
    assert "유형" not in header


def test_the_row_carries_the_hierarchy_in_order(tmp_path, make_tc):
    from openpyxl import load_workbook

    tc = make_tc(title="쓰이지 않는 옛 제목")
    tc.category_major, tc.category_minor = "로그인", "신규 계정 연동"
    tc.category_sub, tc.family = "비밀번호 불일치", "경계값"
    out = export_tc_excel("로그인", [tc], [], tmp_path / "o.xlsx", set())
    ws = load_workbook(out)["테스트케이스"]

    row = [c.value for c in ws[2]]
    assert row[1:4] == ["로그인", "신규 계정 연동", "비밀번호 불일치"]
    assert "쓰이지 않는 옛 제목" not in row


def test_withdrawal_still_shows_on_the_row(tmp_path, make_tc):
    """계열 컬럼이 사라져도 근거 철회 표시는 행에 남는다 — 읽는 사람이
    이 행을 믿어도 되는지 판단하는 유일한 단서다."""
    from openpyxl import load_workbook

    tc = make_tc()
    tc.family, tc.category_sub = "경계값", "비밀번호 불일치"
    out = export_tc_excel("로그인", [tc], [], tmp_path / "o.xlsx", {"경계값"})
    ws = load_workbook(out)["테스트케이스"]
    assert "철회" in str(ws.cell(row=2, column=11).value)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tc_excel.py`
Expected: 3건 FAIL — 헤더 불일치, `category_sub` 없음

- [ ] **Step 3: 구현**

`qatc/models.py` 의 `TestCase` 에 `category_minor` 아래로 더한다:

```python
    #: 소분류 — 그 화면·기능 안에서 확인하는 **케이스 이름**. 결과는 적지
    #: 않는다(`expected` 가 그 자리다). 대+중+소를 읽으면 어떤 테스트인지
    #: 알 수 있어야 하고, 그래서 `title` 은 더 이상 쓰지 않는다.
    category_sub: str = ""
```

`qatc/export/tc_excel.py` 의 `_sheet_testcases` 를 고친다:

```python
    _header(
        ws,
        ["TC ID", "대분류", "중분류", "소분류", "사전조건", "절차", "기대결과",
         "우선순위", "출처", "근거", "근거 상태"],
        [16, 14, 18, 24, 28, 40, 40, 10, 10, 36, 14],
    )
    for r, tc in enumerate(cases, start=2):
        values = [
            tc.id, tc.category_major, tc.category_minor, tc.category_sub,
            tc.precondition,
            "\n".join(f"{i}. {s}" for i, s in enumerate(tc.steps, 1)),
            "\n".join(f"- {s}" for s in tc.expected),
            tc.priority.value, tc.origin.value, tc.rationale,
            _EVIDENCE_WITHDRAWN if tc.family in withdrawn else _EVIDENCE_LIVE,
        ]
        for col, v in enumerate(values, start=1):
            cell = ws.cell(row=r, column=col, value=clean_cell(str(v)))
            cell.alignment = _WRAP
        # 출처 칸이 10번째에서 9번째로 앞당겨졌다 — 색칠 대상도 함께 옮긴다.
        ws.cell(row=r, column=9).fill = _ORIGIN_FILL.get(tc.origin, PatternFill())
```

`_ORIGIN_FILL` 이 칠하던 열 번호가 바뀐 것을 놓치지 말 것 — 옛 코드는 10열(출처)을 칠했고 새 배치에서 출처는 9열이다.

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest`
Expected: 598 + 3 = **601 passed**

기존 엑셀 테스트가 `제목`·`유형` 을 기대하고 있으면 함께 고친다. 고친 테스트가 있으면 무엇을 왜 고쳤는지 보고에 적는다.

- [ ] **Step 5: 뮤테이션 검증**

| # | 뮤테이션 | 죽어야 하는 테스트 |
|---|---|---|
| M4 | 헤더에 `제목` 을 되살림 | `test_the_sheet_has_the_three_level_hierarchy_and_no_title` |
| M5 | 행에서 `category_sub` 대신 `title` 을 씀 | `test_the_row_carries_the_hierarchy_in_order` |
| M6 | 철회 표시를 `_EVIDENCE_LIVE` 로 고정 | `test_withdrawal_still_shows_on_the_row` |
| M7 | 출처 색칠을 10열에 그대로 둠 | (테스트 없음 — 아래 참고) |

M7 은 색이라 값 비교로 안 잡힌다. `test_the_row_carries_the_hierarchy_in_order` 에 색칠된 열이 출처 열인지 보는 단언을 더해 잡는다:

```python
    assert ws.cell(row=2, column=9).value == tc.origin.value
```

- [ ] **Step 6: 커밋**

```bash
git commit -m "표를 대·중·소 계층으로 바꾼다 — 제목과 유형 칸을 뺀다"
```

---

### Task 3: 컨텐츠 코드와 TC ID

**Files:**
- Modify: `qatc/knowledge/store.py`, `qatc/cli_knowledge.py`
- Modify: `tests/test_knowledge_store.py`, `tests/test_cli_slot.py`

**Interfaces:**
- Consumes: `TestCase.category_sub` (Task 2)
- Produces:
  - `contents.code` 컬럼 · `tc_seq` 테이블
  - `KnowledgeStore.content_code(name) -> str` · `set_content_code(name, code)` · `codes_in_use() -> dict[str, str]`
  - `KnowledgeStore.init_content(name, game, types, code="")` — `code` 인자 추가
  - `qatc slot init <컨텐츠> --code <라틴약어>`
  - TC ID 형식 `TC_<CODE>_<NNN>`

ID 는 `TC_LOGIN_001` 이다. **중분류를 넣지 않는다** — 넣으면 재분류할 때마다 ID 가 바뀌어 이미 인용된 ID 가 죽는다.

- [ ] **Step 1: 실패하는 테스트 작성 (저장소)**

`tests/test_knowledge_store.py` 에 추가:

```python
def test_content_code_is_stored_and_returned(tmp_path):
    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("로그인", game="g", types=[], code="LOGIN")
        assert st.content_code("로그인") == "LOGIN"


def test_a_second_init_without_a_code_keeps_the_existing_one(tmp_path):
    """`slot init` 재실행은 유형만 덧붙이는 기존 용법이 그대로 살아야 한다."""
    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("로그인", game="g", types=[], code="LOGIN")
        st.init_content("로그인", game="g", types=["편성"])
        assert st.content_code("로그인") == "LOGIN"


def test_testcase_ids_follow_the_code_and_increase(tmp_path, make_tc):
    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("로그인", game="g", types=[], code="LOGIN")
        for n in range(3):
            tc = make_tc(title=f"T{n}")
            tc.category_sub = f"케이스{n}"
            st.add_testcase("로그인", "경계값", tc, ["constraints"])
        got = sorted(t.id for t in st.testcases("로그인"))
    assert got == ["TC_LOGIN_001", "TC_LOGIN_002", "TC_LOGIN_003"]


def test_a_number_is_never_reused_after_a_delete(tmp_path, make_tc):
    """지워진 번호를 다시 쓰면 버그 리포트가 가리키던 번호가 엉뚱한 TC 를
    가리킨다. 비워 두는 편이 옳다."""
    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("로그인", game="g", types=[], code="LOGIN")
        for n in range(2):
            tc = make_tc(title=f"T{n}")
            tc.category_sub = f"케이스{n}"
            st.add_testcase("로그인", "경계값", tc, ["constraints"])
        st.replace_generated("로그인", "경계값", [], ["constraints"])
        fresh = make_tc(title="새것")
        fresh.category_sub = "새 케이스"
        st.add_testcase("로그인", "경계값", fresh, ["constraints"])
        assert [t.id for t in st.testcases("로그인")] == ["TC_LOGIN_003"]


def test_the_same_case_keeps_its_number_across_a_regeneration(tmp_path, make_tc):
    """같은 `(중분류, 소분류)` 면 같은 TC 로 보고 번호를 물려준다.

    소분류가 케이스 이름이 되면서 이 대조가 가능해졌다. 이것이 없으면 한
    계열을 다시 만들 때마다 그 계열의 모든 ID 가 갈린다.
    """
    def case(text):
        tc = make_tc(title=text)
        tc.category_minor, tc.category_sub = "신규 계정 연동", "비밀번호 불일치"
        return tc

    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("로그인", game="g", types=[], code="LOGIN")
        st.add_testcase("로그인", "경계값", case("처음"), ["constraints"])
        first = st.testcases("로그인")[0].id
        st.replace_generated("로그인", "경계값", [case("다시 쓴 본문")], ["constraints"])
        assert [t.id for t in st.testcases("로그인")] == [first]


def test_adding_a_testcase_without_a_code_is_refused(tmp_path, make_tc):
    """코드가 없으면 ID 를 지어내지 않고 거절한다 — `TC_C01_001` 같은 기계
    이름보다 다음 조치가 적힌 오류가 낫다."""
    with KnowledgeStore(tmp_path / "g.db") as st:
        st.init_content("로그인", game="g", types=[])
        with pytest.raises(KeyError) as e:
            st.add_testcase("로그인", "경계값", make_tc(), ["constraints"])
    assert "--code" in str(e.value.args[0])
```

- [ ] **Step 2: 실패하는 테스트 작성 (CLI)**

`tests/test_cli_slot.py` 에 추가:

```python
def test_slot_init_takes_a_code(cfg_env, capsys):
    from qatc.knowledge.store import KnowledgeStore

    assert main(["slot", "init", "로그인", "--game", "starrail", "--code", "LOGIN"]) == 0
    with KnowledgeStore(cfg_env / "starrail.db") as st:
        assert st.content_code("로그인") == "LOGIN"


def test_a_lowercase_or_symbolic_code_is_refused(cfg_env, capsys):
    rc = main(["slot", "init", "로그인", "--game", "starrail", "--code", "log-in"])
    assert rc == 1
    assert "영문 대문자" in capsys.readouterr().out


def test_a_duplicate_code_in_the_same_game_is_refused(cfg_env, capsys):
    main(["slot", "init", "로그인", "--game", "starrail", "--code", "LOGIN"])
    rc = main(["slot", "init", "로그인보상", "--game", "starrail", "--code", "LOGIN"])
    assert rc == 1
    assert "이미" in capsys.readouterr().out


def test_changing_the_code_of_an_existing_content_is_refused(cfg_env, capsys):
    main(["slot", "init", "로그인", "--game", "starrail", "--code", "LOGIN"])
    rc = main(["slot", "init", "로그인", "--game", "starrail", "--code", "SIGNIN"])
    assert rc == 1
    assert "이미 발급" in capsys.readouterr().out
```

- [ ] **Step 3: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_knowledge_store.py tests/test_cli_slot.py`
Expected: 새 10건 FAIL

- [ ] **Step 4: 스키마 — 컬럼과 번호표**

`_SCHEMA` 의 `contents` 에 `code` 를 더하고 `tc_seq` 테이블을 새로 만든다:

```sql
CREATE TABLE IF NOT EXISTS contents (
    name       TEXT PRIMARY KEY,
    game       TEXT NOT NULL,
    types      TEXT NOT NULL DEFAULT '[]',
    code       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tc_seq (
    content TEXT PRIMARY KEY,
    last    INTEGER NOT NULL DEFAULT 0
);
```

**`CREATE TABLE IF NOT EXISTS` 는 이미 있는 테이블에 컬럼을 더해 주지 않는다.** 첫 실사용으로 만들어진 `knowledge/starrail.db` 가 정확히 그 경우다. 스키마 실행 직후에 한 번 보강한다:

```python
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
```

- [ ] **Step 5: 저장소 메서드**

```python
    def content_code(self, name: str) -> str:
        row = self._db().execute(
            "SELECT code FROM contents WHERE name = ?", (name,)).fetchone()
        return row["code"] if row else ""

    def set_content_code(self, name: str, code: str) -> None:
        db = self._db()
        db.execute("UPDATE contents SET code = ? WHERE name = ?", (code, name))
        db.commit()

    def codes_in_use(self) -> dict[str, str]:
        """코드 -> 컨텐츠 이름. 중복 판정에 쓴다."""
        return {r["code"]: r["name"] for r in self._db().execute(
            "SELECT code, name FROM contents WHERE code != ''")}
```

`init_content` 시그니처를 `(self, name, game, types, code="")` 로 넓힌다. 새로 만들 때는 `code` 를 그대로 넣고, **이미 있으면 기존 코드가 비어 있을 때만** 채운다 — 재실행이 코드를 덮으면 발급된 ID 와 어긋난다.

- [ ] **Step 6: ID 발급**

```python
    def _next_tc_id(self, content: str) -> str:
        """`TC_<코드>_<번호>`. 번호는 컨텐츠 안에서 단조 증가하고 재사용하지 않는다.

        살아 있는 행의 최대값을 쓰지 않는 이유: 전부 지운 뒤 새로 만들면 001
        로 되돌아가, 지워진 TC 를 가리키던 번호가 다른 TC 를 가리키게 된다.
        그래서 마지막 번호를 `tc_seq` 에 따로 기억한다.
        """
        code = self.content_code(content)
        if not code:
            raise KeyError(
                f"'{content}'에 컨텐츠 코드가 없어 TC ID를 만들 수 없습니다. "
                f"'qatc slot init {content} --code <영문대문자약어>' 로 먼저 정하세요."
            )
        db = self._db()
        row = db.execute("SELECT last FROM tc_seq WHERE content = ?", (content,)).fetchone()
        nxt = (row["last"] if row else 0) + 1
        db.execute("INSERT OR REPLACE INTO tc_seq (content, last) VALUES (?, ?)",
                   (content, nxt))
        db.commit()
        return f"TC_{code}_{nxt:03d}"
```

`add_testcase` 의 `if not tc.id: tc.id = new_id("tc")` 를 `tc.id = self._next_tc_id(content)` 로 바꾼다. `qatc/models.py` 의 `new_id` 는 다른 곳에서 쓰면 그대로 두고, 안 쓰면 지운다.

- [ ] **Step 7: 재생성 시 번호 물려받기**

`replace_generated` 에서 지우기 **전에** 지도를 만든다:

```python
        # 같은 (중분류, 소분류) 면 같은 TC 로 본다 - 본문을 다시 써도 번호를
        # 물려주기 위해서다. 소분류가 케이스 이름이 되면서 가능해진 대조다.
        inherited = {}
        for r in db.execute(
            "SELECT id, row FROM testcases WHERE content = ? AND family = ?",
            (content, family),
        ):
            old = TestCase.from_row(json.loads(r["row"]))
            inherited[(old.category_minor, old.category_sub)] = r["id"]
```

새 배치를 넣을 때 `inherited.get((tc.category_minor, tc.category_sub))` 가 있으면 그 id 를, 없으면 `_next_tc_id` 를 쓴다. 보존된(사람이 고친) TC 의 id 는 물려주지 않는다 — 그건 아직 살아 있다.

- [ ] **Step 8: CLI**

`slot init` 파서에 더한다:

```python
    it.add_argument("--code", default="",
                    help="TC ID에 쓸 영문 대문자 약어 (예: LOGIN). 새 컨텐츠에는 필수")
```

`cmd_slot_init` 에서 검증한다. 순서가 중요하다 — 형식을 먼저 보고, 그 다음 기존 코드와의 충돌, 마지막으로 중복이다.

```python
    if args.code and not re.fullmatch(r"[A-Z0-9]{2,12}", args.code):
        _p("오류: 컨텐츠 코드는 영문 대문자와 숫자 2~12자여야 합니다 (예: LOGIN). "
           "다시 지정해 주세요.")
        return 1
```

기존 코드가 있고 `args.code` 가 그와 다르면: `오류: '<컨텐츠>'의 코드는 이미 <기존>으로 발급되어 바꿀 수 없습니다. ...`. 같은 게임에 그 코드를 이미 쓰는 다른 컨텐츠가 있으면: `오류: 코드 <코드>는 이미 '<다른 컨텐츠>'가 쓰고 있습니다. 다른 약어를 지정하세요.`

- [ ] **Step 9: 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest`
Expected: 601 + 10 = **611 passed** (Step 10 에서 한 건을 더해 최종 612)

기존 테스트가 `tc_` 로 시작하는 ID 를 기대하고 있으면 함께 고친다. 무엇을 왜 고쳤는지 보고에 적는다.

- [ ] **Step 10: 뮤테이션 검증**

| # | 뮤테이션 | 죽어야 하는 테스트 |
|---|---|---|
| M8 | 코드 없을 때 `TC_C01_001` 을 지어냄 | `test_adding_a_testcase_without_a_code_is_refused` |
| M9 | `tc_seq` 대신 살아 있는 행의 최대값을 씀 | `test_a_number_is_never_reused_after_a_delete` |
| M10 | 번호 물려받기 제거 | `test_the_same_case_keeps_its_number_across_a_regeneration` |
| M11 | 코드 정규식 검증 제거 | `test_a_lowercase_or_symbolic_code_is_refused` |
| M12 | 중복 코드 검사 제거 | `test_a_duplicate_code_in_the_same_game_is_refused` |
| M13 | 재실행 시 코드를 덮어씀 | `test_a_second_init_without_a_code_keeps_the_existing_one` |
| M14 | `_ensure_code_column` 제거 | (아래 참고) |

M14 는 새 DB 에서는 안 걸린다(스키마에 이미 컬럼이 있다). **옛 스키마로 만든 DB** 를 흉내내는 테스트를 하나 더해 잡는다:

```python
def test_an_old_database_without_the_code_column_still_opens(tmp_path):
    """첫 실사용으로 만들어진 DB 에는 `contents.code` 가 없다."""
    import sqlite3

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE contents (name TEXT PRIMARY KEY, game TEXT NOT NULL,"
                " types TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL)")
    con.execute("INSERT INTO contents VALUES ('로그인','g','[]','2026-01-01')")
    con.commit()
    con.close()

    with KnowledgeStore(path) as st:
        assert st.content_code("로그인") == ""
        st.set_content_code("로그인", "LOGIN")
        assert st.content_code("로그인") == "LOGIN"
```

이 테스트를 포함해 이 작업의 새 테스트는 11건, 최종 **612 passed** 다.

- [ ] **Step 11: 커밋**

```bash
git commit -m "TC ID 를 TC_LOGIN_001 형태로 만든다 — 번호는 재사용하지 않는다"
```

---

### Task 4: 입력 계약 — `middle` / `sub` 와 경고 두 종

**Files:**
- Modify: `qatc/cli_knowledge.py`
- Modify: `tests/test_cli_tc.py`

**Interfaces:**
- Consumes: `TestCase.category_sub` (Task 2), `_next_tc_id` (Task 3)
- Produces: `qatc tc add --json` 의 항목이 `middle`·`sub` 를 필수로 받는다

```json
{"testcases": [
  {"middle": "신규 계정 연동", "sub": "비밀번호 불일치",
   "precondition": "...", "steps": ["..."], "expected": ["..."],
   "rationale": "..."}
]}
```

키 이름이 필드 이름과 다르다: `middle` -> `category_minor`, `sub` -> `category_sub`. 짧은 키를 쓰는 이유는 이 JSON 을 모델이 손으로 쓰기 때문이고, `category_minor` 라는 이름 자체가 지금은 오해를 부르기 때문이다. `title` 은 더 이상 읽지 않는다.

**막는 것과 알리기만 하는 것을 가른다.** 판단이 필요한 규칙은 코드가 셀 수 없다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_cli_tc.py` 에 추가:

```python
def _payload(**over):
    item = {"middle": "신규 계정 연동", "sub": "비밀번호 불일치",
            "precondition": "신규 계정 연동 화면", "steps": ["값을 다르게 입력한다"],
            "expected": ["연동하기 버튼이 회색 비활성 상태로 유지된다"],
            "rationale": "constraints 슬롯에서 도출"}
    item.update(over)
    return json.dumps({"testcases": [item]}, ensure_ascii=False)


def test_middle_and_sub_are_stored(cfg_env, stdin_text, capsys):
    _prepare_content(cfg_env)
    stdin_text(_payload())
    assert main(["tc", "add", "로그인", "--family", "경계값",
                 "--origin", "inferred", "--json", "-"]) == 0
    from qatc.knowledge.store import KnowledgeStore
    with KnowledgeStore(cfg_env / "starrail.db") as st:
        tc = st.testcases("로그인")[0]
    assert (tc.category_minor, tc.category_sub) == ("신규 계정 연동", "비밀번호 불일치")


@pytest.mark.parametrize("field", ["middle", "sub"])
def test_a_blank_middle_or_sub_is_refused(cfg_env, stdin_text, capsys, field):
    """제로폭 공백은 `strip()` 이 못 지운다 — `is_blank` 로 판정한다."""
    _prepare_content(cfg_env)
    stdin_text(_payload(**{field: "  \u200b "}))
    rc = main(["tc", "add", "로그인", "--family", "경계값",
               "--origin", "inferred", "--json", "-"])
    assert rc == 1
    assert "비어" in capsys.readouterr().out


def test_an_empty_expected_is_refused(cfg_env, stdin_text, capsys):
    """확인할 것이 없는 TC 는 TC 가 아니다."""
    _prepare_content(cfg_env)
    stdin_text(_payload(expected=[]))
    assert main(["tc", "add", "로그인", "--family", "경계값",
                 "--origin", "inferred", "--json", "-"]) == 1


def test_more_than_six_expected_warns_but_saves(cfg_env, stdin_text, capsys):
    """규칙 3은 기계적이라 셀 수 있다. 다만 **막지는 않는다** — 나눌지는
    판단이고, 막으면 그 판단을 표현할 방법이 없어진다."""
    _prepare_content(cfg_env)
    stdin_text(_payload(expected=[f"확인 {n}" for n in range(7)]))
    rc = main(["tc", "add", "로그인", "--family", "경계값",
               "--origin", "inferred", "--json", "-"])
    assert rc == 0, "경고여야 하는데 막았습니다"
    out = capsys.readouterr().out
    assert "6개" in out and "나누" in out
    from qatc.knowledge.store import KnowledgeStore
    with KnowledgeStore(cfg_env / "starrail.db") as st:
        assert len(st.testcases("로그인")) == 1


def test_a_long_sub_warns_but_saves(cfg_env, stdin_text, capsys):
    """길다는 것은 결과를 밀어 넣었다는 신호이지 그 자체가 오류는 아니다."""
    _prepare_content(cfg_env)
    stdin_text(_payload(sub="비밀번호 두 필드가 불일치하면 연동하기 버튼이 비활성으로 유지된다"))
    rc = main(["tc", "add", "로그인", "--family", "경계값",
               "--origin", "inferred", "--json", "-"])
    assert rc == 0
    assert "소분류" in capsys.readouterr().out


def test_a_judgement_rule_is_never_enforced_by_code(cfg_env, stdin_text, capsys):
    """기대결과가 2개라는 이유만으로 막으면 안 된다.

    한때 `expected` 를 정확히 1개로 강제하려 했다가 철회했다 — 회원가입의
    DB 저장과 이메일 발송처럼 한 문장으로 이어 쓸 수 없는 독립 결과가 있고,
    반대로 화면 전환과 문구 노출처럼 한 세트인 것도 있다. 그 구분은 판단이라
    코드가 셀 수 없다. 누가 선의로 하드 거부를 되살리면 이 테스트가 막는다.
    """
    _prepare_content(cfg_env)
    stdin_text(_payload(expected=["메인 페이지로 이동한다", "환영 문구가 노출된다"]))
    assert main(["tc", "add", "로그인", "--family", "경계값",
                 "--origin", "inferred", "--json", "-"]) == 0
```

`_prepare_content(cfg_env)` 헬퍼는 `slot init 로그인 --game starrail --code LOGIN` 을 부르고 `constraints` 슬롯을 채워 `경계값` 계열이 생성 대상이 되게 한다. 파일에 이미 비슷한 준비 코드가 있으면 그것을 쓴다.

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_tc.py`
Expected: 새 7건 FAIL (`middle` 을 읽지 않으므로 `KeyError` 또는 빈 값)

- [ ] **Step 3: 구현**

`cmd_tc_add` 의 항목 검증에서 `title` 대신 `middle`·`sub` 를 필수로 본다. 기존 필수 필드 목록에서 `title` 을 빼고 두 키를 넣는다.

```python
    for field in ("middle", "sub"):
        if is_blank(str(item.get(field, ""))):
            _p(f"오류: {field} 가 비어 있습니다 (TC {n}번). "
               f"middle 은 화면·메뉴 이름, sub 는 케이스 이름입니다.")
            return 1
    if not item.get("expected"):
        _p(f"오류: expected 가 비어 있습니다 (TC {n}번). "
           f"확인할 것이 없는 테스트케이스는 만들 수 없습니다.")
        return 1
```

`TestCase(...)` 생성에서 `title=str(item["title"])` 를 빼고 더한다:

```python
                category_minor=str(item["middle"]),
                category_sub=str(item["sub"]),
```

`category_major=args.content` 는 그대로다.

경고 두 개는 **저장 뒤에** 낸다 — 저장은 되었고 다시 볼 것이 있다는 뜻이므로:

```python
    for tc in cases:
        if len(tc.expected) > _MAX_EXPECTED:
            _warn(f"{tc.category_sub}: 기대결과가 {len(tc.expected)}개입니다. "
                  f"{_MAX_EXPECTED}개를 넘으면 영역별로 나누는 편이 낫습니다 — "
                  f"하나가 틀렸을 때 어디가 문제인지 표에서 사라집니다.")
        if len(tc.category_sub) > _MAX_SUB_LEN:
            _warn(f"소분류가 {len(tc.category_sub)}자입니다: '{tc.category_sub}'. "
                  f"결과를 소분류에 넣었거나 두 케이스가 붙어 있을 수 있습니다 — "
                  f"결과는 기대결과 칸에 적습니다.")
```

`_MAX_EXPECTED = 6` · `_MAX_SUB_LEN = 25` 를 모듈 상수로 둔다. `_warn` 이 `qatc/console.py` 에 없으면 `_p(msg, err=True)` 를 쓴다.

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest`
Expected: 612 + 7 = **619 passed**

- [ ] **Step 5: 뮤테이션 검증**

| # | 뮤테이션 | 죽어야 하는 테스트 |
|---|---|---|
| M15 | `is_blank` 대신 `strip()` | `test_a_blank_middle_or_sub_is_refused[middle]` |
| M16 | `expected` 빈 검사 제거 | `test_an_empty_expected_is_refused` |
| M17 | 기대결과 경고를 `return 1` 로 바꿈 | `test_more_than_six_expected_warns_but_saves` |
| M18 | 기대결과 경고 제거 | `test_more_than_six_expected_warns_but_saves` |
| M19 | 소분류 길이 경고 제거 | `test_a_long_sub_warns_but_saves` |
| M20 | `expected` 를 정확히 1개로 강제 | `test_a_judgement_rule_is_never_enforced_by_code` |

- [ ] **Step 6: 커밋**

```bash
git commit -m "tc add 가 중분류·소분류를 받는다 — 판단 규칙은 경고로만 거든다"
```

---

### Task 5: 앱 화면 — TC 라벨과 검토 패널

**Files:**
- Modify: `qatc/app/views.py`, `qatc/app/static/app.js`
- Modify: `tests/test_app_views.py`, `tests/test_app_server.py`

**Interfaces:**
- Consumes: `TestCase.category_sub` · `TestCase.family` (Task 1·2)
- Produces: `/api/content` 의 `testcases[]` 항목에 `middle` · `sub` 가 실린다

왼쪽 트리의 **계열 묶음은 그대로 둔다** — 거기는 게이트 커버리지를 보는 자리라 계열이 맞다. 바뀌는 것은 TC 하나하나의 **라벨**과 오른쪽 검토 패널의 머리말이다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_app_views.py` 에 추가:

```python
def test_content_detail_carries_the_hierarchy(cfg):
    _seed(cfg)
    with KnowledgeStore(cfg.knowledge_path / "starrail.db") as st:
        tc = _tc(title="옛 제목", family="경계값")
        tc.category_minor, tc.category_sub = "신규 계정 연동", "비밀번호 불일치"
        st.add_testcase("파티편성", "경계값", tc, ["constraints"])

    row = [t for t in content_detail(cfg, "starrail", "파티편성")["testcases"]
           if t["sub"] == "비밀번호 불일치"][0]
    assert row["middle"] == "신규 계정 연동"
    assert row["family"] == "경계값"


def test_an_old_testcase_without_a_sub_falls_back_to_its_title(cfg):
    """마이그레이션 전의 행도 화면에서 이름을 잃지 않아야 한다."""
    _seed(cfg)     # `_seed` 가 넣는 TC 는 소분류가 없다
    rows = content_detail(cfg, "starrail", "파티편성")["testcases"]
    assert rows[0]["sub"] == rows[0]["title"]
```

`tests/test_app_server.py` 에 추가:

```python
def test_the_tree_labels_testcases_by_sub_not_by_title(app):
    """화면이 소분류로 라벨링해야 새 계층이 사용자에게 보인다."""
    import re

    js = re.sub("//[^" + chr(10) + "]*", "",
                app.test_client().get("/static/app.js").get_data(as_text=True))
    m = re.search(r"function renderTcRow\([^)]*\)\s*\{([\s\S]*?)" + chr(10) + r"\}", js)
    assert m, "renderTcRow 를 찾을 수 없습니다"
    assert "tc.sub" in m.group(1), "TC 라벨이 소분류를 쓰지 않습니다"


def test_the_review_panel_shows_the_hierarchy_instead_of_a_title(app):
    import re

    js = re.sub("//[^" + chr(10) + "]*", "",
                app.test_client().get("/static/app.js").get_data(as_text=True))
    m = re.search(r"function renderReview\([^)]*\)\s*\{([\s\S]*?)" + chr(10) + r"\}", js)
    assert m, "renderReview 를 찾을 수 없습니다"
    body = m.group(1)
    assert "tc.middle" in body and "tc.sub" in body
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app_views.py tests/test_app_server.py`
Expected: 새 4건 FAIL

- [ ] **Step 3: 백엔드**

`qatc/app/views.py` 의 `content_detail` 이 만드는 TC 항목에 두 키를 더한다. `title` 도 **그대로 남긴다** — 폴백이 그 값을 쓴다.

```python
            {"id": t.id, "family": t.family,
             "middle": t.category_minor,
             # 마이그레이션 전의 행은 소분류가 비어 있다. 그때는 제목이
             # 그 자리를 대신한다 - 화면에서 이름 없는 TC 가 되면 고를 수 없다.
             "sub": t.category_sub or t.title,
             "title": t.title,
             "kind": t.kind.value, "priority": t.priority.value,
             ...
```

- [ ] **Step 4: 화면**

`renderTcRow` 가 `tc.sub` 로 라벨링한다:

```javascript
function renderTcRow(tc) {
  const selected = state.selectedTcId === tc.id;
  let cls = "tc" + (selected ? " selected" : "");
  if (tc.withdrawn) cls += " tc-withdrawn";
  // 소분류가 케이스 이름이다. 백엔드가 옛 행에 대해서는 제목으로 채워 준다.
  return el("div", {
    class: cls, text: tc.sub, title: tc.sub,
    onclick: (e) => { e.stopPropagation(); selectTc(tc.id); },
  });
}
```

`renderReview` 의 제목 자리를 계층으로 바꾼다:

```javascript
  bodyEl.appendChild(el("div", { class: "tc-path", text: `${tc.middle} › ${tc.sub}` }));
```

`app.css` 에 `.tc-path` 규칙을 더한다 (`.tc-title` 옆에, 중분류를 옅게 소분류를 진하게 보이려면 두 span 으로 나눠도 좋다 — 한 줄이어도 무방하다). `.tc-meta` 의 `tc.kind` 태그는 그대로 둔다 — 그건 화면 안쪽이지 산출물 표가 아니다.

- [ ] **Step 5: 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest`
Expected: 619 + 4 = **623 passed**

`node --check qatc/app/static/app.js` 로 구문을 확인한다 — JS 오류는 pytest 가 못 잡고 화면 전체를 죽인다.

- [ ] **Step 6: 뮤테이션 검증**

| # | 뮤테이션 | 죽어야 하는 테스트 |
|---|---|---|
| M21 | `"sub": t.category_sub` (폴백 제거) | `test_an_old_testcase_without_a_sub_falls_back_to_its_title` |
| M22 | `middle` 키를 안 실음 | `test_content_detail_carries_the_hierarchy` |
| M23 | `renderTcRow` 를 `tc.title` 로 되돌림 | `test_the_tree_labels_testcases_by_sub_not_by_title` |
| M24 | `renderReview` 를 `tc.title` 로 되돌림 | `test_the_review_panel_shows_the_hierarchy_instead_of_a_title` |

- [ ] **Step 7: 커밋**

```bash
git commit -m "화면이 소분류로 TC 를 부른다 — 검토 패널은 중분류 › 소분류"
```

---

### Task 6: 스킬과 README

**Files:**
- Modify: `.claude/skills/interview/SKILL.md`, `README.md`
- Modify: `tests/test_interview_skill.py`

판단 규칙 1·2 는 코드가 강제하지 않는다(Task 4). 그러므로 **여기가 유일한 강제 지점**이다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_interview_skill.py` 에 추가:

```python
def test_skill_teaches_the_three_level_hierarchy():
    text = SKILL.read_text(encoding="utf-8")
    for token in ("중분류", "소분류", "middle", "sub"):
        assert token in text, f"스킬이 {token} 를 모릅니다"


def test_skill_says_not_to_put_the_result_in_the_sub():
    """소분류에 결과를 적으면 길어지고 자매 케이스가 안 보인다."""
    text = SKILL.read_text(encoding="utf-8")
    assert "기대결과" in text and "소분류" in text
    assert "비밀번호 불일치" in text, "좋은 예가 없습니다"


def test_skill_carries_both_split_examples():
    """규칙 1·2 는 코드가 못 세므로 예시가 유일한 가르침이다.

    나누는 예(독립된 결과)와 합치는 예(연속·종속) **둘 다** 있어야 한다 —
    한쪽만 있으면 모델이 한 방향으로만 치우친다.
    """
    text = SKILL.read_text(encoding="utf-8")
    assert "회원가입" in text and "이메일" in text        # 나누는 예
    assert "환영" in text or "메인 페이지" in text        # 합치는 예


def test_skill_knows_the_content_code():
    text = SKILL.read_text(encoding="utf-8")
    assert "--code" in text
    assert "LOGIN" in text
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_interview_skill.py`
Expected: 4건 FAIL

- [ ] **Step 3: `SKILL.md`**

1단계의 `slot init` 예시에 `--code` 를 더한다:

```bash
.venv/Scripts/qatc.exe slot init <컨텐츠> --game <게임> --code <영문대문자약어> --types 편성
```

약어는 컨텐츠 이름을 영어로 옮긴 짧은 대문자다 (`로그인` -> `LOGIN`, `파티편성` -> `PARTY`, `워프` -> `WARP`). TC ID 가 `TC_LOGIN_001` 이 되고 **한 번 정하면 바꿀 수 없다** — 이미 발급된 ID 와 어긋나기 때문이다.

3단계의 JSON 모양을 `middle`/`sub` 로 바꾸고, 규칙을 싣는다. 나쁜 예는 지어내지 말고 **지금 산출물에서 가져온다**:

- 중분류는 화면·메뉴 이름이다. 나쁜 예 `경계값` · 좋은 예 `신규 계정 연동`
- 소분류는 케이스 이름이다. **결과를 적지 않는다** — 나쁜 예 `비밀번호 불일치 시 연동하기 버튼 비활성 유지` · 좋은 예 `비밀번호 불일치`
- 대+중+소를 읽으면 어떤 테스트인지 알 수 있어야 한다. 그래서 제목 칸이 없다
- **나눌지 합칠지는 판단이다.** 독립된 결과(회원가입 -> DB 저장 / 환영 이메일 발송: 확인 수단이 다르다)는 나눈다. 연속·종속되거나 한 화면의 한 세트(로그인 성공 -> 메인 페이지로 이동하고 환영 문구가 노출된다)는 합친다
- 세트여도 확인이 6개를 넘으면 영역별로 나눈다. 11개를 묶으면 7번째만 틀려도 어디가 문제인지 표에서 사라진다
- 기대결과를 모호하게 적지 않는다. 나쁜 예 `버튼이 노출된다` · 좋은 예 `연동하기 버튼이 회색 비활성 상태로 표시된다`
- 조작 대상을 특정한다. 나쁜 예 `버튼을 클릭한다`
- 사전조건에 주체를 적는다 — 초기화가 수동인지 자동인지

- [ ] **Step 4: `README.md`**

`qatc slot init` 줄에 `--code` 를 더하고, TC ID 규칙과 새 엑셀 컬럼 구성을 한 문단으로 적는다.

- [ ] **Step 5: 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest`
Expected: 623 + 4 = **627 passed**

`test_every_qatc_command_in_skill_is_registered` 와 `test_skill_uses_allowlisted_executable_form` 이 계속 통과하는지 확인한다 — `slot init --code` 는 `Bash(.venv/Scripts/qatc.exe slot *)` 에 이미 덮인다.

- [ ] **Step 6: 뮤테이션 검증**

| # | 뮤테이션 | 죽어야 하는 테스트 |
|---|---|---|
| M25 | 합치는 예를 지움 | `test_skill_carries_both_split_examples` |
| M26 | 소분류 좋은 예를 지움 | `test_skill_says_not_to_put_the_result_in_the_sub` |
| M27 | `--code` 설명을 지움 | `test_skill_knows_the_content_code` |

- [ ] **Step 7: 커밋**

```bash
git commit -m "스킬이 새 계층과 분리 판단을 가르친다"
```

---

### Task 7: 기존 `로그인` 23건을 29건으로 옮긴다

**Files:**
- Create: `qatc/migrate_login_tc.py`, `tests/test_migrate_login_tc.py`
- Modify: `qatc/knowledge/store.py`
- Read: `docs/superpowers/2026-08-16-login-reclassify.json` (승인 완료, **수정 금지**)

**Interfaces:**
- Consumes: 앞의 모든 작업
- Produces: `apply_reclassification(db_path, plan_path) -> tuple[int, int]` — (지운 수, 넣은 수)
- Produces: `KnowledgeStore.clear_testcases(content) -> int` · `KnowledgeStore.set_tc_seq(content, last)`

**판단은 이미 끝났다.** 어떤 TC 를 나누고 합칠지는 소유자 승인을 거쳐 JSON 에 확정돼 있다. 이 작업은 **적용만** 한다 — 구현자가 분류를 다시 판단하면 안 된다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_migrate_login_tc.py` 를 새로 만든다:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_migrate_login_tc.py`
Expected: 6건 FAIL — `ModuleNotFoundError: No module named 'qatc.migrate_login_tc'`

- [ ] **Step 3: 저장소 메서드 두 개**

```python
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
```

- [ ] **Step 4: 마이그레이션 모듈**

`qatc/migrate_login_tc.py` 를 새로 만든다:

```python
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
```

`add_testcase` 는 `if not tc.id:` 일 때만 번호를 발급하므로(Task 3), JSON 이 준 ID 가 그대로 유지된다.

- [ ] **Step 5: 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest`
Expected: 627 + 6 = **633 passed**

- [ ] **Step 6: 뮤테이션 검증**

| # | 뮤테이션 | 죽어야 하는 테스트 |
|---|---|---|
| M28 | 백업 복사 제거 | `test_a_backup_is_written_before_touching_the_db` |
| M29 | `clear_testcases` 를 안 부름 | `test_applying_twice_does_not_double` |
| M30 | `category_sub` 를 안 넣음 | `test_every_row_has_a_hierarchy_and_a_family` |
| M31 | `family` 를 안 넣음 | `test_every_row_has_a_hierarchy_and_a_family` |
| M32 | JSON 의 `id` 를 버리고 새로 발급 | `test_applying_gives_29_testcases_with_the_new_ids` |

- [ ] **Step 7: 커밋 (실제 적용 전에)**

```bash
git commit -m "승인된 분류를 적용하는 마이그레이션을 만든다"
```

- [ ] **Step 8: 실제 데이터에 적용**

**여기서부터는 되돌릴 수 없다.** 먼저 `git status --porcelain` 이 비어 있는지 확인한다.

```bash
.venv/Scripts/python.exe -c "from pathlib import Path; from qatc.migrate_login_tc import apply_reclassification; print(apply_reclassification(Path('knowledge/starrail.db'), Path('docs/superpowers/2026-08-16-login-reclassify.json')))"
```

`(23, 29)` 가 나와야 한다. 그 다음 확인한다:

```bash
.venv/Scripts/python.exe -c "from qatc.knowledge.store import KnowledgeStore; st=KnowledgeStore('knowledge/starrail.db'); cs=st.testcases('로그인'); print(len(cs), st.content_code('로그인'), sorted(t.id for t in cs)[0], sorted(t.id for t in cs)[-1]); st.close()"
```

29 · `LOGIN` · `TC_LOGIN_001` · `TC_LOGIN_029` 가 나와야 한다. 백업 파일(`knowledge/starrail.db.bak*`)이 생겼는지도 본다 — `knowledge/` 는 `.gitignore` 에 있어 커밋되지 않는다.

- [ ] **Step 9: 엑셀로 눈으로 확인**

```bash
.venv/Scripts/qatc.exe export 로그인
```

열린 xlsx 에서 확인한다: 컬럼이 `TC ID · 대분류 · 중분류 · 소분류 · …` 인지, `제목`·`유형` 칸이 **없는지**, 29행인지, 중분류가 7종(클라이언트 실행·로그인 창·소셜 연동·신규 계정 연동·약관 동의·재로그인·로그인 완료)인지.

---

## 완료 기준

- 전체 스위트 **633 passed** (숫자가 달라지면 사유를 보고한다)
- 뮤테이션 32종 전부 검출
- `git status --porcelain` 비어 있음 · 작업 트리 전부 CRLF · `node --check qatc/app/static/app.js` 통과
- **`knowledge/starrail.db` 의 `로그인` 이 29건**이고 전부 중분류·소분류·계열·`TC_LOGIN_0NN` ID 를 갖는다
- **백업이 남아 있다** (`knowledge/starrail.db.bak*`)
- `qatc export 로그인` 의 xlsx 컬럼이 `TC ID · 대분류 · 중분류 · 소분류 · 사전조건 · 절차 · 기대결과 · 우선순위 · 출처 · 근거 · 근거 상태` 이고 `제목`·`유형` 이 없다
- **라이브 확인**: 앱을 띄워 `로그인` 을 고르고, 트리의 TC 라벨이 소분류인지, 검토 패널이 `중분류 › 소분류` 를 보이는지 본다
- 무쓰기 가드가 여전히 통과한다

## 열려 있는 항목 (명세 12절)

- **999건을 넘는 컨텐츠.** 자릿수를 늘려야 하는데 지금은 29건이라 실제 문제가 아니다. 늘릴 때 옛 번호를 다시 매기지 않는다 — `TC_LOGIN_0001` 과 `TC_LOGIN_001` 이 섞이는 편이 이미 인용된 ID 를 깨는 것보다 낫다.
- **컨텐츠 이름 변경.** 코드를 그대로 둘지 정해야 하는데, 지금은 이름을 바꾸는 기능 자체가 없다.

## 만들지 않는 것

- **옛 ID 와 새 ID 의 매핑표.** uuid 는 아직 어디에도 인용된 적이 없다.
- **다른 컨텐츠의 마이그레이션.** 지금 있는 컨텐츠는 `로그인` 하나뿐이다.
- **`title` 필드 삭제.** 옛 행의 폴백이 그 값을 쓰고, 지우면 되돌릴 수 없다.
- **중분류 자동 추론 · 라틴 약어 자동 생성.** 둘 다 모델이 인터뷰에서 정한다.
- **`자기세정` 컬럼.** 필요하면 사전조건에 적는다.

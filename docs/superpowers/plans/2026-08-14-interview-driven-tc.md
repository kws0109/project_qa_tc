# 인터뷰 기반 TC 생성 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자가 게임 컨텐츠를 설명하면 Claude Code 세션이 인터뷰로 지식을 채우고, `qatc` CLI가 그 지식에서 만들 수 있는 TC 계열만 코드로 강제해 Excel로 내보내는 파이프라인을 만든다.

**Architecture:** Claude Code 세션이 인터뷰어이고 `qatc` CLI가 저장소·계산·게이트다. 대화·컨텍스트 관리·재개는 harness가, 커버리지 계산과 "빈 슬롯 계열은 만들 수 없다"는 불변식은 코드가 맡는다. 지식은 게임 단위 SQLite에 슬롯으로 쌓이고, 슬롯 상태가 곧 생성 가능한 TC 계열을 결정한다.

**Tech Stack:** Python 3.11+ · stdlib `sqlite3` · `argparse` · `openpyxl` · `pytest`

## Global Constraints

- Windows 전용. 경로는 `pathlib.Path` 로만 다룬다.
- 콘솔 출력은 반드시 `qatc.cli._p()` 를 쓴다 (Windows 기본 코드페이지 한글 깨짐 방지).
- 테스트 실행: `.venv\Scripts\python.exe -m pytest`
- 새 코드는 Anthropic API를 호출하지 않는다. `qatc/llm/` 를 import 하지 않는다.
- SQLite 연결은 `qatc/storage.py` 관용구를 따른다 — `row_factory = sqlite3.Row`, `executescript(_SCHEMA)`, `timeout=30.0`.
- 사용자에게 보이는 문자열은 한국어.
- 커밋 메시지는 한국어. 마지막 줄에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` 를 넣는다.
- 기존 테스트 131개는 Task 11 전까지 계속 통과해야 한다.

---

### Task 1: 지식 도메인 타입과 `TCOrigin.INTERVIEW`

**Files:**
- Create: `qatc/knowledge/__init__.py`
- Create: `qatc/knowledge/models.py`
- Modify: `qatc/models.py:504-512` (`TCOrigin` 에 `INTERVIEW` 추가)
- Test: `tests/test_knowledge_models.py`

**Interfaces:**
- Consumes: `qatc.models.TCKind`, `qatc.models.Priority`
- Produces:
  - `SlotStatus(str, Enum)` — `EMPTY="empty"` `FILLED="filled"` `UNKNOWN="unknown"` `NA="na"`
  - `SlotSpec(key: str, prompt_hint: str, tc_family: str)` — frozen dataclass, 슬롯 정의
  - `Slot(key, prompt_hint, tc_family, status=SlotStatus.EMPTY, value="", ord=0)` — 상태를 가진 슬롯
  - `Slot.is_open` → `bool` (status가 EMPTY면 True — 아직 물어볼 대상)
  - `Slot.is_closed` → `bool` (FILLED/UNKNOWN/NA — 다시 묻지 않음)
  - `Content(name: str, game: str, types: list[str])`
  - `TCOrigin.INTERVIEW = "인터뷰"`

- [ ] **Step 1: `TCOrigin` 에 `INTERVIEW` 추가**

`qatc/models.py` 의 `TCOrigin` 을 다음으로 바꾼다. 기존 세 값의 문자열은 건드리지 않는다 — `export/excel.py` 가 색 매핑에 쓰고 있고 기존 세션 DB에 저장된 값과 호환돼야 한다.

```python
class TCOrigin(str, Enum):
    """TC의 근거 출처. **INFERRED는 검증되지 않은 가설이다.**

    이 구분이 없으면 추측이 정식 TC로 둔갑한다. 엑셀에서 색으로 구분된다.
    """

    RECORDED = "기록됨"      # 녹화에서 관측됨 (녹화 파이프라인 전용)
    INTERVIEW = "인터뷰"     # 사용자가 진술한 내용에 직접 근거
    INFERRED = "추론됨"      # 진술에서 도출
    USER = "사용자추가"
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_knowledge_models.py`:

```python
from qatc.knowledge.models import Content, Slot, SlotSpec, SlotStatus
from qatc.models import TCOrigin


def test_interview_origin_exists():
    assert TCOrigin.INTERVIEW.value == "인터뷰"
    assert TCOrigin("인터뷰") is TCOrigin.INTERVIEW


def test_existing_origin_values_unchanged():
    # export/excel.py 의 색 매핑과 기존 세션 DB가 이 문자열에 의존한다
    assert TCOrigin.RECORDED.value == "기록됨"
    assert TCOrigin.INFERRED.value == "추론됨"
    assert TCOrigin.USER.value == "사용자추가"


def test_empty_slot_is_open():
    s = Slot(key="cost", prompt_hint="무엇을 소모하는가", tc_family="재화 부족")
    assert s.status is SlotStatus.EMPTY
    assert s.is_open is True
    assert s.is_closed is False


def test_filled_unknown_na_are_all_closed():
    for st in (SlotStatus.FILLED, SlotStatus.UNKNOWN, SlotStatus.NA):
        s = Slot(key="cost", prompt_hint="h", tc_family="f", status=st)
        assert s.is_open is False, st
        assert s.is_closed is True, st


def test_slot_specs_with_same_key_differ_by_hint():
    a = SlotSpec(key="cost", prompt_hint="h1", tc_family="f1")
    b = SlotSpec(key="cost", prompt_hint="h2", tc_family="f2")
    assert a.key == b.key
    assert a != b  # 힌트가 다르면 다른 스펙


def test_content_holds_types():
    c = Content(name="파티편성", game="starrail", types=["편성"])
    assert c.types == ["편성"]
```

- [ ] **Step 3: 테스트가 실패하는지 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_knowledge_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qatc.knowledge'`

- [ ] **Step 4: 구현**

`qatc/knowledge/models.py`:

```python
"""지식 모델의 도메인 타입.

슬롯은 "이 컨텐츠에 대해 알아야 할 항목 하나"다. 상태가 4단계인 것이 핵심이다 —
`EMPTY`(아직 안 물어봄)와 `NA`(해당 없음)를 구분하지 않으면, 사용자가 "이건 재화
안 써요"라고 답해도 도구가 계속 재화를 묻는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SlotStatus(str, Enum):
    EMPTY = "empty"       # 아직 안 물어봄
    FILLED = "filled"     # 사용자가 답함
    UNKNOWN = "unknown"   # 사용자가 "모른다"고 답함
    NA = "na"             # 이 컨텐츠엔 해당 없음


@dataclass(frozen=True)
class SlotSpec:
    """슬롯의 정의. 상태를 갖지 않는 순수 스펙이다."""

    key: str
    prompt_hint: str
    tc_family: str


@dataclass
class Slot:
    """상태와 값을 가진 슬롯."""

    key: str
    prompt_hint: str
    tc_family: str
    status: SlotStatus = SlotStatus.EMPTY
    value: str = ""
    ord: int = 0

    @property
    def is_open(self) -> bool:
        """아직 물어볼 대상인가."""
        return self.status is SlotStatus.EMPTY

    @property
    def is_closed(self) -> bool:
        """다시 묻지 않아도 되는가. UNKNOWN·NA도 닫힌 것으로 본다."""
        return not self.is_open


@dataclass
class Content:
    """인터뷰 단위. 게임의 기능/모듈 하나."""

    name: str
    game: str
    types: list[str] = field(default_factory=list)
```

`qatc/knowledge/__init__.py`:

```python
"""인터뷰로 쌓은 게임 지식과 그로부터의 TC 생성."""

from .models import Content, Slot, SlotSpec, SlotStatus

__all__ = ["Content", "Slot", "SlotSpec", "SlotStatus"]
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_knowledge_models.py -v`
Expected: PASS (6 passed)

- [ ] **Step 6: 전체 회귀 확인**

Run: `.venv\Scripts\python.exe -m pytest`
Expected: 137 passed (기존 131 + 신규 6)

- [ ] **Step 7: 커밋**

```bash
git add qatc/knowledge/ qatc/models.py tests/test_knowledge_models.py
git commit -m "지식 도메인 타입 추가 및 TCOrigin.INTERVIEW 신설

슬롯 상태를 4단계로 둔다. EMPTY(아직 안 물어봄)와 NA(해당 없음)를
구분해야 '이건 재화 안 써요'라고 답한 항목을 다시 묻지 않는다.

TCOrigin 에 INTERVIEW 를 추가한다. 사용자가 말한 것과 도구가 도출한 것은
테스터가 의심할 지점이 달라 구분이 필요하다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: 슬롯 세트 조립

**Files:**
- Create: `qatc/knowledge/slots.py`
- Modify: `qatc/knowledge/__init__.py` (재수출 추가)
- Test: `tests/test_knowledge_slots.py`

**Interfaces:**
- Consumes: `SlotSpec` (Task 1)
- Produces:
  - `BASE_SLOTS: tuple[SlotSpec, ...]` — 10개 공통 슬롯
  - `TYPE_SLOTS: dict[str, tuple[SlotSpec, ...]]` — 유형별 추가 슬롯. 키는 `가챠` `편성` `성장` `던전` `상점` `임무`
  - `KNOWN_TYPES: tuple[str, ...]` — `TYPE_SLOTS` 의 키 목록
  - `build_slot_set(types: Sequence[str]) -> list[SlotSpec]` — 기본 + 유형별 병합. 키 중복 시 **먼저 조립된 것이 이긴다**. 모르는 유형은 `ValueError`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_knowledge_slots.py`:

```python
import pytest

from qatc.knowledge.slots import BASE_SLOTS, KNOWN_TYPES, TYPE_SLOTS, build_slot_set


def test_base_slots_have_ten_entries_with_unique_keys():
    keys = [s.key for s in BASE_SLOTS]
    assert len(keys) == 10
    assert len(set(keys)) == 10


def test_base_slots_cover_required_keys():
    keys = {s.key for s in BASE_SLOTS}
    assert keys == {
        "overview", "unlock", "entry", "screen", "core_action",
        "constraints", "cost", "failure", "result", "exit",
    }


def test_overview_produces_no_tc_family():
    overview = next(s for s in BASE_SLOTS if s.key == "overview")
    assert overview.tc_family == ""


def test_every_non_overview_base_slot_has_a_family():
    for s in BASE_SLOTS:
        if s.key == "overview":
            continue
        assert s.tc_family, s.key


def test_no_types_returns_base_only():
    got = build_slot_set([])
    assert [s.key for s in got] == [s.key for s in BASE_SLOTS]


def test_type_slots_are_appended_after_base():
    got = build_slot_set(["편성"])
    assert len(got) > len(BASE_SLOTS)
    assert [s.key for s in got[: len(BASE_SLOTS)]] == [s.key for s in BASE_SLOTS]


def test_duplicate_key_across_types_keeps_the_first():
    # 가챠와 상점이 모두 재화 관련 슬롯을 요구할 때 앞에 적힌 쪽이 이긴다
    got_a = build_slot_set(["가챠", "상점"])
    got_b = build_slot_set(["상점", "가챠"])
    keys_a = [s.key for s in got_a]
    keys_b = [s.key for s in got_b]
    assert len(keys_a) == len(set(keys_a))
    assert len(keys_b) == len(set(keys_b))
    assert set(keys_a) == set(keys_b)


def test_base_slot_wins_over_type_slot_with_same_key():
    # 어떤 유형도 base 키를 덮어쓸 수 없다
    base_hint = {s.key: s.prompt_hint for s in BASE_SLOTS}
    for t in KNOWN_TYPES:
        for s in build_slot_set([t]):
            if s.key in base_hint:
                assert s.prompt_hint == base_hint[s.key], (t, s.key)


def test_unknown_type_raises():
    with pytest.raises(ValueError, match="알 수 없는 컨텐츠 유형"):
        build_slot_set(["로그라이크"])


def test_all_known_types_build_without_error():
    for t in KNOWN_TYPES:
        assert build_slot_set([t])
    assert build_slot_set(list(KNOWN_TYPES))


def test_type_slot_keys_are_prefixed_to_avoid_base_collision():
    for t, specs in TYPE_SLOTS.items():
        for s in specs:
            assert s.key.startswith(f"{t}."), (t, s.key)
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_knowledge_slots.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qatc.knowledge.slots'`

- [ ] **Step 3: 구현**

`qatc/knowledge/slots.py`:

```python
"""슬롯 세트 조립.

각 슬롯이 **어떤 TC 계열을 만드는지**를 정의에 못박는다. 이것이 "설명에 근거가
있는 예외만 만든다"를 코드로 강제하는 방법이다 — 슬롯이 비어 있으면 그 계열은
생성 대상에서 아예 빠진다 (:mod:`qatc.knowledge.gate` 참조).

유형별 슬롯 키에 `유형.` 접두사를 붙이는 이유는 기본 슬롯 키와 절대 충돌하지 않게
하기 위해서다. 기본 슬롯은 모든 컨텐츠에서 같은 의미여야 한다.
"""

from __future__ import annotations

from typing import Sequence

from .models import SlotSpec

#: 모든 컨텐츠에 공통인 슬롯. 순서가 곧 인터뷰의 기본 진행 순서다.
BASE_SLOTS: tuple[SlotSpec, ...] = (
    SlotSpec("overview", "이 컨텐츠가 무엇이고 플레이어가 왜 쓰는가", ""),
    SlotSpec("entry", "어디서 어떻게 들어가는가", "진입 경로"),
    SlotSpec("screen", "무엇이 보이고 무엇을 누를 수 있는가", "요소 표시 확인"),
    SlotSpec("core_action", "이 컨텐츠의 주 동작은 무엇인가", "정상 경로"),
    SlotSpec("result", "성공하면 무엇이 어디에 반영되는가", "결과 검증"),
    SlotSpec("constraints", "정원·상한·중복 제한이 있는가", "경계값"),
    SlotSpec("cost", "무엇을 소모하는가", "재화 부족"),
    SlotSpec("failure", "안 되는 경우와 그때의 피드백은", "실패 경로"),
    SlotSpec("exit", "언제 저장되는가, 취소할 수 있는가", "미저장 이탈"),
    SlotSpec("unlock", "언제부터 쓸 수 있는가", "미해금 접근"),
)

#: 유형별 추가 슬롯. 개괄을 듣고 판정한 유형에 따라 붙는다.
TYPE_SLOTS: dict[str, tuple[SlotSpec, ...]] = {
    "가챠": (
        SlotSpec("가챠.확률공개", "확률이 어디에 표시되는가", "요소 표시 확인"),
        SlotSpec("가챠.천장", "천장이 있는가, 몇 회인가", "경계값"),
        SlotSpec("가챠.픽업", "픽업 규칙은 어떻게 되는가", "결과 검증"),
        SlotSpec("가챠.연차", "10연 등 묶음 뽑기의 차이는", "정상 경로"),
    ),
    "편성": (
        SlotSpec("편성.정원", "몇 명/개까지 넣을 수 있는가", "경계값"),
        SlotSpec("편성.중복", "같은 대상을 두 번 넣을 수 있는가", "경계값"),
        SlotSpec("편성.프리셋", "프리셋을 몇 개 저장할 수 있는가", "경계값"),
        SlotSpec("편성.저장시점", "언제 실제로 적용되는가", "미저장 이탈"),
    ),
    "성장": (
        SlotSpec("성장.재료", "무엇을 재료로 쓰는가", "재화 부족"),
        SlotSpec("성장.성공률", "실패할 수 있는가, 확률은", "실패 경로"),
        SlotSpec("성장.실패손실", "실패하면 무엇을 잃는가", "실패 경로"),
        SlotSpec("성장.최대치", "최대 레벨/단계는", "경계값"),
    ),
    "던전": (
        SlotSpec("던전.구성", "층·웨이브 구성은 어떻게 되는가", "정상 경로"),
        SlotSpec("던전.실패페널티", "실패하면 무엇을 잃는가", "실패 경로"),
        SlotSpec("던전.재도전", "다시 도전할 수 있는가, 조건은", "정상 경로"),
        SlotSpec("던전.제한시간", "제한 시간이 있는가", "경계값"),
    ),
    "상점": (
        SlotSpec("상점.재고", "재고가 한정되는가", "경계값"),
        SlotSpec("상점.갱신", "언제 갱신되는가", "결과 검증"),
        SlotSpec("상점.한도", "구매 한도가 있는가", "경계값"),
        SlotSpec("상점.환불", "환불·되돌리기가 되는가", "미저장 이탈"),
    ),
    "임무": (
        SlotSpec("임무.수락", "수락 조건이 있는가", "미해금 접근"),
        SlotSpec("임무.진행판정", "진행이 어떻게 판정되는가", "결과 검증"),
        SlotSpec("임무.보상수령", "보상을 어떻게 받는가", "정상 경로"),
        SlotSpec("임무.만료", "만료가 있는가", "경계값"),
    ),
}

KNOWN_TYPES: tuple[str, ...] = tuple(TYPE_SLOTS)


def build_slot_set(types: Sequence[str]) -> list[SlotSpec]:
    """기본 슬롯과 유형별 슬롯을 합친다.

    키가 겹치면 **먼저 조립된 것을 남긴다.** 조립 순서가 `기본 → types 인자 순서`
    이므로 기본 슬롯이 항상 이기고, 유형 간에는 앞에 적힌 쪽이 이긴다.

    :raises ValueError: 모르는 유형이 들어오면. 조용히 무시하면 사용자는 자기가
        말한 유형의 슬롯이 왜 없는지 알 수 없다.
    """
    unknown = [t for t in types if t not in TYPE_SLOTS]
    if unknown:
        raise ValueError(
            f"알 수 없는 컨텐츠 유형: {', '.join(unknown)} "
            f"(사용 가능: {', '.join(KNOWN_TYPES)})"
        )

    out: list[SlotSpec] = []
    seen: set[str] = set()
    for spec in (*BASE_SLOTS, *(s for t in types for s in TYPE_SLOTS[t])):
        if spec.key in seen:
            continue
        seen.add(spec.key)
        out.append(spec)
    return out
```

`qatc/knowledge/__init__.py` 를 다음으로 바꾼다:

```python
"""인터뷰로 쌓은 게임 지식과 그로부터의 TC 생성."""

from .models import Content, Slot, SlotSpec, SlotStatus
from .slots import BASE_SLOTS, KNOWN_TYPES, TYPE_SLOTS, build_slot_set

__all__ = [
    "BASE_SLOTS",
    "KNOWN_TYPES",
    "TYPE_SLOTS",
    "Content",
    "Slot",
    "SlotSpec",
    "SlotStatus",
    "build_slot_set",
]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_knowledge_slots.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: 커밋**

```bash
git add qatc/knowledge/slots.py qatc/knowledge/__init__.py tests/test_knowledge_slots.py
git commit -m "슬롯 세트 조립 규칙 추가

기본 슬롯 10개와 유형별 슬롯 6종을 정의한다. 각 슬롯에 tc_family 를 달아
'이 슬롯이 비면 이 계열 TC는 만들 수 없다'를 데이터로 표현한다.

유형별 슬롯 키에 '유형.' 접두사를 강제해 기본 슬롯과 절대 충돌하지 않게 했다.
기본 슬롯은 모든 컨텐츠에서 같은 의미여야 한다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: 지식 저장소

**Files:**
- Create: `qatc/knowledge/store.py`
- Modify: `qatc/config.py` (`AppConfig.knowledge_root` / `knowledge_path` 추가)
- Modify: `qatc/knowledge/__init__.py`
- Test: `tests/test_knowledge_store.py`

**Interfaces:**
- Consumes: `Content` `Slot` `SlotStatus` (Task 1), `build_slot_set` (Task 2)
- Produces:
  - `KnowledgeStore(db_path: Path | str)` — 게임 하나당 DB 하나
  - `KnowledgeStore.open() / close()`, context manager 지원
  - `.init_content(name, game, types) -> Content` — 없으면 만들고, 있으면 **기존 슬롯 값을 보존한 채** 새 유형 슬롯만 추가
  - `.get_content(name) -> Content | None`
  - `.list_contents() -> list[Content]`
  - `.slots(name) -> list[Slot]` — `ord` 순
  - `.set_slot(name, key, status, value) -> Slot` — 없는 키면 `KeyError`
  - `.add_slot(name, key, prompt_hint, tc_family) -> Slot` — 이미 있으면 `KeyError`
  - `AppConfig.knowledge_path` → `Path` (없으면 만든다)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_knowledge_store.py`:

```python
import pytest

from qatc.knowledge.models import SlotStatus
from qatc.knowledge.slots import BASE_SLOTS
from qatc.knowledge.store import KnowledgeStore


@pytest.fixture()
def store(tmp_path):
    with KnowledgeStore(tmp_path / "starrail.db") as s:
        yield s


def test_init_content_creates_base_slots(store):
    store.init_content("파티편성", game="starrail", types=[])
    keys = [s.key for s in store.slots("파티편성")]
    assert keys == [s.key for s in BASE_SLOTS]


def test_init_content_with_type_adds_type_slots(store):
    store.init_content("파티편성", game="starrail", types=["편성"])
    keys = {s.key for s in store.slots("파티편성")}
    assert "편성.정원" in keys
    assert "core_action" in keys


def test_slots_start_empty(store):
    store.init_content("파티편성", game="starrail", types=[])
    assert all(s.status is SlotStatus.EMPTY for s in store.slots("파티편성"))
    assert all(s.value == "" for s in store.slots("파티편성"))


def test_set_slot_persists_value_and_status(store):
    store.init_content("파티편성", game="starrail", types=[])
    store.set_slot("파티편성", "constraints", SlotStatus.FILLED, "최대 4명, 중복 불가")
    got = {s.key: s for s in store.slots("파티편성")}["constraints"]
    assert got.status is SlotStatus.FILLED
    assert got.value == "최대 4명, 중복 불가"


def test_set_slot_unknown_key_raises_with_available_keys(store):
    store.init_content("파티편성", game="starrail", types=[])
    with pytest.raises(KeyError) as exc:
        store.set_slot("파티편성", "없는키", SlotStatus.FILLED, "값")
    assert "core_action" in str(exc.value)


def test_set_slot_on_missing_content_raises(store):
    with pytest.raises(KeyError, match="파티편성"):
        store.set_slot("파티편성", "core_action", SlotStatus.FILLED, "값")


def test_init_content_again_preserves_existing_values(store):
    store.init_content("파티편성", game="starrail", types=[])
    store.set_slot("파티편성", "core_action", SlotStatus.FILLED, "파티를 짠다")
    store.init_content("파티편성", game="starrail", types=["편성"])

    slots = {s.key: s for s in store.slots("파티편성")}
    assert slots["core_action"].value == "파티를 짠다"
    assert slots["core_action"].status is SlotStatus.FILLED
    assert "편성.정원" in slots
    assert slots["편성.정원"].status is SlotStatus.EMPTY


def test_init_content_again_accumulates_types(store):
    store.init_content("워프", game="starrail", types=["가챠"])
    c = store.init_content("워프", game="starrail", types=["상점"])
    assert set(c.types) == {"가챠", "상점"}


def test_add_slot_appends_at_end(store):
    store.init_content("파티편성", game="starrail", types=[])
    before = len(store.slots("파티편성"))
    store.add_slot("파티편성", "네트워크", "통신이 끊기면", "중단")
    after = store.slots("파티편성")
    assert len(after) == before + 1
    assert after[-1].key == "네트워크"
    assert after[-1].tc_family == "중단"


def test_add_slot_duplicate_key_raises(store):
    store.init_content("파티편성", game="starrail", types=[])
    with pytest.raises(KeyError, match="이미 있습니다"):
        store.add_slot("파티편성", "core_action", "힌트", "계열")


def test_list_contents(store):
    store.init_content("파티편성", game="starrail", types=[])
    store.init_content("워프", game="starrail", types=["가챠"])
    assert {c.name for c in store.list_contents()} == {"파티편성", "워프"}


def test_reopen_keeps_data(tmp_path):
    p = tmp_path / "starrail.db"
    with KnowledgeStore(p) as s:
        s.init_content("파티편성", game="starrail", types=[])
        s.set_slot("파티편성", "cost", SlotStatus.NA, "")
    with KnowledgeStore(p) as s:
        got = {x.key: x for x in s.slots("파티편성")}["cost"]
        assert got.status is SlotStatus.NA
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_knowledge_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qatc.knowledge.store'`

- [ ] **Step 3: `AppConfig` 에 지식 경로 추가**

`qatc/config.py` 의 `AppConfig` 에 필드와 프로퍼티를 추가한다. `sessions_root` 바로 아래에 넣는다.

```python
    knowledge_root: str = ""
```

`__post_init__` 에 다음 두 줄을 추가한다:

```python
        if not self.knowledge_root:
            self.knowledge_root = str(project_root() / "knowledge")
```

`sessions_path` 프로퍼티 아래에 추가한다:

```python
    @property
    def knowledge_path(self) -> Path:
        p = Path(self.knowledge_root)
        p.mkdir(parents=True, exist_ok=True)
        return p
```

- [ ] **Step 4: 저장소 구현**

`qatc/knowledge/store.py`:

```python
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
```

`qatc/knowledge/__init__.py` 의 import 와 `__all__` 에 `KnowledgeStore` 를 추가한다.

- [ ] **Step 5: 테스트 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_knowledge_store.py -v`
Expected: PASS (12 passed)

- [ ] **Step 6: 전체 회귀 확인**

Run: `.venv\Scripts\python.exe -m pytest`
Expected: 160 passed

- [ ] **Step 7: 커밋**

```bash
git add qatc/knowledge/ qatc/config.py tests/test_knowledge_store.py
git commit -m "게임 단위 지식 저장소 추가

컨텐츠·슬롯·TC 3개 테이블. 대화 로그 테이블은 두지 않는다 — Claude Code
트랜스크립트가 그 역할을 한다.

init_content 재실행이 기존 슬롯 값을 보존하도록 했다. 재실행이 사용자가 채운
내용을 지우면 아무도 다시 실행하지 않는다.

set_slot 은 없는 키를 조용히 무시하지 않고 사용 가능한 키 목록과 함께 던진다.
성공한 척하면 답변이 증발한 것처럼 보인다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: TC 계열 게이트

**Files:**
- Create: `qatc/knowledge/gate.py`
- Modify: `qatc/knowledge/__init__.py`
- Test: `tests/test_knowledge_gate.py`

**이 태스크가 설계 전체의 안전장치다.** 인터뷰어가 Claude Code로 바뀌면서 "빈 슬롯 계열은 만들지 않는다"를 프롬프트로만 부탁하게 될 뻔했는데, 여기서 코드 강제로 되돌린다.

**Interfaces:**
- Consumes: `Slot` `SlotStatus` (Task 1), `TCKind` `Priority` (`qatc.models`)
- Produces:
  - `FAMILY_META: dict[str, tuple[TCKind, Priority]]` — 9개 계열의 기본 종류·우선순위
  - `FamilyPlan(family, slot_key, prompt_hint, kind, priority)` — frozen dataclass
  - `FamilySkip(family, slot_key, prompt_hint, status)` — frozen dataclass
  - `plan_families(slots: Sequence[Slot]) -> tuple[list[FamilyPlan], list[FamilySkip]]`
  - `validate_family(family: str, slots: Sequence[Slot]) -> FamilyPlan` — 생성 대상이 아니면 `ValueError`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_knowledge_gate.py`:

```python
import pytest

from qatc.knowledge.gate import FAMILY_META, plan_families, validate_family
from qatc.knowledge.models import Slot, SlotStatus
from qatc.knowledge.slots import BASE_SLOTS
from qatc.models import Priority, TCKind


def _slots(**statuses) -> list[Slot]:
    """BASE_SLOTS 를 바탕으로 지정한 키만 상태를 바꾼 슬롯 목록을 만든다."""
    out = []
    for i, spec in enumerate(BASE_SLOTS):
        st = statuses.get(spec.key, SlotStatus.EMPTY)
        out.append(Slot(spec.key, spec.prompt_hint, spec.tc_family, status=st, ord=i))
    return out


def test_every_base_family_has_meta():
    for spec in BASE_SLOTS:
        if spec.tc_family:
            assert spec.tc_family in FAMILY_META, spec.tc_family


def test_no_filled_slots_plans_nothing():
    planned, skipped = plan_families(_slots())
    assert planned == []
    assert {s.family for s in skipped} == {s.tc_family for s in BASE_SLOTS if s.tc_family}


def test_filled_slot_is_planned():
    planned, _ = plan_families(_slots(core_action=SlotStatus.FILLED))
    assert [p.family for p in planned] == ["정상 경로"]
    assert planned[0].slot_key == "core_action"
    assert planned[0].kind is TCKind.HAPPY_PATH
    assert planned[0].priority is Priority.HIGH


def test_empty_slot_family_is_skipped_with_status():
    _, skipped = plan_families(_slots(core_action=SlotStatus.FILLED))
    by_family = {s.family: s for s in skipped}
    assert by_family["재화 부족"].status is SlotStatus.EMPTY
    assert by_family["재화 부족"].slot_key == "cost"


def test_unknown_and_na_slots_are_skipped_not_planned():
    planned, skipped = plan_families(
        _slots(failure=SlotStatus.UNKNOWN, cost=SlotStatus.NA)
    )
    assert planned == []
    by_family = {s.family: s for s in skipped}
    assert by_family["실패 경로"].status is SlotStatus.UNKNOWN
    assert by_family["재화 부족"].status is SlotStatus.NA


def test_overview_never_appears_in_plan_or_skip():
    planned, skipped = plan_families(_slots(overview=SlotStatus.FILLED))
    assert all(p.slot_key != "overview" for p in planned)
    assert all(s.slot_key != "overview" for s in skipped)


def test_validate_family_accepts_planned_family():
    got = validate_family("정상 경로", _slots(core_action=SlotStatus.FILLED))
    assert got.family == "정상 경로"


def test_validate_family_rejects_empty_slot_family():
    with pytest.raises(ValueError) as exc:
        validate_family("재화 부족", _slots(core_action=SlotStatus.FILLED))
    msg = str(exc.value)
    assert "재화 부족" in msg
    assert "cost" in msg
    assert "tc plan" in msg


def test_validate_family_rejects_unknown_family_name():
    with pytest.raises(ValueError, match="알 수 없는 계열"):
        validate_family("없는계열", _slots(core_action=SlotStatus.FILLED))


def test_two_slots_sharing_a_family_plan_once():
    # 편성.정원 과 constraints 가 모두 '경계값' 계열이면 한 번만 계획된다
    slots = _slots(constraints=SlotStatus.FILLED)
    slots.append(Slot("편성.정원", "몇 명까지", "경계값", status=SlotStatus.FILLED, ord=99))
    planned, _ = plan_families(slots)
    assert [p.family for p in planned].count("경계값") == 1


def test_family_planned_if_any_source_slot_is_filled():
    slots = _slots()  # constraints 는 EMPTY
    slots.append(Slot("편성.정원", "몇 명까지", "경계값", status=SlotStatus.FILLED, ord=99))
    planned, skipped = plan_families(slots)
    assert "경계값" in [p.family for p in planned]
    assert "경계값" not in [s.family for s in skipped]
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_knowledge_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qatc.knowledge.gate'`

- [ ] **Step 3: 구현**

`qatc/knowledge/gate.py`:

```python
"""TC 계열 게이트 — 이 모듈이 설계 전체의 안전장치다.

인터뷰어가 Claude Code 세션이므로 "빈 슬롯 계열은 만들지 않는다"를 프롬프트로만
부탁하면 지키다 말다 한다. 여기서 **생성 대상 계열을 코드가 계산하고**
(:func:`plan_families`) **대상이 아닌 계열을 코드가 거부해**
(:func:`validate_family`) 프롬프트가 규칙을 어겨도 결과물이 오염되지 않게 한다.

한 계열의 근거 슬롯이 여럿일 수 있다 (`constraints` 와 `편성.정원` 이 둘 다
'경계값'). **하나라도 filled면 그 계열은 생성 대상이다.**
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..models import Priority, TCKind
from .models import Slot, SlotStatus

#: 계열 → (TC 종류, 기본 우선순위).
#: INTERRUPT 를 쓰는 계열이 기본에 없는 이유: 진술에서 통신 끊김·강제 종료가
#: 도출되는 경우가 없다. `slot add` 로 사용자가 추가하면 그때 쓴다.
FAMILY_META: dict[str, tuple[TCKind, Priority]] = {
    "정상 경로": (TCKind.HAPPY_PATH, Priority.HIGH),
    "진입 경로": (TCKind.HAPPY_PATH, Priority.HIGH),
    "결과 검증": (TCKind.HAPPY_PATH, Priority.HIGH),
    "경계값": (TCKind.BOUNDARY, Priority.MEDIUM),
    "재화 부족": (TCKind.EXCEPTION, Priority.MEDIUM),
    "실패 경로": (TCKind.EXCEPTION, Priority.MEDIUM),
    "미해금 접근": (TCKind.EXCEPTION, Priority.LOW),
    "미저장 이탈": (TCKind.REVERSE, Priority.MEDIUM),
    "요소 표시 확인": (TCKind.UIUX, Priority.LOW),
    "중단": (TCKind.INTERRUPT, Priority.MEDIUM),
}


@dataclass(frozen=True)
class FamilyPlan:
    """생성 대상 계열."""

    family: str
    slot_key: str
    prompt_hint: str
    kind: TCKind
    priority: Priority


@dataclass(frozen=True)
class FamilySkip:
    """생성 대상이 아닌 계열과 그 이유."""

    family: str
    slot_key: str
    prompt_hint: str
    status: SlotStatus


def plan_families(slots: Sequence[Slot]) -> tuple[list[FamilyPlan], list[FamilySkip]]:
    """슬롯 상태에서 만들 수 있는 계열과 제외된 계열을 계산한다.

    `tc_family` 가 빈 슬롯(`overview`)은 양쪽 어디에도 넣지 않는다 — 문맥일 뿐
    TC를 만들지 않는다.
    """
    first_filled: dict[str, Slot] = {}
    first_blocked: dict[str, Slot] = {}

    for slot in slots:
        if not slot.tc_family:
            continue
        if slot.status is SlotStatus.FILLED:
            first_filled.setdefault(slot.tc_family, slot)
        else:
            first_blocked.setdefault(slot.tc_family, slot)

    planned: list[FamilyPlan] = []
    for family, slot in first_filled.items():
        kind, priority = FAMILY_META.get(family, (TCKind.HAPPY_PATH, Priority.MEDIUM))
        planned.append(
            FamilyPlan(family, slot.key, slot.prompt_hint, kind, priority)
        )

    skipped = [
        FamilySkip(family, slot.key, slot.prompt_hint, slot.status)
        for family, slot in first_blocked.items()
        if family not in first_filled
    ]
    return planned, skipped


def validate_family(family: str, slots: Sequence[Slot]) -> FamilyPlan:
    """이 계열이 지금 생성 대상인지 검사한다.

    :raises ValueError: 대상이 아니면. 메시지에 막힌 슬롯 키와 다음 조치를 넣는다 —
        "안 됩니다"만 하면 호출자가 무엇을 채워야 할지 모른다.
    """
    planned, skipped = plan_families(slots)
    for p in planned:
        if p.family == family:
            return p

    for s in skipped:
        if s.family == family:
            reason = {
                SlotStatus.EMPTY: "슬롯이 비어 있음",
                SlotStatus.UNKNOWN: "사용자가 모른다고 답함",
                SlotStatus.NA: "해당 없음으로 표시됨",
            }[s.status]
            raise ValueError(
                f"'{family}'은(는) 생성 대상이 아닙니다 "
                f"({s.slot_key} 슬롯: {reason}). "
                f"qatc tc plan 으로 대상 계열을 확인하세요."
            )

    raise ValueError(
        f"알 수 없는 계열: '{family}'. "
        f"qatc tc plan 으로 대상 계열을 확인하세요."
    )
```

`qatc/knowledge/__init__.py` 의 import 와 `__all__` 에 `FamilyPlan` `FamilySkip` `plan_families` `validate_family` 를 추가한다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_knowledge_gate.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: 커밋**

```bash
git add qatc/knowledge/gate.py qatc/knowledge/__init__.py tests/test_knowledge_gate.py
git commit -m "TC 계열 게이트 추가 — 설계의 핵심 안전장치

plan_families 가 만들 수 있는 계열을, validate_family 가 대상 아닌 계열을
거부한다. 인터뷰어가 Claude Code 세션이라 '빈 슬롯 계열은 만들지 않는다'를
프롬프트로만 부탁할 뻔했는데, 코드 강제로 되돌렸다.

한 계열의 근거 슬롯이 여럿일 때 하나라도 filled면 대상으로 본다
(constraints 와 편성.정원 이 둘 다 '경계값').

거부 메시지에 막힌 슬롯 키와 다음 조치를 넣었다. '안 됩니다'만 하면
호출자가 무엇을 채워야 할지 모른다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: TC 저장과 재생성 병합

**Files:**
- Modify: `qatc/knowledge/store.py` (TC 메서드 추가)
- Test: `tests/test_knowledge_tc_store.py`

**Interfaces:**
- Consumes: `KnowledgeStore` (Task 3), `TestCase` `TCOrigin` (`qatc.models`)
- Produces:
  - `testcase_hash(tc: TestCase) -> str` — 본문 필드만 해시 (id·origin 제외)
  - `KnowledgeStore.add_testcase(content, family, tc, slot_keys) -> TestCase` — id가 비면 자동 부여
  - `KnowledgeStore.testcases(content, family=None) -> list[TestCase]`
  - `KnowledgeStore.replace_generated(content, family, cases, slot_keys) -> tuple[int, int]` — `(추가, 보존)`. 사용자가 손댄 TC와 `origin=USER` 는 보존

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_knowledge_tc_store.py`:

```python
import pytest

from qatc.knowledge.store import KnowledgeStore, testcase_hash
from qatc.models import Priority, TCKind, TCOrigin, TestCase


def _tc(title="제목", origin=TCOrigin.INTERVIEW, **kw) -> TestCase:
    return TestCase(
        id=kw.pop("id", ""),
        category_major="파티 편성",
        category_minor="정상 경로",
        title=title,
        precondition="파티 편성 화면",
        steps=["파티 적용을 누른다"],
        expected=["파티가 적용된다"],
        priority=Priority.HIGH,
        kind=TCKind.HAPPY_PATH,
        origin=origin,
        rationale="core_action 슬롯에서 도출",
        **kw,
    )


@pytest.fixture()
def store(tmp_path):
    with KnowledgeStore(tmp_path / "starrail.db") as s:
        s.init_content("파티편성", game="starrail", types=[])
        yield s


def test_hash_ignores_id_and_origin():
    a = _tc(id="tc_1", origin=TCOrigin.INTERVIEW)
    b = _tc(id="tc_2", origin=TCOrigin.INFERRED)
    assert testcase_hash(a) == testcase_hash(b)


def test_hash_changes_with_title():
    assert testcase_hash(_tc(title="가")) != testcase_hash(_tc(title="나"))


def test_hash_changes_with_expected():
    a = _tc()
    b = _tc()
    b.expected = ["다른 결과"]
    assert testcase_hash(a) != testcase_hash(b)


def test_add_testcase_assigns_id(store):
    got = store.add_testcase("파티편성", "정상 경로", _tc(), ["core_action"])
    assert got.id.startswith("tc_")


def test_testcases_roundtrip(store):
    store.add_testcase("파티편성", "정상 경로", _tc(title="원본"), ["core_action"])
    got = store.testcases("파티편성")
    assert len(got) == 1
    assert got[0].title == "원본"
    assert got[0].origin is TCOrigin.INTERVIEW
    assert got[0].steps == ["파티 적용을 누른다"]
    assert got[0].priority is Priority.HIGH


def test_testcases_filter_by_family(store):
    store.add_testcase("파티편성", "정상 경로", _tc(title="A"), ["core_action"])
    store.add_testcase("파티편성", "경계값", _tc(title="B"), ["constraints"])
    assert [t.title for t in store.testcases("파티편성", family="경계값")] == ["B"]


def test_replace_generated_replaces_untouched_case(store):
    store.add_testcase("파티편성", "정상 경로", _tc(title="구버전"), ["core_action"])
    added, kept = store.replace_generated(
        "파티편성", "정상 경로", [_tc(title="신버전")], ["core_action"]
    )
    assert (added, kept) == (1, 0)
    assert [t.title for t in store.testcases("파티편성")] == ["신버전"]


def test_replace_generated_preserves_user_edited_case(store):
    saved = store.add_testcase("파티편성", "정상 경로", _tc(title="원본"), ["core_action"])
    # 사용자가 손댄 것처럼 본문만 바꿔 다시 저장 (해시는 그대로 두어 불일치를 만든다)
    saved.title = "사람이 고침"
    store.update_testcase_row(saved)

    added, kept = store.replace_generated(
        "파티편성", "정상 경로", [_tc(title="신버전")], ["core_action"]
    )
    assert kept == 1
    titles = {t.title for t in store.testcases("파티편성")}
    assert "사람이 고침" in titles
    assert "신버전" in titles


def test_replace_generated_preserves_user_origin_case(store):
    store.add_testcase(
        "파티편성", "정상 경로", _tc(title="사람이 추가", origin=TCOrigin.USER), []
    )
    added, kept = store.replace_generated(
        "파티편성", "정상 경로", [_tc(title="신버전")], ["core_action"]
    )
    assert kept == 1
    assert "사람이 추가" in {t.title for t in store.testcases("파티편성")}


def test_replace_generated_only_touches_named_family(store):
    store.add_testcase("파티편성", "경계값", _tc(title="경계값 것"), ["constraints"])
    store.replace_generated("파티편성", "정상 경로", [_tc(title="신규")], ["core_action"])
    assert "경계값 것" in {t.title for t in store.testcases("파티편성")}
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_knowledge_tc_store.py -v`
Expected: FAIL — `ImportError: cannot import name 'testcase_hash'`

- [ ] **Step 3: 구현**

`qatc/knowledge/store.py` 상단 import 에 다음을 추가한다:

```python
import hashlib

from ..models import TestCase, new_id
```

파일 끝에 모듈 수준 함수를 추가한다:

```python
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
```

`KnowledgeStore` 클래스에 다음 메서드를 추가한다 (`add_slot` 아래):

```python
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
        """본문만 갱신한다. `generated_hash` 는 건드리지 않으므로 이후
        :meth:`replace_generated` 가 '사용자가 고쳤다'로 판정한다."""
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

    def replace_generated(
        self,
        content: str,
        family: str,
        cases: Sequence[TestCase],
        slot_keys: Sequence[str],
    ) -> tuple[int, int]:
        """한 계열의 생성분을 갈아끼운다. 사람 손이 닿은 것은 보존한다.

        보존 조건 두 가지 —
        `origin=USER` 이거나, 저장된 본문 해시가 `generated_hash` 와 다른 것.
        후자가 "사용자가 고쳤다"의 판정이다.

        :returns: (추가한 수, 보존한 수)
        """
        db = self._db()
        kept = 0
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
        db.commit()

        for tc in cases:
            self.add_testcase(content, family, tc, slot_keys)
        return len(cases), kept
```

`store.py` 의 import 에 `TCOrigin` 도 추가한다: `from ..models import TCOrigin, TestCase, new_id`

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_knowledge_tc_store.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: 커밋**

```bash
git add qatc/knowledge/store.py tests/test_knowledge_tc_store.py
git commit -m "TC 저장과 재생성 병합 추가

generated_hash 로 '사용자가 고쳤는가'를 판정한다. TestCase 에 수정 플래그를
넣는 대신 저장소 테이블에 열을 둔 이유는, TestCase 가 xlsx 출력과 공유하는
순수 데이터 타입이고 수정 추적은 저장소의 관심사이기 때문이다.

해시는 id 와 origin 을 제외한다. 둘 중 하나가 달라졌다고 사람이 고친 것으로
볼 수 없다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: `qatc slot` 하위명령

**Files:**
- Create: `qatc/console.py`
- Create: `qatc/cli_knowledge.py`
- Modify: `qatc/cli.py` (`_p` 를 `console` 에서 가져오고, `build_parser` 에 등록 한 줄)
- Test: `tests/test_cli_slot.py`

`cli.py` 가 이미 680줄이라 새 명령을 그 안에 넣으면 더 나빠진다. 지식 관련 명령은 별도 모듈에 두고 `cli.py` 는 등록 함수 하나만 부른다.

`_p` 를 `qatc/console.py` 로 먼저 빼는 이유: `cli_knowledge.py` 가 `cli.py` 의 `_p` 를 쓰고 `cli.py` 가 `cli_knowledge.register` 를 쓰면 순환이 된다. 함수 안 import 로 덮으면 동작은 하지만 구조가 남는다. 양쪽이 의존하는 것을 제3의 모듈로 빼는 것이 정직한 해법이고, Task 11에서 `cli.py` 를 크게 손볼 때도 안 깨진다.

**Interfaces:**
- Consumes: `KnowledgeStore` (Task 3), `KNOWN_TYPES` (Task 2), `AppConfig.knowledge_path` (Task 3)
- Produces:
  - `qatc.console._p(msg: str = "") -> None` — Windows 코드페이지 안전 출력
  - `resolve_store(cfg, game: str | None, content: str | None) -> KnowledgeStore` — `--game` 이 없으면 컨텐츠를 가진 DB를 찾는다. 0개나 2개 이상이면 `SystemExit`
  - `register(sub) -> None` — argparse 하위파서 등록
  - `cmd_slot_status/init/set/add(args, cfg) -> int`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_cli_slot.py`:

```python
import json

import pytest

from qatc.cli import main
from qatc.config import AppConfig
from qatc.knowledge.store import KnowledgeStore


@pytest.fixture()
def cfg_env(tmp_path, monkeypatch):
    """AppConfig.load() 가 임시 knowledge 디렉터리를 쓰게 만든다."""
    kroot = tmp_path / "knowledge"
    original = AppConfig.load

    def patched(cls=AppConfig):
        c = original()
        c.knowledge_root = str(kroot)
        return c

    monkeypatch.setattr(AppConfig, "load", staticmethod(patched))
    return kroot


def test_init_creates_db_and_slots(cfg_env, capsys):
    assert main(["slot", "init", "파티편성", "--game", "starrail", "--types", "편성"]) == 0
    with KnowledgeStore(cfg_env / "starrail.db") as s:
        keys = {x.key for x in s.slots("파티편성")}
    assert "core_action" in keys
    assert "편성.정원" in keys


def test_init_unknown_type_fails_with_message(cfg_env, capsys):
    rc = main(["slot", "init", "던전", "--game", "starrail", "--types", "로그라이크"])
    assert rc == 1
    assert "알 수 없는 컨텐츠 유형" in capsys.readouterr().out


def test_status_json_lists_open_slots(cfg_env, capsys):
    main(["slot", "init", "파티편성", "--game", "starrail"])
    assert main(["slot", "status", "파티편성", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["content"] == "파티편성"
    assert data["filled"] == 0
    assert data["total"] == 10
    assert any(s["key"] == "core_action" for s in data["open"])


def test_set_then_status_reflects_it(cfg_env, capsys):
    main(["slot", "init", "파티편성", "--game", "starrail"])
    rc = main(["slot", "set", "파티편성", "core_action", "--status", "filled",
               "--value", "파티를 짠다"])
    assert rc == 0
    main(["slot", "status", "파티편성", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["filled"] == 1
    assert all(s["key"] != "core_action" for s in data["open"])


def test_na_slot_leaves_open_list(cfg_env, capsys):
    main(["slot", "init", "파티편성", "--game", "starrail"])
    main(["slot", "set", "파티편성", "cost", "--status", "na"])
    main(["slot", "status", "파티편성", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert all(s["key"] != "cost" for s in data["open"])


def test_set_unknown_key_fails_and_lists_keys(cfg_env, capsys):
    main(["slot", "init", "파티편성", "--game", "starrail"])
    rc = main(["slot", "set", "파티편성", "없는키", "--status", "filled", "--value", "x"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "core_action" in out


def test_set_on_missing_content_fails(cfg_env, capsys):
    rc = main(["slot", "set", "없는것", "core_action", "--status", "filled", "--value", "x"])
    assert rc == 1
    assert "없는것" in capsys.readouterr().out


def test_add_slot_appends(cfg_env, capsys):
    main(["slot", "init", "파티편성", "--game", "starrail"])
    rc = main(["slot", "add", "파티편성", "네트워크",
               "--hint", "통신이 끊기면", "--family", "중단"])
    assert rc == 0
    main(["slot", "status", "파티편성", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["total"] == 11


def test_game_is_inferred_when_only_one_db_has_the_content(cfg_env):
    main(["slot", "init", "파티편성", "--game", "starrail"])
    # --game 없이도 찾아낸다
    assert main(["slot", "status", "파티편성"]) == 0


def test_ambiguous_content_across_games_fails(cfg_env, capsys):
    main(["slot", "init", "공통", "--game", "starrail"])
    main(["slot", "init", "공통", "--game", "genshin"])
    rc = main(["slot", "status", "공통"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "--game" in out
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli_slot.py -v`
Expected: FAIL — `argparse` 가 `slot` 을 모르는 명령으로 거부 (`SystemExit: 2`)

- [ ] **Step 3: `_p` 를 공유 모듈로 분리**

`qatc/console.py` 를 만든다:

```python
"""콘솔 출력 헬퍼.

`cli.py` 와 `cli_knowledge.py` 가 둘 다 쓴다. 한쪽에 두면 순환 import 가 되므로
양쪽이 의존하는 제3의 모듈로 뺀다.
"""

from __future__ import annotations


def _p(msg: str = "") -> None:
    """콘솔 출력. Windows 기본 코드페이지에서 한글이 깨지지 않게 감싼다."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("utf-8", "replace").decode("utf-8", "replace"))
```

`qatc/cli.py` 에서 기존 `_p` 정의(29~34행)를 지우고 import 로 바꾼다:

```python
from .console import _p
```

`cli.py` 의 나머지 코드는 그대로 `_p(...)` 를 부르므로 다른 수정은 필요 없다.

- [ ] **Step 4: 구현**

`qatc/cli_knowledge.py`:

```python
"""지식·인터뷰 관련 CLI 하위명령.

`cli.py` 에 넣지 않고 분리한 이유는 그 파일이 이미 녹화 파이프라인 명령으로
680줄이기 때문이다. 지식 계열은 함께 바뀌므로 함께 둔다.

이 모듈의 명령은 **Claude Code 세션이 인터뷰 중 호출한다.** 출력이 사람과 모델
양쪽에게 읽히므로 `--json` 을 제공하고, 오류는 다음 조치를 항상 함께 알린다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import AppConfig
from .console import _p
from .knowledge.gate import plan_families
from .knowledge.models import SlotStatus
from .knowledge.slots import KNOWN_TYPES
from .knowledge.store import KnowledgeStore


def resolve_store(cfg: AppConfig, game: str | None, content: str | None) -> KnowledgeStore:
    """어느 게임 DB를 열지 정한다.

    `--game` 이 있으면 그대로. 없으면 컨텐츠를 가진 DB를 찾는다 — 인터뷰 중
    매번 `--game` 을 치게 하면 호출이 길어지고 오타가 난다.
    """
    root = cfg.knowledge_path
    if game:
        return KnowledgeStore(root / f"{game}.db").open()

    dbs = sorted(root.glob("*.db"))
    if not dbs:
        raise SystemExit("지식 DB가 없습니다. 먼저 'qatc slot init <컨텐츠> --game <게임>'을 실행하세요.")

    hits: list[Path] = []
    for p in dbs:
        with KnowledgeStore(p) as s:
            if content is None or s.get_content(content) is not None:
                hits.append(p)
    if not hits:
        raise SystemExit(f"'{content}' 컨텐츠를 가진 게임 DB가 없습니다. --game 으로 지정하세요.")
    if len(hits) > 1:
        names = ", ".join(p.stem for p in hits)
        raise SystemExit(f"'{content}'가 여러 게임에 있습니다 ({names}). --game 으로 지정하세요.")
    return KnowledgeStore(hits[0]).open()


def _status_payload(store: KnowledgeStore, content: str) -> dict:
    slots = store.slots(content)
    planned, skipped = plan_families(slots)
    return {
        "content": content,
        "game": store.game,
        "total": len(slots),
        "filled": sum(1 for s in slots if s.status is SlotStatus.FILLED),
        "open": [
            {"key": s.key, "hint": s.prompt_hint, "family": s.tc_family}
            for s in slots if s.is_open
        ],
        "closed": [
            {"key": s.key, "status": s.status.value, "value": s.value}
            for s in slots if s.is_closed
        ],
        "planned_families": [p.family for p in planned],
        "skipped_families": [
            {"family": s.family, "slot": s.slot_key, "status": s.status.value}
            for s in skipped
        ],
    }


def cmd_slot_status(args: argparse.Namespace, cfg: AppConfig) -> int:
    store = resolve_store(cfg, args.game, args.content)
    try:
        if store.get_content(args.content) is None:
            _p(f"컨텐츠 '{args.content}'가 없습니다. 'qatc slot init'을 먼저 실행하세요.")
            return 1
        payload = _status_payload(store, args.content)
    finally:
        store.close()

    if args.json:
        _p(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    _p(f"[{payload['content']}] {payload['game']} · {payload['filled']}/{payload['total']} 채움")
    if payload["open"]:
        _p("\n남은 항목:")
        for s in payload["open"]:
            _p(f"  {s['key']:<16} {s['hint']}")
    else:
        _p("\n모든 항목이 채워졌습니다. 'qatc tc plan'으로 생성 대상을 확인하세요.")
    return 0


def cmd_slot_init(args: argparse.Namespace, cfg: AppConfig) -> int:
    types = [t.strip() for t in (args.types or "").split(",") if t.strip()]
    store = KnowledgeStore(cfg.knowledge_path / f"{args.game}.db").open()
    try:
        content = store.init_content(args.content, game=args.game, types=types)
        n = len(store.slots(args.content))
    except ValueError as exc:
        _p(f"오류: {exc}")
        return 1
    finally:
        store.close()
    _p(f"[{content.name}] 슬롯 {n}개 준비됨 (유형: {', '.join(content.types) or '없음'})")
    return 0


def cmd_slot_set(args: argparse.Namespace, cfg: AppConfig) -> int:
    store = resolve_store(cfg, args.game, args.content)
    try:
        slot = store.set_slot(
            args.content, args.key, SlotStatus(args.status), args.value or ""
        )
    except KeyError as exc:
        _p(f"오류: {exc.args[0]}")
        return 1
    finally:
        store.close()
    _p(f"✓ {slot.key} = {slot.status.value}")
    return 0


def cmd_slot_add(args: argparse.Namespace, cfg: AppConfig) -> int:
    store = resolve_store(cfg, args.game, args.content)
    try:
        store.add_slot(args.content, args.key, args.hint, args.family)
    except KeyError as exc:
        _p(f"오류: {exc.args[0]}")
        return 1
    finally:
        store.close()
    _p(f"✓ 슬롯 추가됨: {args.key} → {args.family}")
    return 0


def register(sub) -> None:
    """`qatc` 하위파서에 지식 명령을 등록한다."""
    slot = sub.add_parser("slot", help="컨텐츠 지식 슬롯 조회·기록")
    slot_sub = slot.add_subparsers(dest="slot_command", required=True)

    st = slot_sub.add_parser("status", help="슬롯 상태 (질문 전 매번 호출)")
    st.add_argument("content")
    st.add_argument("--game", "-g")
    st.add_argument("--json", action="store_true", help="기계가 읽을 JSON으로 출력")
    st.set_defaults(func=cmd_slot_status)

    it = slot_sub.add_parser("init", help="컨텐츠 슬롯 세트 생성 (재실행 시 값 보존)")
    it.add_argument("content")
    it.add_argument("--game", "-g", required=True)
    it.add_argument("--types", "-t", default="",
                    help=f"쉼표 구분. 사용 가능: {', '.join(KNOWN_TYPES)}")
    it.set_defaults(func=cmd_slot_init)

    se = slot_sub.add_parser("set", help="슬롯 값 기록")
    se.add_argument("content")
    se.add_argument("key")
    se.add_argument("--status", required=True, choices=[s.value for s in SlotStatus])
    se.add_argument("--value", default="")
    se.add_argument("--game", "-g")
    se.set_defaults(func=cmd_slot_set)

    ad = slot_sub.add_parser("add", help="유형에 없던 슬롯 추가")
    ad.add_argument("content")
    ad.add_argument("key")
    ad.add_argument("--hint", required=True)
    ad.add_argument("--family", required=True)
    ad.add_argument("--game", "-g")
    ad.set_defaults(func=cmd_slot_add)
```

`qatc/cli.py` 의 `build_parser()` 에서 `sub = parser.add_subparsers(...)` 바로 아래에 한 줄을 추가한다:

```python
    from .cli_knowledge import register as _register_knowledge
    _register_knowledge(sub)
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli_slot.py -v`
Expected: PASS (10 passed)

- [ ] **Step 6: 전체 회귀 확인**

Run: `.venv\Scripts\python.exe -m pytest`
Expected: 191 passed

- [ ] **Step 7: 커밋**

```bash
git add qatc/cli_knowledge.py qatc/cli.py tests/test_cli_slot.py
git commit -m "qatc slot 하위명령 추가

status/init/set/add 4개. status 는 --json 을 제공한다 — Claude Code 가 질문
전에 매번 호출해 이미 물어본 항목을 다시 묻지 않게 하는 것이 목적이라,
사람이 읽는 표가 아니라 기계가 읽는 구조가 필요하다.

--game 을 생략하면 컨텐츠를 가진 DB를 찾는다. 인터뷰 중 매번 치게 하면
호출이 길어지고 오타가 난다. 모호하면 게임 목록과 함께 거부한다.

cli.py 가 이미 680줄이라 별도 모듈로 분리했다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: `qatc tc` 하위명령 — plan / add / list

**Files:**
- Modify: `qatc/cli_knowledge.py`
- Test: `tests/test_cli_tc.py`

**Interfaces:**
- Consumes: `plan_families` `validate_family` (Task 4), `KnowledgeStore.replace_generated` (Task 5)
- Produces:
  - `cmd_tc_plan/add/list(args, cfg) -> int`
  - `tc add` 가 읽는 JSON 스키마: `{"testcases": [{"title", "precondition", "steps": [], "expected": [], "rationale", "priority"?}]}`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_cli_tc.py`:

```python
import json

import pytest

from qatc.cli import main
from qatc.config import AppConfig
from qatc.knowledge.store import KnowledgeStore


@pytest.fixture()
def cfg_env(tmp_path, monkeypatch):
    kroot = tmp_path / "knowledge"
    original = AppConfig.load

    def patched(cls=AppConfig):
        c = original()
        c.knowledge_root = str(kroot)
        return c

    monkeypatch.setattr(AppConfig, "load", staticmethod(patched))
    return kroot


@pytest.fixture()
def ready(cfg_env):
    main(["slot", "init", "파티편성", "--game", "starrail"])
    main(["slot", "set", "파티편성", "core_action", "--status", "filled",
          "--value", "파티를 짜고 적용한다"])
    return cfg_env


def _payload(title="정상 동작"):
    return json.dumps({
        "testcases": [{
            "title": title,
            "precondition": "파티 편성 화면",
            "steps": ["파티 적용을 누른다"],
            "expected": ["파티가 적용된다"],
            "rationale": "core_action 슬롯에서 도출",
        }]
    }, ensure_ascii=False)


def test_plan_lists_filled_family(ready, capsys):
    assert main(["tc", "plan", "파티편성", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "정상 경로" in [p["family"] for p in data["planned"]]


def test_plan_lists_skipped_with_reason(ready, capsys):
    main(["tc", "plan", "파티편성", "--json"])
    data = json.loads(capsys.readouterr().out)
    skipped = {s["family"]: s for s in data["skipped"]}
    assert skipped["재화 부족"]["status"] == "empty"
    assert skipped["재화 부족"]["slot"] == "cost"


def test_add_accepts_planned_family(ready, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", _StdIn(_payload()))
    rc = main(["tc", "add", "파티편성", "--family", "정상 경로",
               "--origin", "interview", "--json", "-"])
    assert rc == 0
    with KnowledgeStore(ready / "starrail.db") as s:
        assert [t.title for t in s.testcases("파티편성")] == ["정상 동작"]


def test_add_rejects_unplanned_family(ready, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", _StdIn(_payload()))
    rc = main(["tc", "add", "파티편성", "--family", "재화 부족",
               "--origin", "inferred", "--json", "-"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "재화 부족" in out
    assert "cost" in out
    assert "tc plan" in out


def test_add_rejects_unknown_family(ready, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _StdIn(_payload()))
    rc = main(["tc", "add", "파티편성", "--family", "없는계열",
               "--origin", "interview", "--json", "-"])
    assert rc == 1
    assert "알 수 없는 계열" in capsys.readouterr().out


def test_add_rejects_missing_required_field(ready, monkeypatch, capsys):
    bad = json.dumps({"testcases": [{"title": "제목만 있음"}]}, ensure_ascii=False)
    monkeypatch.setattr("sys.stdin", _StdIn(bad))
    rc = main(["tc", "add", "파티편성", "--family", "정상 경로",
               "--origin", "interview", "--json", "-"])
    assert rc == 1
    assert "steps" in capsys.readouterr().out


def test_add_sets_kind_and_priority_from_family(ready, monkeypatch):
    monkeypatch.setattr("sys.stdin", _StdIn(_payload()))
    main(["tc", "add", "파티편성", "--family", "정상 경로",
          "--origin", "interview", "--json", "-"])
    with KnowledgeStore(ready / "starrail.db") as s:
        tc = s.testcases("파티편성")[0]
    assert tc.kind.value == "정상"
    assert tc.priority.value == "High"
    assert tc.origin.value == "인터뷰"
    assert tc.category_major == "파티편성"
    assert tc.category_minor == "정상 경로"


def test_list_shows_unmet_slots(ready, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _StdIn(_payload()))
    main(["tc", "add", "파티편성", "--family", "정상 경로",
          "--origin", "interview", "--json", "-"])
    assert main(["tc", "list", "파티편성"]) == 0
    out = capsys.readouterr().out
    assert "정상 동작" in out
    assert "재화 부족" in out  # 미충족 리포트


class _StdIn:
    def __init__(self, text: str):
        self._text = text

    def read(self) -> str:
        return self._text
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli_tc.py -v`
Expected: FAIL — `argparse` 가 `tc plan` 을 거부 (기존 `tc` 는 세션 인자를 받는다)

- [ ] **Step 3: 구현**

`qatc/cli_knowledge.py` 의 import 에 추가한다:

```python
import sys

from .knowledge.gate import FAMILY_META, plan_families, validate_family
from .models import Priority, TCOrigin, TestCase
```

명령 함수를 추가한다 (`cmd_slot_add` 아래):

```python
_ORIGIN_BY_FLAG = {
    "interview": TCOrigin.INTERVIEW,
    "inferred": TCOrigin.INFERRED,
    "user": TCOrigin.USER,
}


def cmd_tc_plan(args: argparse.Namespace, cfg: AppConfig) -> int:
    store = resolve_store(cfg, args.game, args.content)
    try:
        if store.get_content(args.content) is None:
            _p(f"컨텐츠 '{args.content}'가 없습니다.")
            return 1
        planned, skipped = plan_families(store.slots(args.content))
    finally:
        store.close()

    if args.json:
        _p(json.dumps({
            "content": args.content,
            "planned": [
                {"family": p.family, "slot": p.slot_key,
                 "kind": p.kind.value, "priority": p.priority.value}
                for p in planned
            ],
            "skipped": [
                {"family": s.family, "slot": s.slot_key,
                 "hint": s.prompt_hint, "status": s.status.value}
                for s in skipped
            ],
        }, ensure_ascii=False, indent=2))
        return 0

    _p(f"[{args.content}] 생성 대상 계열 {len(planned)}개")
    for p in planned:
        _p(f"  {p.family:<16} {p.slot_key:<16} {p.kind.value} / {p.priority.value}")
    if skipped:
        _p("\n제외됨:")
        reason = {"empty": "슬롯 비어 있음", "unknown": "사용자가 모름", "na": "해당 없음"}
        for s in skipped:
            _p(f"  {s.family:<16} {s.slot_key:<16} {reason[s.status.value]}")
    return 0


def _read_json_arg(source: str) -> dict:
    raw = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    return json.loads(raw)


def cmd_tc_add(args: argparse.Namespace, cfg: AppConfig) -> int:
    try:
        payload = _read_json_arg(args.json)
    except (OSError, json.JSONDecodeError) as exc:
        _p(f"오류: JSON을 읽을 수 없습니다 — {exc}")
        return 1

    items = payload.get("testcases")
    if not isinstance(items, list) or not items:
        _p("오류: 최상위에 비어 있지 않은 'testcases' 배열이 필요합니다.")
        return 1

    store = resolve_store(cfg, args.game, args.content)
    try:
        slots = store.slots(args.content)
        if not slots:
            _p(f"컨텐츠 '{args.content}'가 없습니다.")
            return 1
        try:
            plan = validate_family(args.family, slots)
        except ValueError as exc:
            _p(f"오류: {exc}")
            return 1

        cases: list[TestCase] = []
        for i, item in enumerate(items):
            missing = [k for k in ("title", "steps", "expected") if not item.get(k)]
            if missing:
                _p(f"오류: testcases[{i}] 에 필수 필드가 없습니다 — {', '.join(missing)}")
                return 1
            cases.append(TestCase(
                id="",
                category_major=args.content,
                category_minor=args.family,
                title=str(item["title"]),
                precondition=str(item.get("precondition", "")),
                steps=[str(x) for x in item["steps"]],
                expected=[str(x) for x in item["expected"]],
                priority=Priority(item["priority"]) if item.get("priority") else plan.priority,
                kind=plan.kind,
                origin=_ORIGIN_BY_FLAG[args.origin],
                rationale=str(item.get("rationale", "")),
            ))

        added, kept = store.replace_generated(
            args.content, args.family, cases, [plan.slot_key]
        )
    finally:
        store.close()

    _p(f"✓ [{args.family}] TC {added}건 저장" + (f" · 사람 손댄 {kept}건 보존" if kept else ""))
    return 0


def cmd_tc_list(args: argparse.Namespace, cfg: AppConfig) -> int:
    store = resolve_store(cfg, args.game, args.content)
    try:
        if store.get_content(args.content) is None:
            _p(f"컨텐츠 '{args.content}'가 없습니다.")
            return 1
        cases = store.testcases(args.content)
        _, skipped = plan_families(store.slots(args.content))
    finally:
        store.close()

    by_kind: dict[str, int] = {}
    for tc in cases:
        by_kind[tc.kind.value] = by_kind.get(tc.kind.value, 0) + 1
    summary = " · ".join(f"{k} {v}" for k, v in sorted(by_kind.items()))
    _p(f"TC {len(cases)}건" + (f" ({summary})" if summary else ""))

    for tc in cases:
        _p(f"  [{tc.category_minor}] {tc.title}  ({tc.origin.value})")

    if skipped:
        _p("\n⚠ 다음 항목이 미확인이라 해당 TC가 없습니다")
        reason = {"empty": "비어있음", "unknown": "사용자가 모름", "na": "해당 없음"}
        for s in skipped:
            _p(f"   {s.slot_key:<16} ({s.prompt_hint}) → {s.family} TC 없음  "
               f"[{reason[s.status.value]}]")
        _p("\n   이어서 채우려면 Claude Code에서 인터뷰를 재개하세요.")
    return 0
```

`register()` 함수 끝에 `tc` 하위파서를 추가한다:

```python
    tc = sub.add_parser("tc", help="지식에서 테스트케이스 생성·조회")
    tc_sub = tc.add_subparsers(dest="tc_command", required=True)

    pl = tc_sub.add_parser("plan", help="만들 수 있는 계열과 제외된 계열")
    pl.add_argument("content")
    pl.add_argument("--game", "-g")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=cmd_tc_plan)

    ad2 = tc_sub.add_parser("add", help="TC 저장 (계열이 생성 대상인지 검증)")
    ad2.add_argument("content")
    # choices 를 쓰지 않는 이유: argparse 가 먼저 거부하면 "왜 안 되는지"가
    # 사라진다. validate_family 가 "cost 슬롯이 비어 있음 → tc plan 을 보라"까지
    # 알려주는데, argparse 는 유효값 나열만 하고 종료 코드 2로 죽는다.
    ad2.add_argument("--family", required=True,
                     help=f"TC 계열. 대상 여부는 tc plan 이 정한다 "
                          f"(정의된 계열: {', '.join(sorted(FAMILY_META))})")
    ad2.add_argument("--origin", required=True, choices=sorted(_ORIGIN_BY_FLAG))
    ad2.add_argument("--json", required=True, help="JSON 파일 경로 또는 '-' (표준입력)")
    ad2.add_argument("--game", "-g")
    ad2.set_defaults(func=cmd_tc_add)

    ls = tc_sub.add_parser("list", help="TC 목록 + 미충족 슬롯 리포트")
    ls.add_argument("content")
    ls.add_argument("--game", "-g")
    ls.set_defaults(func=cmd_tc_list)
```

> ⚠️ 기존 `cli.py` 에도 `tc` 파서가 있다. `register()` 가 `cli.py` 의 `tc` 파서 **등록 전에** 호출되면 argparse 가 중복 이름으로 죽는다. Task 8에서 기존 `tc`·`export` 를 `--legacy` 뒤로 옮기므로, 그 전까지는 `cli.py` 의 `tc = sub.add_parser("tc", ...)` 줄을 **일시적으로 `legacy-tc` 로 이름만 바꿔** 충돌을 피한다. Task 8에서 정리한다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli_tc.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: 커밋**

```bash
git add qatc/cli_knowledge.py qatc/cli.py tests/test_cli_tc.py
git commit -m "qatc tc plan/add/list 추가 — 계열 게이트를 CLI 경계에 배치

tc add 가 plan 에 없는 계열을 거부한다. 프롬프트가 규칙을 어겨도 저장소가
오염되지 않는다.

tc list 는 TC와 함께 미충족 슬롯을 항상 표시한다. 화면에 안 뜨는 누락은
테스트되지 않은 기능이 된다.

kind 와 기본 priority 는 계열에서 자동으로 온다. 호출자가 매번 지정하면
같은 계열의 TC가 세션마다 다른 종류로 저장된다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: `qatc knowledge` 와 레거시 명령 정리

**Files:**
- Modify: `qatc/cli_knowledge.py` (`knowledge` 명령)
- Modify: `qatc/cli.py` (레거시 명령을 `--legacy` 뒤로, epilog 갱신)
- Test: `tests/test_cli_knowledge_cmd.py`

**Interfaces:**
- Consumes: `KnowledgeStore` (Task 3), `plan_families` (Task 4)
- Produces: `cmd_knowledge(args, cfg) -> int`, `qatc --legacy <명령>` 게이트

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_cli_knowledge_cmd.py`:

```python
import json

import pytest

from qatc.cli import build_parser, main
from qatc.config import AppConfig


@pytest.fixture()
def cfg_env(tmp_path, monkeypatch):
    kroot = tmp_path / "knowledge"
    original = AppConfig.load

    def patched(cls=AppConfig):
        c = original()
        c.knowledge_root = str(kroot)
        return c

    monkeypatch.setattr(AppConfig, "load", staticmethod(patched))
    return kroot


def test_knowledge_lists_contents_with_coverage(cfg_env, capsys):
    main(["slot", "init", "파티편성", "--game", "starrail"])
    main(["slot", "set", "파티편성", "core_action", "--status", "filled", "--value", "v"])
    main(["slot", "init", "워프", "--game", "starrail", "--types", "가챠"])

    assert main(["knowledge", "--game", "starrail", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    by_name = {c["content"]: c for c in data["contents"]}
    assert by_name["파티편성"]["filled"] == 1
    assert by_name["파티편성"]["total"] == 10
    assert by_name["워프"]["total"] == 14


def test_knowledge_on_missing_game_fails(cfg_env, capsys):
    rc = main(["knowledge", "--game", "없는게임"])
    assert rc == 1
    assert "없는게임" in capsys.readouterr().out


def test_legacy_commands_are_hidden_without_flag():
    parser = build_parser()
    # argparse 는 등록된 하위명령만 받는다
    with pytest.raises(SystemExit):
        parser.parse_args(["record", "--profile", "starrail"])


def test_legacy_commands_available_with_flag():
    parser = build_parser(legacy=True)
    args = parser.parse_args(["analyze", "some-session"])
    assert args.command == "analyze"


def test_new_commands_available_without_flag():
    parser = build_parser()
    args = parser.parse_args(["tc", "plan", "파티편성"])
    assert args.tc_command == "plan"
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli_knowledge_cmd.py -v`
Expected: FAIL — `build_parser()` 가 `legacy` 인자를 받지 않음 (`TypeError`)

- [ ] **Step 3: `knowledge` 명령 구현**

`qatc/cli_knowledge.py` 에 추가한다:

```python
def cmd_knowledge(args: argparse.Namespace, cfg: AppConfig) -> int:
    path = cfg.knowledge_path / f"{args.game}.db"
    if not path.exists():
        _p(f"'{args.game}' 지식 DB가 없습니다 ({path}).")
        return 1

    rows = []
    with KnowledgeStore(path) as store:
        for c in store.list_contents():
            slots = store.slots(c.name)
            planned, skipped = plan_families(slots)
            rows.append({
                "content": c.name,
                "types": c.types,
                "total": len(slots),
                "filled": sum(1 for s in slots if s.status is SlotStatus.FILLED),
                "planned_families": len(planned),
                "skipped_families": len(skipped),
                "testcases": len(store.testcases(c.name)),
            })

    if args.json:
        _p(json.dumps({"game": args.game, "contents": rows}, ensure_ascii=False, indent=2))
        return 0

    _p(f"[{args.game}] 컨텐츠 {len(rows)}개\n")
    _p(f"  {'컨텐츠':<14} {'채움':>7}  {'계열':>7}  {'TC':>4}")
    _p(f"  {'-' * 40}")
    for r in rows:
        _p(f"  {r['content']:<14} {r['filled']:>3}/{r['total']:<3}  "
           f"{r['planned_families']:>3}/{r['planned_families'] + r['skipped_families']:<3}  "
           f"{r['testcases']:>4}")
    return 0
```

`register()` 에 추가한다:

```python
    kn = sub.add_parser("knowledge", help="게임별 지식 커버리지")
    kn.add_argument("--game", "-g", required=True)
    kn.add_argument("--json", action="store_true")
    kn.set_defaults(func=cmd_knowledge)
```

- [ ] **Step 4: 레거시 게이트 구현**

`qatc/cli.py` 의 `build_parser` 시그니처를 바꾸고, 녹화 계열 하위파서를 조건부로 만든다.

```python
def build_parser(legacy: bool = False) -> argparse.ArgumentParser:
```

epilog 를 다음으로 교체한다:

```python
        epilog="""\
일반적인 흐름 (Claude Code 세션에서 인터뷰 진행):
  qatc slot init 파티편성 --game starrail --types 편성
  qatc slot status 파티편성 --json     남은 항목 확인
  qatc slot set 파티편성 <키> --status filled --value "..."
  qatc tc plan 파티편성                 만들 수 있는 계열
  qatc tc list 파티편성                 TC + 미충족 항목
  qatc export 파티편성                  xlsx 출력

녹화 기반 명령은 'qatc --legacy <명령>' 으로만 노출됩니다.
""",
```

`sub = parser.add_subparsers(...)` 아래에서 지식 명령을 먼저 등록하고, 녹화 계열은 `if legacy:` 블록으로 감싼다. `record` `analyze` `name` `review` `run` `list` `icons` 와 Task 7에서 `legacy-tc` 로 임시 개명한 파서, 그리고 기존 `export` 파서가 대상이다. `config` 는 양쪽에서 쓰므로 밖에 둔다.

```python
    sub = parser.add_subparsers(dest="command", required=True)

    from .cli_knowledge import register as _register_knowledge
    _register_knowledge(sub)

    if legacy:
        def session_arg(sp: argparse.ArgumentParser) -> None:
            sp.add_argument("session", nargs="?", default="latest",
                            help="세션 ID 또는 경로")
        # ... 기존 record/analyze/name/review/run/list/icons/legacy-tc/export 등록 ...
```

`main()` 을 다음으로 바꾼다:

```python
def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    legacy = "--legacy" in raw
    if legacy:
        raw.remove("--legacy")
    args = build_parser(legacy=legacy).parse_args(raw)
    cfg = AppConfig.load()
    try:
        return args.func(args, cfg)
    except KeyboardInterrupt:
        _p("\n중단되었습니다.")
        return 130
    except SystemExit as exc:
        if isinstance(exc.code, str):
            _p(f"오류: {exc.code}")
            return 1
        raise
    except Exception as exc:
        _p(f"\n오류: {type(exc).__name__}: {exc}")
        if "--debug" in sys.argv:
            raise
        return 1
```

> `SystemExit` 처리를 바꾼 이유: `resolve_store()` 가 안내 문자열과 함께 `SystemExit` 을 던지는데, 그대로 두면 argparse 스타일로 stderr에 나가고 종료 코드가 1이 아니다. 문자열 코드만 잡아 `_p` 로 내보내고 1을 돌려준다.

- [ ] **Step 5: 새 `export` 를 컨텐츠 기반으로 임시 연결**

`register()` 에 컨텐츠 기반 `export` 를 등록한다. 실제 xlsx 생성은 Task 9에서 붙이므로, 지금은 미구현 안내만 낸다.

```python
def cmd_export(args: argparse.Namespace, cfg: AppConfig) -> int:
    _p("xlsx 출력은 아직 연결되지 않았습니다 (구현 계획 Task 9).")
    return 1
```

```python
    ex = sub.add_parser("export", help="xlsx 출력")
    ex.add_argument("content")
    ex.add_argument("--game", "-g")
    ex.add_argument("--out", "-o")
    ex.set_defaults(func=cmd_export)
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli_knowledge_cmd.py -v`
Expected: PASS (5 passed)

- [ ] **Step 7: 전체 회귀 확인**

Run: `.venv\Scripts\python.exe -m pytest`
Expected: 204 passed

기존 테스트가 `qatc analyze` 등을 직접 호출한다면 `--legacy` 를 붙이도록 고친다.

- [ ] **Step 8: 커밋**

```bash
git add qatc/cli_knowledge.py qatc/cli.py tests/test_cli_knowledge_cmd.py
git commit -m "qatc knowledge 추가, 녹화 명령을 --legacy 뒤로 이동

tc 와 export 를 컨텐츠 기반으로 재정의했다. 세션 기반 해석과 공존하면
같은 명령이 상황에 따라 다르게 동작해 사고가 난다.

resolve_store 가 던지는 안내형 SystemExit 을 main 에서 잡아 _p 로 내보낸다.
그대로 두면 한글이 stderr 로 나가 Windows 코드페이지에서 깨진다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: 컨텐츠 기반 xlsx 출력

**Files:**
- Create: `qatc/export/tc_excel.py`
- Modify: `qatc/export/__init__.py`
- Modify: `qatc/cli_knowledge.py` (`cmd_export` 실제 연결)
- Test: `tests/test_tc_excel.py`

기존 `export/excel.py` 는 `FlowGraph` · `SessionStore` 에 묶여 있어 그대로 못 쓴다. 새 모듈을 만들고 **제어문자 sanitize를 처음부터 넣는다** — 기존 export를 통째로 죽였던 버그다.

**Interfaces:**
- Consumes: `TestCase` `TCOrigin` (`qatc.models`), `Slot` (Task 1), `FamilySkip` (Task 4)
- Produces:
  - `clean_cell(value: str) -> str` — openpyxl 금지 제어문자 제거
  - `export_tc_excel(content, testcases, skipped, out_path) -> Path` — 3시트 (테스트케이스 / 미확인 항목 / 요약)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_tc_excel.py`:

```python
import pytest
from openpyxl import load_workbook

from qatc.export.tc_excel import clean_cell, export_tc_excel
from qatc.knowledge.gate import FamilySkip
from qatc.knowledge.models import SlotStatus
from qatc.models import Priority, TCKind, TCOrigin, TestCase


def _tc(title="제목", origin=TCOrigin.INTERVIEW) -> TestCase:
    return TestCase(
        id="tc_1", category_major="파티 편성", category_minor="정상 경로",
        title=title, precondition="파티 편성 화면",
        steps=["파티 적용을 누른다"], expected=["파티가 적용된다"],
        priority=Priority.HIGH, kind=TCKind.HAPPY_PATH, origin=origin,
        rationale="core_action 슬롯에서 도출",
    )


def test_clean_cell_strips_control_characters():
    assert clean_cell("A\x03B") == "AB"
    assert clean_cell("정상\x00문자") == "정상문자"


def test_clean_cell_keeps_newline_and_tab():
    assert clean_cell("가\n나\t다") == "가\n나\t다"


def test_clean_cell_passes_through_normal_text():
    assert clean_cell("파티 적용") == "파티 적용"


def test_export_creates_three_sheets(tmp_path):
    p = export_tc_excel("파티편성", [_tc()], [], tmp_path / "out.xlsx")
    wb = load_workbook(p)
    assert wb.sheetnames == ["테스트케이스", "미확인 항목", "요약"]


def test_export_writes_testcase_row(tmp_path):
    p = export_tc_excel("파티편성", [_tc(title="정상 동작")], [], tmp_path / "out.xlsx")
    ws = load_workbook(p)["테스트케이스"]
    values = [c.value for c in ws[2]]
    assert "정상 동작" in values
    assert "인터뷰" in values


def test_export_survives_control_characters(tmp_path):
    # 이 케이스가 예전 export 를 통째로 죽였다 (IllegalCharacterError)
    bad = _tc(title="제어문자\x03포함")
    p = export_tc_excel("파티편성", [bad], [], tmp_path / "out.xlsx")
    ws = load_workbook(p)["테스트케이스"]
    assert any(c.value == "제어문자포함" for c in ws[2])


def test_export_lists_skipped_families(tmp_path):
    skip = FamilySkip("재화 부족", "cost", "무엇을 소모하는가", SlotStatus.EMPTY)
    p = export_tc_excel("파티편성", [_tc()], [skip], tmp_path / "out.xlsx")
    ws = load_workbook(p)["미확인 항목"]
    row = [c.value for c in ws[2]]
    assert "cost" in row
    assert "재화 부족" in row


def test_export_summary_counts_by_origin(tmp_path):
    cases = [_tc(origin=TCOrigin.INTERVIEW), _tc(origin=TCOrigin.INFERRED)]
    p = export_tc_excel("파티편성", cases, [], tmp_path / "out.xlsx")
    ws = load_workbook(p)["요약"]
    text = "\n".join(str(c.value) for row in ws.iter_rows() for c in row if c.value)
    assert "인터뷰" in text
    assert "추론됨" in text


def test_export_with_no_testcases_still_writes(tmp_path):
    p = export_tc_excel("파티편성", [], [], tmp_path / "out.xlsx")
    assert p.exists()
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_tc_excel.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qatc.export.tc_excel'`

- [ ] **Step 3: 구현**

`qatc/export/tc_excel.py`:

```python
"""컨텐츠 지식에서 만든 TC를 xlsx로 내보낸다.

`export/excel.py` 와 달리 `FlowGraph` 나 `SessionStore` 를 요구하지 않는다.
녹화 세션이 없는 인터뷰 기반 파이프라인의 출력 경로다.

**모든 셀 값은 :func:`clean_cell` 을 통과시킨다.** openpyxl은 제어문자가 든
문자열을 거부하는데, 예전 구현에 이 방어가 없어 OCR·키 이름에 섞여 들어온
`\\x03` 하나가 export 전체를 죽였다.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..knowledge.gate import FamilySkip
from ..models import TCOrigin, TestCase

#: openpyxl이 거부하는 제어문자. 탭·개행·복귀는 남긴다 (셀 안에서 유효하다).
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

_HEADER_FILL = PatternFill("solid", fgColor="1F3864")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_WRAP = Alignment(vertical="top", wrap_text=True)

_ORIGIN_FILL = {
    TCOrigin.INTERVIEW: PatternFill("solid", fgColor="E2EFDA"),
    TCOrigin.INFERRED: PatternFill("solid", fgColor="FCE4D6"),
    TCOrigin.USER: PatternFill("solid", fgColor="DDEBF7"),
    TCOrigin.RECORDED: PatternFill("solid", fgColor="E2EFDA"),
}

_STATUS_LABEL = {"empty": "비어있음", "unknown": "사용자가 모름", "na": "해당 없음"}


def clean_cell(value: str) -> str:
    """셀에 넣어도 안전한 문자열로 만든다."""
    return _ILLEGAL.sub("", value)


def _header(ws, titles: Sequence[str], widths: Sequence[int]) -> None:
    for i, (t, w) in enumerate(zip(titles, widths), start=1):
        c = ws.cell(row=1, column=i, value=t)
        c.fill = _HEADER_FILL
        c.font = _HEADER_FONT
        c.alignment = _WRAP
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"


def _sheet_testcases(wb: Workbook, cases: Sequence[TestCase]) -> None:
    ws = wb.active
    ws.title = "테스트케이스"
    _header(
        ws,
        ["TC ID", "대분류", "중분류", "제목", "사전조건", "절차", "기대결과",
         "우선순위", "유형", "출처", "근거"],
        [14, 14, 14, 40, 28, 40, 40, 10, 10, 10, 36],
    )
    for r, tc in enumerate(cases, start=2):
        values = [
            tc.id, tc.category_major, tc.category_minor, tc.title, tc.precondition,
            "\n".join(f"{i}. {s}" for i, s in enumerate(tc.steps, 1)),
            "\n".join(f"- {s}" for s in tc.expected),
            tc.priority.value, tc.kind.value, tc.origin.value, tc.rationale,
        ]
        for col, v in enumerate(values, start=1):
            cell = ws.cell(row=r, column=col, value=clean_cell(str(v)))
            cell.alignment = _WRAP
        ws.cell(row=r, column=10).fill = _ORIGIN_FILL.get(tc.origin, PatternFill())


def _sheet_skipped(wb: Workbook, skipped: Sequence[FamilySkip]) -> None:
    ws = wb.create_sheet("미확인 항목")
    _header(ws, ["슬롯", "묻는 것", "만들지 못한 계열", "상태"], [18, 40, 18, 14])
    for r, s in enumerate(skipped, start=2):
        for col, v in enumerate(
            [s.slot_key, s.prompt_hint, s.family, _STATUS_LABEL[s.status.value]], start=1
        ):
            ws.cell(row=r, column=col, value=clean_cell(str(v))).alignment = _WRAP


def _sheet_summary(
    wb: Workbook, content: str, cases: Sequence[TestCase], skipped: Sequence[FamilySkip]
) -> None:
    ws = wb.create_sheet("요약")
    _header(ws, ["항목", "값"], [28, 60])

    by_origin = Counter(tc.origin.value for tc in cases)
    by_kind = Counter(tc.kind.value for tc in cases)
    rows = [
        ("컨텐츠", content),
        ("TC 총계", str(len(cases))),
        ("유형별", ", ".join(f"{k} {v}" for k, v in sorted(by_kind.items())) or "-"),
        ("출처별", ", ".join(f"{k} {v}" for k, v in sorted(by_origin.items())) or "-"),
        ("미확인 항목", str(len(skipped))),
        ("읽는 방법",
         "출처 '인터뷰'는 담당자가 진술한 내용, '추론됨'은 진술에서 도출한 것입니다. "
         "'추론됨'은 실제로 그렇게 동작하는지 검증되지 않았습니다. "
         "'미확인 항목' 시트는 아직 설명되지 않아 TC를 만들지 못한 부분입니다."),
    ]
    for r, (k, v) in enumerate(rows, start=2):
        ws.cell(row=r, column=1, value=clean_cell(k)).alignment = _WRAP
        ws.cell(row=r, column=2, value=clean_cell(v)).alignment = _WRAP


def export_tc_excel(
    content: str,
    testcases: Sequence[TestCase],
    skipped: Sequence[FamilySkip],
    out_path: Path | str,
) -> Path:
    """TC를 xlsx로 내보낸다. 반환값은 생성된 파일 경로."""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    _sheet_testcases(wb, testcases)
    _sheet_skipped(wb, skipped)
    _sheet_summary(wb, content, testcases, skipped)
    wb.save(path)
    return path
```

`qatc/export/__init__.py` 에 재수출을 추가한다:

```python
from .tc_excel import clean_cell, export_tc_excel
```

`__all__` 에도 두 이름을 추가한다.

- [ ] **Step 4: `cmd_export` 를 실제 출력에 연결**

`qatc/cli_knowledge.py` 의 `cmd_export` 를 교체한다:

```python
def cmd_export(args: argparse.Namespace, cfg: AppConfig) -> int:
    from .export.tc_excel import export_tc_excel

    store = resolve_store(cfg, args.game, args.content)
    try:
        if store.get_content(args.content) is None:
            _p(f"컨텐츠 '{args.content}'가 없습니다.")
            return 1
        cases = store.testcases(args.content)
        _, skipped = plan_families(store.slots(args.content))
        game = store.game
    finally:
        store.close()

    out = Path(args.out) if args.out else cfg.knowledge_path / f"{game}_{args.content}_TC.xlsx"
    path = export_tc_excel(args.content, cases, skipped, out)
    _p(f"✓ {path}  (TC {len(cases)}건 · 미확인 {len(skipped)}건)")
    return 0
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_tc_excel.py -v`
Expected: PASS (9 passed)

- [ ] **Step 6: 전체 회귀 확인**

Run: `.venv\Scripts\python.exe -m pytest`
Expected: 213 passed

- [ ] **Step 7: 커밋**

```bash
git add qatc/export/ qatc/cli_knowledge.py tests/test_tc_excel.py
git commit -m "컨텐츠 기반 xlsx 출력 추가

FlowGraph·SessionStore 를 요구하지 않는 새 출력 경로. 3시트 —
테스트케이스 / 미확인 항목 / 요약.

모든 셀 값에 제어문자 sanitize 를 건다. 예전 구현에 이 방어가 없어
OCR·키 이름에 섞인 \\x03 하나가 export 전체를 IllegalCharacterError 로
죽였다. 회귀 테스트를 함께 넣었다.

'미확인 항목' 시트가 예전 '커버리지' 시트 자리를 대신한다 — 무엇이 아직
테스트되지 않는지가 가장 실무적인 정보라는 점은 같다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: 인터뷰 스킬

**Files:**
- Create: `.claude/skills/interview/SKILL.md`
- Test: `tests/test_interview_skill.py`

스킬은 코드가 아니라 지시문이라 동작을 테스트할 수 없다. 대신 **스킬이 참조하는 명령이 실제로 존재하는지**를 검증한다 — 스킬이 없는 명령을 부르면 인터뷰가 첫 턴에 죽는다.

**Interfaces:**
- Consumes: Task 6·7의 CLI 명령
- Produces: `.claude/skills/interview/SKILL.md` (frontmatter `name: interview`)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_interview_skill.py`:

```python
import argparse
import json
import re
from pathlib import Path

import pytest

from qatc.cli import build_parser

SKILL = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "interview" / "SKILL.md"


def test_skill_file_exists():
    assert SKILL.exists(), f"{SKILL} 가 없습니다"


def test_skill_has_frontmatter_name_and_description():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    head = text.split("---", 2)[1]
    assert re.search(r"^name:\s*interview\s*$", head, re.M)
    assert re.search(r"^description:\s*\S", head, re.M)


def test_skill_mandates_slot_status_before_asking():
    text = SKILL.read_text(encoding="utf-8")
    assert "qatc slot status" in text
    assert "질문" in text


def _subparser_choices(parser) -> dict:
    """argparse 파서에서 하위명령 이름 → 하위파서 매핑을 꺼낸다."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def test_every_qatc_command_in_skill_is_registered():
    """스킬이 없는 명령을 부르면 인터뷰가 첫 턴에 죽는다.

    스킬은 지시문이라 타입 검사도 import 오류도 이걸 잡아주지 않는다.
    """
    text = SKILL.read_text(encoding="utf-8")
    top = _subparser_choices(build_parser())
    assert top, "하위명령이 하나도 등록되지 않았습니다"

    # 스킬은 `.venv/Scripts/qatc.exe slot status` 와 `qatc slot status` 두 형태를
    # 모두 쓴다. `.exe` 를 선택적으로 허용해야 실제 호출을 검사할 수 있다.
    pattern = re.compile(r"qatc(?:\.exe)?\s+([a-z]+)(?:\s+([a-z]+))?")
    found = list(pattern.finditer(text))
    assert found, "스킬에 qatc 명령 호출이 하나도 없습니다"

    for m in found:
        cmd, sub = m.group(1), m.group(2)
        assert cmd in top, f"등록되지 않은 명령: qatc {cmd}"
        nested = _subparser_choices(top[cmd])
        if nested and sub:
            assert sub in nested, f"등록되지 않은 하위명령: qatc {cmd} {sub}"


def test_skill_uses_allowlisted_executable_form():
    """`.claude/settings.json` 의 권한 규칙은 명령 접두사로 매칭된다.

    스킬이 쓰는 호출 형태와 allowlist 접두사가 어긋나면 매 슬롯 기록마다
    승인 창이 떠서 인터뷰가 성립하지 않는다.
    """
    settings = Path(__file__).resolve().parents[1] / ".claude" / "settings.json"
    allow = json.loads(settings.read_text(encoding="utf-8"))["permissions"]["allow"]
    prefixes = [a[len("Bash("):-len(" *)")] for a in allow if a.startswith("Bash(")]
    assert any("qatc.exe slot" in p for p in prefixes)
    assert any("qatc.exe tc" in p for p in prefixes)
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_interview_skill.py -v`
Expected: FAIL — `AssertionError: ... SKILL.md 가 없습니다`

- [ ] **Step 3: 스킬 작성**

`.claude/skills/interview/SKILL.md`:

```markdown
---
name: interview
description: 게임 컨텐츠 QA 인터뷰를 진행한다. 사용자가 컨텐츠 기능을 설명하면 지식 슬롯을 채우고, 채워진 슬롯에서 테스트케이스를 생성한다. "인터뷰 시작", "<컨텐츠> TC 만들어줘", "파티편성 설명할게" 같은 요청에 쓴다.
---

# 게임 컨텐츠 QA 인터뷰

사용자에게 게임 컨텐츠를 물어 지식 슬롯을 채우고, 채워진 슬롯에서만 테스트케이스를
만든다. **어떤 TC를 만들 수 있는지는 당신이 아니라 `qatc tc plan` 이 정한다.**

## 절대 규칙

1. **질문하기 전에 항상 `qatc slot status <컨텐츠> --json` 을 실행한다.**
   기억에 의존하지 않는다. 컨텍스트가 압축돼도 이 출력이 진실이다.
2. `open` 배열에 없는 항목은 **절대 다시 묻지 않는다.**
3. 한 번에 **1~2개만** 묻는다. 여러 개를 나열하면 인터뷰가 아니라 설문지가 된다.
4. 사용자가 "모른다"고 하면 `--status unknown`, "해당 없음"이면 `--status na` 로
   닫는다. 캐묻지 않는다.
5. 답변이 모호하면 다음 항목으로 넘어가지 말고 그 자리에서 되묻는다.
6. 한 답변에 여러 항목의 정보가 있으면 `slot set` 을 **여러 번** 실행한다.

## 1단계 — 개괄 (항상 여기서 시작)

컨텐츠 이름을 받으면 이 문구로 시작한다:

```
[<컨텐츠>] 인터뷰를 시작합니다.

먼저 이 컨텐츠가 어떤 것인지 설명해주세요.
플레이어가 무엇을 하는 곳이고, 왜 쓰는지 편하게 적으시면 됩니다.
```

답변에서 유형을 판정한다. 사용 가능한 유형: `가챠` `편성` `성장` `던전` `상점` `임무`.
복수 적용할 수 있다 (워프는 `가챠,상점`). 해당 없으면 비워 둔다.

```bash
.venv/Scripts/qatc.exe slot init <컨텐츠> --game <게임> --types 편성
.venv/Scripts/qatc.exe slot set <컨텐츠> overview --status filled --value "<개괄 요약>"
```

개괄 답변에 진입 경로·정원·재화가 이미 들어 있으면 **그 자리에서 전부 기록한다.**
컨텍스트 우선으로 시작하는 이득이 여기서 나온다.

## 2단계 — 인터뷰 루프

```bash
.venv/Scripts/qatc.exe slot status <컨텐츠> --json
```

`open` 배열에서 하나를 골라 `hint` 를 참고해 자연스럽게 묻는다. 힌트를 그대로
읽지 말고 이 게임·컨텐츠 맥락에 맞게 바꿔 묻는다.

답변을 받으면:

```bash
.venv/Scripts/qatc.exe slot set <컨텐츠> <키> --status filled --value "<사용자가 말한 내용>"
```

기록할 때마다 사용자에게 무엇이 기록됐고 몇 개가 남았는지 보여준다:

```
  ✓ constraints 기록됨
  [7/12 채움 · 남은 것: 재화, 실패 조건, 저장 시점]
```

## 3단계 — TC 생성

`open` 이 비면 (또는 사용자가 끝내자고 하면) 생성 대상을 확인한다:

```bash
.venv/Scripts/qatc.exe tc plan <컨텐츠> --json
```

`planned` 에 있는 계열만 작성한다. **`skipped` 에 있는 계열은 절대 만들지 않는다** —
만들어도 `tc add` 가 거부한다.

계열마다 한 번씩 실행한다:

```bash
.venv/Scripts/qatc.exe tc add <컨텐츠> --family "정상 경로" --origin interview --json - <<'JSON'
{"testcases": [
  {"title": "...", "precondition": "...",
   "steps": ["..."], "expected": ["..."],
   "rationale": "core_action 슬롯: '<사용자 진술 인용>'에서 도출"}
]}
JSON
```

`--origin` 을 정확히 고른다:

- `interview` — 사용자가 **말한 것**을 그대로 확인하는 TC
- `inferred` — 진술에서 **도출한 것** (예: "최대 4명"에서 "5명 넣으면 거부")

`rationale` 에는 **어느 슬롯의 어떤 문장에서 나왔는지** 적는다. TC를 의심할 때
근거를 되짚을 수 있어야 한다.

## 4단계 — 마무리

```bash
.venv/Scripts/qatc.exe tc list <컨텐츠>
.venv/Scripts/qatc.exe export <컨텐츠>
```

미확인 항목이 남아 있으면 사용자에게 알리고, 채우면 어떤 TC가 추가되는지 말해준다.

## 재개

세션이 끊겼거나 컨텍스트가 압축됐으면 `slot status` 한 번이면 복구된다.
채워진 항목을 건너뛰고 빈 항목부터 이어간다. 사용자에게 다시 물어보지 않는다.
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_interview_skill.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 실제 인터뷰 한 번 돌려 확인**

Claude Code 세션에서 `/interview` 를 호출해 짧은 컨텐츠 하나(예: 우편함)를 인터뷰한다.
확인할 것:

- 권한 승인 창이 뜨지 않는가 (`.claude/settings.json` allowlist 동작)
- 이미 채운 항목을 다시 묻지 않는가
- `tc add` 가 `skipped` 계열을 거부하는가 (일부러 시켜 본다)

승인 창이 뜨면 `.claude/settings.json` 의 접두사와 스킬이 쓰는 명령 형태가 다른 것이다.
스킬 쪽을 allowlist 에 맞춘다.

- [ ] **Step 6: 커밋**

```bash
git add .claude/skills/interview/SKILL.md tests/test_interview_skill.py
git commit -m "인터뷰 스킬 추가

Claude Code 세션이 인터뷰어 역할을 하도록 규칙을 정의한다. 핵심은 '질문 전
항상 slot status 를 실행한다' — 기억에 의존하면 컨텍스트 압축 직후 이미
물어본 것을 다시 묻는다.

스킬이 부르는 qatc 명령이 실제로 등록돼 있는지 테스트로 검증한다. 없는 명령을
부르면 인터뷰가 첫 턴에 죽는데, 스킬은 지시문이라 그걸 잡아줄 다른 장치가 없다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: 녹화 파이프라인 제거

**Files:**
- Delete: `qatc/capture/` `qatc/record/` `qatc/analyze/` `qatc/icons/` `qatc/review/` `qatc/llm/`
- Delete: `qatc/export/excel.py` `qatc/export/mermaid.py`
- Delete: `scripts/diag_input.py` `scripts/spike.py` `scripts/make_fixture.py`
- Delete: `tests/test_capture.py` `tests/test_analyze.py` `tests/test_icons.py` `tests/test_pipeline.py`
- Modify: `qatc/models.py` (녹화 전용 타입 제거)
- Modify: `qatc/storage.py` → 삭제
- Modify: `qatc/cli.py` (레거시 블록 제거)
- Modify: `qatc/config.py` (`CaptureConfig` `AnalyzeConfig` `LlmConfig` 제거)
- Modify: `qatc/export/__init__.py`
- Modify: `pyproject.toml` (선택 의존성 정리)
- Modify: `README.md`
- Test: `tests/test_models.py` (녹화 타입 테스트 제거)

새 파이프라인이 전부 동작하는 것을 확인한 뒤에 지운다. 되돌리려면 `git revert` 하면 된다.

- [ ] **Step 1: 삭제 전 기준선 확인**

Run: `.venv\Scripts\python.exe -m pytest`
Expected: 218 passed

새 경로가 끝까지 동작하는지 손으로 확인한다:

```bash
.venv/Scripts/qatc.exe slot init 우편함 --game starrail
.venv/Scripts/qatc.exe slot set 우편함 core_action --status filled --value "보상을 일괄 수령한다"
.venv/Scripts/qatc.exe tc plan 우편함
.venv/Scripts/qatc.exe export 우편함
```

Expected: xlsx 파일이 생성되고 "미확인 항목" 시트에 채우지 않은 슬롯이 나온다.

- [ ] **Step 2: 녹화 전용 코드 삭제**

```bash
git rm -r -q qatc/capture qatc/record qatc/analyze qatc/icons qatc/review qatc/llm
git rm -q qatc/export/excel.py qatc/export/mermaid.py qatc/storage.py
git rm -q scripts/diag_input.py scripts/spike.py scripts/make_fixture.py
git rm -q tests/test_capture.py tests/test_analyze.py tests/test_icons.py tests/test_pipeline.py
```

- [ ] **Step 3: `models.py` 에서 녹화 전용 타입 제거**

다음 타입을 지운다: `NormRect` `InputKind` `CaptureReason` `Frame` `InputEvent`
`ElementKind` `UIElement` `AutoFeatures` `LlmGuess` `UserConfirm` `ScreenState`
`Transition` `SessionMeta` `FlowGraph`, 그리고 `coverage()` 함수.

**남기는 것**: `TCOrigin` `TCKind` `Priority` `TestCase` `new_id`.
`SETTLED_REASONS` 같은 녹화 전용 상수도 함께 지운다.

`tests/test_models.py` 에서 지운 타입을 쓰는 테스트를 제거하고, `TestCase` 직렬화
테스트만 남긴다.

- [ ] **Step 4: `config.py` 정리**

`CaptureConfig` `AnalyzeConfig` `LlmConfig` 데이터클래스와 `AppConfig` 의
`sessions_root` `capture` `analyze` `llm` 필드, `sessions_path` 프로퍼티,
`MODEL_BULK` `MODEL_DEEP` `MAX_IMAGE_EDGE` 상수, `get_api_key` / `set_api_key` 를
제거한다. `knowledge_root` `profiles_dir` 만 남긴다.

`cli.py` 의 `from .config import AppConfig, get_api_key, set_api_key` 를
`from .config import AppConfig` 로 바꾸고, `cmd_config` 에서 API 키 관련 분기를 지운다.

- [ ] **Step 5: `cli.py` 에서 레거시 블록 제거**

`build_parser(legacy: bool = False)` 를 `build_parser()` 로 되돌리고 `if legacy:`
블록 전체를 지운다. `main()` 의 `--legacy` 처리도 지운다.
`SystemExit` 문자열 처리는 **남긴다** (`resolve_store` 가 쓴다).

`export/__init__.py` 에서 `export_excel` `export_mermaid` 재수출을 지운다.

- [ ] **Step 6: `pyproject.toml` 정리**

`[project.optional-dependencies]` 에서 캡처·GUI·OCR 관련 항목을 지운다. 남는 런타임
의존성은 `openpyxl` 뿐이다. `pytest` 는 dev 로 남긴다.

- [ ] **Step 7: 테스트 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest`
Expected: 62 passed (지식·CLI·xlsx·스킬 테스트만 남는다)

`ImportError` 가 나면 지운 모듈을 아직 참조하는 곳이 있다는 뜻이다. 다음으로 찾는다:

```bash
grep -rn "from .capture\|from .record\|from .analyze\|from .icons\|from .review\|from .llm\|from .storage\|FlowGraph\|SessionStore" qatc/ tests/
```

- [ ] **Step 8: README 갱신**

"현재 상태 — 방향 전환 중" 표를 지우고 인터뷰 파이프라인만 설명한다.
"지금 동작하는 명령 (녹화 방향)" 절을 새 명령으로 교체한다.
"실측으로 확인된 것" 과 "미해결 버그" 절은 **지운다** — 지운 코드의 버그 목록은
git 이력에만 있으면 된다. "안전 원칙" 절도 캡처를 안 하므로 지운다.

- [ ] **Step 9: 커밋**

```bash
git add -A
git commit -m "녹화 파이프라인 제거

capture·record·analyze·icons·review·llm 과 storage·export/excel·export/mermaid,
관련 테스트·스크립트를 지운다. 인터뷰 기반 경로가 끝까지 동작하는 것을 확인한
뒤 삭제했다.

models.py 에서 녹화 전용 타입 14개를 지우고 TestCase 계열 4개만 남겼다.
config.py 에서 캡처·분석·LLM 설정과 API 키 처리를 제거했다.

되돌리려면 이 커밋을 revert 하면 된다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## 진행 순서 요약

| # | 태스크 | 산출물 | 누적 테스트 |
|---|---|---|---|
| 1 | 지식 도메인 타입 | `Slot` `SlotStatus` `Content`, `TCOrigin.INTERVIEW` | 137 |
| 2 | 슬롯 세트 조립 | `BASE_SLOTS` `TYPE_SLOTS` `build_slot_set` | 148 |
| 3 | 지식 저장소 | `KnowledgeStore` | 160 |
| 4 | **계열 게이트** | `plan_families` `validate_family` | 171 |
| 5 | TC 저장·병합 | `add_testcase` `replace_generated` | 181 |
| 6 | `qatc slot` | status/init/set/add | 191 |
| 7 | `qatc tc` | plan/add/list | 199 |
| 8 | `qatc knowledge` + 레거시 게이트 | `--legacy` | 204 |
| 9 | xlsx 출력 | `export_tc_excel` `clean_cell` | 213 |
| 10 | 인터뷰 스킬 | `.claude/skills/interview/SKILL.md` | 218 |
| 11 | 녹화 파이프라인 제거 | — | 62 |

Task 4와 7이 이 설계의 불변식을 담는다. 나머지가 흔들려도 이 둘이 지켜지면
근거 없는 TC는 만들어지지 않는다.

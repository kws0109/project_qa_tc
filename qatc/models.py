"""QATC 핵심 데이터 모델.

인터뷰 기반 파이프라인이 쓰는 것은 테스트케이스 표현뿐이다. 화면·전이·프레임
계층(3층 분리: auto/llm/user)은 녹화 파이프라인 전용이었고, 그 파이프라인과
함께 지워졌다 — 이력은 git log에 남아 있다.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------- TC 계층


class TCOrigin(str, Enum):
    """TC의 근거 출처. **INFERRED는 검증되지 않은 가설이다.**

    이 구분이 없으면 추측이 정식 TC로 둔갑한다. 엑셀에서 색으로 구분된다.
    """

    RECORDED = "기록됨"      # 녹화에서 관측됨 (녹화 파이프라인은 삭제됨 — 과거 데이터·엑셀 호환용으로 남긴다)
    INTERVIEW = "인터뷰"     # 사용자가 진술한 내용에 직접 근거
    INFERRED = "추론됨"      # 진술에서 도출
    USER = "사용자추가"


class TCKind(str, Enum):
    HAPPY_PATH = "정상"
    BOUNDARY = "경계값"
    EXCEPTION = "예외"
    REVERSE = "역방향"
    INTERRUPT = "중단"
    UIUX = "UI/UX"


class Priority(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


@dataclass
class TestCase:
    #: pytest가 이 클래스를 테스트 클래스로 수집하지 않게 막는다
    #: (이름이 Test로 시작하는 탓에 생기는 오탐).
    __test__ = False

    id: str
    category_major: str = ""
    category_minor: str = ""
    #: 소분류 — 그 화면·기능 안에서 확인하는 **케이스 이름**. 결과는 적지
    #: 않는다(`expected` 가 그 자리다). 대+중+소를 읽으면 어떤 테스트인지
    #: 알 수 있어야 하고, 그래서 `title` 은 더 이상 쓰지 않는다.
    category_sub: str = ""
    #: 이 TC 가 속한 계열. 게이트(`plan_families`)와 근거 철회 판정이 쓰는 단위다.
    #: `category_minor` 와 값이 같던 시절이 있었지만 그건 우연이었다 — 그쪽은
    #: 이제 화면·메뉴 계층을 담는다. 진실은 `testcases.family` 컬럼이고,
    #: `KnowledgeStore.testcases()` 가 그 값으로 이 필드를 채운다.
    family: str = ""
    title: str = ""
    precondition: str = ""
    steps: list[str] = field(default_factory=list)
    expected: list[str] = field(default_factory=list)
    priority: Priority = Priority.MEDIUM
    kind: TCKind = TCKind.HAPPY_PATH
    origin: TCOrigin = TCOrigin.RECORDED
    evidence_frames: list[str] = field(default_factory=list)
    state_path: list[str] = field(default_factory=list)
    edge_ids: list[str] = field(default_factory=list)
    rationale: str = ""

    _LIST_FIELDS = ("steps", "expected", "evidence_frames", "state_path", "edge_ids")

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["priority"] = self.priority.value
        d["kind"] = self.kind.value
        d["origin"] = self.origin.value
        for k in self._LIST_FIELDS:
            d[k] = json.dumps(d[k], ensure_ascii=False)
        return d

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> TestCase:
        row = dict(row)
        row["priority"] = Priority(row["priority"])
        row["kind"] = TCKind(row["kind"])
        row["origin"] = TCOrigin(row["origin"])
        for k in cls._LIST_FIELDS:
            v = row.get(k)
            row[k] = json.loads(v) if isinstance(v, str) else list(v or [])
        return cls(**row)

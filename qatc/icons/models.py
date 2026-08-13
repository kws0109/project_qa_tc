"""아이콘 사전 데이터 모델.

**해결하려는 문제**: 텍스트가 있는 UI는 OCR과 LLM이 알아보지만, 아이콘만 있는
버튼(기원, 가방, 우편, 임무…)은 무엇인지도 무엇을 하는지도 알 수 없습니다.
결과적으로 TC 절차가 ``화면 (0.42, 0.31) 위치 클릭``으로 남습니다.

**해결 방식**: 담당자가 아이콘의 **이름**과 **누르면 일어나는 일**을 한 번 지정하면,
그 지식이 게임 단위로 영구 저장되어 이후 모든 세션에서 자동으로 적용됩니다.

세션 간 식별자 함정
-------------------
화면 ID(``st_004``)는 **세션마다 새로 부여**됩니다. 사전은 세션을 넘어 살아남아야
하므로 ID로 참조하면 다음 세션에서 전혀 다른 화면을 가리킵니다. 그래서 이 모듈은
**화면 이름**(담당자가 확정한 "기원 배너")으로만 참조하고, 사용 시점에 현재
그래프에서 이름으로 되찾습니다.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from ..models import NormRect


class ActionKind(str, Enum):
    """아이콘을 눌렀을 때 일어나는 일의 종류.

    TC 생성에서 각각 다르게 쓰인다 — 자세한 것은 :meth:`ActionKind.tc_hint` 참고.
    """

    NAVIGATE = "이동"      # 다른 화면으로 진입
    BACK = "뒤로"          # 이전 화면으로 복귀
    TAB = "탭전환"         # 같은 화면 안에서 탭 전환
    OPEN = "열기"          # 팝업·패널 열기
    CLOSE = "닫기"         # 팝업·패널 닫기
    CONFIRM = "확인"       # 실행·적용 (되돌리기 어려운 경우가 많음)
    CANCEL = "취소"        # 실행 취소
    PURCHASE = "구매"      # 재화 소모 — 경계값 TC의 핵심 근거
    TOGGLE = "토글"        # 켜기/끄기 전환
    SELECT = "선택"        # 목록에서 항목 선택
    UNKNOWN = "미상"

    @property
    def default_reversible(self) -> bool:
        """이 유형이 보통 되돌릴 수 있는가. 등록 폼의 기본값으로 쓴다."""
        return self not in (ActionKind.CONFIRM, ActionKind.PURCHASE)

    @property
    def default_priority(self) -> str:
        """이 유형의 기본 TC 우선순위."""
        if self in (ActionKind.PURCHASE, ActionKind.CONFIRM):
            return "High"
        if self in (ActionKind.NAVIGATE, ActionKind.OPEN, ActionKind.TAB):
            return "Medium"
        return "Low"

    def tc_hint(self) -> str:
        """LLM에게 이 유형이 어떤 테스트를 필요로 하는지 알려주는 문구."""
        return {
            ActionKind.NAVIGATE: "진입 후 화면이 올바로 표시되는지, 뒤로가기로 복귀되는지",
            ActionKind.BACK: "이전 화면으로 정확히 복귀하고 작업 중이던 상태가 어떻게 되는지",
            ActionKind.TAB: "탭 전환 시 내용이 바뀌고 선택 표시가 이동하는지",
            ActionKind.OPEN: "팝업이 뜨고 배경 조작이 차단되는지",
            ActionKind.CLOSE: "팝업이 닫히고 이전 상태가 유지되는지",
            ActionKind.CONFIRM: "실행 결과가 반영되는지, 실행 조건 미충족 시 막히는지",
            ActionKind.CANCEL: "변경사항이 반영되지 않고 원래 상태로 돌아가는지",
            ActionKind.PURCHASE: "재화가 정확히 차감되는지, 재화 부족 시 차단되는지, 수량 경계값",
            ActionKind.TOGGLE: "상태가 전환되고 유지되는지, 화면 재진입 후에도 유지되는지",
            ActionKind.SELECT: "선택 표시가 이동하고 상세 정보가 갱신되는지",
            ActionKind.UNKNOWN: "",
        }[self]


@dataclass
class IconAction:
    """아이콘을 눌렀을 때 일어나는 일. **구조화되어 있어 TC에 직접 꽂힌다.**"""

    kind: ActionKind = ActionKind.UNKNOWN
    #: 이동 대상 **화면 이름**. ID가 아니다 — 모듈 docstring의 식별자 함정 참고.
    target_screen_name: str = ""
    #: 기대 결과 문장. TC의 '기대 결과' 칸에 그대로 들어간다.
    expected: str = ""
    #: 소모하는 재화 이름. 있으면 '재화 부족' 경계값 TC를 자동 제안한다.
    consumes: str = ""
    #: 되돌릴 수 있는가. False면 TC 우선순위를 High로 올린다.
    reversible: bool = True

    @property
    def is_defined(self) -> bool:
        return self.kind is not ActionKind.UNKNOWN or bool(self.expected)

    def describe(self) -> str:
        """사람이 읽는 한 줄 요약. GUI 목록과 LLM 프롬프트에 쓴다."""
        parts = [self.kind.value]
        if self.target_screen_name:
            parts.append(f"→ {self.target_screen_name}")
        if self.consumes:
            parts.append(f"({self.consumes} 소모)")
        if not self.reversible:
            parts.append("[되돌리기 불가]")
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IconAction:
        d = dict(d)
        try:
            d["kind"] = ActionKind(d.get("kind", "미상"))
        except ValueError:
            d["kind"] = ActionKind.UNKNOWN
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class IconSample:
    """확정된 관측 하나. **이게 쌓여서 학습 데이터가 된다.**

    이미지는 대표 샘플 하나만 파일로 남기고 나머지는 벡터만 보관한다 —
    수백 개의 64x64 PNG를 쌓아둘 이유가 없다.
    """

    descriptor: list[float]
    dhash: str
    #: 관측된 화면 **이름**. 같은 모양 아이콘이 화면마다 다른 일을 할 때 구분용.
    screen_name: str = ""
    #: 관측된 위치(정규화). 동점 처리에 쓴다.
    rect: list[float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IconSample:
        return cls(
            descriptor=[float(x) for x in d.get("descriptor", [])],
            dhash=d.get("dhash", ""),
            screen_name=d.get("screen_name", ""),
            rect=list(d["rect"]) if d.get("rect") else None,
        )


def new_icon_id() -> str:
    return f"ic_{uuid.uuid4().hex[:10]}"


@dataclass
class IconEntry:
    """사전에 등록된 아이콘 하나."""

    id: str
    name: str
    action: IconAction = field(default_factory=IconAction)
    #: 대표 이미지 파일명 (templates/ 하위 상대 경로)
    template_rel: str = ""
    #: 확정된 관측들. 늘어날수록 매칭이 정확해진다.
    samples: list[IconSample] = field(default_factory=list)
    #: 담당자 메모 — TC 생성 시 LLM에 함께 전달된다.
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""

    # -- 파생 속성 ---------------------------------------------------

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    @property
    def seen_screens(self) -> list[str]:
        """이 아이콘이 관측된 화면 이름들 (중복 제거)."""
        seen: list[str] = []
        for s in self.samples:
            if s.screen_name and s.screen_name not in seen:
                seen.append(s.screen_name)
        return seen

    @property
    def typical_rect(self) -> NormRect | None:
        """관측된 위치의 평균. 동점 처리와 GUI 표시에 쓴다."""
        rects = [s.rect for s in self.samples if s.rect]
        if not rects:
            return None
        n = len(rects)
        return NormRect(*(sum(r[i] for r in rects) / n for i in range(4)))

    @property
    def is_complete(self) -> bool:
        """TC 생성에 쓸 만큼 정보가 채워졌는가."""
        return bool(self.name) and self.action.is_defined

    def display_label(self) -> str:
        """캔버스 오버레이와 TC 절차 문구에 쓰는 이름."""
        return self.name or "(이름 없음)"

    def tc_context(self) -> str:
        """LLM에게 전달할 이 아이콘의 지식. **담당자가 확정한 사실이다.**"""
        lines = [f"- [{self.name}] {self.action.describe()}"]
        if self.action.expected:
            lines.append(f"    누르면: {self.action.expected}")
        if self.notes:
            lines.append(f"    담당자 메모: {self.notes}")
        hint = self.action.kind.tc_hint()
        if hint:
            lines.append(f"    검증 포인트: {hint}")
        return "\n".join(lines)

    # -- 직렬화 ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "action": self.action.to_dict(),
            "template_rel": self.template_rel,
            "samples": [s.to_dict() for s in self.samples],
            "notes": self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IconEntry:
        return cls(
            id=d["id"],
            name=d.get("name", ""),
            action=IconAction.from_dict(d.get("action") or {}),
            template_rel=d.get("template_rel", ""),
            samples=[IconSample.from_dict(s) for s in d.get("samples", [])],
            notes=d.get("notes", ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )


@dataclass
class IconMatch:
    """매칭 결과 하나."""

    entry: IconEntry
    confidence: float
    #: "exact"(1단계 해시) | "knn"(2단계 학습) | "position"(동점 처리)
    method: str = "knn"

    @property
    def is_confident(self) -> bool:
        return self.confidence >= 0.80

    def describe(self) -> str:
        label = {"exact": "정확 일치", "knn": "학습 기반", "position": "위치 보정"}
        return f"{self.entry.name} ({label.get(self.method, self.method)} {self.confidence:.0%})"

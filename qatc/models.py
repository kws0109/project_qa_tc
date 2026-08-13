"""QATC 핵심 데이터 모델.

설계 원칙 — **3층 분리**::

    auto : 프레임에서 결정론적으로 재계산 가능 (해시, 검출 박스, OCR 텍스트)
    llm  : API 재호출로 재생성 가능 (화면 이름, 역할, 요소 의미)
    user : 사람이 확정한 것. **절대 자동으로 덮어쓰지 않는다.**

분석 파라미터를 바꿔 재분석하거나 프롬프트를 고쳐 재생성해도 ``user`` 층은 살아남는다.
리뷰 작업 30분이 재실행 한 번에 날아가지 않게 하는 유일한 장치다.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable

# ---------------------------------------------------------------- 기본 타입


@dataclass(frozen=True)
class NormRect:
    """게임 클라이언트 영역 기준 정규화 사각형 (0.0~1.0).

    픽셀이 아니라 비율로 저장하므로 해상도가 달라도 그대로 유효하다.
    1920x1080에서 녹화한 TC를 2560x1440에서 그대로 재현할 수 있다.
    """

    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> float:
        return self.w * self.h

    def to_pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        return (
            int(round(self.x * width)),
            int(round(self.y * height)),
            int(round(self.w * width)),
            int(round(self.h * height)),
        )

    @classmethod
    def from_pixels(cls, x: int, y: int, w: int, h: int, width: int, height: int) -> NormRect:
        if width <= 0 or height <= 0:
            raise ValueError(f"클라이언트 크기가 유효하지 않습니다: {width}x{height}")
        return cls(x / width, y / height, w / width, h / height)

    def iou(self, other: NormRect) -> float:
        """두 사각형의 Intersection-over-Union."""
        ix1, iy1 = max(self.x, other.x), max(self.y, other.y)
        ix2 = min(self.x + self.w, other.x + other.w)
        iy2 = min(self.y + self.h, other.y + other.h)
        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0
        inter = (ix2 - ix1) * (iy2 - iy1)
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0

    def contains_point(self, nx: float, ny: float, tol: float = 0.0) -> bool:
        return (
            self.x - tol <= nx <= self.x + self.w + tol
            and self.y - tol <= ny <= self.y + self.h + tol
        )

    def contains(self, other: NormRect, tol: float = 0.01) -> bool:
        return (
            self.x - tol <= other.x
            and self.y - tol <= other.y
            and self.x + self.w + tol >= other.x + other.w
            and self.y + self.h + tol >= other.y + other.h
        )

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.w, self.h)


class InputKind(str, Enum):
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    DRAG = "drag"
    SCROLL = "scroll"
    KEY = "key"
    BOOKMARK = "bookmark"
    AUTO_SNAPSHOT = "auto_snapshot"


class CaptureReason(str, Enum):
    """프레임이 디스크에 저장된 이유. 분석 단계에서 프레임 신뢰도를 가른다."""

    PRE_ACTION = "pre_action"
    POST_FAST = "post_fast"
    POST_MID = "post_mid"
    POST_SETTLED = "post_settled"
    IDLE_CHANGE = "idle_change"
    MANUAL = "manual"


#: 상태 식별에 가장 신뢰할 수 있는 캡처 사유 (전이 애니메이션이 끝난 시점).
#: POST_FAST/POST_MID는 페이드 중간 프레임일 수 있어 상태 대표로 쓰지 않는다.
SETTLED_REASONS: frozenset[CaptureReason] = frozenset(
    {CaptureReason.POST_SETTLED, CaptureReason.IDLE_CHANGE, CaptureReason.MANUAL}
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------- 기록 계층


@dataclass
class Frame:
    """디스크에 저장된 한 장의 캡처."""

    id: str
    session_id: str
    ts: float
    path: str
    reason: CaptureReason
    client_w: int
    client_h: int
    phash: str = ""
    masked_phash: str = ""
    event_id: str | None = None

    @property
    def is_settled(self) -> bool:
        return self.reason in SETTLED_REASONS

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["reason"] = self.reason.value
        return d

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Frame:
        row = dict(row)
        row["reason"] = CaptureReason(row["reason"])
        return cls(**row)


@dataclass
class InputEvent:
    """게임 창이 포그라운드인 동안 관찰된 사용자 입력 하나.

    좌표는 게임 클라이언트 영역 기준 정규화 값. 창 밖 입력은 기록하지 않는다.
    """

    id: str
    session_id: str
    ts: float
    kind: InputKind
    nx: float | None = None
    ny: float | None = None
    nx2: float | None = None
    ny2: float | None = None
    key: str | None = None
    scroll_dy: int | None = None
    note: str | None = None
    #: 좌표를 믿을 수 있는가. 포인터 수식키(예: 스타레일 필드의 Alt)를 누르지
    #: 않은 클릭은 포인터가 화면 중앙에 잠겨 있어 좌표가 무의미하다.
    coords_reliable: bool = True

    def describe(self) -> str:
        """TC 절차 문구의 원재료가 되는, 사람이 읽을 수 있는 표현."""
        if self.kind is InputKind.CLICK:
            if not self.coords_reliable:
                return "클릭 (좌표 불확실 — 포인터 미활성 상태)"
            return f"({self.nx:.2f}, {self.ny:.2f}) 위치 클릭"
        if self.kind is InputKind.DOUBLE_CLICK:
            return f"({self.nx:.2f}, {self.ny:.2f}) 위치 더블클릭"
        if self.kind is InputKind.RIGHT_CLICK:
            return f"({self.nx:.2f}, {self.ny:.2f}) 위치 우클릭"
        if self.kind is InputKind.DRAG:
            return f"({self.nx:.2f}, {self.ny:.2f}) 에서 ({self.nx2:.2f}, {self.ny2:.2f}) 로 드래그"
        if self.kind is InputKind.SCROLL:
            direction = "아래로" if (self.scroll_dy or 0) < 0 else "위로"
            return f"마우스 휠 {direction} 스크롤"
        if self.kind is InputKind.KEY:
            # 키 이름은 대문자로. QA 문서 관례이기도 하고,
            # qatc.analyze.flow.describe_action 과 표기를 맞춘다.
            return f"[{(self.key or '').upper()}] 키 입력"
        if self.kind is InputKind.BOOKMARK:
            return f"북마크: {self.note or '(메모 없음)'}"
        if self.kind is InputKind.AUTO_SNAPSHOT:
            return "입력 없이 화면 전환 (컷씬/로딩/자동진행)"
        return self.kind.value

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> InputEvent:
        row = dict(row)
        row["kind"] = InputKind(row["kind"])
        return cls(**row)


# ---------------------------------------------------------------- 분석 계층


class ElementKind(str, Enum):
    PANEL = "panel"
    BUTTON = "button"
    TAB = "tab"
    TEXT = "text"
    ICON = "icon"
    INPUT = "input"
    LIST_ITEM = "list_item"
    UNKNOWN = "unknown"


@dataclass
class UIElement:
    """화면에서 검출된 UI 요소 하나.

    OpenCV는 "여기에 클릭 가능해 보이는 것이 있다"까지만 말한다 (``kind=UNKNOWN``).
    의미(``kind``, ``label``)는 LLM 또는 사용자가 채운다.
    """

    rect: NormRect
    kind: ElementKind = ElementKind.UNKNOWN
    text: str = ""
    label: str = ""
    confidence: float = 0.0
    source: str = "cv"  # cv | ocr | llm | user

    @property
    def display(self) -> str:
        return self.label or self.text or f"{self.kind.value}@({self.rect.cx:.2f},{self.rect.cy:.2f})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rect": list(self.rect.as_tuple()),
            "kind": self.kind.value,
            "text": self.text,
            "label": self.label,
            "confidence": self.confidence,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> UIElement:
        return cls(
            rect=NormRect(*d["rect"]),
            kind=ElementKind(d.get("kind", "unknown")),
            text=d.get("text", ""),
            label=d.get("label", ""),
            confidence=float(d.get("confidence", 0.0)),
            source=d.get("source", "cv"),
        )


@dataclass
class AutoFeatures:
    """프레임에서 결정론적으로 재계산 가능한 특징. 언제든 버리고 다시 만들어도 된다."""

    exemplar_frame_id: str = ""
    member_frame_ids: list[str] = field(default_factory=list)
    masked_phash: str = ""
    struct_sig: list[list[float]] = field(default_factory=list)
    text_sig: list[str] = field(default_factory=list)
    elements: list[UIElement] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exemplar_frame_id": self.exemplar_frame_id,
            "member_frame_ids": self.member_frame_ids,
            "masked_phash": self.masked_phash,
            "struct_sig": self.struct_sig,
            "text_sig": self.text_sig,
            "elements": [e.to_dict() for e in self.elements],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AutoFeatures:
        return cls(
            exemplar_frame_id=d.get("exemplar_frame_id", ""),
            member_frame_ids=list(d.get("member_frame_ids", [])),
            masked_phash=d.get("masked_phash", ""),
            struct_sig=[list(r) for r in d.get("struct_sig", [])],
            text_sig=list(d.get("text_sig", [])),
            elements=[UIElement.from_dict(e) for e in d.get("elements", [])],
        )


@dataclass
class LlmGuess:
    """LLM이 추측한 화면의 의미. API 재호출로 언제든 재생성 가능."""

    name: str = ""
    role: str = ""
    category: str = ""
    confidence: float = 0.0
    element_labels: dict[int, str] = field(default_factory=dict)
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "category": self.category,
            "confidence": self.confidence,
            "element_labels": {str(k): v for k, v in self.element_labels.items()},
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LlmGuess:
        return cls(
            name=d.get("name", ""),
            role=d.get("role", ""),
            category=d.get("category", ""),
            confidence=float(d.get("confidence", 0.0)),
            element_labels={int(k): v for k, v in (d.get("element_labels") or {}).items()},
            model=d.get("model", ""),
        )


@dataclass
class UserConfirm:
    """사람이 확정한 지식. **자동 프로세스가 절대 덮어쓰지 않는 유일한 층.**"""

    name: str | None = None
    role: str | None = None
    category: str | None = None
    notes: str = ""
    locked: bool = False
    hidden: bool = False

    @property
    def has_content(self) -> bool:
        return bool(
            self.name or self.role or self.category or self.notes or self.locked or self.hidden
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> UserConfirm:
        return cls(**d)


@dataclass
class ScreenState:
    """게임의 한 화면. 여러 프레임이 하나의 상태로 묶인다."""

    id: str
    auto: AutoFeatures = field(default_factory=AutoFeatures)
    llm: LlmGuess | None = None
    user: UserConfirm = field(default_factory=UserConfirm)

    @property
    def name(self) -> str:
        """표시용 이름. user > llm > 폴백 순으로 우선한다."""
        if self.user.name:
            return self.user.name
        if self.llm and self.llm.name:
            return self.llm.name
        return f"미확인 화면 {self.id[-4:]}"

    @property
    def role(self) -> str:
        if self.user.role:
            return self.user.role
        return self.llm.role if self.llm else ""

    @property
    def category(self) -> str:
        if self.user.category:
            return self.user.category
        if self.llm and self.llm.category:
            return self.llm.category
        return "미분류"

    @property
    def confidence(self) -> float:
        """사용자가 확정했으면 1.0, 아니면 LLM 신뢰도."""
        if self.user.name or self.user.locked:
            return 1.0
        return self.llm.confidence if self.llm else 0.0

    @property
    def needs_review(self) -> bool:
        """리뷰 GUI 타임라인에서 경고 표시를 띄울지."""
        return self.confidence < 0.7

    def element_at(self, nx: float, ny: float) -> UIElement | None:
        """정규화 좌표에 있는 가장 작은 요소. 클릭 지점의 UI를 알아낼 때 쓴다.

        가장 작은 것을 고르는 이유: 패널 안의 버튼을 클릭했다면 패널이 아니라
        버튼이 정답이기 때문이다.
        """
        hits = [e for e in self.elements_sorted() if e.rect.contains_point(nx, ny, tol=0.005)]
        return hits[0] if hits else None

    def elements_sorted(self) -> list[UIElement]:
        return sorted(self.auto.elements, key=lambda e: e.rect.area)

    def merge_user_from(self, other: ScreenState) -> None:
        """상태 병합 시 사용자 확정 정보를 흡수한다 (기존 값이 있으면 유지)."""
        if not self.user.name and other.user.name:
            self.user.name = other.user.name
        if not self.user.role and other.user.role:
            self.user.role = other.user.role
        if not self.user.category and other.user.category:
            self.user.category = other.user.category
        if other.user.notes:
            self.user.notes = "\n".join(x for x in (self.user.notes, other.user.notes) if x)
        self.user.locked = self.user.locked or other.user.locked

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "auto_json": json.dumps(self.auto.to_dict(), ensure_ascii=False),
            "llm_json": json.dumps(self.llm.to_dict(), ensure_ascii=False) if self.llm else None,
            "user_json": json.dumps(self.user.to_dict(), ensure_ascii=False),
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ScreenState:
        return cls(
            id=row["id"],
            auto=AutoFeatures.from_dict(json.loads(row.get("auto_json") or "{}")),
            llm=LlmGuess.from_dict(json.loads(row["llm_json"])) if row.get("llm_json") else None,
            user=UserConfirm.from_dict(json.loads(row.get("user_json") or "{}")),
        )


@dataclass
class Transition:
    """상태 A에서 어떤 행동을 해서 상태 B로 갔다는 관측 사실."""

    id: str
    from_state: str
    to_state: str
    event_id: str | None = None
    action_desc: str = ""
    target_element: UIElement | None = None
    evidence_frames: list[str] = field(default_factory=list)
    observed_count: int = 1

    @property
    def is_self_loop(self) -> bool:
        return self.from_state == self.to_state

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "event_id": self.event_id,
            "action_desc": self.action_desc,
            "target_json": (
                json.dumps(self.target_element.to_dict(), ensure_ascii=False)
                if self.target_element
                else None
            ),
            "evidence_json": json.dumps(self.evidence_frames),
            "observed_count": self.observed_count,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Transition:
        return cls(
            id=row["id"],
            from_state=row["from_state"],
            to_state=row["to_state"],
            event_id=row.get("event_id"),
            action_desc=row.get("action_desc", ""),
            target_element=(
                UIElement.from_dict(json.loads(row["target_json"]))
                if row.get("target_json")
                else None
            ),
            evidence_frames=json.loads(row.get("evidence_json") or "[]"),
            observed_count=int(row.get("observed_count", 1)),
        )


# ---------------------------------------------------------------- TC 계층


class TCOrigin(str, Enum):
    """TC의 근거 출처. **INFERRED는 검증되지 않은 LLM 가설이다.**

    이 구분이 없으면 LLM 환각이 정식 TC로 둔갑한다. 엑셀에서 색으로 구분된다.
    """

    RECORDED = "기록됨"
    INFERRED = "추론됨"
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


# ---------------------------------------------------------------- 세션 / 그래프


@dataclass
class SessionMeta:
    id: str
    profile_name: str
    game_name: str
    started_at: str
    ended_at: str | None = None
    capture_backend: str = ""
    client_w: int = 0
    client_h: int = 0
    note: str = ""

    def to_row(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> SessionMeta:
        return cls(**row)


@dataclass
class FlowGraph:
    """분석 결과 전체. 리뷰 GUI와 TC 생성기가 공유하는 단일 진실 원천."""

    session_id: str
    states: dict[str, ScreenState] = field(default_factory=dict)
    transitions: list[Transition] = field(default_factory=list)
    step_order: list[str] = field(default_factory=list)

    # -- 조회 --------------------------------------------------------

    def state(self, sid: str) -> ScreenState | None:
        return self.states.get(sid)

    def outgoing(self, sid: str) -> list[Transition]:
        return [t for t in self.transitions if t.from_state == sid]

    def incoming(self, sid: str) -> list[Transition]:
        return [t for t in self.transitions if t.to_state == sid]

    def visible_states(self) -> list[ScreenState]:
        """사용자가 노이즈로 숨긴 상태를 제외한 목록."""
        return [s for s in self.states.values() if not s.user.hidden]

    def ordered_transitions(self) -> list[Transition]:
        """시간순 전이 목록. 리뷰 타임라인과 정상 경로 TC의 기준."""
        index = {t.id: t for t in self.transitions}
        ordered = [index[tid] for tid in self.step_order if tid in index]
        seen = {t.id for t in ordered}
        ordered.extend(t for t in self.transitions if t.id not in seen)
        return ordered

    def find_transition(self, from_state: str, to_state: str) -> Transition | None:
        for t in self.transitions:
            if t.from_state == from_state and t.to_state == to_state:
                return t
        return None

    def reverse_gaps(self) -> list[tuple[str, str]]:
        """정방향 전이는 있는데 역방향 전이가 관측되지 않은 (from, to) 쌍.

        역방향/중단 TC 생성의 근거가 된다 — "들어갔는데 나오는 걸 테스트 안 했다".
        """
        observed = {(t.from_state, t.to_state) for t in self.transitions}
        gaps = []
        for a, b in observed:
            if a != b and (b, a) not in observed:
                gaps.append((b, a))
        return gaps

    # -- 편집 --------------------------------------------------------

    def merge_states(self, keep_id: str, absorb_id: str) -> None:
        """``absorb_id`` 상태를 ``keep_id``로 흡수한다. 전이의 끝점도 함께 갱신된다.

        리뷰 GUI의 '병합' 버튼과 LLM 채팅의 ``merge_states`` 툴이 모두 여기로 들어온다.
        """
        if keep_id == absorb_id:
            return
        keep, absorb = self.states.get(keep_id), self.states.get(absorb_id)
        if keep is None or absorb is None:
            raise KeyError(f"병합 대상 상태를 찾을 수 없습니다: {keep_id} / {absorb_id}")

        keep.merge_user_from(absorb)
        keep.auto.member_frame_ids.extend(absorb.auto.member_frame_ids)
        # 위치가 겹치지 않는 요소만 추가 (같은 버튼이 두 번 들어가지 않게)
        for el in absorb.auto.elements:
            if not any(el.rect.iou(k.rect) > 0.7 for k in keep.auto.elements):
                keep.auto.elements.append(el)
        keep.auto.text_sig = sorted(set(keep.auto.text_sig) | set(absorb.auto.text_sig))

        for t in self.transitions:
            if t.from_state == absorb_id:
                t.from_state = keep_id
            if t.to_state == absorb_id:
                t.to_state = keep_id
        del self.states[absorb_id]
        self._dedupe_transitions()

    def split_state(self, state_id: str, frame_ids: list[str], new_name: str | None = None) -> str:
        """상태에서 지정한 프레임들을 떼어내 새 상태로 만든다 (과병합 교정).

        전이 재배정은 하지 않는다 — 어떤 전이가 어느 쪽에 속하는지는 프레임 증거를 보고
        사용자가 판단해야 하는 문제라 자동 추측이 오히려 해롭다. 대신 새 상태를
        미확정으로 만들어 리뷰 대상에 올린다.
        """
        src = self.states.get(state_id)
        if src is None:
            raise KeyError(f"분리 대상 상태를 찾을 수 없습니다: {state_id}")
        moving = [f for f in frame_ids if f in src.auto.member_frame_ids]
        if not moving:
            raise ValueError("분리할 프레임이 원본 상태에 없습니다")
        if len(moving) == len(src.auto.member_frame_ids):
            raise ValueError("모든 프레임을 분리할 수는 없습니다")

        src.auto.member_frame_ids = [f for f in src.auto.member_frame_ids if f not in moving]
        if src.auto.exemplar_frame_id in moving:
            src.auto.exemplar_frame_id = src.auto.member_frame_ids[0]

        new = ScreenState(
            id=new_id("st"),
            auto=AutoFeatures(exemplar_frame_id=moving[0], member_frame_ids=list(moving)),
            user=UserConfirm(name=new_name),
        )
        self.states[new.id] = new
        return new.id

    def delete_transition(self, transition_id: str) -> None:
        """전이를 제거한다. 병합으로 생긴 무의미한 self-loop를 사용자가 지울 때 쓴다.

        병합 시 self-loop를 자동으로 지우지 않는 이유: 리스트 스크롤처럼 같은 화면에
        머무는 진짜 self-loop가 존재하기 때문이다. 무엇이 노이즈인지는 사람이 판단한다.
        """
        self.transitions = [t for t in self.transitions if t.id != transition_id]
        self.step_order = [tid for tid in self.step_order if tid != transition_id]

    def _dedupe_transitions(self) -> None:
        """병합 후 생긴 동일 (from, to, action) 전이를 합치고 관측 횟수를 누적한다."""
        seen: dict[tuple[str, str, str], Transition] = {}
        for t in self.transitions:
            key = (t.from_state, t.to_state, t.action_desc)
            base = seen.get(key)
            if base is None:
                seen[key] = t
            else:
                base.observed_count += t.observed_count
                base.evidence_frames.extend(t.evidence_frames)
        kept = {t.id for t in seen.values()}
        self.transitions = [t for t in self.transitions if t.id in kept]
        self.step_order = [tid for tid in self.step_order if tid in kept]

    # -- 직렬화 ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "states": {k: v.to_row() for k, v in self.states.items()},
            "transitions": [t.to_row() for t in self.transitions],
            "step_order": self.step_order,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FlowGraph:
        return cls(
            session_id=d["session_id"],
            states={k: ScreenState.from_row(v) for k, v in d.get("states", {}).items()},
            transitions=[Transition.from_row(t) for t in d.get("transitions", [])],
            step_order=list(d.get("step_order", [])),
        )


def coverage(graph: FlowGraph, testcases: Iterable[TestCase]) -> tuple[set[str], set[str]]:
    """TC들이 커버한 전이 ID와 커버하지 못한 전이 ID를 돌려준다.

    Mermaid 다이어그램에서 미커버 간선을 점선으로 그리는 데 쓰인다 — 커버리지 갭을 눈으로 본다.
    """
    covered: set[str] = set()
    for tc in testcases:
        covered.update(tc.edge_ids)
    all_edges = {t.id for t in graph.transitions}
    return covered & all_edges, all_edges - covered

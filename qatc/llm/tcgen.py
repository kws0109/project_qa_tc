"""테스트케이스 생성 — 플로우 그래프에서 TC 초안을 만든다.

**세 종류를 각각 다른 호출로 만들고, 출처를 코드가 붙인다.**

=========== ========================================= ==========
종류         근거                                       출처 태그
=========== ========================================= ==========
정상 경로     실제 관측된 전이 경로                        기록됨
경계/예외     검출된 UI 요소에서 LLM이 추론                 추론됨
역방향/중단   관측된 전이의 역방향 공백                     추론됨
=========== ========================================= ==========

출처를 **LLM에게 물어보지 않고 코드가 결정하는 것**이 중요하다. 모델에게
"이건 추론이야?"라고 물으면 자기 출력을 낙관적으로 분류하는 경향이 있다.
어떤 프롬프트로 만들었는지는 코드가 알고 있으므로 코드가 태깅한다.

이 구분이 없으면 검증되지 않은 LLM 가설이 정식 TC로 둔갑하고, 리뷰어는
그걸 구분할 방법이 없다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np

from ..config import MODEL_DEEP
from ..models import (
    FlowGraph,
    Priority,
    ScreenState,
    TCKind,
    TCOrigin,
    TestCase,
    Transition,
    new_id,
)
from ..profiles import GameProfile
from . import prompts, schemas
from .client import EDGE_DETAIL, LlmClient, LlmError, encode_image, text_block

ImageLoader = Callable[[str], "np.ndarray | None"]

#: 파생 TC를 만들 때 한 번에 보내는 화면 수. 이미지를 고해상도로 보내므로 작게 잡는다.
DERIVED_BATCH = 4
#: 정상 경로 TC 한 번에 다룰 전이 수. 너무 길면 응답이 잘린다.
PATH_CHUNK = 12


@dataclass
class TcGenReport:
    recorded: int = 0
    inferred: int = 0
    failed_batches: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.recorded + self.inferred

    def summary(self) -> str:
        s = f"TC {self.total}건 생성 (기록됨 {self.recorded} / 추론됨 {self.inferred})"
        if self.failed_batches:
            s += f" · 실패 배치 {self.failed_batches}개"
        return s


def estimate_cost_usd(
    graph: FlowGraph, model: str = MODEL_DEEP, max_derived_screens: int = 24
) -> float:
    """실행 전 예상 비용(USD). 사용자에게 보여주고 진행 여부를 묻는 데 쓴다.

    경계·예외 단계가 유일하게 고해상도 이미지를 보내므로 비용의 대부분을 차지한다 —
    실측에서 70~80%였다. 그래서 그 단계의 화면 수가 견적의 주 변수다.
    """
    from .cost import model_rates

    in_rate, out_rate = model_rates(model)
    m = 1_000_000
    n_edges = len([t for t in graph.transitions if not _is_hidden(graph, t)])
    n_derived = min(len(graph.visible_states()), max_derived_screens)

    # 정상 경로: 텍스트만. 청크당 입력 ~2.5k / 출력 ~4k
    happy_calls = max(1, (n_edges + PATH_CHUNK - 1) // PATH_CHUNK)
    cost = happy_calls * (2_500 * in_rate + 4_000 * out_rate) / m
    # 파생: 배치당 이미지 4장(각 ~3k토큰) + 텍스트, 출력 ~4.5k
    derived_calls = (n_derived + DERIVED_BATCH - 1) // DERIVED_BATCH
    cost += derived_calls * (14_000 * in_rate + 4_500 * out_rate) / m
    # 역방향: 1회
    cost += (3_000 * in_rate + 3_500 * out_rate) / m
    return cost


def generate_testcases(
    client: LlmClient,
    graph: FlowGraph,
    profile: GameProfile,
    load_image: ImageLoader,
    *,
    model: str = MODEL_DEEP,
    include_derived: bool = True,
    include_reverse: bool = True,
    max_derived_screens: int = 24,
    icon_store=None,
    on_progress: Callable[[str, float], None] | None = None,
) -> tuple[list[TestCase], TcGenReport]:
    """플로우 그래프에서 TC를 생성한다.

    :param max_derived_screens: 경계·예외 TC 대상 화면 수 상한. 비용의 주 변수다.
    :param icon_store: 아이콘 사전 (:class:`~qatc.icons.IconStore`). 있으면 담당자가
        확정한 아이콘 지식 — 무엇을 누르면 무슨 일이 나는지, 어떤 재화를 소모하는지 —
        을 프롬프트에 넣는다. **추측이 아니라 확정된 사실**이므로 캐시 접두사에 둔다.
    """
    report = TcGenReport()
    progress = on_progress or (lambda _m, _p: None)
    out: list[TestCase] = []

    icon_block = _icon_knowledge(icon_store, graph)
    system = [
        text_block(prompts.QA_PERSONA),
        text_block(prompts.game_context_block(profile.name, profile.llm_context)),
    ]
    if icon_block:
        system.append(text_block(icon_block))
    system.append(text_block(prompts.TC_COMMON_RULES, cache=True))

    progress("정상 경로 TC 생성 중", 0.1)
    out.extend(_happy_path(client, graph, system, model, report))

    if include_derived:
        progress("경계값·예외 TC 생성 중", 0.5)
        out.extend(
            _derived(client, graph, system, load_image, model, report, max_derived_screens)
        )

    if include_reverse:
        progress("역방향·중단 TC 생성 중", 0.8)
        out.extend(_reverse(client, graph, system, model, report))

    progress("TC 생성 완료", 1.0)
    _assign_ids(out)
    return out, report


# ---------------------------------------------------------------- 정상 경로


def _happy_path(
    client: LlmClient,
    graph: FlowGraph,
    system: Sequence[dict],
    model: str,
    report: TcGenReport,
) -> list[TestCase]:
    """실제 플레이 경로를 TC로. 유일하게 '기록됨' 출처를 받는 종류다."""
    transitions = [
        t for t in graph.ordered_transitions() if not _is_hidden(graph, t)
    ]
    if not transitions:
        report.notes.append("관측된 전이가 없어 정상 경로 TC를 만들지 못했습니다")
        return []

    out: list[TestCase] = []
    for start in range(0, len(transitions), PATH_CHUNK):
        chunk = transitions[start : start + PATH_CHUNK]
        content = [
            text_block(prompts.TC_HAPPY_PATH),
            text_block("[관측된 플레이 경로]\n" + _describe_path(graph, chunk)),
            text_block("[화면 정보]\n" + _describe_states(graph, _states_in(chunk))),
        ]
        cases = _call(client, system, content, model, "정상 경로 TC", report)
        for raw in cases:
            tc = _to_testcase(raw, graph, TCOrigin.RECORDED, default_kind=TCKind.HAPPY_PATH)
            out.append(tc)
            report.recorded += 1
    return out


# ---------------------------------------------------------------- 파생 케이스


def _derived(
    client: LlmClient,
    graph: FlowGraph,
    system: Sequence[dict],
    load_image: ImageLoader,
    model: str,
    report: TcGenReport,
    max_screens: int = 24,
) -> list[TestCase]:
    """검출된 UI 요소에서 경계값·예외를 추론한다. 이미지를 고해상도로 보낸다.

    여기서만 스크린샷을 보내는 이유: 경계 조건은 "수량 조절 버튼이 있다",
    "재화 표시가 있다" 같은 **화면에 보이는 것**에서 나온다. 텍스트 요약만으로는
    모델이 화면에 없는 기능을 만들어낸다.

    **비용의 70~80%가 이 단계에서 나온다.** 그래서 상한을 두고, 넘치면
    상호작용이 많은 화면(전이 수 기준)을 우선한다 — 클릭할 곳이 많은 화면일수록
    테스트되지 않은 경우도 많기 때문이다.
    """
    states = [s for s in graph.visible_states() if s.auto.elements]
    if not states:
        return []

    if len(states) > max_screens:
        def interaction_score(s: ScreenState) -> tuple[int, int]:
            return (len(graph.outgoing(s.id)) + len(graph.incoming(s.id)), len(s.auto.elements))

        states = sorted(states, key=interaction_score, reverse=True)[:max_screens]
        report.notes.append(
            f"경계·예외 TC 대상을 상호작용이 많은 화면 {max_screens}개로 제한했습니다 "
            f"(설정: llm.max_derived_screens)"
        )

    out: list[TestCase] = []
    for start in range(0, len(states), DERIVED_BATCH):
        batch = states[start : start + DERIVED_BATCH]
        content: list[dict] = [text_block(prompts.TC_DERIVED)]
        used = 0
        for state in batch:
            img = load_image(state.auto.exemplar_frame_id)
            if img is None:
                continue
            used += 1
            content.append(text_block(f"--- {state.name} ({state.id}) ---"))
            content.append(encode_image(img, max_edge=EDGE_DETAIL))
            content.append(text_block(_describe_state_detail(state)))
        if not used:
            continue
        content.append(text_block("위 화면들에서 미테스트 케이스를 찾아 TC를 만들어 주세요."))

        cases = _call(client, system, content, model, "경계·예외 TC", report)
        for raw in cases:
            tc = _to_testcase(raw, graph, TCOrigin.INFERRED, default_kind=TCKind.BOUNDARY)
            out.append(tc)
            report.inferred += 1
    return out


# ---------------------------------------------------------------- 역방향


def _reverse(
    client: LlmClient,
    graph: FlowGraph,
    system: Sequence[dict],
    model: str,
    report: TcGenReport,
) -> list[TestCase]:
    """역방향 공백에서 되돌아가기·중단 시나리오를 만든다."""
    gaps = [
        (a, b)
        for a, b in graph.reverse_gaps()
        if a in graph.states and b in graph.states
        and not graph.states[a].user.hidden and not graph.states[b].user.hidden
    ]
    if not gaps:
        return []

    lines = [
        f"- {graph.states[a].name} ({a}) 에서 {graph.states[b].name} ({b}) 로 "
        f"되돌아가는 경로가 테스트되지 않음"
        for a, b in gaps[:20]
    ]
    content = [
        text_block(prompts.TC_REVERSE),
        text_block("[역방향 미테스트 경로]\n" + "\n".join(lines)),
        text_block(
            "[화면 정보]\n"
            + _describe_states(graph, {s for pair in gaps[:20] for s in pair})
        ),
    ]
    cases = _call(client, system, content, model, "역방향·중단 TC", report)

    out: list[TestCase] = []
    for raw in cases:
        tc = _to_testcase(raw, graph, TCOrigin.INFERRED, default_kind=TCKind.REVERSE)
        out.append(tc)
        report.inferred += 1
    return out


# ---------------------------------------------------------------- 공통 도우미


def _call(
    client: LlmClient,
    system: Sequence[dict],
    content: Sequence[dict],
    model: str,
    purpose: str,
    report: TcGenReport,
) -> list[dict]:
    try:
        result = client.structured(
            purpose=purpose,
            model=model,
            system=system,
            content=content,
            schema=schemas.TESTCASES,
            max_tokens=12000,
            effort="high",
        )
        return list(result.data.get("testcases", []))
    except LlmError as exc:
        report.failed_batches += 1
        report.notes.append(f"{purpose} 실패: {exc}")
        return []


def _icon_knowledge(icon_store, graph: FlowGraph) -> str:
    """아이콘 사전을 프롬프트 블록으로.

    **이건 담당자가 확정한 사실이다** — LLM의 추측과 구별되어야 한다. 그래서
    "확정된 지식"이라고 명시하고, 이 정보에 근거한 TC는 추론이 아니라고 알려준다.

    화면에 실제로 등장한 아이콘만 넣는다. 사전이 커지면(수십~수백 개) 전부 넣을 때
    프롬프트가 비대해지고, 이 세션과 무관한 아이콘이 엉뚱한 TC를 유도한다.
    """
    if icon_store is None or not len(icon_store):
        return ""

    used_names = {
        (el.label or "").strip()
        for state in graph.visible_states()
        for el in state.auto.elements
        if el.source == "icon" and el.label
    }
    entries = [e for e in icon_store.complete_entries() if e.name in used_names]
    if not entries:
        # 매칭 기록이 없으면 완성된 아이콘 전부를 넣되 상한을 둔다
        entries = icon_store.complete_entries()[:40]
    if not entries:
        return ""

    lines = [
        "[담당자가 확정한 아이콘 지식]",
        "아래는 QA 담당자가 직접 확인해 등록한 사실입니다. 추측이 아니므로 그대로 신뢰하고,",
        "이 정보에 근거한 테스트케이스는 관측된 사실로 취급하세요.",
        "",
    ]
    lines.extend(entry.tc_context() for entry in entries)

    consumables = sorted({e.action.consumes for e in entries if e.action.consumes})
    if consumables:
        lines.append("")
        lines.append(
            "재화를 소모하는 동작이 있습니다: "
            + ", ".join(consumables)
            + ". 각각에 대해 '재화 부족 상태' 경계값 테스트케이스를 반드시 포함하세요."
        )
    irreversible = [e.name for e in entries if not e.action.reversible]
    if irreversible:
        lines.append(
            "되돌릴 수 없는 동작입니다(우선순위 High로 잡을 것): " + ", ".join(irreversible)
        )
    return "\n".join(lines)


def _is_hidden(graph: FlowGraph, t: Transition) -> bool:
    for sid in (t.from_state, t.to_state):
        state = graph.states.get(sid)
        if state is None or state.user.hidden:
            return True
    return False


def _states_in(transitions: Sequence[Transition]) -> set[str]:
    ids: set[str] = set()
    for t in transitions:
        ids.add(t.from_state)
        ids.add(t.to_state)
    return ids


def _describe_path(graph: FlowGraph, transitions: Sequence[Transition]) -> str:
    lines = []
    for n, t in enumerate(transitions, 1):
        src = graph.states.get(t.from_state)
        dst = graph.states.get(t.to_state)
        lines.append(
            f"{n}. [{t.id}] {src.name if src else t.from_state} "
            f"→ ({t.action_desc}) → {dst.name if dst else t.to_state}"
        )
    return "\n".join(lines)


def _describe_states(graph: FlowGraph, state_ids: set[str]) -> str:
    lines = []
    for sid in sorted(state_ids):
        state = graph.states.get(sid)
        if state is None:
            continue
        lines.append(f"- {state.name} ({sid}) / 분류: {state.category}")
        if state.role:
            lines.append(f"    역할: {state.role}")
        if state.user.notes:
            lines.append(f"    담당자 메모: {state.user.notes}")
        labels = [e.display for e in state.elements_sorted() if e.label or e.text][:10]
        if labels:
            lines.append(f"    요소: {', '.join(labels)}")
    return "\n".join(lines)


def _describe_state_detail(state: ScreenState) -> str:
    """파생 TC용 상세 설명. 요소 좌표까지 준다 — 어디에 무엇이 있는지가 근거다."""
    lines = [f"화면 이름: {state.name} / 분류: {state.category}"]
    if state.role:
        lines.append(f"역할: {state.role}")
    if state.user.notes:
        lines.append(f"담당자 메모: {state.user.notes}")
    elements = [e for e in state.elements_sorted() if e.label or e.text][:18]
    if elements:
        lines.append("검출된 UI 요소 (화면 대비 위치):")
        for e in elements:
            lines.append(
                f"  - {e.display} @ ({e.rect.cx:.2f}, {e.rect.cy:.2f}) "
                f"크기 {e.rect.w:.2f}x{e.rect.h:.2f}"
            )
    return "\n".join(lines)


def _to_testcase(
    raw: dict, graph: FlowGraph, origin: TCOrigin, default_kind: TCKind
) -> TestCase:
    """LLM 출력을 :class:`TestCase`로. **출처는 코드가 결정한다.**

    모델에게 자기 출력이 추론인지 묻지 않는다 — 어떤 프롬프트로 만들었는지는
    코드가 알고 있고, 모델은 자기 출력을 관대하게 분류하는 경향이 있다.
    """
    steps = [str(s).strip() for s in (raw.get("steps") or []) if str(s).strip()]
    expected = [str(s).strip() for s in (raw.get("expected") or []) if str(s).strip()]
    # 절차와 기대결과 개수가 어긋나면 엑셀에서 대응이 무너진다. 짧은 쪽에 맞춘다.
    if len(steps) != len(expected):
        n = min(len(steps), len(expected))
        if n == 0:
            expected = expected or ["(기대 결과 누락 — 검토 필요)"] * len(steps)
        else:
            steps, expected = steps[:n], expected[:n]

    valid_edges = {t.id for t in graph.transitions}
    edge_ids = [e for e in (raw.get("edge_ids") or []) if e in valid_edges]

    try:
        kind = TCKind(str(raw.get("kind", "")))
    except ValueError:
        kind = default_kind
    try:
        priority = Priority(str(raw.get("priority", "Medium")))
    except ValueError:
        priority = Priority.MEDIUM

    state_path = _states_from_edges(graph, edge_ids)
    evidence = [
        f for eid in edge_ids
        for t in graph.transitions if t.id == eid
        for f in t.evidence_frames[:2]
    ]

    return TestCase(
        id="",  # 나중에 일괄 부여
        category_major=str(raw.get("category_major", "")).strip() or "미분류",
        category_minor=str(raw.get("category_minor", "")).strip(),
        title=str(raw.get("title", "")).strip() or "(제목 없음)",
        precondition=str(raw.get("precondition", "")).strip() or "없음",
        steps=steps,
        expected=expected,
        priority=priority,
        kind=kind,
        origin=origin,
        evidence_frames=evidence[:6],
        state_path=state_path,
        edge_ids=edge_ids,
        rationale=str(raw.get("rationale", "")).strip(),
    )


def _states_from_edges(graph: FlowGraph, edge_ids: list[str]) -> list[str]:
    by_id = {t.id: t for t in graph.transitions}
    path: list[str] = []
    for eid in edge_ids:
        t = by_id.get(eid)
        if t is None:
            continue
        if not path or path[-1] != t.from_state:
            path.append(t.from_state)
        path.append(t.to_state)
    return path


def _assign_ids(cases: list[TestCase]) -> None:
    """대분류별로 읽기 좋은 TC ID를 부여한다. 엑셀 정렬과 추적에 쓰인다."""
    counters: dict[str, int] = {}
    for tc in cases:
        key = tc.category_major or "ETC"
        counters[key] = counters.get(key, 0) + 1
        prefix = "".join(ch for ch in key if ch.isalnum())[:6] or "TC"
        tc.id = f"{prefix}-{counters[key]:03d}"
    # 중복 ID 방지 (같은 접두사가 다른 대분류에서 나온 경우)
    seen: set[str] = set()
    for tc in cases:
        if tc.id in seen:
            tc.id = new_id("tc")
        seen.add(tc.id)

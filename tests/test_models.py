"""데이터 모델 불변식 테스트.

여기서 지키는 것은 하나다 — **사용자가 확정한 정보는 어떤 자동 처리도 덮어쓰지
않는다.** 이 성질이 깨지면 리뷰 작업 30분이 재분석 한 번에 사라진다.
"""

from __future__ import annotations

import pytest

from qatc.models import (
    AutoFeatures,
    ElementKind,
    FlowGraph,
    InputEvent,
    InputKind,
    LlmGuess,
    NormRect,
    Priority,
    ScreenState,
    TCKind,
    TCOrigin,
    TestCase,
    Transition,
    UIElement,
    UserConfirm,
    coverage,
    new_id,
)


# ---------------------------------------------------------------- NormRect


def test_normrect_pixel_roundtrip():
    rect = NormRect.from_pixels(100, 50, 200, 80, 1920, 1080)
    assert rect.to_pixels(1920, 1080) == (100, 50, 200, 80)


def test_normrect_resolution_independence():
    """정규화 좌표의 존재 이유 — 해상도가 달라도 같은 위치를 가리킨다."""
    rect = NormRect.from_pixels(960, 540, 192, 108, 1920, 1080)
    x, y, w, h = rect.to_pixels(2560, 1440)
    assert (x, y, w, h) == (1280, 720, 256, 144)


def test_normrect_iou_and_containment():
    outer = NormRect(0.0, 0.0, 0.5, 0.5)
    inner = NormRect(0.1, 0.1, 0.2, 0.2)
    assert outer.contains(inner)
    assert not inner.contains(outer)
    assert outer.iou(outer) == pytest.approx(1.0)
    assert outer.iou(NormRect(0.9, 0.9, 0.1, 0.1)) == 0.0


def test_normrect_rejects_bad_client_size():
    with pytest.raises(ValueError):
        NormRect.from_pixels(0, 0, 10, 10, 0, 100)


# ---------------------------------------------------------------- 3층 분리


def test_user_layer_wins_over_llm():
    state = ScreenState(id="st_001", llm=LlmGuess(name="LLM 추정", category="가챠"))
    assert state.name == "LLM 추정"
    state.user.name = "확정 이름"
    assert state.name == "확정 이름"


def test_confidence_is_full_when_user_confirmed():
    state = ScreenState(id="st_001", llm=LlmGuess(name="추정", confidence=0.3))
    assert state.needs_review
    state.user.name = "확정"
    assert state.confidence == 1.0
    assert not state.needs_review


def test_fallback_name_when_nothing_known():
    state = ScreenState(id="st_abcd")
    assert "abcd" in state.name


def test_merge_preserves_existing_user_values():
    """병합 시 흡수되는 쪽 값이 남는 쪽을 덮어쓰지 않는다."""
    keep = ScreenState(id="a", user=UserConfirm(name="유지", notes="기존"))
    absorb = ScreenState(id="b", user=UserConfirm(name="흡수", role="역할", notes="추가"))
    keep.merge_user_from(absorb)
    assert keep.user.name == "유지"          # 덮어쓰지 않음
    assert keep.user.role == "역할"          # 비어 있던 칸은 채움
    assert "기존" in keep.user.notes and "추가" in keep.user.notes


# ---------------------------------------------------------------- 요소


def test_element_at_picks_smallest():
    """패널 안의 버튼을 눌렀으면 정답은 패널이 아니라 버튼이다."""
    state = ScreenState(
        id="s",
        auto=AutoFeatures(
            elements=[
                UIElement(NormRect(0.0, 0.0, 0.8, 0.8), ElementKind.PANEL),
                UIElement(NormRect(0.1, 0.1, 0.1, 0.05), ElementKind.BUTTON),
            ]
        ),
    )
    hit = state.element_at(0.15, 0.12)
    assert hit is not None and hit.kind is ElementKind.BUTTON


def test_element_at_returns_none_outside():
    state = ScreenState(
        id="s", auto=AutoFeatures(elements=[UIElement(NormRect(0.0, 0.0, 0.2, 0.2))])
    )
    assert state.element_at(0.9, 0.9) is None


# ---------------------------------------------------------------- 그래프


def _graph_with_path(names: list[str]) -> FlowGraph:
    graph = FlowGraph(session_id="s")
    for n in names:
        graph.states[n] = ScreenState(id=n)
    for a, b in zip(names, names[1:]):
        t = Transition(id=f"tr_{a}_{b}", from_state=a, to_state=b, action_desc="클릭")
        graph.transitions.append(t)
        graph.step_order.append(t.id)
    return graph


def test_merge_rewires_transitions():
    graph = _graph_with_path(["a", "b", "c"])
    graph.merge_states("a", "c")
    assert "c" not in graph.states
    assert all(t.from_state != "c" and t.to_state != "c" for t in graph.transitions)


def test_merge_missing_state_raises():
    graph = _graph_with_path(["a", "b"])
    with pytest.raises(KeyError):
        graph.merge_states("a", "없음")


def test_merge_is_noop_for_same_id():
    graph = _graph_with_path(["a", "b"])
    before = len(graph.states)
    graph.merge_states("a", "a")
    assert len(graph.states) == before


def test_reverse_gaps_finds_missing_return():
    graph = _graph_with_path(["home", "menu"])
    assert ("menu", "home") in graph.reverse_gaps()


def test_reverse_gaps_empty_when_bidirectional():
    graph = _graph_with_path(["home", "menu"])
    t = Transition(id="back", from_state="menu", to_state="home", action_desc="ESC")
    graph.transitions.append(t)
    assert graph.reverse_gaps() == []


def test_split_state_moves_frames():
    graph = FlowGraph(session_id="s")
    graph.states["a"] = ScreenState(
        id="a", auto=AutoFeatures(exemplar_frame_id="f1", member_frame_ids=["f1", "f2", "f3"])
    )
    new_sid = graph.split_state("a", ["f3"], new_name="분리됨")
    assert graph.states["a"].auto.member_frame_ids == ["f1", "f2"]
    assert graph.states[new_sid].auto.member_frame_ids == ["f3"]
    assert graph.states[new_sid].name == "분리됨"


def test_split_state_rejects_taking_everything():
    graph = FlowGraph(session_id="s")
    graph.states["a"] = ScreenState(id="a", auto=AutoFeatures(member_frame_ids=["f1"]))
    with pytest.raises(ValueError):
        graph.split_state("a", ["f1"])


def test_delete_transition_removes_from_order():
    graph = _graph_with_path(["a", "b", "c"])
    tid = graph.transitions[0].id
    graph.delete_transition(tid)
    assert tid not in graph.step_order
    assert all(t.id != tid for t in graph.transitions)


def test_graph_roundtrip_preserves_user_layer():
    graph = _graph_with_path(["a", "b"])
    graph.states["a"].user.name = "확정된 홈"
    graph.states["a"].user.locked = True
    restored = FlowGraph.from_dict(graph.to_dict())
    assert restored.states["a"].user.name == "확정된 홈"
    assert restored.states["a"].user.locked


# ---------------------------------------------------------------- 커버리지


def test_coverage_split():
    graph = _graph_with_path(["a", "b", "c"])
    covered_edge = graph.transitions[0].id
    tc = TestCase(id="t", edge_ids=[covered_edge, "존재하지않는간선"])
    covered, uncovered = coverage(graph, [tc])
    assert covered == {covered_edge}
    assert "존재하지않는간선" not in covered   # 그래프에 없는 ID는 무시
    assert len(uncovered) == len(graph.transitions) - 1


def test_testcase_roundtrip():
    tc = TestCase(
        id="TC-001",
        title="제목",
        steps=["1단계"],
        expected=["결과"],
        priority=Priority.HIGH,
        kind=TCKind.BOUNDARY,
        origin=TCOrigin.INFERRED,
        edge_ids=["e1"],
    )
    back = TestCase.from_row(tc.to_row())
    assert back.priority is Priority.HIGH
    assert back.origin is TCOrigin.INFERRED
    assert back.steps == ["1단계"]


# ---------------------------------------------------------------- 입력


@pytest.mark.parametrize(
    "kind,extra,expect",
    [
        (InputKind.CLICK, {"nx": 0.5, "ny": 0.25}, "클릭"),
        (InputKind.KEY, {"key": "esc"}, "ESC"),
        (InputKind.SCROLL, {"scroll_dy": -1}, "아래로"),
        (InputKind.BOOKMARK, {"note": "핵심"}, "핵심"),
    ],
)
def test_input_describe(kind, extra, expect):
    event = InputEvent(id=new_id("ev"), session_id="s", ts=0.0, kind=kind, **extra)
    assert expect in event.describe()


def test_input_roundtrip():
    event = InputEvent(
        id="e1", session_id="s", ts=1.5, kind=InputKind.DRAG,
        nx=0.1, ny=0.2, nx2=0.3, ny2=0.4,
    )
    assert InputEvent.from_row(event.to_row()).kind is InputKind.DRAG

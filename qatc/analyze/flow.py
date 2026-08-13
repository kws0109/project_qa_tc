"""플로우 그래프 구축 — 클러스터와 입력 이벤트를 화면 전이로 엮는다.

레코더가 남긴 것은 "입력 이벤트 하나 + 그 전후 프레임 4장"의 나열이다.
클러스터링이 각 프레임에 화면 번호를 붙였으니, 이제 이렇게 읽으면 된다::

    PRE_ACTION 프레임의 화면  --(그 입력)-->  POST_SETTLED 프레임의 화면

**어떤 POST 프레임을 결과로 볼 것인가**가 이 모듈의 핵심 판단이다.
POST_FAST(+250ms)는 페이드 중간일 수 있고, POST_SETTLED(+1500ms)는 이미 다음
자동 전환이 일어났을 수 있다. 그래서 세 후보 중 **가장 늦으면서 PRE와 다른 화면**을
고른다. 전이가 없었다면(같은 화면) 그대로 self-loop로 남긴다 — 클릭했는데 아무 일도
안 일어난 것 역시 QA에서 의미 있는 관측이다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from ..models import (
    AutoFeatures,
    CaptureReason,
    Frame,
    FlowGraph,
    InputEvent,
    InputKind,
    ScreenState,
    Transition,
    UIElement,
    new_id,
)
from ..profiles import GameProfile
from .cluster import ClusterResult, FrameFeatures
from .ui_detect import element_at

#: 결과 화면 후보를 늦은 순으로 본다. 전이 애니메이션이 끝난 프레임을 선호한다.
_RESULT_PRIORITY = (
    CaptureReason.POST_SETTLED,
    CaptureReason.POST_MID,
    CaptureReason.POST_FAST,
    CaptureReason.IDLE_CHANGE,
)


@dataclass
class FlowBuildResult:
    graph: FlowGraph
    #: 대응하는 프레임이 없어 버려진 이벤트 (창 밖 클릭 직후 등)
    orphan_events: int = 0
    #: 화면이 바뀌지 않은 입력 (클릭했는데 반응 없음)
    no_change_events: int = 0
    #: 게임 규칙(이동 키·포인터 수식키)으로 제외한 입력
    filtered_events: int = 0

    def summary(self) -> str:
        return (
            f"상태 {len(self.graph.states)}개 · 전이 {len(self.graph.transitions)}개"
            + (f" · 무반응 입력 {self.no_change_events}건" if self.no_change_events else "")
            + (f" · 규칙 제외 {self.filtered_events}건" if self.filtered_events else "")
            + (f" · 미매칭 입력 {self.orphan_events}건" if self.orphan_events else "")
        )


def build_flow(
    session_id: str,
    frames: list[Frame],
    events: list[InputEvent],
    features: list[FrameFeatures],
    result: ClusterResult,
    profile: GameProfile | None = None,
    dedupe_map: dict[str, str] | None = None,
) -> FlowBuildResult:
    """클러스터링 결과와 입력 기록을 합쳐 :class:`FlowGraph`를 만든다.

    :param features: 클러스터링에 실제로 쓰인 프레임들 (1차 dedupe 후 대표 프레임)
    :param result: :func:`~qatc.analyze.cluster.cluster_frames` 결과
    :param dedupe_map: {접힌 프레임 ID: 대표 프레임 ID}. 1차 dedupe가 만든 정확한
        매핑이다. 없으면 시각 최근접으로 추정하는데, 전이 경계에서는 직전 화면의
        대표가 더 가까울 수 있어 틀린다. **되도록 넘길 것.**
    """
    # 대표 프레임 → 클러스터 번호
    frame_to_cluster: dict[str, int] = {}
    for idx, feat in enumerate(features):
        frame_to_cluster[feat.frame_id] = int(result.labels[idx])

    # dedupe로 접힌 프레임도 대표의 클러스터를 물려받는다.
    if dedupe_map:
        for folded_id, rep_id in dedupe_map.items():
            if folded_id not in frame_to_cluster and rep_id in frame_to_cluster:
                frame_to_cluster[folded_id] = frame_to_cluster[rep_id]
    _propagate_clusters(frames, features, frame_to_cluster)

    graph = FlowGraph(session_id=session_id)
    _create_states(graph, features, result, frame_to_cluster)

    cluster_to_state = {
        int(result.labels[i]): _state_id_for(graph, int(result.labels[i]))
        for i in range(len(features))
    }

    frames_by_event: dict[str, list[Frame]] = {}
    for f in frames:
        if f.event_id:
            frames_by_event.setdefault(f.event_id, []).append(f)

    orphans = 0
    no_change = 0
    filtered = 0
    prev_state: str | None = None

    for event in sorted(events, key=lambda e: e.ts):
        # 이미 녹화된 세션도 살릴 수 있게 분석 단계에서 한 번 더 거른다.
        # 레코더의 필터는 디스크를 아끼고, 이 필터는 규칙을 바꿔 **재분석**할 수
        # 있게 한다 — 프로파일을 고친 뒤 다시 녹화할 필요가 없어야 한다.
        if profile is not None and _is_noise_input(event, profile):
            filtered += 1
            continue

        group = sorted(frames_by_event.get(event.id, []), key=lambda f: f.ts)
        if not group:
            orphans += 1
            continue

        from_state = _resolve_from(group, frame_to_cluster, cluster_to_state, prev_state)
        to_state = _resolve_to(group, frame_to_cluster, cluster_to_state, from_state)

        if from_state is None or to_state is None:
            orphans += 1
            continue

        if from_state == to_state:
            no_change += 1

        target = _target_element(graph, from_state, event)
        transition = Transition(
            id=new_id("tr"),
            from_state=from_state,
            to_state=to_state,
            event_id=event.id,
            action_desc=describe_action(event, target, profile),
            target_element=target,
            evidence_frames=[f.id for f in group],
        )
        graph.transitions.append(transition)
        graph.step_order.append(transition.id)
        prev_state = to_state

    graph._dedupe_transitions()
    _prune_empty_states(graph)
    return FlowBuildResult(
        graph=graph, orphan_events=orphans, no_change_events=no_change,
        filtered_events=filtered,
    )


def _is_noise_input(event: InputEvent, profile: GameProfile) -> bool:
    """게임 규칙상 TC 대상이 아닌 입력인가.

    두 가지를 거른다.

    * **이동·카메라 키** — 필드에서 걷는 것은 QA 테스트케이스가 아니다.
    * **포인터 수식키** — 스타레일 필드의 Alt처럼 마우스를 활성화하는 수단일 뿐
      게임 동작이 아니다. 실측 세션에서 Alt가 72건 기록돼 있었다.

    좌표가 불확실한 클릭(수식키 미홀드)은 **거르지 않는다.** "필드에서 무언가를
    클릭했다"는 사실 자체는 유효하고, 좌표에만 의존하지 않으면 되기 때문이다.
    """
    if event.kind is not InputKind.KEY or not event.key:
        return False
    rules = profile.input_rules
    return rules.is_ignored_key(event.key) or rules.is_pointer_modifier(event.key)


def _propagate_clusters(
    frames: list[Frame], features: list[FrameFeatures], frame_to_cluster: dict[str, int]
) -> None:
    """대표가 아닌 프레임에 가장 가까운 시각의 대표 클러스터를 물려준다."""
    if not features:
        return
    rep_points = sorted((f.ts, f.frame_id) for f in features)
    rep_ts = [t for t, _ in rep_points]
    rep_ids = [i for _, i in rep_points]

    import bisect

    for f in frames:
        if f.id in frame_to_cluster:
            continue
        pos = bisect.bisect_left(rep_ts, f.ts)
        best, best_d = None, float("inf")
        for k in (pos - 1, pos, pos + 1):
            if 0 <= k < len(rep_ts):
                d = abs(rep_ts[k] - f.ts)
                if d < best_d:
                    best, best_d = rep_ids[k], d
        if best is not None:
            frame_to_cluster[f.id] = frame_to_cluster[best]


def _create_states(
    graph: FlowGraph,
    features: list[FrameFeatures],
    result: ClusterResult,
    frame_to_cluster: dict[str, int],
) -> None:
    """클러스터마다 :class:`ScreenState`를 만든다. 대표 프레임은 가장 안정된 것으로."""
    for label, member_idx in sorted(result.clusters.items()):
        members = [features[i] for i in member_idx]
        # 대표 프레임은 settled 우선, 그중 UI 요소가 가장 많이 검출된 것.
        # 요소가 많다는 건 전이 애니메이션이 아니라 완성된 화면이라는 뜻이다.
        settled = [m for m in members if m.is_settled] or members
        exemplar = max(settled, key=lambda m: (len(m.elements), len(m.ocr_lines)))

        state = ScreenState(
            id=f"st_{label:03d}",
            auto=AutoFeatures(
                exemplar_frame_id=exemplar.frame_id,
                member_frame_ids=[m.frame_id for m in members],
                struct_sig=exemplar.struct_sig,
                text_sig=_merged_text_sig(members),
                elements=list(exemplar.elements) + [ln.to_element() for ln in exemplar.ocr_lines],
            ),
        )
        graph.states[state.id] = state


def _merged_text_sig(members: list[FrameFeatures]) -> list[str]:
    """클러스터 전체에서 **과반이 본** 텍스트만 남긴다.

    한 프레임에서만 읽힌 토큰은 OCR 오인식이거나 순간적으로 뜬 토스트일 가능성이 높다.
    화면의 정체성을 담은 텍스트는 대부분의 프레임에 나타난다.
    """
    if not members:
        return []
    counter: Counter[str] = Counter()
    for m in members:
        counter.update(set(m.text_sig))
    need = max(1, len(members) // 2)
    return sorted(tok for tok, c in counter.items() if c >= need)


def _state_id_for(graph: FlowGraph, label: int) -> str:
    return f"st_{label:03d}"


def _resolve_from(
    group: list[Frame],
    frame_to_cluster: dict[str, int],
    cluster_to_state: dict[int, str],
    prev_state: str | None,
) -> str | None:
    """행동 직전 화면. PRE_ACTION 프레임이 없으면 직전 전이의 도착 화면을 쓴다."""
    for f in group:
        if f.reason is CaptureReason.PRE_ACTION and f.id in frame_to_cluster:
            return cluster_to_state.get(frame_to_cluster[f.id])
    return prev_state


def _resolve_to(
    group: list[Frame],
    frame_to_cluster: dict[str, int],
    cluster_to_state: dict[int, str],
    from_state: str | None,
) -> str | None:
    """행동 결과 화면.

    가장 늦은 프레임부터 훑되, **from_state와 다른 화면**을 만나면 그걸 택한다.
    POST_SETTLED가 이미 다음 자동 전환까지 가버린 경우를 대비해 우선순위를 두고,
    끝까지 같은 화면이면 self-loop(반응 없음)로 남긴다.
    """
    by_reason = {f.reason: f for f in group}
    fallback: str | None = None
    for reason in _RESULT_PRIORITY:
        f = by_reason.get(reason)
        if f is None or f.id not in frame_to_cluster:
            continue
        state = cluster_to_state.get(frame_to_cluster[f.id])
        if state is None:
            continue
        if fallback is None:
            fallback = state
        if state != from_state:
            return state
    return fallback if fallback is not None else from_state


def _target_element(graph: FlowGraph, state_id: str, event: InputEvent) -> UIElement | None:
    """클릭 좌표에 있던 UI 요소를 찾는다. TC 절차 문구의 근거가 된다."""
    if event.nx is None or event.ny is None:
        return None
    state = graph.states.get(state_id)
    if state is None:
        return None
    return element_at(state.auto.elements, event.nx, event.ny)


def describe_action(
    event: InputEvent, target: UIElement | None, profile: GameProfile | None = None
) -> str:
    """TC 절차에 그대로 들어갈 행동 설명.

    검출된 요소에 OCR 텍스트가 있으면 그걸 쓴다 — "(0.42, 0.31) 클릭"보다
    "[강화하기] 버튼 클릭"이 TC로서 훨씬 쓸모 있다.
    """
    label = ""
    if target is not None:
        label = (target.label or target.text).strip()

    if event.kind is InputKind.KEY and event.key:
        key = profile.describe_key(event.key) if profile else event.key.upper()
        return f"[{key}] 키 입력"
    if event.kind is InputKind.SCROLL:
        direction = "아래로" if (event.scroll_dy or 0) < 0 else "위로"
        return f"화면 {direction} 스크롤"
    if event.kind is InputKind.DRAG:
        return (
            f"[{label}] 드래그" if label
            else f"({event.nx:.2f}, {event.ny:.2f}) 에서 ({event.nx2:.2f}, {event.ny2:.2f}) 로 드래그"
        )
    if event.kind is InputKind.BOOKMARK:
        return f"[북마크] {event.note or '사용자 표시 지점'}"
    if event.kind is InputKind.AUTO_SNAPSHOT:
        return "자동 진행 (입력 없이 화면 전환)"

    verb = {
        InputKind.CLICK: "클릭",
        InputKind.DOUBLE_CLICK: "더블클릭",
        InputKind.RIGHT_CLICK: "우클릭",
    }.get(event.kind, "클릭")

    if label:
        return f"[{label}] {verb}"
    # 포인터 수식키를 안 누른 클릭은 좌표가 화면 중앙에 잠겨 있어 무의미하다.
    # 그 좌표를 TC 절차에 쓰면 테스터가 엉뚱한 곳을 누르게 된다.
    if not event.coords_reliable:
        return f"화면 {verb} (좌표 확인 필요 — 포인터 미활성 상태에서 기록됨)"
    if event.nx is not None:
        return f"화면 ({event.nx:.2f}, {event.ny:.2f}) 위치 {verb}"
    return verb


def _prune_empty_states(graph: FlowGraph) -> None:
    """어떤 전이에도 연결되지 않은 고립 상태를 제거한다.

    다만 상태가 하나뿐이면 남긴다 — 아무 전이도 없는 세션(화면 하나만 보고 끝냄)에서
    그래프가 통째로 비면 사용자가 무슨 일이 있었는지 알 수 없다.
    """
    if len(graph.states) <= 1:
        return
    connected: set[str] = set()
    for t in graph.transitions:
        connected.add(t.from_state)
        connected.add(t.to_state)
    for sid in list(graph.states):
        if sid not in connected:
            del graph.states[sid]

"""자동 제안 — 플로우 그래프에서 아이콘의 동작 초안을 뽑아낸다.

**시스템은 이미 답을 절반 알고 있습니다.** 담당자가 (0.22, 0.06)을 눌렀더니
``홈 → 캐릭터``로 갔다는 사실이 그래프에 기록돼 있습니다. 그러니 빈 폼을 내밀고
전부 입력하게 하는 대신, 초안을 채워두고 **이름만 받으면** 됩니다.

입력 노동이 10배 차이납니다. 아이콘이 수십 개인 게임에서 이건 기능을 쓰느냐 마느냐를
가릅니다.

무엇을 추론하고 무엇을 못 하는가
--------------------------------
* **추론 가능** — 동작 유형(이동/뒤로/토글), 대상 화면, 기대 결과 문장
* **추론 불가** — 아이콘의 **이름**. 화면 전이만으로는 그 그림이 "기원"인지
  "워프"인지 알 수 없습니다. 이름은 사람이 채웁니다.
* **부분 추론** — 소모 재화. 대상 화면 이름이나 OCR 텍스트에 힌트가 있으면 제안합니다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import FlowGraph, InputEvent, InputKind, ScreenState, Transition, UIElement
from .models import ActionKind, IconAction

#: 재화 소모를 암시하는 단어. 화면 이름이나 버튼 텍스트에서 찾는다.
_PURCHASE_HINTS = ("구매", "뽑기", "기원", "워프", "소환", "모집", "강화", "돌파", "레벨업", "구입")
#: 되돌리기 어려운 확정 동작을 암시하는 단어.
_CONFIRM_HINTS = ("확인", "실행", "적용", "완료", "시작", "장착", "사용", "제작", "분해")
#: 취소·닫기를 암시하는 단어.
_CANCEL_HINTS = ("취소", "닫기", "돌아가기", "뒤로")


@dataclass
class IconSuggestion:
    """등록 다이얼로그를 미리 채울 초안."""

    action: IconAction
    #: 이름 후보. 확신이 없으면 빈 문자열 — 잘못된 이름을 미리 채우면
    #: 사용자가 무심코 승인해 사전이 오염된다.
    name_hint: str = ""
    #: 이 초안이 어디서 나왔는지. GUI가 "왜 이렇게 제안했나"를 보여준다.
    rationale: str = ""

    @property
    def has_action(self) -> bool:
        return self.action.kind is not ActionKind.UNKNOWN


def suggest_from_transition(
    graph: FlowGraph,
    transition: Transition,
    event: InputEvent | None = None,
    element: UIElement | None = None,
) -> IconSuggestion:
    """관측된 전이 하나에서 아이콘 동작 초안을 만든다."""
    src = graph.states.get(transition.from_state)
    dst = graph.states.get(transition.to_state)
    if src is None or dst is None:
        return IconSuggestion(action=IconAction(), rationale="전이의 화면 정보를 찾을 수 없습니다")

    label = _element_text(element)

    if transition.is_self_loop:
        return _suggest_self_loop(src, label)

    if _is_return(graph, transition):
        return IconSuggestion(
            action=IconAction(
                kind=ActionKind.BACK,
                target_screen_name=dst.name,
                expected=f"{dst.name} 화면으로 돌아간다",
                reversible=True,
            ),
            name_hint=label or "뒤로가기",
            rationale=f"이 경로의 반대 방향({dst.name} → {src.name})이 앞서 관측되었습니다",
        )

    if event is not None and event.kind is InputKind.KEY:
        # ESC 등 키 입력은 아이콘이 아니다. 초안만 주고 등록은 막지 않는다.
        return IconSuggestion(
            action=IconAction(
                kind=ActionKind.BACK,
                target_screen_name=dst.name,
                expected=f"{dst.name} 화면으로 돌아간다",
            ),
            name_hint="",
            rationale="키 입력으로 발생한 전이입니다 (아이콘이 아닐 수 있습니다)",
        )

    kind = _infer_navigate_kind(label, dst)
    action = IconAction(
        kind=kind,
        target_screen_name=dst.name,
        expected=_expected_sentence(kind, dst),
        consumes=_guess_consumes(label, dst),
        reversible=kind.default_reversible,
    )
    return IconSuggestion(
        action=action,
        name_hint=label,
        rationale=f"관측된 전이: {src.name} → {dst.name}",
    )


def _suggest_self_loop(state: ScreenState, label: str) -> IconSuggestion:
    """화면이 바뀌지 않은 클릭.

    셋 중 하나다 — 토글(설정 on/off), 확인 팝업 없이 즉시 적용, 또는 아무 반응 없음.
    구분할 근거가 없으므로 **유형을 단정하지 않고** 사용자가 고르게 한다.
    잘못 단정하면 "재화가 차감된다" 같은 사실과 다른 TC가 나온다.
    """
    hint = ""
    if any(word in label for word in _CONFIRM_HINTS):
        hint = ActionKind.CONFIRM
    elif any(word in label for word in _CANCEL_HINTS):
        hint = ActionKind.CANCEL

    return IconSuggestion(
        action=IconAction(
            kind=hint or ActionKind.UNKNOWN,
            expected="",
            reversible=True,
        ),
        name_hint=label,
        rationale=(
            f"클릭해도 화면({state.name})이 바뀌지 않았습니다. "
            "토글·즉시 적용·무반응 중 무엇인지 확인이 필요합니다."
        ),
    )


def _is_return(graph: FlowGraph, transition: Transition) -> bool:
    """이 전이가 '되돌아가기'인가 — 반대 방향이 **먼저** 관측되었는가.

    시간 순서를 보는 것이 핵심이다. A→B와 B→A가 둘 다 있을 때, 나중에 일어난
    쪽이 되돌아가기다. 순서를 안 보면 둘 다 BACK으로 잡힌다.
    """
    order = {tid: i for i, tid in enumerate(graph.step_order)}
    mine = order.get(transition.id)
    if mine is None:
        return False
    for other in graph.transitions:
        if other.from_state == transition.to_state and other.to_state == transition.from_state:
            theirs = order.get(other.id)
            if theirs is not None and theirs < mine:
                return True
    return False


def _infer_navigate_kind(label: str, dst: ScreenState) -> ActionKind:
    """화면이 바뀌는 전이의 유형. 기본은 이동이다."""
    text = f"{label} {dst.name}"
    if any(word in text for word in _CANCEL_HINTS):
        return ActionKind.CANCEL
    if any(word in label for word in _CONFIRM_HINTS):
        return ActionKind.CONFIRM
    return ActionKind.NAVIGATE


def _expected_sentence(kind: ActionKind, dst: ScreenState) -> str:
    """TC의 '기대 결과' 칸에 그대로 들어갈 문장."""
    name = dst.name
    if kind is ActionKind.NAVIGATE:
        return f"{name} 화면이 표시된다"
    if kind is ActionKind.OPEN:
        return f"{name} 팝업이 표시된다"
    if kind is ActionKind.CONFIRM:
        return f"동작이 실행되고 {name} 화면이 표시된다"
    if kind is ActionKind.CANCEL:
        return f"변경사항이 반영되지 않고 {name} 화면으로 돌아간다"
    if kind is ActionKind.BACK:
        return f"{name} 화면으로 돌아간다"
    return f"{name} 화면으로 전환된다"


def _guess_consumes(label: str, dst: ScreenState) -> str:
    """재화 소모 가능성. **재화 이름까지는 모르므로 빈 값을 두고 사용자가 채운다.**

    여기서 "재화"라고 뭉뚱그려 채워 넣으면 TC에 "재화가 차감된다"는 모호한
    문장이 들어간다. 모르는 것은 비워두는 편이 낫다.
    """
    return ""


def suggest_for_element(
    graph: FlowGraph, state: ScreenState, element: UIElement
) -> IconSuggestion:
    """요소 하나에 대한 초안. 그 요소를 클릭해 발생한 전이를 찾아 역추적한다.

    캔버스에서 아이콘을 더블클릭했을 때 호출된다 — 클릭 기록이 있으면 그 결과를,
    없으면 빈 초안을 준다.
    """
    for transition in graph.outgoing(state.id):
        target = transition.target_element
        if target is not None and target.rect.iou(element.rect) > 0.5:
            return suggest_from_transition(graph, transition, None, element)

    return IconSuggestion(
        action=IconAction(),
        name_hint=_element_text(element),
        rationale="이 요소를 클릭한 기록이 없어 동작을 추론할 수 없습니다",
    )


def _element_text(element: UIElement | None) -> str:
    if element is None:
        return ""
    return (element.label or element.text).strip()


def pending_icons(graph: FlowGraph) -> list[tuple[Transition, ScreenState]]:
    """등록하면 가치가 큰 순서로 전이를 정렬한다.

    아이콘이 수십 개일 때 어디부터 손대야 하는지 알려주기 위한 것이다.
    자주 지나간 경로일수록, 그리고 클릭 지점에 텍스트 없는 요소가 있을수록 우선한다.
    """
    scored: list[tuple[float, Transition, ScreenState]] = []
    for transition in graph.transitions:
        src = graph.states.get(transition.from_state)
        if src is None or src.user.hidden:
            continue
        target = transition.target_element
        if target is None or target.text.strip() or target.label:
            continue  # 텍스트가 있으면 이미 알아볼 수 있다
        scored.append((float(transition.observed_count), transition, src))
    scored.sort(key=lambda t: -t[0])
    return [(t, s) for _, t, s in scored]

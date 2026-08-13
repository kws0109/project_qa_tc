"""리뷰 채팅 에이전트 — 사용자와 대화하며 그래프를 함께 정리한다.

여기가 **LLM이 앱 상태를 실제로 바꾸는 유일한 지점**이다. 다른 모듈은 JSON을
돌려받아 코드가 반영하지만, 채팅은 대화 흐름에 따라 무엇을 바꿀지가 정해지므로
도구 호출로 처리한다.

**도구 실행 루프를 직접 돈다.** SDK의 tool runner 대신 수동 루프를 쓰는 이유는,
각 도구 호출이 GUI 상태를 바꾸므로 호출 사이에 UI를 갱신하고 실패를 사용자에게
보여줘야 하기 때문이다. 도구 결과를 그대로 되돌려주기만 하는 자동 루프로는
"무엇이 바뀌었는지"를 화면에 반영할 수 없다.

**대화 이력이 캐시 접두사가 된다.** 시스템 블록은 고정이고 메시지는 뒤에 쌓이므로,
턴이 늘어도 이전 이력은 캐시에서 읽힌다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from ..config import MODEL_DEEP
from ..models import FlowGraph, ScreenState
from ..profiles import GameProfile
from . import prompts, schemas
from .client import EDGE_DETAIL, LlmClient, LlmError, encode_image, text_block

ImageLoader = Callable[[str], "np.ndarray | None"]

#: 도구 호출 루프 상한. 무한 루프 방지.
MAX_TOOL_ROUNDS = 8


@dataclass
class ToolEffect:
    """도구 호출 하나가 실제로 무엇을 바꿨는지. GUI가 이걸 보고 화면을 갱신한다."""

    tool: str
    ok: bool
    message: str
    changed_states: list[str] = field(default_factory=list)


@dataclass
class ChatTurn:
    """한 번의 대화 왕복 결과."""

    reply: str
    effects: list[ToolEffect] = field(default_factory=list)
    error: str | None = None

    @property
    def changed(self) -> bool:
        return any(e.ok for e in self.effects)


class ReviewChat:
    """리뷰 워크스페이스의 대화 세션.

    그래프를 직접 들고 있으면서 도구 호출을 그 위에 적용한다. GUI는
    :meth:`send`를 부르고 반환된 :class:`ChatTurn`을 보고 화면을 다시 그린다.
    """

    def __init__(
        self,
        client: LlmClient,
        graph: FlowGraph,
        profile: GameProfile,
        load_image: ImageLoader,
        *,
        model: str = MODEL_DEEP,
        on_change: Callable[[], None] | None = None,
    ):
        self.client = client
        self.graph = graph
        self.profile = profile
        self.load_image = load_image
        self.model = model
        self.on_change = on_change or (lambda: None)
        self.messages: list[dict[str, Any]] = []

    # -- 시스템 프롬프트 ---------------------------------------------

    def _system(self) -> list[dict]:
        return [
            text_block(prompts.QA_PERSONA),
            text_block(prompts.game_context_block(self.profile.name, self.profile.llm_context)),
            text_block(prompts.CHAT_SYSTEM, cache=True),
        ]

    # -- 대화 --------------------------------------------------------

    def send(self, user_text: str, focus_state_id: str | None = None) -> ChatTurn:
        """사용자 메시지를 보내고 도구 호출까지 처리한다.

        :param focus_state_id: 사용자가 지금 보고 있는 화면. 있으면 그 화면의
            스크린샷과 상세 정보를 함께 보낸다 — "이 화면 뭐야?"가 통하게 만든다.
        """
        content: list[dict] = []
        if focus_state_id:
            content.extend(self._focus_blocks(focus_state_id))
        content.append(text_block(user_text))
        self.messages.append({"role": "user", "content": content})

        effects: list[ToolEffect] = []
        reply_parts: list[str] = []

        try:
            for _ in range(MAX_TOOL_ROUNDS):
                response = self.client.converse(
                    purpose="리뷰 채팅",
                    model=self.model,
                    system=self._system(),
                    messages=self.messages,
                    tools=schemas.CHAT_TOOLS,
                    max_tokens=6000,
                )
                self.messages.append({"role": "assistant", "content": response.content})

                for block in response.content:
                    if block.type == "text" and block.text.strip():
                        reply_parts.append(block.text.strip())

                tool_uses = [b for b in response.content if b.type == "tool_use"]
                if not tool_uses:
                    break

                results = []
                for call in tool_uses:
                    effect = self._apply_tool(call.name, dict(call.input))
                    effects.append(effect)
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call.id,
                            "content": effect.message,
                            "is_error": not effect.ok,
                        }
                    )
                self.messages.append({"role": "user", "content": results})
                if any(e.ok for e in effects):
                    self.on_change()
        except LlmError as exc:
            return ChatTurn(reply="", effects=effects, error=str(exc))

        return ChatTurn(reply="\n\n".join(reply_parts), effects=effects)

    def _focus_blocks(self, state_id: str) -> list[dict]:
        state = self.graph.states.get(state_id)
        if state is None:
            return []
        blocks: list[dict] = [text_block(f"[담당자가 현재 보고 있는 화면: {state_id}]")]
        img = self.load_image(state.auto.exemplar_frame_id)
        if img is not None:
            blocks.append(encode_image(img, max_edge=EDGE_DETAIL))
        blocks.append(text_block(self._state_summary(state)))
        return blocks

    def _state_summary(self, state: ScreenState) -> str:
        lines = [
            f"화면 ID: {state.id}",
            f"현재 이름: {state.name} ({'담당자 확정' if state.user.name else 'LLM 추정'})",
            f"분류: {state.category}",
        ]
        if state.role:
            lines.append(f"역할: {state.role}")
        if state.user.notes:
            lines.append(f"메모: {state.user.notes}")

        elements = state.elements_sorted()
        if elements:
            lines.append("검출된 UI 요소 (인덱스: 내용 @ 위치):")
            for i, e in enumerate(elements[:20]):
                lines.append(f"  {i}: {e.display} @ ({e.rect.cx:.2f}, {e.rect.cy:.2f})")

        outgoing = self.graph.outgoing(state.id)
        if outgoing:
            lines.append("이 화면에서 나가는 전이:")
            for t in outgoing[:10]:
                dst = self.graph.states.get(t.to_state)
                lines.append(
                    f"  [{t.id}] {t.action_desc} → {dst.name if dst else t.to_state}"
                )
        incoming = self.graph.incoming(state.id)
        if incoming:
            lines.append("이 화면으로 들어오는 전이:")
            for t in incoming[:10]:
                src = self.graph.states.get(t.from_state)
                lines.append(
                    f"  [{t.id}] {src.name if src else t.from_state} → {t.action_desc}"
                )
        return "\n".join(lines)

    # -- 도구 실행 ---------------------------------------------------

    def _apply_tool(self, name: str, args: dict[str, Any]) -> ToolEffect:
        """도구 호출을 그래프에 적용한다.

        실패해도 예외를 던지지 않고 ``is_error`` 결과로 모델에게 알린다 —
        모델이 스스로 바로잡을 수 있게 하는 편이 대화를 끊는 것보다 낫다.
        """
        handler = {
            "rename_state": self._t_rename,
            "merge_states": self._t_merge,
            "add_note": self._t_note,
            "mark_element": self._t_mark,
            "hide_state": self._t_hide,
            "delete_transition": self._t_del_transition,
        }.get(name)

        if handler is None:
            return ToolEffect(name, False, f"알 수 없는 도구입니다: {name}")
        try:
            return handler(args)
        except Exception as exc:  # 도구 하나가 채팅 세션을 죽이면 안 된다
            return ToolEffect(name, False, f"실행 중 오류: {exc}")

    def _state_or_error(self, state_id: str) -> tuple[ScreenState | None, ToolEffect | None]:
        state = self.graph.states.get(state_id)
        if state is None:
            available = ", ".join(sorted(self.graph.states)[:12])
            return None, ToolEffect(
                "", False, f"화면 '{state_id}'을(를) 찾을 수 없습니다. 존재하는 화면: {available}"
            )
        return state, None

    def _t_rename(self, args: dict[str, Any]) -> ToolEffect:
        state, err = self._state_or_error(str(args.get("state_id", "")))
        if err:
            return ToolEffect("rename_state", False, err.message)
        assert state is not None
        name = str(args.get("name", "")).strip()
        if not name:
            return ToolEffect("rename_state", False, "이름이 비어 있습니다")
        state.user.name = name
        if str(args.get("category", "")).strip():
            state.user.category = str(args["category"]).strip()
        if str(args.get("role", "")).strip():
            state.user.role = str(args["role"]).strip()
        return ToolEffect(
            "rename_state", True, f"{state.id} 이름을 '{name}'(으)로 확정했습니다.", [state.id]
        )

    def _t_merge(self, args: dict[str, Any]) -> ToolEffect:
        keep = str(args.get("keep_id", ""))
        absorb = str(args.get("absorb_id", ""))
        if keep == absorb:
            return ToolEffect("merge_states", False, "같은 화면을 자기 자신과 병합할 수 없습니다")
        for sid in (keep, absorb):
            _, err = self._state_or_error(sid)
            if err:
                return ToolEffect("merge_states", False, err.message)
        keep_name = self.graph.states[keep].name
        absorb_name = self.graph.states[absorb].name
        self.graph.merge_states(keep, absorb)
        return ToolEffect(
            "merge_states",
            True,
            f"'{absorb_name}'({absorb})을 '{keep_name}'({keep})에 병합했습니다. "
            f"두 화면을 오가던 전이는 자기 전이가 되었으니 불필요하면 delete_transition으로 지우세요.",
            [keep],
        )

    def _t_note(self, args: dict[str, Any]) -> ToolEffect:
        state, err = self._state_or_error(str(args.get("state_id", "")))
        if err:
            return ToolEffect("add_note", False, err.message)
        assert state is not None
        note = str(args.get("note", "")).strip()
        if not note:
            return ToolEffect("add_note", False, "메모가 비어 있습니다")
        state.user.notes = "\n".join(x for x in (state.user.notes, note) if x)
        return ToolEffect("add_note", True, f"{state.id}에 메모를 추가했습니다.", [state.id])

    def _t_mark(self, args: dict[str, Any]) -> ToolEffect:
        state, err = self._state_or_error(str(args.get("state_id", "")))
        if err:
            return ToolEffect("mark_element", False, err.message)
        assert state is not None
        elements = state.elements_sorted()
        idx = int(args.get("element_index", -1))
        if not (0 <= idx < len(elements)):
            return ToolEffect(
                "mark_element",
                False,
                f"요소 인덱스 {idx}가 범위를 벗어났습니다 (0~{len(elements) - 1})",
            )
        label = str(args.get("label", "")).strip()
        if not label:
            return ToolEffect("mark_element", False, "라벨이 비어 있습니다")
        elements[idx].label = label
        elements[idx].source = "user"
        return ToolEffect(
            "mark_element", True, f"{state.id}의 {idx}번 요소를 '{label}'로 표시했습니다.", [state.id]
        )

    def _t_hide(self, args: dict[str, Any]) -> ToolEffect:
        state, err = self._state_or_error(str(args.get("state_id", "")))
        if err:
            return ToolEffect("hide_state", False, err.message)
        assert state is not None
        state.user.hidden = bool(args.get("hidden", True))
        verb = "TC 생성에서 제외" if state.user.hidden else "TC 생성에 포함"
        return ToolEffect("hide_state", True, f"{state.id}을(를) {verb}했습니다.", [state.id])

    def _t_del_transition(self, args: dict[str, Any]) -> ToolEffect:
        tid = str(args.get("transition_id", ""))
        if not any(t.id == tid for t in self.graph.transitions):
            return ToolEffect("delete_transition", False, f"전이 '{tid}'을(를) 찾을 수 없습니다")
        self.graph.delete_transition(tid)
        return ToolEffect("delete_transition", True, f"전이 {tid}을(를) 제거했습니다.")

    # -- 이력 --------------------------------------------------------

    def reset(self) -> None:
        """대화를 새로 시작한다. 그래프 변경은 유지된다."""
        self.messages.clear()

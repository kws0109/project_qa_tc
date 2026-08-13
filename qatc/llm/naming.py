"""화면 명명 — 스크린샷을 보고 화면의 정체를 판단한다.

**배치로 처리한다.** 화면 하나에 호출 하나면 세션당 수십 번인데, 매번 시스템
프롬프트를 다시 보낸다(캐시가 있어도 왕복 지연은 그대로다). 한 번에 여러 장을
보내면 호출 수가 1/N로 줄고 모델이 화면들을 서로 비교하며 판단할 수 있어
이름의 일관성도 좋아진다 — "캐릭터"와 "캐릭터 목록"이 따로 나오는 일이 줄어든다.

배치가 너무 크면 응답이 ``max_tokens``에서 잘리므로 6장 정도가 균형점이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from ..config import MODEL_BULK
from ..models import FlowGraph, LlmGuess, ScreenState
from ..profiles import GameProfile
from . import prompts, schemas
from .client import EDGE_BULK, LlmClient, LlmError, encode_image, text_block

#: 한 번에 명명할 화면 수. 늘리면 호출이 줄지만 응답 잘림 위험이 커진다.
BATCH_SIZE = 6

#: 이미지 로더 — 프레임 ID를 BGR 이미지로. 파이프라인이 주입한다.
ImageLoader = Callable[[str], "np.ndarray | None"]


@dataclass
class NamingReport:
    named: int = 0
    skipped_locked: int = 0
    failed: int = 0
    notes: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = []

    def summary(self) -> str:
        parts = [f"화면 {self.named}개 명명"]
        if self.skipped_locked:
            parts.append(f"사용자 확정 {self.skipped_locked}개 유지")
        if self.failed:
            parts.append(f"실패 {self.failed}개")
        return " · ".join(parts)


def name_states(
    client: LlmClient,
    graph: FlowGraph,
    profile: GameProfile,
    load_image: ImageLoader,
    *,
    model: str = MODEL_BULK,
    batch_size: int = BATCH_SIZE,
    on_progress: Callable[[str, float], None] | None = None,
) -> NamingReport:
    """그래프의 모든 화면에 이름을 붙인다.

    **사용자가 확정한 화면은 건드리지 않는다.** ``user.name``이 있거나 ``locked``이면
    LLM을 아예 호출하지 않는다 — 재생성해도 사용자 작업이 살아남는다는 보장은
    데이터 모델의 우선순위만으로는 부족하고, 호출 자체를 건너뛰어야 비용도 아낀다.
    """
    report = NamingReport()
    progress = on_progress or (lambda _m, _p: None)

    targets: list[ScreenState] = []
    for state in graph.states.values():
        if state.user.name or state.user.locked:
            report.skipped_locked += 1
            continue
        targets.append(state)

    if not targets:
        return report

    system = _system_blocks(profile)
    total = len(targets)
    done = 0

    for start in range(0, total, batch_size):
        batch = targets[start : start + batch_size]
        progress(f"화면 명명 중 ({done}/{total})", done / total)
        try:
            _name_batch(client, batch, system, load_image, model, report)
        except LlmError as exc:
            report.failed += len(batch)
            report.notes.append(str(exc))
        done += len(batch)

    progress("화면 명명 완료", 1.0)
    return report


def _system_blocks(profile: GameProfile) -> list[dict]:
    """시스템 프롬프트. **마지막 블록에 캐시 표시**를 달아 접두사 전체를 캐시한다.

    페르소나 → 게임 컨텍스트 → 작업 지시 순서는 고정이다. 이 순서가 바뀌거나
    중간에 가변 내용이 끼면 캐시가 매번 무효화된다.
    """
    return [
        text_block(prompts.QA_PERSONA),
        text_block(prompts.game_context_block(profile.name, profile.llm_context)),
        text_block(prompts.NAMING_TASK, cache=True),
    ]


def _name_batch(
    client: LlmClient,
    batch: list[ScreenState],
    system: list[dict],
    load_image: ImageLoader,
    model: str,
    report: NamingReport,
) -> None:
    content: list[dict] = []
    index_map: dict[int, ScreenState] = {}

    for i, state in enumerate(batch):
        img = load_image(state.auto.exemplar_frame_id)
        if img is None:
            report.failed += 1
            continue
        index_map[i] = state
        content.append(text_block(f"--- 화면 {i} ---"))
        content.append(encode_image(img, max_edge=EDGE_BULK))
        hint = _hint_text(state)
        if hint:
            content.append(text_block(hint))

    if not index_map:
        return

    content.append(
        text_block(f"위 {len(index_map)}개 화면 각각에 대해 판단 결과를 돌려주세요.")
    )

    result = client.structured(
        purpose="화면 명명",
        model=model,
        system=system,
        content=content,
        schema=schemas.SCREEN_NAMING,
        max_tokens=4000,
    )

    for item in result.data.get("screens", []):
        state = index_map.get(int(item.get("index", -1)))
        if state is None:
            continue
        state.llm = LlmGuess(
            name=str(item.get("name", "")).strip(),
            role=str(item.get("role", "")).strip(),
            category=str(item.get("category", "")).strip(),
            confidence=float(item.get("confidence", 0.0)),
            model=model,
        )
        _apply_element_labels(state, item.get("key_elements") or [])
        report.named += 1


def _hint_text(state: ScreenState) -> str:
    """OCR 텍스트를 힌트로 준다.

    이미지만으로도 대부분 읽히지만, 작은 글씨나 저해상도 캡처에서는 OCR이
    보완한다. 반대로 OCR이 틀렸을 때 모델이 이미지를 보고 바로잡을 수 있으므로
    참고 자료임을 명시한다.
    """
    tokens = state.auto.text_sig[:14]
    if not tokens:
        return ""
    return (
        "화면에서 자동 인식된 텍스트(오인식 가능, 참고용): "
        + ", ".join(tokens)
    )


def _apply_element_labels(state: ScreenState, key_elements: list) -> None:
    """LLM이 지목한 핵심 요소 이름을, 검출된 요소 중 가장 가까운 것에 붙인다.

    LLM은 요소의 좌표를 모르고 이름만 말한다. 검출된 요소 중 OCR 텍스트가
    일치하는 것을 찾아 연결한다. 못 찾으면 무시한다 — 억지로 붙이면
    엉뚱한 좌표에 이름이 달려 TC 문구가 틀어진다.
    """
    labels = [str(x).strip() for x in key_elements if str(x).strip()]
    if not labels:
        return
    for label in labels:
        norm = label.replace(" ", "").lower()
        for el in state.auto.elements:
            if el.label:
                continue
            text = el.text.replace(" ", "").lower()
            if text and (text in norm or norm in text):
                el.label = label
                break

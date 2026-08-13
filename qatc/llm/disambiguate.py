"""화면 동일성 판별 — 클러스터링이 애매하다고 표시한 쌍만 LLM에게 묻는다.

**전수 조사를 하지 않는 것이 핵심이다.** 프레임 200장이면 쌍이 2만 개인데 전부
물어보면 세션 하나에 수백 달러가 나온다. 결합 유사도가 회색 구간(0.55~0.80)인
쌍만, 그것도 경계에 가까운 것부터 상한까지만 묻는다.

이 모듈은 :mod:`qatc.analyze.cluster`가 요구하는 ``DisambiguateFn`` 시그니처
(``list[(i, j)] -> dict[(i, j), bool]``)를 만족하는 콜백을 만들어 준다.
분석 계층이 LLM 계층에 의존하지 않도록 방향을 뒤집은 것이다 —
LLM 없이도 파이프라인이 돌아간다.
"""

from __future__ import annotations

from typing import Callable, Sequence

import numpy as np

from ..config import MODEL_BULK
from ..profiles import GameProfile
from . import prompts, schemas
from .client import EDGE_BULK, LlmClient, LlmError, encode_image, text_block

#: 한 번에 판별할 쌍 수. 쌍마다 이미지 2장이라 배치를 크게 잡으면 금방 비싸진다.
PAIR_BATCH = 4

ImageLoader = Callable[[str], "np.ndarray | None"]


def make_disambiguator(
    client: LlmClient,
    frame_ids: Sequence[str],
    load_image: ImageLoader,
    profile: GameProfile,
    *,
    model: str = MODEL_BULK,
    on_progress: Callable[[str, float], None] | None = None,
) -> Callable[[list[tuple[int, int]]], dict[tuple[int, int], bool]]:
    """클러스터링에 주입할 판별 콜백을 만든다.

    :param frame_ids: 프레임 인덱스 → 프레임 ID. 클러스터링은 인덱스로 말하고
        여기서는 이미지가 필요하므로 변환표가 필요하다.
    """
    system = [
        text_block(prompts.QA_PERSONA),
        text_block(prompts.game_context_block(profile.name, profile.llm_context)),
        text_block(prompts.DISAMBIGUATE_TASK, cache=True),
    ]
    progress = on_progress or (lambda _m, _p: None)

    def disambiguate(pairs: list[tuple[int, int]]) -> dict[tuple[int, int], bool]:
        verdicts: dict[tuple[int, int], bool] = {}
        total = len(pairs)
        for start in range(0, total, PAIR_BATCH):
            batch = pairs[start : start + PAIR_BATCH]
            progress(f"화면 동일성 판별 중 ({start}/{total})", start / max(1, total))
            try:
                verdicts.update(_judge_batch(client, batch, system, frame_ids, load_image, model))
            except LlmError:
                # 판별에 실패한 쌍은 그냥 넘긴다. 클러스터링은 원래 점수로 판단하고,
                # 결과가 애매하면 사용자가 리뷰에서 정리한다. LLM 장애가 분석
                # 전체를 멈추게 두면 안 된다.
                continue
        return verdicts

    return disambiguate


def _judge_batch(
    client: LlmClient,
    batch: list[tuple[int, int]],
    system: list[dict],
    frame_ids: Sequence[str],
    load_image: ImageLoader,
    model: str,
) -> dict[tuple[int, int], bool]:
    content: list[dict] = []
    pair_map: dict[int, tuple[int, int]] = {}

    for n, (i, j) in enumerate(batch):
        if not (0 <= i < len(frame_ids) and 0 <= j < len(frame_ids)):
            continue
        img_a = load_image(frame_ids[i])
        img_b = load_image(frame_ids[j])
        if img_a is None or img_b is None:
            continue
        pair_map[n] = (i, j)
        content.append(text_block(f"--- 쌍 {n} / 이미지 A ---"))
        content.append(encode_image(img_a, max_edge=EDGE_BULK))
        content.append(text_block(f"--- 쌍 {n} / 이미지 B ---"))
        content.append(encode_image(img_b, max_edge=EDGE_BULK))

    if not pair_map:
        return {}

    content.append(text_block(f"위 {len(pair_map)}개 쌍 각각을 판단해 주세요."))

    result = client.structured(
        purpose="화면 동일성 판별",
        model=model,
        system=system,
        content=content,
        schema=schemas.SCREEN_SAME,
        max_tokens=2000,
    )

    out: dict[tuple[int, int], bool] = {}
    for item in result.data.get("verdicts", []):
        pair = pair_map.get(int(item.get("pair_index", -1)))
        if pair is None:
            continue
        out[pair] = bool(item.get("same_screen", False))
    return out

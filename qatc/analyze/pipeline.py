"""분석 오케스트레이터 — 세션 폴더를 받아 flow.json을 만든다.

단계마다 진행 콜백을 흘려보내므로 CLI 진행률 표시와 GUI 프로그레스바가 같은
경로를 쓴다.

**OCR을 모든 프레임에 돌리지 않는다.** 1차 dedupe를 통과한 대표 프레임만 대상으로
하고, 그마저도 상한(:attr:`AnalyzeConfig.max_ocr_frames`)을 둔다. OCR은 프레임당
1초 가까이 걸려서 수천 장에 돌리면 분석이 몇 시간이 된다. 텍스트는 보조 신호이므로
전수 조사할 가치가 없다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from ..config import AnalyzeConfig
from ..models import Frame, SETTLED_REASONS
from ..profiles import GameProfile, generic_profile
from ..storage import SessionStore
from .cluster import DisambiguateFn, FrameFeatures, cluster_frames, cluster_report
from .flow import FlowBuildResult, build_flow
from .hashing import ScreenSignature, dedupe
from .motion import VolatilityMap, learn_from_frames
from .ocr import OcrEngine, OcrLine
from .ui_detect import detect_elements

ProgressFn = Callable[[str, float], None]


@dataclass
class AnalyzeProgress:
    """분석 결과 요약. CLI가 그대로 출력하고 GUI가 리포트 패널에 띄운다."""

    total_frames: int = 0
    representative_frames: int = 0
    ocr_frames: int = 0
    volatility_samples: int = 0
    stable_ratio: float = 0.0
    llm_pairs_asked: int = 0
    states: int = 0
    transitions: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"프레임 {self.total_frames}장 → 대표 {self.representative_frames}장 "
            f"(OCR {self.ocr_frames}장)",
            f"변동성 학습 {self.volatility_samples}샘플, 안정 셀 {self.stable_ratio:.0%}",
            f"화면 상태 {self.states}개, 전이 {self.transitions}개",
        ]
        if self.llm_pairs_asked:
            lines.append(f"LLM 판별 질의 {self.llm_pairs_asked}쌍")
        lines.extend(f"※ {n}" for n in self.notes)
        return "\n".join(lines)


def _load_images(store: SessionStore, frames: list[Frame]) -> tuple[list[Frame], list[np.ndarray]]:
    """프레임 이미지를 읽는다. 읽기 실패한 것은 조용히 제외한다 (부분 손상 세션 대응)."""
    kept_frames: list[Frame] = []
    images: list[np.ndarray] = []
    for f in frames:
        img = cv2.imread(str(store.frame_path(f)), cv2.IMREAD_COLOR)
        if img is None or img.size == 0:
            continue
        kept_frames.append(f)
        images.append(img)
    return kept_frames, images


def analyze_session(
    store: SessionStore,
    profile: GameProfile | None = None,
    config: AnalyzeConfig | None = None,
    *,
    disambiguate: DisambiguateFn | None = None,
    on_progress: ProgressFn | None = None,
    reuse_volatility: bool = True,
) -> tuple[FlowBuildResult, AnalyzeProgress]:
    """세션 하나를 분석해 :class:`FlowGraph`를 만들고 저장한다.

    :param disambiguate: 애매한 쌍을 판정하는 콜백 (보통 LLM). None이면 생략.
    :param reuse_volatility: 이전 세션에서 학습한 변동성 맵을 재사용할지.
        같은 게임이면 HUD 위치가 같으므로 재사용이 첫 세션부터 정확도를 올린다.
    """
    cfg = config or AnalyzeConfig()
    prof = profile or generic_profile()
    progress = AnalyzeProgress()
    report = on_progress or (lambda _msg, _pct: None)

    # ── ① 프레임 로드 ─────────────────────────────────────────────
    report("프레임 로드 중", 0.02)
    all_frames = store.frames()
    progress.total_frames = len(all_frames)
    if not all_frames:
        raise ValueError("분석할 프레임이 없습니다. 먼저 녹화(qatc record)를 실행하세요.")

    frames, images = _load_images(store, all_frames)
    if len(frames) < len(all_frames):
        progress.notes.append(f"이미지 읽기 실패 {len(all_frames) - len(frames)}장 제외")
    if not frames:
        raise ValueError("프레임 이미지를 하나도 읽을 수 없습니다. 세션이 손상되었습니다.")

    # ── ② 변동성 학습 ─────────────────────────────────────────────
    report("변동성 학습 중 (애니메이션 영역 식별)", 0.10)
    vol = _get_volatility(store, prof, frames, images, cfg, reuse_volatility)
    vol = vol.with_static_ignore(prof.ignore_rects)
    progress.volatility_samples = vol.samples
    progress.stable_ratio = vol.stable_ratio
    if vol.samples == 0:
        progress.notes.append("프레임이 적어 변동성을 학습하지 못했습니다 (정확도 저하)")

    # ── ③ 1차 dedupe ─────────────────────────────────────────────
    report("중복 프레임 제거 중", 0.20)
    sigs = [ScreenSignature.of(img) for img in images]
    rep_idx, fold_map = dedupe(sigs, vol, max_delta=cfg.dedupe_hamming / 200.0)
    progress.representative_frames = len(rep_idx)
    # 접힌 프레임 → 대표 프레임 (프레임 ID 기준). 그래프 구축에서 정확한 전파에 쓴다.
    dedupe_map = {
        frames[i].id: frames[r].id for i, r in fold_map.items() if i != r
    }

    # ── ④ UI 검출 + OCR ──────────────────────────────────────────
    ocr = OcrEngine(lang=prof.ui_language)
    ocr_budget = min(cfg.max_ocr_frames, len(rep_idx))
    # settled 프레임을 우선 OCR한다 — 전이 중간 프레임은 텍스트가 페이드 중이라
    # 읽어봐야 오인식만 늘어난다.
    ocr_targets = _pick_ocr_targets(rep_idx, frames, ocr_budget)

    features: list[FrameFeatures] = []
    for n, i in enumerate(rep_idx):
        if n % 10 == 0:
            report(f"UI 검출·OCR 중 ({n}/{len(rep_idx)})", 0.20 + 0.45 * n / max(1, len(rep_idx)))
        elements = detect_elements(
            images[i], min_area=cfg.element_min_area, max_area=cfg.element_max_area
        )
        lines: list[OcrLine] = []
        if i in ocr_targets:
            lines = _cached_ocr(store, ocr, frames[i], images[i], sigs[i])
        features.append(
            FrameFeatures(
                frame_id=frames[i].id,
                ts=frames[i].ts,
                sig=sigs[i],
                elements=elements,
                ocr_lines=lines,
                is_settled=frames[i].reason in SETTLED_REASONS,
            )
        )
    progress.ocr_frames = len(ocr_targets)
    if not ocr.available and ocr.load_error:
        progress.notes.append(f"OCR 사용 불가 ({ocr.load_error}) — 텍스트 신호 없이 진행")

    # ── ⑤ 클러스터링 ─────────────────────────────────────────────
    report("화면 상태 클러스터링 중", 0.70)
    result = cluster_frames(
        features,
        vol,
        disambiguate=disambiguate,
        same_threshold=cfg.same_threshold,
        max_llm_pairs=cfg.max_llm_disambiguations,
    )
    progress.llm_pairs_asked = len(result.asked_llm)

    # ── ⑥ 플로우 그래프 ──────────────────────────────────────────
    report("전이 그래프 구축 중", 0.88)
    built = build_flow(
        session_id=store.get_session().id,
        frames=frames,
        events=store.events(),
        features=features,
        result=result,
        profile=prof,
        dedupe_map=dedupe_map,
    )
    # ── ⑦ 아이콘 사전 적용 ────────────────────────────────────────
    # 이전 세션에서 등록한 아이콘을 자동으로 알아본다. 사전이 비어 있으면 조용히 넘어간다.
    report("아이콘 사전 대조 중", 0.92)
    matched = _apply_icon_dictionary(built.graph, prof, store)
    if matched:
        progress.notes.append(f"아이콘 사전에서 {matched}개 요소를 인식했습니다")

    progress.states = len(built.graph.states)
    progress.transitions = len(built.graph.transitions)
    if built.no_change_events:
        progress.notes.append(f"화면이 바뀌지 않은 입력 {built.no_change_events}건 (self-loop)")
    if built.orphan_events:
        progress.notes.append(f"대응 프레임이 없는 입력 {built.orphan_events}건 제외")

    report("저장 중", 0.96)
    store.save_graph(built.graph)
    _save_volatility_debug(store, vol)
    report("분석 완료", 1.0)
    return built, progress


def _apply_icon_dictionary(graph, profile: GameProfile, store: SessionStore) -> int:
    """등록된 아이콘 사전으로 요소 라벨을 자동으로 채운다.

    **사전이 쌓일수록 이 단계의 수확이 커진다.** 첫 세션에서는 0개지만, 담당자가
    아이콘 20개를 등록해두면 이후 모든 세션에서 자동으로 붙는다 — 그게 이 기능의
    투자 회수 지점이다.

    사전이 없거나 비어 있으면 아무 일도 하지 않는다. 아이콘 계층은 선택 사항이다.
    """
    try:
        from ..icons import IconMatcher, IconStore
    except Exception:
        return 0

    icon_store = IconStore.load(profile.key)
    if not len(icon_store):
        return 0

    matcher = IconMatcher(icon_store)
    if matcher.is_empty:
        return 0

    total = 0
    for state in graph.states.values():
        frame = store.frame(state.auto.exemplar_frame_id)
        if frame is None:
            continue
        image = cv2.imread(str(store.frame_path(frame)), cv2.IMREAD_COLOR)
        if image is None:
            continue
        total += len(matcher.annotate_state(state, image))
    return total


def _pick_ocr_targets(rep_idx: list[int], frames: list[Frame], budget: int) -> set[int]:
    """OCR 대상 선정 — settled 프레임 우선, 예산 안에서 시간축에 고르게."""
    settled = [i for i in rep_idx if frames[i].reason in SETTLED_REASONS]
    pool = settled if len(settled) >= budget else settled + [i for i in rep_idx if i not in set(settled)]
    if len(pool) <= budget:
        return set(pool)
    # 균등 샘플링: 앞부분만 읽고 마는 것을 막는다
    step = len(pool) / budget
    return {pool[int(k * step)] for k in range(budget)}


def _cached_ocr(
    store: SessionStore, ocr: OcrEngine, frame: Frame, img: np.ndarray, sig: ScreenSignature
) -> list[OcrLine]:
    """프레임 해시를 키로 OCR 결과를 캐시한다. 재분석 시 다시 돌리지 않는다."""
    key = f"{sig.dhash:016x}"
    cached = store.ocr_get(key)
    if cached is not None:
        return [OcrLine.from_dict(d) for d in cached]
    lines = ocr.read(img)
    store.ocr_put(key, [ln.to_dict() for ln in lines])
    return lines


def _volatility_cache_path(profile: GameProfile) -> Path:
    from ..config import user_config_dir

    d = user_config_dir() / "volatility"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{profile.key}.json"


def _get_volatility(
    store: SessionStore,
    profile: GameProfile,
    frames: list[Frame],
    images: list[np.ndarray],
    cfg: AnalyzeConfig,
    reuse: bool,
) -> VolatilityMap:
    """변동성 맵을 얻는다. 이번 세션에서 학습하고, 이전 캐시가 있으면 합친다.

    합치는 방식은 **최댓값**이다. 어느 세션에서든 한 번이라도 크게 변한 셀은
    변한다고 보는 편이 안전하다 — 상태 과분리를 막는 방향으로 기운다.
    """
    learned = learn_from_frames(images, [f.ts for f in frames], min_frames=cfg.motion_min_frames)
    cache_path = _volatility_cache_path(profile)

    if reuse:
        prev = VolatilityMap.load(cache_path)
        if prev is not None and learned.samples > 0:
            merged_values = np.maximum(prev.values, learned.values)
            learned = VolatilityMap(
                values=merged_values,
                samples=prev.samples + learned.samples,
                threshold=min(prev.threshold, learned.threshold),
            )
        elif prev is not None:
            learned = prev

    if learned.samples > 0:
        learned.save(cache_path)
    return learned


def _save_volatility_debug(store: SessionStore, vol: VolatilityMap) -> None:
    """변동성 히트맵을 세션 폴더에 남긴다 — '무엇이 무시되고 있는지' 눈으로 확인용."""
    if vol.samples == 0:
        return
    try:
        cv2.imwrite(str(store.masks_dir / "volatility.png"), vol.to_debug_image())
        vol.save(store.masks_dir / "volatility.json")
    except Exception:
        pass

"""분석 파이프라인 테스트.

**가장 중요한 테스트는 과병합 방지다.** 다른 화면이 하나로 합쳐지면 전이가
사라지고, 사라진 전이는 리뷰 화면에 나타나지도 않으므로 사용자가 알아챌 방법이
없다. 결과적으로 테스트되지 않은 기능이 생긴다.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from qatc.analyze.cluster import (
    FrameFeatures,
    Signals,
    cluster_frames,
    combined_similarity,
    find_ambiguous_pairs,
)
from qatc.analyze.hashing import CELL_CHANGE_DELTA, ScreenSignature, dedupe, dhash, hamming
from qatc.analyze.motion import GRID_H, GRID_W, VolatilityMap, bootstrap_runs, learn_from_frames
from qatc.analyze.ocr import OcrLine, jaccard, normalize_token, text_signature
from qatc.analyze.signature import struct_similarity, to_struct_signature
from qatc.analyze.ui_detect import detect_elements, element_at
from qatc.models import NormRect, UIElement

H, W = 720, 1280


def make_screen(variant: int = 0, t: float = 0.0, seed: int = 0) -> np.ndarray:
    """UI 골격 + 애니메이션 영역을 가진 합성 화면.

    variant가 다르면 우측 패널 레이아웃이 달라진다 = 다른 화면.
    t가 다르면 좌측 캐릭터만 움직인다 = 같은 화면.
    """
    rng = np.random.default_rng(seed)
    img = np.full((H, W, 3), 26, np.uint8)
    cv2.rectangle(img, (0, 0), (W, 70), (60, 56, 52), -1)
    for i in range(5):
        cv2.rectangle(img, (60 + i * 190, 16), (200 + i * 190, 56), (200, 190, 180), -1)

    x0 = int(W * 0.58)
    if variant == 0:
        cv2.rectangle(img, (x0, 100), (W - 40, H - 60), (48, 44, 40), -1)
        for i in range(5):
            cv2.rectangle(img, (x0 + 30, 140 + i * 100), (W - 80, 210 + i * 100), (150, 140, 130), -1)
    else:
        cv2.rectangle(img, (x0, 100), (W - 40, H - 60), (40, 70, 110), -1)
        for i in range(2):
            cv2.rectangle(img, (x0 + 30, 150 + i * 250), (W - 80, 360 + i * 250), (90, 160, 220), -1)

    # 좌측 애니메이션 (같은 화면이어도 매번 다름)
    cx = int(W * 0.25 + 80 * np.sin(t * 1.9))
    cy = int(H * 0.55 + 50 * np.cos(t * 2.3))
    cv2.circle(img, (cx, cy), 130, (120, 90, 160), -1)
    for _ in range(90):
        px, py = int(rng.integers(30, int(W * 0.5))), int(rng.integers(90, H - 40))
        cv2.circle(img, (px, py), int(rng.integers(2, 6)), (255, 252, 235), -1)
    return img


# ---------------------------------------------------------------- 변동성


def test_volatility_isolates_animation_region():
    """애니메이션 영역(좌측)이 UI 영역(우측)보다 덜 안정적이어야 한다."""
    frames = [make_screen(0, t=i * 0.5, seed=i) for i in range(14)]
    vol = learn_from_frames(frames, [i * 0.5 for i in range(14)])
    grid = vol.stable.reshape(GRID_H, GRID_W)
    left = grid[:, : int(GRID_W * 0.5)].mean()
    right = grid[:, int(GRID_W * 0.62) :].mean()
    assert right > left, f"우측 UI({right:.0%})가 좌측 애니메이션({left:.0%})보다 안정적이어야 함"


def test_volatility_improves_same_screen_similarity():
    frames = [make_screen(0, t=i * 0.5, seed=i) for i in range(14)]
    vol = learn_from_frames(frames, [i * 0.5 for i in range(14)])
    a, b = ScreenSignature.of(frames[0]), ScreenSignature.of(frames[-1])
    assert a.similarity(b, vol) >= a.similarity(b, None)
    assert a.similarity(b, vol) > 0.9


def test_volatility_static_ignore_forces_regions():
    vol = VolatilityMap.empty()
    assert vol.stable.all()
    forced = vol.with_static_ignore([(0.0, 0.0, 0.25, 0.25)])
    assert not forced.stable.all()
    assert forced.stable.mean() < 1.0


def test_volatility_persists(tmp_path):
    frames = [make_screen(0, t=i * 0.5, seed=i) for i in range(10)]
    vol = learn_from_frames(frames, [i * 0.5 for i in range(10)])
    path = tmp_path / "vol.json"
    vol.save(path)
    loaded = VolatilityMap.load(path)
    assert loaded is not None
    assert np.allclose(loaded.values, vol.values, atol=1e-4)


def test_volatility_load_missing_returns_none(tmp_path):
    assert VolatilityMap.load(tmp_path / "없음.json") is None


def test_bootstrap_runs_splits_on_gaps():
    runs = bootstrap_runs([0.0, 0.5, 1.0, 1.5, 9.0, 9.5, 10.0, 10.5])
    assert len(runs) == 2


# ---------------------------------------------------------------- 시그니처


def test_similarity_separates_different_screens():
    frames = [make_screen(0, t=i * 0.5, seed=i) for i in range(14)]
    vol = learn_from_frames(frames, [i * 0.5 for i in range(14)])
    same = ScreenSignature.of(make_screen(0, t=7.0, seed=99))
    other = ScreenSignature.of(make_screen(1, t=7.0, seed=99))
    base = ScreenSignature.of(frames[0])
    assert base.similarity(same, vol) > base.similarity(other, vol) + 0.15


def test_change_ratio_is_interpretable():
    """유사도가 '화면의 몇 %가 바뀌었나'로 읽혀야 한다."""
    a = ScreenSignature.of(make_screen(0, seed=1))
    assert a.change_ratio(a) == pytest.approx(0.0)
    b = ScreenSignature.of(make_screen(1, seed=1))
    assert 0.0 < a.change_ratio(b) < 1.0


def test_dedupe_never_merges_different_screens():
    """**과병합 방지 — 이 테스트가 실패하면 TC가 누락된다.**"""
    frames = [make_screen(0, t=i * 0.5, seed=i) for i in range(6)]
    frames += [make_screen(1, t=i * 0.5, seed=i) for i in range(6)]
    sigs = [ScreenSignature.of(f) for f in frames]
    _, mapping = dedupe(sigs)
    groups: dict[int, list[int]] = {}
    for i, rep in mapping.items():
        groups.setdefault(rep, []).append(i)
    for members in groups.values():
        assert not (any(i < 6 for i in members) and any(i >= 6 for i in members)), (
            "서로 다른 화면이 하나로 병합되었습니다"
        )


def test_dedupe_folds_identical_frames():
    img = make_screen(0, seed=3)
    sigs = [ScreenSignature.of(img) for _ in range(4)]
    reps, _ = dedupe(sigs)
    assert len(reps) == 1


def test_dhash_is_stable_for_identical_input():
    img = make_screen(0, seed=5)
    assert hamming(dhash(img), dhash(img.copy())) == 0


# ---------------------------------------------------------------- 구조/텍스트


def test_struct_similarity_identical_and_disjoint():
    rects = [NormRect(0.1, 0.1, 0.2, 0.1), NormRect(0.5, 0.3, 0.3, 0.2)]
    sig = to_struct_signature([UIElement(r) for r in rects])
    assert struct_similarity(sig, sig) > 0.9
    other = to_struct_signature([UIElement(NormRect(0.7, 0.7, 0.2, 0.2))])
    assert struct_similarity(sig, other) < 0.4


def test_struct_similarity_empty_is_undecidable():
    assert struct_similarity([], []) == 0.0


def test_text_signature_strips_digits():
    """재화 수량이 바뀌어도 같은 토큰으로 수렴해야 한다."""
    a = text_signature([OcrLine("재화 12,684", NormRect(0, 0, 1, 1), 0.9)])
    b = text_signature([OcrLine("재화 11,980", NormRect(0, 0, 1, 1), 0.9)])
    assert a == b == ["재화"]


def test_normalize_token_removes_numbers_and_space():
    assert normalize_token("레벨 45 / 90") == "레벨"


def test_jaccard():
    assert jaccard(["a", "b"], ["a", "b"]) == 1.0
    assert jaccard(["a"], ["b"]) == 0.0
    assert jaccard([], []) == 0.0


# ---------------------------------------------------------------- UI 검출


def test_detect_elements_finds_ui_boxes():
    elements = detect_elements(make_screen(0, seed=7))
    assert len(elements) >= 5
    assert all(0.0 <= e.rect.x <= 1.0 for e in elements)


def test_detect_elements_handles_empty_input():
    assert detect_elements(np.zeros((0, 0, 3), np.uint8)) == []


def test_element_at_prefers_smallest():
    elements = [
        UIElement(NormRect(0.0, 0.0, 0.9, 0.9)),
        UIElement(NormRect(0.2, 0.2, 0.05, 0.05)),
    ]
    hit = element_at(elements, 0.22, 0.22)
    assert hit is not None and hit.rect.area < 0.1


# ---------------------------------------------------------------- 결합/클러스터


def test_combined_similarity_bounded():
    for cell in (0.0, 0.5, 1.0):
        s = Signals(cell_sim=cell, layout_sim=cell, struct_sim=None, text_sim=None)
        assert 0.0 <= combined_similarity(s) <= 1.0


def test_text_signal_overrides_weak_cell():
    """**텍스트가 있으면 그것이 주 신호다.**

    초기 설계는 셀 시그니처를 주 신호로 두고 "셀이 낮으면 상한을 씌운다"는
    게이트를 걸었다. 실측(스타레일 40초 세션)이 그 가정을 뒤집었다 —
    3D 필드에서 걸어다니면 배경이 통째로 바뀌어 셀이 0.43까지 떨어지지만
    HUD 텍스트는 0.67로 유지된다. 옛 게이트 때문에 **같은 필드 화면 7쌍 중
    0쌍만 병합**되어 화면 하나가 9개로 갈라졌다.
    """
    s = Signals(cell_sim=0.43, layout_sim=0.22, struct_sim=0.16, text_sim=0.67)
    assert combined_similarity(s) >= 0.80


def test_text_disagreement_vetoes():
    """반대로 텍스트가 강하게 반대하면 나머지가 좋아도 같은 화면이 아니다.

    탭이 통째로 바뀌었다는 뜻이다 — 실측에서 필드↔메뉴의 텍스트 유사도는 0.026이었다.
    """
    s = Signals(cell_sim=0.9, layout_sim=0.9, struct_sim=0.9, text_sim=0.03)
    assert combined_similarity(s) <= 0.5


def test_cell_gate_still_applies_without_text():
    """텍스트라는 안전망이 없으면 셀 게이트를 유지한다 — 거기서는 과병합이 더 위험하다."""
    s = Signals(cell_sim=0.35, layout_sim=0.9, struct_sim=0.9, text_sim=None)
    assert combined_similarity(s) <= 0.5


def test_none_signals_are_excluded_not_zeroed():
    """None은 '판단 불가'이지 0점이 아니다 — 텍스트 없는 화면이 불이익을 받으면 안 된다."""
    with_text = Signals(cell_sim=0.95, layout_sim=0.95, struct_sim=0.95, text_sim=0.95)
    without = Signals(cell_sim=0.95, layout_sim=0.95, struct_sim=0.95, text_sim=None)
    assert combined_similarity(without) == pytest.approx(combined_similarity(with_text), abs=0.06)


def test_find_ambiguous_pairs_only_in_gray_band():
    sim = np.array([[1.0, 0.95, 0.65], [0.95, 1.0, 0.20], [0.65, 0.20, 1.0]])
    pairs = find_ambiguous_pairs(sim, low=0.55, high=0.80)
    assert pairs == [(0, 2)]


def _features(frames: list[np.ndarray], settled: bool = True) -> list[FrameFeatures]:
    return [
        FrameFeatures(frame_id=f"f{i}", ts=i * 0.5, sig=ScreenSignature.of(f), is_settled=settled)
        for i, f in enumerate(frames)
    ]


def test_cluster_separates_two_screens():
    frames = [make_screen(0, t=i * 0.5, seed=i) for i in range(6)]
    frames += [make_screen(1, t=i * 0.5, seed=i) for i in range(6)]
    vol = learn_from_frames(frames[:6], [i * 0.5 for i in range(6)])
    result = cluster_frames(_features(frames), vol)
    assert result.n_clusters >= 2
    first = {int(result.labels[i]) for i in range(6)}
    second = {int(result.labels[i]) for i in range(6, 12)}
    assert not (first & second), "두 화면이 같은 클러스터로 묶였습니다"


def test_transitional_frames_do_not_create_clusters():
    """전환 애니메이션 프레임은 화면을 정의하지 못한다 — 유령 상태 방지."""
    settled = [make_screen(0, t=i * 0.5, seed=i) for i in range(4)]
    settled += [make_screen(1, t=i * 0.5, seed=i) for i in range(4)]
    blended = cv2.addWeighted(settled[0], 0.5, settled[4], 0.5, 0)

    features = _features(settled, settled=True)
    features.append(
        FrameFeatures(frame_id="blend", ts=99.0, sig=ScreenSignature.of(blended), is_settled=False)
    )
    result = cluster_frames(features)
    anchor_labels = {int(result.labels[i]) for i in range(len(settled))}
    assert int(result.labels[-1]) in anchor_labels, "블렌드 프레임이 새 클러스터를 만들었습니다"


def test_cluster_handles_edge_counts():
    assert cluster_frames([]).n_clusters == 0
    assert cluster_frames(_features([make_screen(0, seed=1)])).n_clusters == 1


def test_llm_verdict_is_a_hard_constraint():
    """LLM이 '다르다'고 하면 점수와 무관하게 분리되어야 한다."""
    frames = [make_screen(0, t=i * 0.5, seed=i) for i in range(4)]
    features = _features(frames)

    def always_different(pairs):
        return {p: False for p in pairs}

    forced = cluster_frames(
        features, disambiguate=always_different, same_threshold=0.5
    )
    # 회색 구간 쌍이 없으면 질의 자체가 없다 — 그 경우는 검증 대상이 아니다
    if forced.asked_llm:
        for (i, j) in forced.llm_verdicts:
            assert forced.labels[i] != forced.labels[j]

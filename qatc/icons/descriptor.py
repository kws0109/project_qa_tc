"""아이콘 특징 벡터.

**왜 딥러닝이 아닌가**: CNN을 새로 학습시키려면 클래스당 수백~수천 장이 필요합니다.
QA 담당자가 아이콘 하나에 스크린샷 500장을 라벨링할 리 없습니다. 반면 **손으로
설계한 디스크립터 + kNN**은 클래스당 샘플 1개부터 동작하고, 라벨이 늘수록 결정
경계가 실제로 좋아집니다. "데이터가 쌓일수록 정교해진다"는 목표에 맞는 건 후자입니다.

**세 블록으로 구성**합니다. 각각 다른 종류의 혼동을 막습니다.

============ ======== ===============================================
블록          차원      막아주는 혼동
============ ======== ===============================================
셀 평균       64       모양이 다른 아이콘 (구조)
색 히스토그램  32       모양은 비슷하나 색이 다른 아이콘 (파랑 vs 주황)
엣지 방향     32       색이 비슷하나 윤곽이 다른 아이콘
============ ======== ===============================================

블록별로 L2 정규화한 뒤 이어붙입니다. 한 블록이 다른 블록을 압도하지 않게 하려면
이게 필요합니다 — 색 히스토그램의 값 범위가 셀 평균보다 크면 색만 보고 판단하게 됩니다.
"""

from __future__ import annotations

import cv2
import numpy as np

#: 아이콘 패치를 정규화할 크기. 게임 아이콘은 대개 정사각형이고,
#: 64면 세부 형태를 담으면서 계산이 가볍다.
PATCH = 64

_CELL = 8       # 8x8 셀 평균 → 64차원
_H_BINS = 16    # 색상(Hue)
_S_BINS = 8     # 채도
_V_BINS = 8     # 명도
_ORI_BINS = 8   # 엣지 방향
_ORI_GRID = 2   # 2x2 격자 → 8*4 = 32차원

DIM = _CELL * _CELL + (_H_BINS + _S_BINS + _V_BINS) + (_ORI_BINS * _ORI_GRID * _ORI_GRID)


def normalize_patch(bgr: np.ndarray) -> np.ndarray:
    """아이콘 패치를 고정 크기 BGR로. 크기가 달라도 비교할 수 있게 만든다."""
    if bgr is None or bgr.size == 0:
        raise ValueError("빈 이미지로는 디스크립터를 만들 수 없습니다")
    if bgr.ndim == 2:
        bgr = cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGR)
    elif bgr.shape[2] == 4:
        bgr = bgr[:, :, :3]
    return cv2.resize(bgr, (PATCH, PATCH), interpolation=cv2.INTER_AREA)


def _l2(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 1e-8 else vec


def _cell_means(gray: np.ndarray) -> np.ndarray:
    """8x8 격자의 평균 밝기. 아이콘의 대략적인 형태를 담는다."""
    step = PATCH // _CELL
    tiles = gray.reshape(_CELL, step, _CELL, step).astype(np.float32)
    return tiles.mean(axis=(1, 3)).flatten() / 255.0


def _color_hist(bgr: np.ndarray) -> np.ndarray:
    """HSV 히스토그램.

    채도가 낮은 픽셀은 색상(Hue)이 불안정하므로 Hue 히스토그램에서 가중치를 낮춘다.
    회색 아이콘의 Hue 값은 노이즈일 뿐인데 그걸 그대로 세면 엉뚱한 특징이 생긴다.
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    weight = (s.astype(np.float32) / 255.0).flatten()
    h_hist = np.bincount(
        (h.flatten().astype(np.int32) * _H_BINS // 180).clip(0, _H_BINS - 1),
        weights=weight,
        minlength=_H_BINS,
    ).astype(np.float32)

    s_hist = np.bincount(
        (s.flatten().astype(np.int32) * _S_BINS // 256).clip(0, _S_BINS - 1), minlength=_S_BINS
    ).astype(np.float32)
    v_hist = np.bincount(
        (v.flatten().astype(np.int32) * _V_BINS // 256).clip(0, _V_BINS - 1), minlength=_V_BINS
    ).astype(np.float32)

    return np.concatenate([_l2(h_hist), _l2(s_hist), _l2(v_hist)])


def _edge_orientation(gray: np.ndarray) -> np.ndarray:
    """2x2 격자별 엣지 방향 히스토그램 (HOG의 축소판).

    방향을 0~180도로 접는다(부호 무시) — 아이콘 윤곽에서 밝기 방향이 뒤집히는 것은
    의미가 없고, 접지 않으면 같은 선이 두 빈으로 갈라진다.
    """
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    magnitude = np.sqrt(gx * gx + gy * gy)
    angle = np.rad2deg(np.arctan2(gy, gx)) % 180.0
    bins = (angle * _ORI_BINS / 180.0).astype(np.int32).clip(0, _ORI_BINS - 1)

    step = PATCH // _ORI_GRID
    blocks: list[np.ndarray] = []
    for row in range(_ORI_GRID):
        for col in range(_ORI_GRID):
            b = bins[row * step : (row + 1) * step, col * step : (col + 1) * step].flatten()
            m = magnitude[row * step : (row + 1) * step, col * step : (col + 1) * step].flatten()
            hist = np.bincount(b, weights=m, minlength=_ORI_BINS).astype(np.float32)
            blocks.append(_l2(hist))
    return np.concatenate(blocks)


def describe(bgr: np.ndarray) -> np.ndarray:
    """아이콘 패치 → 특징 벡터 (float32, 길이 :data:`DIM`).

    세 블록을 각각 정규화해 이어붙인다. 블록별 정규화가 없으면 값 범위가 큰 블록이
    유사도를 지배해 나머지가 무의미해진다.
    """
    patch = normalize_patch(bgr)
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    vec = np.concatenate(
        [_l2(_cell_means(gray)), _color_hist(patch), _edge_orientation(gray)]
    ).astype(np.float32)
    return vec


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """두 디스크립터의 코사인 유사도 (0.0~1.0).

    블록별로 이미 정규화돼 있어 음수가 거의 나오지 않지만, 안전하게 0으로 자른다.
    """
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom < 1e-8:
        return 0.0
    return float(max(0.0, np.dot(a, b) / denom))


def patch_hash(bgr: np.ndarray) -> str:
    """정규화 패치의 dHash (64비트 16진 문자열).

    1단계 정확 매칭용. 게임 아이콘은 같은 에셋을 렌더링하므로 세션이 달라도
    해밍거리가 0~2로 나온다.
    """
    gray = cv2.cvtColor(normalize_patch(bgr), cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = 0
    for value in (small[:, 1:] > small[:, :-1]).flatten():
        bits = (bits << 1) | int(value)
    return f"{bits:016x}"


def hash_distance(a: str, b: str) -> int:
    """두 dHash의 해밍거리 (0~64). 파싱 불가면 최대값."""
    try:
        return int(int(a, 16) ^ int(b, 16)).bit_count()
    except (ValueError, TypeError):
        return 64


def crop_patch(image: np.ndarray, rect, pad: float = 0.06) -> np.ndarray | None:
    """이미지에서 정규화 사각형 영역을 잘라낸다.

    :param pad: 여백 비율. 검출 박스가 아이콘 경계에 딱 붙으면 잘려서 특징이
        약해지므로 살짝 넓게 잡는다. 다만 너무 넓히면 배경이 섞여 오히려 나빠지므로
        6% 정도가 균형점이다.
    """
    if image is None or image.size == 0:
        return None
    h, w = image.shape[:2]
    px, py = rect.w * pad, rect.h * pad
    x0 = int(max(0, (rect.x - px) * w))
    y0 = int(max(0, (rect.y - py) * h))
    x1 = int(min(w, (rect.x + rect.w + px) * w))
    y1 = int(min(h, (rect.y + rect.h + py) * h))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return image[y0:y1, x0:x1]

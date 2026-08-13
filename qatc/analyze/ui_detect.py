"""OpenCV 기반 UI 요소 검출.

**이 모듈이 하는 일과 하지 않는 일을 분명히 해둔다.**

하는 일 — "여기에 클릭 가능해 보이는 사각형이 있다"는 후보 제시.
하지 않는 일 — 그게 무슨 버튼인지, 무슨 의미인지 판단. 그건 LLM과 사용자 몫이다.

OpenCV로 게임 UI의 시맨틱을 알아내려는 시도는 거의 항상 실패한다. 게임마다 디자인이
다르고, 같은 게임에서도 이벤트마다 스킨이 바뀐다. 대신 기하학적 후보만 뽑아
LLM에게 "이 좌표에 무언가 있다"고 알려주면, LLM은 화면 전체를 보면서 훨씬 정확하게
의미를 붙인다. 역할 분담이 이 파이프라인의 핵심이다.

검출된 후보의 실용적 가치는 셋이다.

1. 클릭 좌표에 무엇이 있었는지 매칭 (TC 절차 문구 생성)
2. 구조 시그니처 — 레이아웃이 같은지 비교하는 신호
3. 경계값/예외 TC 추론의 근거 (입력창·수량 버튼이 있으면 그 주변 케이스를 만든다)
"""

from __future__ import annotations

import cv2
import numpy as np

from ..models import ElementKind, NormRect, UIElement


def _auto_canny(gray: np.ndarray, sigma: float = 0.33) -> np.ndarray:
    """이미지 중앙값 기준으로 Canny 임계값을 자동 결정한다.

    게임 화면은 어두운 장면과 밝은 장면의 편차가 커서 고정 임계값이 통하지 않는다.
    """
    med = float(np.median(gray))
    lo = int(max(0, (1.0 - sigma) * med))
    hi = int(min(255, (1.0 + sigma) * med))
    return cv2.Canny(gray, lo, max(hi, lo + 30))


def _rect_candidates(
    gray: np.ndarray, min_area_px: float, max_area_px: float
) -> list[tuple[int, int, int, int]]:
    """엣지 기반 사각형 후보. 둥근 모서리 버튼도 boundingRect로 잡힌다."""
    edges = _auto_canny(gray)
    # 둥근 모서리와 점선 테두리에서 끊긴 윤곽을 이어붙인다
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    out: list[tuple[int, int, int, int]] = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area_px or area > max_area_px:
            continue
        x, y, w, h = cv2.boundingRect(c)
        if w < 12 or h < 8:
            continue
        # 윤곽이 자기 바운딩박스를 얼마나 채우는가. 낮으면 사각형 UI가 아니라
        # 캐릭터 실루엣이나 이펙트일 가능성이 높다.
        if area / float(w * h) < 0.45:
            continue
        aspect = w / float(h)
        if aspect > 25 or aspect < 0.04:
            continue
        out.append((x, y, w, h))
    return out


def _panel_candidates(
    bgr: np.ndarray, min_area_px: float, max_area_px: float
) -> list[tuple[int, int, int, int]]:
    """색이 균일한 큰 영역 = 반투명 패널/배경 박스.

    서브컬쳐 게임의 UI 패널은 대개 반투명 단색이라 엣지가 약하다. Canny로는 놓치므로
    "국소 분산이 낮은 넓은 영역"을 따로 찾는다.
    """
    small = cv2.resize(bgr, (bgr.shape[1] // 4, bgr.shape[0] // 4), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (7, 7), 0)
    # 국소 표준편차: 평균의 제곱과 제곱의 평균 차이
    mean = cv2.boxFilter(blur.astype(np.float32), -1, (15, 15))
    sq = cv2.boxFilter((blur.astype(np.float32)) ** 2, -1, (15, 15))
    std = np.sqrt(np.maximum(sq - mean**2, 0))
    flat = (std < 6.0).astype(np.uint8) * 255

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    flat = cv2.morphologyEx(flat, cv2.MORPH_OPEN, kernel)
    contours, _ = cv2.findContours(flat, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    out: list[tuple[int, int, int, int]] = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        x, y, w, h = x * 4, y * 4, w * 4, h * 4
        area = w * h
        if area < min_area_px * 4 or area > max_area_px:
            continue
        if w < 60 or h < 40:
            continue
        out.append((x, y, w, h))
    return out


def _nms(rects: list[tuple[int, int, int, int]], iou_thresh: float = 0.62) -> list[tuple[int, int, int, int]]:
    """겹치는 후보를 정리한다. 큰 것을 우선 남겨 패널 → 버튼 계층이 보이게 한다."""
    if not rects:
        return []
    ordered = sorted(rects, key=lambda r: r[2] * r[3], reverse=True)
    kept: list[tuple[int, int, int, int]] = []
    for r in ordered:
        rx, ry, rw, rh = r
        dup = False
        for k in kept:
            kx, ky, kw, kh = k
            ix1, iy1 = max(rx, kx), max(ry, ky)
            ix2, iy2 = min(rx + rw, kx + kw), min(ry + rh, ky + kh)
            if ix2 <= ix1 or iy2 <= iy1:
                continue
            inter = (ix2 - ix1) * (iy2 - iy1)
            union = rw * rh + kw * kh - inter
            if union > 0 and inter / union > iou_thresh:
                dup = True
                break
        if not dup:
            kept.append(r)
    return kept


def _classify(rect: NormRect) -> ElementKind:
    """기하학만으로 붙일 수 있는 최소한의 힌트. 확정이 아니라 LLM에게 주는 단서다."""
    aspect = rect.w / rect.h if rect.h > 0 else 0
    if rect.area > 0.15:
        return ElementKind.PANEL
    if rect.area < 0.0025 and 0.6 < aspect < 1.7:
        return ElementKind.ICON
    if aspect > 4.0 and rect.h < 0.06:
        return ElementKind.TEXT
    if 1.4 < aspect < 6.0 and 0.02 < rect.h < 0.09:
        return ElementKind.BUTTON
    return ElementKind.UNKNOWN


def detect_elements(
    bgr: np.ndarray,
    *,
    min_area: float = 0.0004,
    max_area: float = 0.60,
    max_elements: int = 80,
) -> list[UIElement]:
    """화면에서 UI 요소 후보를 검출한다.

    :param min_area: 최소 면적 (화면 대비 비율). 너무 작으면 텍스처 노이즈가 잡힌다.
    :param max_area: 최대 면적. 화면 전체를 감싸는 윤곽을 배제한다.
    :param max_elements: 상한. 검출이 폭주하면 LLM 프롬프트가 터지고 비교도 느려진다.
    """
    if bgr is None or bgr.size == 0:
        return []
    h, w = bgr.shape[:2]
    total = float(w * h)
    min_px, max_px = min_area * total, max_area * total

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    # 양방향 필터: 노이즈는 죽이고 UI 경계는 살린다 (게임 화면의 그레인·압축 아티팩트 대응)
    gray = cv2.bilateralFilter(gray, 7, 45, 45)

    rects = _rect_candidates(gray, min_px, max_px) + _panel_candidates(bgr, min_px, max_px)
    rects = _nms(rects)
    # 큰 것부터 남기되, 상한을 넘으면 작은 노이즈부터 버린다
    rects = sorted(rects, key=lambda r: r[2] * r[3], reverse=True)[:max_elements]

    elements: list[UIElement] = []
    for x, y, rw, rh in rects:
        nr = NormRect.from_pixels(x, y, rw, rh, w, h)
        elements.append(UIElement(rect=nr, kind=_classify(nr), source="cv", confidence=0.5))
    return elements


def assign_hierarchy(elements: list[UIElement]) -> dict[int, int | None]:
    """요소 포함 관계. {자식 인덱스: 부모 인덱스 또는 None}.

    "패널 안의 버튼"을 알면 TC 문구가 정확해진다 — "캐릭터 패널의 강화 버튼".
    """
    order = sorted(range(len(elements)), key=lambda i: elements[i].rect.area)
    parent: dict[int, int | None] = {}
    for pos, i in enumerate(order):
        parent[i] = None
        # 자기보다 큰 것 중 자기를 포함하는 가장 작은 것이 부모
        for j in order[pos + 1 :]:
            if elements[j].rect.contains(elements[i].rect, tol=0.005):
                parent[i] = j
                break
    return parent


def element_at(elements: list[UIElement], nx: float, ny: float, tol: float = 0.006) -> UIElement | None:
    """정규화 좌표에 있는 **가장 작은** 요소.

    가장 작은 것을 고르는 이유: 패널 안의 버튼을 눌렀다면 정답은 패널이 아니라 버튼이다.
    """
    hits = [e for e in elements if e.rect.contains_point(nx, ny, tol)]
    if not hits:
        return None
    return min(hits, key=lambda e: e.rect.area)


def draw_overlay(bgr: np.ndarray, elements: list[UIElement], click: tuple[float, float] | None = None) -> np.ndarray:
    """검출 결과를 그려 넣은 이미지. 리뷰 GUI 캔버스와 디버깅에 쓴다."""
    out = bgr.copy()
    h, w = out.shape[:2]
    colors = {
        ElementKind.PANEL: (90, 90, 90),
        ElementKind.BUTTON: (60, 220, 60),
        ElementKind.ICON: (220, 180, 40),
        ElementKind.TEXT: (240, 130, 200),
        ElementKind.UNKNOWN: (170, 170, 170),
    }
    for el in sorted(elements, key=lambda e: e.rect.area, reverse=True):
        x, y, rw, rh = el.rect.to_pixels(w, h)
        cv2.rectangle(out, (x, y), (x + rw, y + rh), colors.get(el.kind, (170, 170, 170)), 2)
    if click is not None:
        cx, cy = int(click[0] * w), int(click[1] * h)
        cv2.circle(out, (cx, cy), 18, (0, 0, 255), 3)
        cv2.drawMarker(out, (cx, cy), (0, 0, 255), cv2.MARKER_CROSS, 34, 2)
    return out

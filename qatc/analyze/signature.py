"""구조 시그니처 — 검출된 UI 사각형의 배치로 레이아웃이 같은지 본다.

셀 시그니처(색)와 텍스트 시그니처(OCR)를 보완하는 세 번째 신호다. 강점이 서로 다르다.

* **셀 시그니처** — 빠르고 대체로 정확하지만, 배경 테마가 바뀌면(낮/밤, 이벤트 스킨)
  같은 화면을 다르게 본다.
* **텍스트 시그니처** — 탭 이름이 같으면 강력하지만, 텍스트가 거의 없는 화면
  (지도, 전투, 이미지 위주 화면)에서는 무력하다.
* **구조 시그니처** — 색과 글자가 달라도 **버튼이 같은 자리에 같은 크기로 있으면**
  같은 화면이라고 본다. 위 둘이 모두 약한 지점을 메운다.

구현은 사각형 집합의 IoU 매칭 기반 유사도다. 순서에 의존하지 않아야 하므로
그리디 최적 매칭을 쓴다 (헝가리안까지 갈 만큼 정밀할 필요는 없다).
"""

from __future__ import annotations

from typing import Sequence

from ..models import NormRect, UIElement

#: 이 IoU 미만이면 같은 요소로 치지 않는다. 게임 UI는 요소가 정확히 같은 자리에
#: 오므로 꽤 높게 잡아도 된다.
MATCH_IOU = 0.55
#: 구조 비교에서 무시할 만큼 작은 요소 (아이콘 노이즈)
MIN_STRUCT_AREA = 0.0008


def to_struct_signature(elements: Sequence[UIElement]) -> list[list[float]]:
    """UI 요소 목록을 저장 가능한 사각형 목록으로. 큰 것부터 정렬해 안정적인 순서를 만든다."""
    rects = [e.rect for e in elements if e.rect.area >= MIN_STRUCT_AREA]
    rects.sort(key=lambda r: (-r.area, r.y, r.x))
    return [[round(v, 4) for v in r.as_tuple()] for r in rects]


def from_struct_signature(sig: Sequence[Sequence[float]]) -> list[NormRect]:
    return [NormRect(*r) for r in sig]


def struct_similarity(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> float:
    """두 구조 시그니처의 유사도 (0.0~1.0).

    그리디 매칭: 큰 사각형부터 상대에서 IoU가 가장 높은 짝을 찾아 소비한다.
    점수는 **매칭된 쌍의 IoU 합을 전체 요소 수로 나눈 값** — 한쪽에만 있는 요소는
    분모에 남아 점수를 깎는다. "버튼이 3개 더 생겼다"가 반영되어야 하기 때문이다.
    """
    ra, rb = from_struct_signature(a), from_struct_signature(b)
    if not ra and not rb:
        return 0.0  # 판단 불가. 호출부가 이 신호를 빼고 계산해야 한다.
    if not ra or not rb:
        return 0.0

    used: set[int] = set()
    total_iou = 0.0
    for r in ra:
        best_j, best_iou = -1, 0.0
        for j, s in enumerate(rb):
            if j in used:
                continue
            iou = r.iou(s)
            if iou > best_iou:
                best_j, best_iou = j, iou
        if best_iou >= MATCH_IOU:
            used.add(best_j)
            total_iou += best_iou

    denom = max(len(ra), len(rb))
    return total_iou / denom if denom else 0.0


def has_struct_evidence(
    a: Sequence[Sequence[float]], b: Sequence[Sequence[float]], min_elements: int = 3
) -> bool:
    """구조 신호를 신뢰할 만한지. 양쪽 모두 요소가 충분해야 한다.

    검출된 요소가 1~2개뿐인 화면(로딩, 컷씬)에서 구조 유사도는 잡음일 뿐이다.
    그런 경우를 걸러내지 않으면 무관한 화면끼리 우연히 높은 점수를 받는다.
    """
    return len(a) >= min_elements and len(b) >= min_elements


def layout_bucket(sig: Sequence[Sequence[float]]) -> tuple[int, int, int]:
    """레이아웃의 거친 요약 — (요소 수 구간, 상단 밀집도, 좌측 밀집도).

    수천 개 쌍을 전부 비교하기 전에 명백히 다른 것을 빠르게 걸러내는 용도다.
    """
    rects = from_struct_signature(sig)
    if not rects:
        return (0, 0, 0)
    n_bucket = min(len(rects) // 5, 8)
    top = sum(1 for r in rects if r.cy < 0.25)
    left = sum(1 for r in rects if r.cx < 0.35)
    return (n_bucket, min(top // 3, 6), min(left // 3, 6))

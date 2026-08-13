"""화면 상태 클러스터링 — 프레임 수백 장을 '화면' 수십 개로 접는다.

세 신호를 결합해 유사도를 내고, 애매한 쌍만 LLM에게 물어본 뒤, 응집 클러스터링으로
묶는다.

**연결 방식으로 complete linkage를 쓴다.** 클러스터에 새 프레임이 들어오려면
**기존 모든 구성원과** 유사해야 한다는 뜻이다. single linkage였다면 A~B, B~C인데
A≁C인 사슬이 하나로 묶여 서로 다른 화면이 합쳐진다. 과병합은 전이가 통째로 사라져
TC가 누락되는 치명적 실패이므로, 가장 보수적인 연결 방식을 고른다.

**LLM 판정은 거리 행렬의 하드 제약이 된다.** "같다"고 하면 거리 0, "다르다"고 하면
거리 1로 못박아 클러스터링에 강제한다. 사후 보정보다 깔끔하고 결과가 예측 가능하다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np

from ..models import UIElement
from .hashing import ScreenSignature
from .motion import VolatilityMap
from .ocr import OcrLine, has_text_evidence, jaccard, text_signature
from .signature import has_struct_evidence, struct_similarity, to_struct_signature


@dataclass
class FrameFeatures:
    """한 프레임에서 뽑아낸 모든 비교 재료."""

    frame_id: str
    ts: float
    sig: ScreenSignature
    elements: list[UIElement] = field(default_factory=list)
    ocr_lines: list[OcrLine] = field(default_factory=list)
    is_settled: bool = True

    @property
    def struct_sig(self) -> list[list[float]]:
        return to_struct_signature(self.elements)

    @property
    def text_sig(self) -> list[str]:
        return text_signature(self.ocr_lines)


@dataclass
class Signals:
    """두 프레임을 비교해 얻은 원시 신호들.

    ``None``은 "0점"이 아니라 **"이 신호로는 판단할 수 없다"**는 뜻이다.
    그 구분이 없으면 텍스트가 없는 화면(지도, 컷씬)이 전부 서로 다른 화면으로 잡힌다.
    """

    #: 안정 셀 중 달라지지 않은 비율. 항상 유효한 주 신호.
    cell_sim: float
    #: 밝기 윤곽 상관. 테마/배경색이 바뀐 같은 화면을 잡는 보조 신호. 항상 유효.
    layout_sim: float
    #: UI 사각형 배치 유사도. 요소가 너무 적으면 None.
    struct_sim: float | None
    #: OCR 텍스트 집합 자카드. 양쪽 텍스트가 부족하면 None.
    text_sim: float | None

    def describe(self) -> str:
        def f(v: float | None) -> str:
            return "  n/a" if v is None else f"{v:.3f}"

        return (
            f"cell={self.cell_sim:.3f} layout={self.layout_sim:.3f} "
            f"struct={f(self.struct_sim)} text={f(self.text_sim)}"
        )


def compute_signals(a: FrameFeatures, b: FrameFeatures, vol: VolatilityMap | None) -> Signals:
    """두 프레임의 원시 신호를 계산한다. 결합은 :func:`combined_similarity`가 한다."""
    struct: float | None = None
    if has_struct_evidence(a.struct_sig, b.struct_sig):
        struct = struct_similarity(a.struct_sig, b.struct_sig)

    text: float | None = None
    ta, tb = a.text_sig, b.text_sig
    if has_text_evidence(ta, tb):
        text = jaccard(ta, tb)

    return Signals(
        cell_sim=a.sig.similarity(b.sig, vol),
        layout_sim=a.sig.layout_similarity(b.sig, vol),
        struct_sim=struct,
        text_sim=text,
    )


# ══════════════════════════════════════════════════════════════════════════
#  ▼▼▼  사용자 기여 지점  ▼▼▼
# ══════════════════════════════════════════════════════════════════════════


def combined_similarity(s: Signals) -> float:
    """세 신호를 하나의 유사도(0.0~1.0)로 결합한다.

    ─────────────────────────────────────────────────────────────────────
    TODO(사용자): 이 함수의 가중치와 결합 방식을 직접 정해주세요.

    **왜 이 판단이 QA 실무 지식을 필요로 하는가** — 두 실패 모드의 비용이 다릅니다.

    * **과분리** (같은 화면을 여러 개로 봄)
      → 리뷰 GUI에서 병합 버튼 몇 번. 시간은 들지만 결과물은 온전합니다.

    * **과병합** (다른 화면을 하나로 봄)
      → 두 화면 사이의 전이가 그래프에서 **사라집니다**. 사라진 전이는 리뷰 화면에
        나타나지도 않으므로 사용자가 알아챌 방법이 없고, 그대로 **TC가 누락**됩니다.
        누락된 TC는 테스트되지 않은 기능이 됩니다.

    즉 이 함수는 **의심스러우면 낮은 점수를 주는 쪽**으로 기울어야 합니다.

    **각 신호의 성격**

    ========== ==================================== ==============================
    신호        강점                                  약점
    ========== ==================================== ==============================
    cell_sim   빠르고 대체로 정확. 항상 유효          배경 테마가 바뀌면 같은 화면을
                                                     다르게 봄 (낮/밤, 이벤트 스킨)
    layout_sim 색이 달라도 밝기 윤곽으로 판단          해상도가 낮아 정밀도가 떨어짐
    struct_sim 색·글자가 달라도 버튼 배치로 판단       요소 검출이 불안정할 때 잡음
    text_sim   탭 이름이 같으면 매우 강력             텍스트 없는 화면에선 판단 불가
    ========== ==================================== ==============================

    **고려할 만한 접근** (무엇을 골라도 좋습니다)

    * 가중 평균 — 단순하고 예측 가능. ``None``인 신호는 분모에서 빼세요.
    * 최솟값 기반 — "하나라도 강하게 반대하면 낮은 점수". 과병합에 가장 보수적.
    * 게이팅 — cell_sim이 일정 이하면 다른 신호와 무관하게 즉시 낮은 점수.
    * 위 조합 — 예: 가중 평균을 내되 어떤 신호가 임계 이하면 상한을 씌움.

    :param s: :func:`compute_signals`가 만든 원시 신호. ``struct_sim``/``text_sim``은
        ``None``일 수 있고, 그건 0점이 아니라 **판단 불가**를 뜻합니다.
    :returns: 0.0(완전히 다름) ~ 1.0(같은 화면). :data:`SAME_THRESHOLD` 이상이면
        같은 화면으로 확정되고, :data:`AMBIGUOUS_LOW`~:data:`AMBIGUOUS_HIGH` 구간이면
        LLM에게 물어봅니다.
    ─────────────────────────────────────────────────────────────────────

    실측 근거 (붕괴:스타레일 40초 세션, 필드 이동 위주)
    ---------------------------------------------------
    같은 필드 화면 7쌍과 필드↔메뉴 1쌍의 신호를 측정한 결과::

        신호      같은 화면          다른 화면    판별 간격
        text      0.615 ~ 0.818     0.026       0.59   ← 압도적
        cell      0.431 ~ 0.792     0.318       0.11   (겹침)
        layout    0.223 ~ 0.723     0.072       0.15
        struct    0.128 ~ 0.197     0.009       0.12   (같은 화면도 낮음)

    **텍스트가 화면 정체성의 가장 강한 증거다.** 3D 필드는 걸어다니면 배경이
    통째로 바뀌어 셀 시그니처가 무너지지만, HUD(파티 목록·임무 표시)는 그대로
    남는다. 반대로 메뉴가 다르면 탭 이름이 통째로 바뀌어 텍스트가 0.03까지 떨어진다.

    구조 시그니처는 이 데이터에서 사실상 쓸모가 없었다 — 3D 배경에서 요소 검출이
    불안정해 같은 화면끼리도 0.13에 그쳤다. 가중치를 크게 낮춘다.
    """
    # 텍스트가 충분하면 그것을 주 신호로 삼고, 아니면 셀로 되돌아간다.
    # (텍스트 없는 화면 — 지도, 컷씬, 로딩 — 에서는 셀만이 유일한 근거다)
    if s.text_sim is not None:
        score = (
            _calibrate_text(s.text_sim) * 0.80
            + s.cell_sim * 0.15
            + s.layout_sim * 0.05
        )
        # 텍스트가 강하게 반대하면 나머지가 아무리 좋아도 같은 화면이 아니다.
        # 탭이 통째로 바뀌었다는 뜻이기 때문이다.
        if s.text_sim < 0.20:
            score = min(score, 0.45)
        return float(np.clip(score, 0.0, 1.0))

    # -- 텍스트 없음: 셀 중심 --------------------------------------
    weighted = [(s.cell_sim, 0.62), (s.layout_sim, 0.25)]
    if s.struct_sim is not None:
        weighted.append((s.struct_sim, 0.13))
    total_w = sum(w for _, w in weighted)
    score = sum(v * w for v, w in weighted) / total_w

    # 텍스트라는 안전망이 없으므로 셀 게이트를 유지한다 — 여기서는 과병합이
    # 더 위험하다. 텍스트가 있을 때 이 게이트를 걸면 필드 화면이 9개로 갈라진다
    # (실측에서 같은 화면 7쌍 중 0쌍만 병합됐다).
    if s.cell_sim < 0.55:
        score = min(score, 0.45)
    return float(np.clip(score, 0.0, 1.0))


# ══════════════════════════════════════════════════════════════════════════
#  ▲▲▲  사용자 기여 지점 끝  ▲▲▲
# ══════════════════════════════════════════════════════════════════════════


def _calibrate_text(t: float) -> float:
    """OCR 자카드를 '같은 화면일 확률'로 보정한다.

    **왜 원값을 그대로 못 쓰는가**: OCR은 프레임마다 조금씩 다르게 읽는다.
    같은 화면을 찍은 두 프레임에서도 토큰 하나가 더 잡히거나 빠지므로 자카드가
    1.0이 되지 않는다. 실측에서 같은 화면은 0.615~0.818, 다른 화면은 0.026이었다.

    즉 **0.6은 이미 "같은 화면"의 강한 증거**인데 원값을 가중 평균에 넣으면
    0.6이 그대로 반영돼 임계 0.80을 못 넘는다. 관측된 분포에 맞춰 늘려 준다.

    이 곡선은 스타레일 한 세션에서 뽑은 값이라 다른 게임에서는 재조정이 필요할
    수 있다. 다른 게임을 붙일 때 :func:`compute_signals` 값을 찍어 보고 조정할 것.
    """
    if t >= 0.55:              # 같은 화면 영역 → 0.88~1.00
        return min(1.0, 0.88 + (t - 0.55) * 0.27)
    if t <= 0.15:              # 다른 화면 영역 → 0.00~0.20
        return t * 1.33
    return 0.20 + (t - 0.15) * 1.70   # 회색 지대 → 0.20~0.88


#: 이 값 이상이면 LLM에 묻지 않고 같은 화면으로 확정.
SAME_THRESHOLD = 0.80
#: 이 구간은 LLM에게 물어본다. 아래면 다른 화면으로 확정.
AMBIGUOUS_LOW = 0.55
AMBIGUOUS_HIGH = 0.80

#: LLM 판정 콜백. {(i, j): 같은 화면인가} 를 돌려준다.
DisambiguateFn = Callable[[list[tuple[int, int]]], dict[tuple[int, int], bool]]


@dataclass
class ClusterResult:
    #: 프레임 인덱스 → 클러스터 번호
    labels: np.ndarray
    #: 클러스터 번호 → 프레임 인덱스 목록
    clusters: dict[int, list[int]]
    similarity: np.ndarray
    asked_llm: list[tuple[int, int]] = field(default_factory=list)
    llm_verdicts: dict[tuple[int, int], bool] = field(default_factory=dict)

    @property
    def n_clusters(self) -> int:
        return len(self.clusters)


def similarity_matrix(
    features: Sequence[FrameFeatures], vol: VolatilityMap | None
) -> np.ndarray:
    """모든 쌍의 결합 유사도. 대각선은 1.0."""
    n = len(features)
    m = np.eye(n, dtype=np.float32)
    for i in range(n):
        for j in range(i + 1, n):
            s = compute_signals(features[i], features[j], vol)
            m[i, j] = m[j, i] = combined_similarity(s)
    return m


def find_ambiguous_pairs(
    sim: np.ndarray, low: float = AMBIGUOUS_LOW, high: float = AMBIGUOUS_HIGH, limit: int = 60
) -> list[tuple[int, int]]:
    """LLM에게 물어볼 쌍을 고른다.

    회색 구간 전체를 묻지 않고 **경계에 가장 가까운 것부터** 상한까지만 묻는다.
    구간 한가운데(0.67 같은)가 가장 불확실하므로 그쪽을 우선한다. 비용이 제한된
    상황에서 정보량이 가장 큰 질문을 고르는 것이다.
    """
    n = sim.shape[0]
    mid = (low + high) / 2
    pairs: list[tuple[float, int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            v = float(sim[i, j])
            if low <= v < high:
                pairs.append((abs(v - mid), i, j))
    pairs.sort()
    return [(i, j) for _, i, j in pairs[:limit]]


def cluster_frames(
    features: Sequence[FrameFeatures],
    vol: VolatilityMap | None = None,
    *,
    disambiguate: DisambiguateFn | None = None,
    same_threshold: float = SAME_THRESHOLD,
    max_llm_pairs: int = 60,
) -> ClusterResult:
    """프레임들을 화면 상태로 묶는다.

    **전환 중 프레임은 화면을 정의하지 못한다.** 클러스터는 ``is_settled=True``인
    프레임(전이 애니메이션이 끝난 시점)으로만 만들고, 페이드 중간 프레임은 만들어진
    클러스터 중 가장 가까운 곳에 배정한다.

    이 구분이 없으면 50% 블렌드된 프레임이 출발 화면도 도착 화면도 아닌 제3의
    클러스터를 만들어 **유령 상태**가 생긴다. 유령 상태는 전이 경로를 끊어
    TC를 통째로 잘못 만든다. "전환 애니메이션은 화면이 아니다"를 구조로 못박는다.

    :param disambiguate: 애매한 쌍을 판정하는 콜백 (보통 LLM). None이면 건너뛴다 —
        LLM 없이도 파이프라인은 동작하고, 정확도만 떨어진다.
    """
    n = len(features)
    if n == 0:
        return ClusterResult(labels=np.zeros(0, dtype=int), clusters={}, similarity=np.zeros((0, 0)))
    if n == 1:
        return ClusterResult(labels=np.zeros(1, dtype=int), clusters={0: [0]}, similarity=np.ones((1, 1)))

    anchor_idx = [i for i, f in enumerate(features) if f.is_settled]
    # settled 프레임이 너무 적으면(짧은 세션) 구분 없이 전부 쓴다.
    if len(anchor_idx) < 2:
        anchor_idx = list(range(n))
    transient_idx = [i for i in range(n) if i not in set(anchor_idx)]

    anchors = [features[i] for i in anchor_idx]
    sim = similarity_matrix(anchors, vol)

    asked: list[tuple[int, int]] = []
    verdicts: dict[tuple[int, int], bool] = {}
    if disambiguate is not None:
        local_pairs = find_ambiguous_pairs(sim, limit=max_llm_pairs)
        # 콜백에는 원본 인덱스로 물어본다 — 호출부가 프레임을 찾을 수 있어야 한다.
        asked = [(anchor_idx[i], anchor_idx[j]) for i, j in local_pairs]
        if asked:
            verdicts = disambiguate(asked) or {}
            for (gi, gj), same in verdicts.items():
                try:
                    i, j = anchor_idx.index(gi), anchor_idx.index(gj)
                except ValueError:
                    continue
                # LLM 판정을 거리 행렬의 하드 제약으로 못박는다.
                sim[i, j] = sim[j, i] = 1.0 if same else 0.0

    anchor_labels = _agglomerative(sim, same_threshold)

    labels = np.full(n, -1, dtype=int)
    for local, global_i in enumerate(anchor_idx):
        labels[global_i] = int(anchor_labels[local])

    # 전환 중 프레임을 가장 가까운 클러스터에 배정 (새 클러스터를 만들지 않는다)
    for i in transient_idx:
        labels[i] = _nearest_cluster(features[i], features, anchor_idx, anchor_labels, vol)

    clusters: dict[int, list[int]] = {}
    for idx, lab in enumerate(labels):
        clusters.setdefault(int(lab), []).append(idx)

    return ClusterResult(
        labels=labels, clusters=clusters, similarity=sim, asked_llm=asked, llm_verdicts=verdicts
    )


def _nearest_cluster(
    frame: FrameFeatures,
    features: Sequence[FrameFeatures],
    anchor_idx: list[int],
    anchor_labels: np.ndarray,
    vol: VolatilityMap | None,
) -> int:
    """전환 중 프레임을 가장 유사한 앵커의 클러스터에 배정한다."""
    best_label, best_score = int(anchor_labels[0]), -1.0
    for local, global_i in enumerate(anchor_idx):
        score = combined_similarity(compute_signals(frame, features[global_i], vol))
        if score > best_score:
            best_score, best_label = score, int(anchor_labels[local])
    return best_label


def _agglomerative(sim: np.ndarray, threshold: float) -> np.ndarray:
    """complete-linkage 응집 클러스터링.

    sklearn이 있으면 쓰고, 없으면 동등한 동작의 순수 구현으로 폴백한다.
    complete linkage를 고른 이유는 모듈 docstring 참고 — 과병합 방지가 최우선이다.
    """
    distance = np.clip(1.0 - sim, 0.0, 1.0).astype(np.float64)
    np.fill_diagonal(distance, 0.0)
    max_dist = 1.0 - threshold

    try:
        from sklearn.cluster import AgglomerativeClustering

        model = AgglomerativeClustering(
            n_clusters=None,
            metric="precomputed",
            linkage="complete",
            distance_threshold=max_dist,
        )
        return model.fit_predict(distance).astype(int)
    except Exception:
        return _agglomerative_fallback(distance, max_dist)


def _agglomerative_fallback(distance: np.ndarray, max_dist: float) -> np.ndarray:
    """sklearn 없이 동작하는 complete-linkage 구현.

    n이 수백 규모라 O(n^3)이어도 실용적으로 충분하다.
    """
    n = distance.shape[0]
    clusters: list[list[int]] = [[i] for i in range(n)]

    while len(clusters) > 1:
        best = (max_dist, -1, -1)
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                # complete linkage: 두 클러스터 사이의 **최대** 거리
                d = max(distance[i, j] for i in clusters[a] for j in clusters[b])
                if d <= best[0]:
                    best = (d, a, b)
        _, a, b = best
        if a < 0:
            break
        clusters[a].extend(clusters[b])
        clusters.pop(b)

    labels = np.zeros(n, dtype=int)
    for lab, members in enumerate(clusters):
        for i in members:
            labels[i] = lab
    return labels


def cluster_report(result: ClusterResult, features: Sequence[FrameFeatures]) -> str:
    """사람이 읽는 요약. CLI에서 분석 결과를 확인할 때 쓴다."""
    lines = [f"프레임 {len(features)}장 → 화면 상태 {result.n_clusters}개"]
    if result.asked_llm:
        same = sum(1 for v in result.llm_verdicts.values() if v)
        lines.append(
            f"LLM 판별 {len(result.asked_llm)}쌍 질의 → 같음 {same}건, 다름 {len(result.llm_verdicts) - same}건"
        )
    for lab in sorted(result.clusters):
        members = result.clusters[lab]
        span = f"{features[members[0]].ts:.1f}s~{features[members[-1]].ts:.1f}s"
        lines.append(f"  상태 {lab:2d}: 프레임 {len(members):3d}장 ({span})")
    return "\n".join(lines)

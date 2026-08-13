"""화면 지문 — 빠른 1차 필터와 정밀 비교.

두 단계로 나눈 이유는 비용이다. 세션 하나에 프레임 수천 장이 나오면 쌍 비교가
수백만 번인데, 정밀 비교를 다 돌리면 느리다.

1. **dHash 1차 필터** — 64비트 정수 비교. 거의 동일한 프레임을 즉시 제거한다.
2. **셀 시그니처 정밀 비교** — 변동성 맵으로 안정 셀만 골라 비교한다.
   :mod:`qatc.analyze.motion` 참고.
"""

from __future__ import annotations

import cv2
import numpy as np

from .motion import GRID_H, GRID_W, N_CELLS, VolatilityMap, cell_means

#: 셀 평균색이 이 값(0~255) 이상 달라지면 "바뀐 셀"로 센다.
#: 24는 JPEG 압축 노이즈와 미세한 그라데이션 변화는 넘기고, 패널·버튼 교체는 잡는 선이다.
CELL_CHANGE_DELTA = 24.0


def dhash(img: np.ndarray, size: int = 8) -> int:
    """difference hash — 인접 픽셀의 밝기 대소만 보므로 밝기 변화에 강하다.

    pHash(DCT) 대신 쓰는 이유: 게임 화면은 밝기 변화(페이드)가 잦은데 dHash는
    구조만 보고, 계산이 훨씬 싸다. 어차피 1차 필터라 정밀도는 2단계가 담당한다.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    small = cv2.resize(gray, (size + 1, size), interpolation=cv2.INTER_AREA)
    diff = small[:, 1:] > small[:, :-1]
    bits = 0
    for bit in diff.flatten():
        bits = (bits << 1) | int(bit)
    return bits


def hamming(a: int, b: int) -> int:
    return int(a ^ b).bit_count()


def hash_to_hex(h: int) -> str:
    return f"{h:016x}"


def hash_from_hex(s: str) -> int:
    return int(s, 16) if s else 0


class ScreenSignature:
    """한 프레임의 정밀 지문. 셀별 BGR 평균 (N_CELLS x 3)."""

    __slots__ = ("cells", "dhash")

    def __init__(self, cells: np.ndarray, dh: int = 0):
        self.cells = cells.astype(np.float32)
        self.dhash = dh

    @classmethod
    def of(cls, img: np.ndarray) -> ScreenSignature:
        return cls(cell_means(img), dhash(img))

    def to_list(self) -> list[list[float]]:
        return [[round(float(v), 2) for v in row] for row in self.cells]

    @classmethod
    def from_list(cls, data: list[list[float]], dh: int = 0) -> ScreenSignature:
        return cls(np.asarray(data, dtype=np.float32).reshape(N_CELLS, 3), dh)

    # -- 비교 --------------------------------------------------------

    def _mask(self, vol: VolatilityMap | None) -> np.ndarray:
        mask = vol.stable if vol is not None else np.ones(N_CELLS, dtype=bool)
        if not mask.any():
            # 전부 변동으로 학습됐다면 (전투 화면 등) 변동성을 쓰지 않고 비교한다.
            return np.ones(N_CELLS, dtype=bool)
        return mask

    def similarity(self, other: ScreenSignature, vol: VolatilityMap | None = None) -> float:
        """안정 셀 중 **유의미하게 달라진 셀의 비율**로 유사도를 낸다 (0.0~1.0).

        평균 절대차를 쓰지 않는 이유: 넓은 균일 배경이 점수를 지배해서, 우측 패널이
        통째로 교체됐는데도 "거의 같은 화면"으로 나온다. 셀 단위로 "바뀌었나/아닌가"를
        판정한 뒤 비율을 세면 **화면의 몇 %가 달라졌는가**를 직접 측정하게 된다.

        변동성 맵이 주어지면 캐릭터 애니메이션·파티클 셀은 애초에 세지 않는다.
        이게 "같은 홈 화면이 매번 다른 화면으로 잡히는" 문제를 푸는 핵심이다.
        """
        mask = self._mask(vol)
        diff = np.abs(self.cells[mask] - other.cells[mask]).mean(axis=1)
        changed = float((diff > CELL_CHANGE_DELTA).mean())
        return float(max(0.0, 1.0 - changed))

    def change_ratio(self, other: ScreenSignature, vol: VolatilityMap | None = None) -> float:
        """달라진 안정 셀의 비율. :meth:`similarity`의 여집합 — 진단 출력용."""
        return 1.0 - self.similarity(other, vol)

    def mean_delta(self, other: ScreenSignature, vol: VolatilityMap | None = None) -> float:
        """안정 셀 평균 절대차(0~1). 1차 필터에서 '거의 동일'을 판정할 때 쓴다."""
        mask = self._mask(vol)
        return float(np.abs(self.cells[mask] - other.cells[mask]).mean() / 255.0)

    def layout_similarity(self, other: ScreenSignature, vol: VolatilityMap | None = None) -> float:
        """색이 아니라 **밝기 윤곽**만 비교한다.

        같은 화면인데 테마/배경이 바뀐 경우(낮/밤, 이벤트 스킨)를 잡기 위한 보조 신호.
        셀 평균을 그레이로 바꾼 뒤 각자 정규화해서 상관계수를 낸다.
        """
        mask = vol.stable if vol is not None else np.ones(N_CELLS, dtype=bool)
        if not mask.any():
            mask = np.ones(N_CELLS, dtype=bool)
        a = self.cells[mask].mean(axis=1)
        b = other.cells[mask].mean(axis=1)
        a = a - a.mean()
        b = b - b.mean()
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denom < 1e-6:
            return 0.0
        return float(max(0.0, np.dot(a, b) / denom))

    def edge_map(self) -> np.ndarray:
        """셀 평균의 격자 이미지 (GRID_H x GRID_W, uint8). 디버깅/시각화용."""
        return self.cells.mean(axis=1).reshape(GRID_H, GRID_W).clip(0, 255).astype(np.uint8)


def dedupe(
    sigs: list[ScreenSignature],
    vol: VolatilityMap | None = None,
    *,
    max_hamming: int = 3,
    max_delta: float = 0.02,
) -> tuple[list[int], dict[int, int]]:
    """1차 중복 제거 — **거의 픽셀 단위로 동일한** 프레임만 대표 하나로 접는다.

    dHash 단독으로 판정하지 않는다. 실측 결과 서브컬쳐 게임에서는 캐릭터 애니메이션이
    dHash를 지배해, 같은 화면의 해밍거리(9)가 다른 화면(8)보다 오히려 큰 역전이
    일어났다. dHash만 믿으면 **다른 화면을 병합**하게 되는데, 그건 전이가 통째로
    사라져 TC가 누락되는 치명적 실패다.

    그래서 두 신호를 **모두** 통과해야 접는다: dHash가 가깝고(구조 동일) 셀 평균차도
    작을 것(내용 동일). 둘 중 하나만 맞으면 남겨서 2단계 클러스터링이 판단하게 한다.
    보수적으로 남기는 쪽의 비용은 계산 시간뿐이다.

    :returns: (대표 인덱스 목록, {원본 인덱스: 대표 인덱스})
    """
    reps: list[int] = []
    mapping: dict[int, int] = {}
    for i, sig in enumerate(sigs):
        for r in reps:
            if (
                hamming(sig.dhash, sigs[r].dhash) <= max_hamming
                and sig.mean_delta(sigs[r], vol) <= max_delta
            ):
                mapping[i] = r
                break
        else:
            reps.append(i)
            mapping[i] = i
    return reps, mapping

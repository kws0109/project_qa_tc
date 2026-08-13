"""변동성 학습 — 이 프로젝트에서 가장 중요한 기법.

**문제**: 서브컬쳐 게임 화면은 캐릭터 대기 모션, 파티클, 배경 루프로 픽셀이 끊임없이
변한다. 원본을 그대로 해시하면 **같은 홈 화면이 매 프레임 다른 화면으로 잡힌다.**

**해결**: 화면을 격자로 나누고, 셀마다 "이 셀은 얼마나 자주 변하는가"(변동성)를
학습한다. 변동성이 높은 셀은 캐릭터·이펙트이고, 낮은 셀은 패널·버튼·탭 —
즉 **화면의 정체성**이다. 비교할 때 안정적인 셀만 쓴다.

**왜 마스킹 후 pHash가 아니라 격자인가**: 마스킹된 픽셀을 0으로 채우고 pHash를 돌리면
마스크 경계에 인공 엣지가 생겨 DCT를 오염시킨다. 격자 방식은 그 문제가 없고,
"A에서도 안정적이고 B에서도 안정적인 셀"만 교집합으로 비교할 수 있다는 이점까지 있다.
전역 마스크 하나로는 불가능한 일이다.

**닭과 달걀**: 변동성을 학습하려면 "같은 화면" 프레임 묶음이 필요한데, 같은 화면을
알려면 변동성이 필요하다. 부트스트랩으로 푼다 — 2fps 연속 프레임은 애니메이션이
있어도 같은 화면일 확률이 매우 높으므로, 시간적 연속성으로 초기 묶음을 만든다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

#: 격자 크기. 16:9 화면에서 셀이 대략 정사각형이 되도록 잡았다.
#: 32x18 = 576셀. 1920x1080에서 셀 하나가 60x60px — 버튼 하나가 여러 셀에 걸치는 크기다.
GRID_W = 32
GRID_H = 18
N_CELLS = GRID_W * GRID_H

#: 변동성 학습에 쓰는 작업 해상도. 격자 평균만 낼 것이라 크게 잡을 이유가 없다.
WORK_W = GRID_W * 8   # 256
WORK_H = GRID_H * 8   # 144


def to_work(img: np.ndarray) -> np.ndarray:
    """작업 해상도의 BGR 이미지로 정규화한다. 해상도가 달라도 격자 좌표가 일치하게 만든다."""
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:
        img = img[:, :, :3]
    return cv2.resize(img, (WORK_W, WORK_H), interpolation=cv2.INTER_AREA)


def cell_means(img: np.ndarray) -> np.ndarray:
    """격자 셀별 BGR 평균. 반환 shape = (N_CELLS, 3), float32 0~255.

    이게 화면의 지문이다. 셀 단위로 평균을 내면 미세한 애니메이션 흔들림은
    자연히 평균화되어 사라지고, 패널·버튼 같은 큰 색 덩어리만 남는다.
    """
    work = to_work(img).astype(np.float32)
    # (GRID_H, 8, GRID_W, 8, 3) 으로 재배열해 셀 내부를 평균
    tiles = work.reshape(GRID_H, WORK_H // GRID_H, GRID_W, WORK_W // GRID_W, 3)
    return tiles.mean(axis=(1, 3)).reshape(N_CELLS, 3)


@dataclass
class VolatilityMap:
    """셀별 변동성 (0.0 = 완전히 정적, 1.0 = 계속 변함).

    게임 프로파일 단위로 캐시된다 — 원신의 홈 화면 HUD 위치는 세션이 바뀌어도 같다.
    """

    values: np.ndarray                    # shape (N_CELLS,), float32
    samples: int = 0                      # 학습에 쓴 프레임 쌍 수
    #: 이 값을 넘으면 "변하는 셀"로 본다. 학습 데이터에서 자동 결정된다.
    threshold: float = 0.06

    @classmethod
    def empty(cls) -> VolatilityMap:
        return cls(values=np.zeros(N_CELLS, dtype=np.float32), samples=0)

    @property
    def stable(self) -> np.ndarray:
        """안정 셀 불리언 마스크. 화면의 정체성을 담은 셀들."""
        return self.values <= self.threshold

    @property
    def stable_ratio(self) -> float:
        return float(self.stable.mean())

    def with_static_ignore(self, rects) -> VolatilityMap:
        """프로파일에 사람이 적어둔 무시 영역(시계, 핑 표시 등)을 강제로 변동 처리한다.

        자동 학습은 세션 데이터가 쌓여야 정확해진다. 사람이 이미 아는 것을 미리
        넣어두면 첫 세션부터 안정적으로 동작한다. 둘은 합쳐서 쓴다.

        :param rects: :class:`~qatc.models.NormRect` 목록 또는 (x, y, w, h) 튜플 목록.
            도메인 타입을 그대로 받는다 — 호출부가 매번 튜플로 풀어 넘기게 하면
            한 곳만 빠뜨려도 터진다(실제로 그렇게 터졌다).
        """
        if not rects:
            return self
        boxes = [
            r.as_tuple() if hasattr(r, "as_tuple") else tuple(r) for r in rects
        ]
        forced = self.values.copy()
        grid = forced.reshape(GRID_H, GRID_W)
        for x, y, w, h in boxes:
            c0, r0 = int(x * GRID_W), int(y * GRID_H)
            c1, r1 = int(np.ceil((x + w) * GRID_W)), int(np.ceil((y + h) * GRID_H))
            c0, r0 = max(0, c0), max(0, r0)
            c1, r1 = min(GRID_W, c1), min(GRID_H, r1)
            if c1 > c0 and r1 > r0:
                grid[r0:r1, c0:c1] = 1.0
        return VolatilityMap(values=forced, samples=self.samples, threshold=self.threshold)

    # -- 영속화 ------------------------------------------------------

    def save(self, path: Path | str) -> None:
        Path(path).write_text(
            json.dumps(
                {
                    "grid_w": GRID_W,
                    "grid_h": GRID_H,
                    "samples": self.samples,
                    "threshold": float(self.threshold),
                    "values": [round(float(v), 5) for v in self.values],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path | str) -> VolatilityMap | None:
        p = Path(path)
        if not p.exists():
            return None
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        # 격자 크기가 바뀌었으면 캐시를 버린다 (호환되지 않는 지문이다)
        if d.get("grid_w") != GRID_W or d.get("grid_h") != GRID_H:
            return None
        vals = np.asarray(d.get("values", []), dtype=np.float32)
        if vals.size != N_CELLS:
            return None
        return cls(values=vals, samples=int(d.get("samples", 0)), threshold=float(d.get("threshold", 0.06)))

    def to_debug_image(self, width: int = 640) -> np.ndarray:
        """변동성 히트맵. 리뷰 GUI에서 "무엇이 무시되고 있는지" 보여줄 때 쓴다."""
        grid = (self.values.reshape(GRID_H, GRID_W) * 255).clip(0, 255).astype(np.uint8)
        big = cv2.resize(grid, (width, width * GRID_H // GRID_W), interpolation=cv2.INTER_NEAREST)
        return cv2.applyColorMap(big, cv2.COLORMAP_INFERNO)


def bootstrap_runs(
    timestamps: list[float], max_gap: float = 1.2, min_len: int = 3
) -> list[tuple[int, int]]:
    """시간적으로 연속된 프레임 구간을 찾는다 (변동성 학습의 부트스트랩).

    2fps로 찍은 연속 프레임은 애니메이션이 있어도 같은 화면일 확률이 매우 높다.
    그 가정으로 초기 묶음을 만들고, 거기서 "무엇이 움직이는가"를 배운다.

    :returns: [(start_index, end_index_exclusive), ...]
    """
    runs: list[tuple[int, int]] = []
    if not timestamps:
        return runs
    start = 0
    for i in range(1, len(timestamps)):
        if timestamps[i] - timestamps[i - 1] > max_gap:
            if i - start >= min_len:
                runs.append((start, i))
            start = i
    if len(timestamps) - start >= min_len:
        runs.append((start, len(timestamps)))
    return runs


def learn_volatility(
    frame_groups: list[list[np.ndarray]],
    *,
    percentile: float = 78.0,
    min_threshold: float = 0.02,
    max_threshold: float = 0.20,
) -> VolatilityMap:
    """같은 화면이라고 믿는 프레임 묶음들에서 셀별 변동성을 학습한다.

    각 묶음 안에서 셀 평균색의 **표준편차**를 재고, 묶음들의 평균을 취한다.
    표준편차를 쓰는 이유: 인접 프레임 차분만 보면 느린 애니메이션(천천히 도는 배경)을
    놓친다. 묶음 전체의 분산을 보면 잡힌다.

    :param percentile: 이 백분위수를 변동/안정의 경계로 삼는다. 78이면 상위 22% 셀을
        "변한다"고 본다 — 게임 화면에서 캐릭터·이펙트가 차지하는 대략적인 비율이다.
    """
    accum = np.zeros(N_CELLS, dtype=np.float64)
    weight = 0.0

    for group in frame_groups:
        if len(group) < 2:
            continue
        sigs = np.stack([cell_means(f) for f in group])  # (n, N_CELLS, 3)
        # 채널 표준편차의 평균 → 셀 하나의 변동성. 0~255 스케일을 0~1로 정규화.
        vol = sigs.std(axis=0).mean(axis=1) / 255.0
        accum += vol * len(group)
        weight += len(group)

    if weight == 0:
        return VolatilityMap.empty()

    values = (accum / weight).astype(np.float32)
    # 임계값을 백분위수로 잡으면 게임/장면에 따라 자동 적응한다.
    thr = float(np.percentile(values, percentile))
    thr = float(np.clip(thr, min_threshold, max_threshold))
    return VolatilityMap(values=values, samples=int(weight), threshold=thr)


def learn_from_frames(
    images: list[np.ndarray], timestamps: list[float], min_frames: int = 8
) -> VolatilityMap:
    """프레임 시퀀스에서 바로 변동성을 학습하는 편의 함수.

    프레임이 너무 적으면 학습이 의미 없으므로 빈 맵(= 모든 셀 안정)을 돌려준다.
    그 경우 상태 식별은 원본 비교로 동작한다 — 정확도는 떨어지지만 멈추지는 않는다.
    """
    if len(images) < min_frames:
        return VolatilityMap.empty()
    runs = bootstrap_runs(timestamps)
    groups = [images[a:b] for a, b in runs if b - a >= 3]
    if not groups:
        # 연속 구간이 안 잡히면 전체를 하나로 본다. 과대추정 위험이 있지만
        # 그 방향의 오류(더 많이 무시)가 과소추정보다 안전하다 — 상태 과분리를 막는다.
        groups = [images]
    return learn_volatility(groups)

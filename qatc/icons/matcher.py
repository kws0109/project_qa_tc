"""아이콘 매칭 — 2단계 + 동점 처리.

::

    아이콘 패치
       ↓
    1단계  dHash 정확 매칭        해밍 ≤ 6 AND 디스크립터 ≥ 0.85  →  즉시 확정
       ↓ (실패)
    2단계  디스크립터 kNN         k=3 다수결, 최고 유사도 ≥ 0.78   →  학습 기반 확정
       ↓ (동점)
    3단계  위치·화면 tie-break    같은 점수면 위치가 가까운 쪽

**1단계에서 두 신호를 모두 요구하는 이유**는 앞서 프레임 중복 제거에서 배운 것과
같습니다. dHash는 64비트로 압축된 구조 요약이라 서로 다른 아이콘이 우연히 가까운
값을 가질 수 있습니다. 해시가 가깝고 **동시에** 디스크립터도 유사할 때만 정확
매칭으로 인정합니다. 한쪽만 맞으면 2단계로 넘겨 판단하게 둡니다.

**오매칭이 미매칭보다 나쁩니다.** 아이콘을 못 알아보면 TC 문구가 좌표로 남을 뿐이지만,
잘못 알아보면 "[구매] 버튼 클릭 → 재화가 차감된다"처럼 **사실과 다른 TC**가 나옵니다.
그래서 임계값을 보수적으로 잡고, 애매하면 미매칭으로 둡니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from ..models import NormRect, ScreenState, UIElement
from . import descriptor as desc
from .models import IconEntry, IconMatch
from .store import IconStore

#: 1단계 — dHash 해밍거리 상한. 게임 아이콘은 보통 0~2가 나온다.
EXACT_HAMMING = 6
#: 1단계 — 해시가 가까워도 디스크립터가 이만큼은 되어야 정확 매칭으로 인정한다.
EXACT_MIN_SIM = 0.85
#: 2단계 — kNN 확정 임계. 이 아래는 미매칭으로 남긴다.
KNN_MIN_SIM = 0.78
#: **자동 라벨링 임계.** 사람이 후보를 보고 고르는 것과 전 화면을 말없이 라벨링하는 것은
#: 요구 정확도가 다르다. 실측에서 0.80은 한 아이콘이 한 화면의 요소 6개를 전부
#: 자기 것이라 주장하게 만들었다. 자동 적용은 훨씬 보수적이어야 한다.
AUTO_LABEL_MIN = 0.90
#: 2단계 — 이웃 수.
K = 3
#: 3단계 — 동점으로 볼 유사도 차이.
TIE_EPS = 0.03
#: 위치가 이만큼 가까우면 같은 자리로 본다 (IoU).
POSITION_IOU = 0.30


@dataclass
class _Candidate:
    entry: IconEntry
    best_sim: float
    votes: float
    method: str


class IconMatcher:
    """사전을 로드해 두고 반복 질의하는 매처.

    디스크립터를 한 번만 배열로 만들어 두므로 화면 수십 개를 훑어도 빠릅니다.
    사전이 바뀌면 :meth:`refresh` 를 부르세요.
    """

    def __init__(self, store: IconStore):
        self.store = store
        self._vectors: np.ndarray | None = None
        self._owner: list[str] = []
        self._hashes: list[str] = []
        self.refresh()

    def refresh(self) -> None:
        """사전 내용을 매칭용 배열로 펼친다."""
        vectors: list[np.ndarray] = []
        self._owner.clear()
        self._hashes.clear()
        for entry in self.store.entries.values():
            for sample in entry.samples:
                if len(sample.descriptor) != desc.DIM:
                    continue  # 디스크립터 버전이 바뀐 오래된 샘플은 건너뛴다
                vectors.append(np.asarray(sample.descriptor, dtype=np.float32))
                self._owner.append(entry.id)
                self._hashes.append(sample.dhash)
        self._vectors = np.stack(vectors) if vectors else None

    @property
    def is_empty(self) -> bool:
        return self._vectors is None

    # -- 매칭 --------------------------------------------------------

    def match_patch(
        self, patch: np.ndarray, rect: NormRect | None = None, screen_name: str = ""
    ) -> IconMatch | None:
        """아이콘 패치 하나를 사전과 대조한다. 확신이 없으면 None."""
        if self._vectors is None:
            return None
        try:
            vector = desc.describe(patch)
            digest = desc.patch_hash(patch)
        except ValueError:
            return None

        sims = self._vectors @ vector / (
            np.linalg.norm(self._vectors, axis=1) * np.linalg.norm(vector) + 1e-8
        )

        exact = self._exact_match(digest, sims)
        if exact is not None:
            return exact

        candidate = self._knn(sims, rect, screen_name)
        if candidate is None or candidate.best_sim < KNN_MIN_SIM:
            return None
        return IconMatch(
            entry=candidate.entry,
            confidence=self._confidence(candidate, sims),
            method=candidate.method,
        )

    def _exact_match(self, digest: str, sims: np.ndarray) -> IconMatch | None:
        """1단계 — 해시와 디스크립터가 **둘 다** 통과할 때만 정확 매칭."""
        best_idx, best_dist = -1, EXACT_HAMMING + 1
        for i, sample_hash in enumerate(self._hashes):
            dist = desc.hash_distance(digest, sample_hash)
            if dist < best_dist:
                best_idx, best_dist = i, dist
        if best_idx < 0 or best_dist > EXACT_HAMMING:
            return None
        if float(sims[best_idx]) < EXACT_MIN_SIM:
            return None  # 해시만 우연히 가까운 경우 — 2단계에 맡긴다

        entry = self.store.get(self._owner[best_idx])
        if entry is None:
            return None
        # 해밍 0이면 사실상 동일 이미지. 거리에 따라 신뢰도를 매긴다.
        confidence = 1.0 - (best_dist / (EXACT_HAMMING * 2.0))
        return IconMatch(entry=entry, confidence=min(1.0, confidence), method="exact")

    def _knn(
        self, sims: np.ndarray, rect: NormRect | None, screen_name: str
    ) -> _Candidate | None:
        """2단계 — 상위 k개 이웃의 가중 투표. 동점이면 위치·화면으로 가른다."""
        k = min(K, len(sims))
        top = np.argsort(sims)[-k:][::-1]

        tally: dict[str, _Candidate] = {}
        for idx in top:
            owner = self._owner[idx]
            entry = self.store.get(owner)
            if entry is None:
                continue
            sim = float(sims[idx])
            slot = tally.get(owner)
            if slot is None:
                tally[owner] = _Candidate(entry=entry, best_sim=sim, votes=sim, method="knn")
            else:
                slot.best_sim = max(slot.best_sim, sim)
                slot.votes += sim
        if not tally:
            return None

        ranked = sorted(tally.values(), key=lambda c: (-c.votes, -c.best_sim))
        best = ranked[0]

        # 3단계 — 1·2위가 사실상 동점이면 위치와 관측 화면으로 가른다
        if len(ranked) > 1 and abs(ranked[0].votes - ranked[1].votes) < TIE_EPS:
            resolved = self._break_tie(ranked[:2], rect, screen_name)
            if resolved is not None:
                resolved.method = "position"
                return resolved
        return best

    @staticmethod
    def _break_tie(
        candidates: Sequence[_Candidate], rect: NormRect | None, screen_name: str
    ) -> _Candidate | None:
        """위치가 겹치거나 같은 화면에서 본 적 있는 쪽을 고른다."""
        scored: list[tuple[float, _Candidate]] = []
        for cand in candidates:
            score = 0.0
            typical = cand.entry.typical_rect
            if rect is not None and typical is not None:
                score += typical.iou(rect)
            if screen_name and screen_name in cand.entry.seen_screens:
                score += 0.5
            scored.append((score, cand))
        scored.sort(key=lambda p: -p[0])
        # 근거가 전혀 없으면 동점을 억지로 가르지 않는다
        return scored[0][1] if scored[0][0] > 0.0 else None

    @staticmethod
    def _confidence(candidate: _Candidate, sims: np.ndarray) -> float:
        """유사도와 2위와의 격차를 함께 반영한다.

        유사도가 높아도 2위와 붙어 있으면 확신할 수 없다 — 비슷한 아이콘이
        사전에 여럿 있다는 뜻이기 때문이다.
        """
        base = candidate.best_sim
        ordered = np.sort(sims)[::-1]
        margin = float(ordered[0] - ordered[1]) if len(ordered) > 1 else 0.2
        return float(np.clip(base * 0.85 + min(margin, 0.2) * 0.75, 0.0, 1.0))

    # -- 화면 단위 적용 ----------------------------------------------

    def annotate_state(
        self,
        state: ScreenState,
        image: np.ndarray,
        *,
        overwrite_user: bool = False,
        min_confidence: float = AUTO_LABEL_MIN,
    ) -> list[tuple[int, IconMatch]]:
        """화면의 요소들을 사전과 대조해 라벨을 채운다.

        **두 가지 안전장치가 걸려 있다.**

        1. **사용자가 붙인 라벨은 덮어쓰지 않는다.** 자동 매칭이 사람의 판단을
           지우면 사전의 신뢰가 무너진다.
        2. **상호 최선 매칭만 인정한다.** 요소에게 그 아이콘이 최선이면서 동시에
           아이콘에게도 그 요소가 최선일 때만 라벨을 붙인다. 이게 없으면 한
           아이콘이 화면의 요소 여러 개를 전부 자기 것이라 주장한다 — 실측에서
           실제로 6개씩 잡혔다. 같은 아이콘이 한 화면에 여러 번 나오는 일은
           드물기 때문에, 가장 닮은 하나만 남기는 것이 옳다.

        :returns: [(요소 인덱스, 매칭 결과)] — GUI가 표시에 쓴다.
        """
        results: list[tuple[int, IconMatch]] = []
        if self._vectors is None or image is None:
            return results

        # 1차: 후보 수집 (아직 반영하지 않는다)
        candidates: list[tuple[int, IconMatch]] = []
        for index, element in enumerate(state.elements_sorted()):
            if element.source == "user" and not overwrite_user:
                continue
            if element.text.strip():
                continue  # 텍스트가 읽힌 요소는 아이콘 사전의 대상이 아니다
            patch = desc.crop_patch(image, element.rect)
            if patch is None:
                continue
            match = self.match_patch(patch, element.rect, state.name)
            if match is None or match.confidence < min_confidence:
                continue
            candidates.append((index, match))

        # 2차: 아이콘마다 가장 확신하는 요소 하나만 남긴다
        best_for_entry: dict[str, tuple[int, IconMatch]] = {}
        for index, match in candidates:
            current = best_for_entry.get(match.entry.id)
            if current is None or match.confidence > current[1].confidence:
                best_for_entry[match.entry.id] = (index, match)

        elements = state.elements_sorted()
        for index, match in sorted(best_for_entry.values()):
            element = elements[index]
            element.label = match.entry.display_label()
            element.source = "icon"
            element.confidence = match.confidence
            results.append((index, match))
        return results

    # -- 학습 --------------------------------------------------------

    def confirm(
        self,
        icon_id: str,
        patch: np.ndarray,
        *,
        screen_name: str = "",
        rect: NormRect | None = None,
    ) -> bool:
        """이 패치가 그 아이콘이 맞다고 확정한다 — 학습 샘플이 하나 늘어난다."""
        added = self.store.add_sample(icon_id, patch, screen_name=screen_name, rect=rect)
        if added:
            self.refresh()
        return added

    def correct(
        self,
        wrong_icon_id: str,
        right_icon_id: str,
        patch: np.ndarray,
        *,
        screen_name: str = "",
        rect: NormRect | None = None,
    ) -> bool:
        """오분류를 교정한다 — "이건 A가 아니라 B야".

        틀린 쪽에서 가장 닮은 샘플을 찾아 옮긴다. 단순히 B에 추가만 하면 A는
        여전히 그 패치를 자기 것이라 주장하므로 경계가 고쳐지지 않는다.
        """
        wrong = self.store.get(wrong_icon_id)
        if wrong is None or not wrong.samples:
            return self.confirm(right_icon_id, patch, screen_name=screen_name, rect=rect)
        try:
            vector = desc.describe(patch)
        except ValueError:
            return False

        sims = [
            desc.similarity(np.asarray(s.descriptor, np.float32), vector) for s in wrong.samples
        ]
        best = int(np.argmax(sims))
        moved = False
        if sims[best] > 0.90:
            moved = self.store.move_sample(wrong_icon_id, right_icon_id, best)
        added = self.store.add_sample(
            right_icon_id, patch, screen_name=screen_name, rect=rect
        )
        if moved or added:
            self.refresh()
        return moved or added


def unmatched_icon_elements(
    state: ScreenState, matched_indices: set[int]
) -> list[tuple[int, UIElement]]:
    """등록되지 않은 아이콘 후보 — 텍스트도 없고 매칭도 안 된 요소.

    GUI가 "이건 등록이 필요합니다"라고 점선으로 표시하는 데 쓴다.
    사전에 무엇이 빠졌는지 보이지 않으면 사용자가 채울 수 없다.
    """
    out: list[tuple[int, UIElement]] = []
    for index, element in enumerate(state.elements_sorted()):
        if index in matched_indices or element.text.strip() or element.label:
            continue
        # 너무 크면 패널이고 너무 작으면 노이즈다
        if 0.0004 <= element.rect.area <= 0.05:
            out.append((index, element))
    return out

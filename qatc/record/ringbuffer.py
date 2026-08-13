"""최근 프레임을 메모리에 보관하는 링버퍼.

**존재 이유**: 클릭이 일어난 *뒤에야* 그 직전 화면이 필요하다는 걸 알게 된다.
그래서 상시 저속(2fps)으로 찍어 메모리에 담아두고, 입력이 발생하면 과거로 되돌아가
"행동 직전 프레임"을 꺼낸다. 입력이 없으면 그냥 덮어써 버린다.

디스크에 쓰지 않는 이유: 1시간 세션에서 2fps면 7200장인데 대부분은 쓸모없다.
필요한 것만 골라 쓰는 편이 저장 공간과 분석 시간을 모두 아낀다.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class TimedFrame:
    ts: float          # 세션 시작 기준 경과 초
    image: np.ndarray  # BGR


class FrameRingBuffer:
    """시간 기준 링버퍼. 스레드 안전.

    캡처 스레드가 :meth:`push` 하고, 이벤트 처리 스레드가 :meth:`at` / :meth:`latest`로
    읽어간다.
    """

    def __init__(self, seconds: float, fps: float):
        self.seconds = seconds
        self.fps = fps
        # 여유분 2장 — 경계에서 방금 필요한 프레임이 밀려나는 걸 막는다
        self.maxlen = max(3, int(seconds * fps) + 2)
        self._buf: deque[TimedFrame] = deque(maxlen=self.maxlen)
        self._lock = threading.Lock()

    def push(self, ts: float, image: np.ndarray) -> None:
        with self._lock:
            self._buf.append(TimedFrame(ts, image))

    def latest(self) -> TimedFrame | None:
        with self._lock:
            return self._buf[-1] if self._buf else None

    def at(self, ts: float) -> TimedFrame | None:
        """주어진 시각에 가장 가까운 프레임.

        정확히 그 시각의 프레임은 존재하지 않는다 (2fps 샘플링이므로).
        가장 가까운 것을 주되, 호출부가 얼마나 벗어났는지 판단할 수 있게 ts를 함께 준다.
        """
        with self._lock:
            if not self._buf:
                return None
            return min(self._buf, key=lambda f: abs(f.ts - ts))

    def before(self, ts: float) -> TimedFrame | None:
        """주어진 시각 **이전**의 가장 최근 프레임. 없으면 가장 오래된 것.

        "행동 직전 화면"에는 이쪽이 맞다. :meth:`at`은 클릭 직후 프레임을 고를 수 있는데,
        그건 이미 화면이 반응하기 시작한 상태일 수 있다.
        """
        with self._lock:
            if not self._buf:
                return None
            older = [f for f in self._buf if f.ts <= ts]
            return older[-1] if older else self._buf[0]

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)

"""플레이 기록 계층 — 캡처·입력 관찰·세션 저장.

이 계층은 **관찰만** 한다. 입력 주입, 프로세스 메모리 접근, 오버레이 후킹을
일절 하지 않는다. 커널 안티치트가 도는 게임(원신/스타레일/명조)에서
안전하게 쓰기 위한 설계 원칙이다.
"""

from .hooks import ClickResolver, InputObserver, RawInput
from .recorder import Recorder, RecorderStats, create_session
from .ringbuffer import FrameRingBuffer, TimedFrame

__all__ = [
    "ClickResolver",
    "FrameRingBuffer",
    "InputObserver",
    "RawInput",
    "Recorder",
    "RecorderStats",
    "TimedFrame",
    "create_session",
]

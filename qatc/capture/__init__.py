"""화면 캡처 계층.

게임 창을 찾아(:mod:`.window`) 세 백엔드 중 실제로 동작하는 것을 골라(:mod:`.base`)
BGR 프레임을 공급한다.
"""

from .base import (
    CaptureBackend,
    CaptureError,
    CaptureTarget,
    available_backends,
    probe,
    select_backend,
)
from .window import (
    WindowInfo,
    ensure_dpi_aware,
    enumerate_windows,
    find_game_window,
    foreground_hwnd,
    get_window_info,
)

__all__ = [
    "CaptureBackend",
    "CaptureError",
    "CaptureTarget",
    "WindowInfo",
    "available_backends",
    "ensure_dpi_aware",
    "enumerate_windows",
    "find_game_window",
    "foreground_hwnd",
    "get_window_info",
    "probe",
    "select_backend",
]

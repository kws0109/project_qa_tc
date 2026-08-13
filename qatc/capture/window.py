"""게임 창 탐색과 클라이언트 영역 추적 (Windows 전용).

두 가지 함정을 여기서 막는다.

**DPI 가상화** — 프로세스가 DPI-unaware이면 Windows 배율 125%에서 ``GetWindowRect``가
가상화된 좌표를 돌려준다. 캡처는 실제 픽셀로 일어나므로 영역이 어긋난다.
:func:`ensure_dpi_aware`를 프로세스 시작 시 반드시 부른다.

**창 영역 vs 클라이언트 영역** — ``GetWindowRect``에는 타이틀바와 테두리가 포함된다.
그걸로 정규화 좌표를 만들면 창모드/전체화면에서 값이 달라진다. 게임 콘텐츠만 담긴
클라이언트 영역을 기준으로 삼아야 해상도·창모드가 바뀌어도 좌표가 유효하다.
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import win32api
    import win32con
    import win32gui
    import win32process
else:  # pragma: no cover - 개발 편의용. 실제 동작은 Windows 전용.
    win32api = win32con = win32gui = win32process = None  # type: ignore[assignment]


_DPI_READY = False


def ensure_dpi_aware() -> bool:
    """프로세스를 Per-Monitor DPI Aware V2로 만든다. 캡처 좌표 정합성의 전제 조건.

    이미 매니페스트로 설정된 경우 실패하는데, 그건 정상이므로 조용히 넘어간다.
    """
    global _DPI_READY
    if _DPI_READY or not _IS_WINDOWS:
        return _DPI_READY
    try:
        # PER_MONITOR_AWARE_V2 = -4
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        _DPI_READY = True
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
            _DPI_READY = True
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
                _DPI_READY = True
            except Exception:
                _DPI_READY = False
    return _DPI_READY


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    process_name: str
    #: 클라이언트 영역의 화면 절대 좌표 (left, top, width, height)
    client_rect: tuple[int, int, int, int]
    is_foreground: bool
    is_minimized: bool

    @property
    def width(self) -> int:
        return self.client_rect[2]

    @property
    def height(self) -> int:
        return self.client_rect[3]

    @property
    def is_capturable(self) -> bool:
        return not self.is_minimized and self.width > 0 and self.height > 0

    def to_normalized(self, screen_x: int, screen_y: int) -> tuple[float, float] | None:
        """화면 절대 좌표를 클라이언트 기준 0.0~1.0으로 바꾼다.

        클라이언트 영역 밖이면 None — 게임 밖 클릭은 기록하지 않는다는 뜻이다.
        """
        left, top, w, h = self.client_rect
        if w <= 0 or h <= 0:
            return None
        nx = (screen_x - left) / w
        ny = (screen_y - top) / h
        if not (0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0):
            return None
        return nx, ny


def _process_name(hwnd: int) -> str:
    """창 소유 프로세스의 실행 파일명. 실패하면 빈 문자열.

    안티치트가 도는 게임은 ``PROCESS_QUERY_INFORMATION`` 이 거부될 수 있다.
    그래서 실패를 정상 경로로 취급하고 제목 매칭으로 폴백한다.
    """
    if not _IS_WINDOWS:
        return ""
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        try:
            path = win32process.GetModuleFileNameEx(handle, 0)
        finally:
            win32api.CloseHandle(handle)
        return path.rsplit("\\", 1)[-1]
    except Exception:
        return ""


def get_window_info(hwnd: int) -> WindowInfo | None:
    if not _IS_WINDOWS or not hwnd or not win32gui.IsWindow(hwnd):
        return None
    ensure_dpi_aware()
    try:
        title = win32gui.GetWindowText(hwnd)
        minimized = bool(win32gui.IsIconic(hwnd))
        # 클라이언트 영역 크기 → 화면 절대 좌표로 변환
        _, _, cw, ch = win32gui.GetClientRect(hwnd)
        left, top = win32gui.ClientToScreen(hwnd, (0, 0))
        return WindowInfo(
            hwnd=hwnd,
            title=title,
            process_name=_process_name(hwnd),
            client_rect=(left, top, cw, ch),
            is_foreground=(win32gui.GetForegroundWindow() == hwnd),
            is_minimized=minimized,
        )
    except Exception:
        return None


def enumerate_windows(visible_only: bool = True) -> list[WindowInfo]:
    """최상위 창 목록. 사용자가 창을 직접 고를 때와 프로파일 자동 매칭에 쓴다."""
    if not _IS_WINDOWS:
        return []
    ensure_dpi_aware()
    found: list[WindowInfo] = []

    def _cb(hwnd: int, _: object) -> bool:
        if visible_only and not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title.strip():
            return True
        info = get_window_info(hwnd)
        # 1x1 툴 윈도우 등 노이즈 제거
        if info and info.width > 200 and info.height > 200:
            found.append(info)
        return True

    win32gui.EnumWindows(_cb, None)
    return found


def find_game_window(profile) -> WindowInfo | None:  # noqa: ANN001 - 순환 import 방지
    """프로파일 규칙에 맞는 창을 찾는다. 여러 개면 포그라운드 → 큰 창 순으로 고른다."""
    candidates = [
        w
        for w in enumerate_windows()
        if profile.matches_window(w.title, w.process_name) and w.is_capturable
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda w: (w.is_foreground, w.width * w.height), reverse=True)
    return candidates[0]


#: 무결성 RID → 이름. UIPI 판정에 쓴다.
_INTEGRITY_NAMES = {
    0: "Untrusted", 4096: "Low", 8192: "Medium",
    8448: "Medium+", 12288: "High", 16384: "System",
}


def process_integrity(pid: int) -> tuple[int, str]:
    """프로세스 무결성 수준 ``(RID, 이름)``. 조회 실패하면 ``(-1, "알 수 없음")``.

    원시 포인터 연산을 쓰지 않는다 — ctypes로 SID를 직접 훑으면 반환 타입 지정을
    빠뜨렸을 때 64비트 포인터가 잘려 세그폴트가 난다. pywin32가 마샬링을 대신하게 둔다.
    """
    if not _IS_WINDOWS:
        return (-1, "알 수 없음")
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TOKEN_QUERY = 0x0008
    TOKEN_INTEGRITY_LEVEL = 25
    try:
        import win32security

        handle = win32api.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        try:
            token = win32security.OpenProcessToken(handle, TOKEN_QUERY)
            sid = win32security.GetTokenInformation(token, TOKEN_INTEGRITY_LEVEL)[0]
            rid = int(win32security.ConvertSidToStringSid(sid).rsplit("-", 1)[-1])
        finally:
            win32api.CloseHandle(handle)
        return (rid, _INTEGRITY_NAMES.get(rid, f"RID {rid}"))
    except Exception:
        return (-1, "알 수 없음")


def input_hook_blocked_by(window: WindowInfo) -> str | None:
    """저수준 입력 훅이 이 창에서 막힐지 미리 판정한다.

    **UIPI**: Windows는 무결성이 낮은 프로세스가 높은 프로세스로 향하는 입력을
    저수준 훅으로 받는 것을 차단한다. 게임이 관리자 권한(High)으로 돌고 레코더가
    일반 권한(Medium)이면 클릭이 하나도 기록되지 않는다 — 그런데 캡처는 정상
    동작하므로 겉보기에는 녹화가 잘 되는 것처럼 보인다.

    5분을 녹화하고 나서 입력이 0건인 걸 발견하는 것은 최악의 실패 방식이다.
    시작 전에 알려준다.

    :returns: 문제가 있으면 사용자에게 보여줄 설명, 없으면 None
    """
    if not _IS_WINDOWS:
        return None
    try:
        import os

        _, target_pid = win32process.GetWindowThreadProcessId(window.hwnd)
        game_rid, game_name = process_integrity(target_pid)
        self_rid, self_name = process_integrity(os.getpid())
    except Exception:
        return None

    if game_rid < 0 or self_rid < 0:
        return None
    if self_rid >= game_rid:
        return None

    return (
        f"게임이 더 높은 권한으로 실행 중입니다 "
        f"(게임 {game_name} vs 이 프로그램 {self_name}).\n"
        f"    Windows UIPI가 입력 훅을 차단하므로 **클릭과 키 입력이 기록되지 않습니다.**\n"
        f"    화면 캡처는 정상 동작하므로 겉보기에는 녹화가 되는 것처럼 보입니다.\n"
        f"    → 이 터미널을 관리자 권한으로 다시 열고 실행하세요."
    )


def foreground_hwnd() -> int:
    return win32gui.GetForegroundWindow() if _IS_WINDOWS else 0


def cursor_pos() -> tuple[int, int]:
    if not _IS_WINDOWS:
        return (0, 0)
    return win32gui.GetCursorPos()

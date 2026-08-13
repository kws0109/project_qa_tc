"""Windows Graphics Capture 백엔드 (1순위).

WGC는 OBS·Discord가 쓰는 OS 표준 캡처 경로다. 창 단위로 잡을 수 있고, 창이 가려져
있어도, 전체화면이어도 동작한다. 안티치트와 무관한 계층이라 안전하다.

**push → pull 변환**: ``windows-capture``는 프레임이 도착할 때마다 콜백을 부르는
push 모델이다. 레코더는 "지금 최신 프레임 하나 줘"라는 pull이 필요하므로,
콜백이 최신 프레임을 슬롯에 덮어쓰고 :meth:`grab`이 그걸 꺼내간다.
큐가 아니라 단일 슬롯인 이유: 오래된 프레임은 가치가 없고, 큐로 두면 소비가
생산을 못 따라갈 때 메모리가 무한히 늘어난다.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

import numpy as np
from windows_capture import Frame, InternalCaptureControl, WindowsCapture

from .base import CaptureBackend, CaptureError, CaptureTarget

#: DwmGetWindowAttribute 의 DWMWA_EXTENDED_FRAME_BOUNDS.
#: WGC가 실제로 캡처하는 영역이 이것이다 — GetWindowRect(그림자 포함)도,
#: 클라이언트 영역도 아닌 제3의 사각형이다.
_DWMWA_EXTENDED_FRAME_BOUNDS = 9


def _extended_frame_bounds(hwnd: int) -> tuple[int, int, int, int] | None:
    """WGC 프레임에 해당하는 화면 좌표 (left, top, width, height). 실패하면 None."""
    rect = wintypes.RECT()
    try:
        hresult = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(_DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
    except Exception:
        return None
    if hresult != 0:
        return None
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


class WgcBackend(CaptureBackend):
    name = "wgc"

    def __init__(self, target: CaptureTarget):
        super().__init__(target)
        self._lock = threading.Lock()
        self._latest: np.ndarray | None = None
        self._control = None
        self._capture: WindowsCapture | None = None
        self._closed = threading.Event()
        self._error: BaseException | None = None

    def start(self) -> None:
        hwnd = self.target.window.hwnd
        capture = WindowsCapture(
            cursor_capture=False,   # 커서는 화면 정체성과 무관하고 해시만 흔든다
            draw_border=False,      # 캡처 테두리가 그려지면 UI 검출에 노이즈가 된다
            window_hwnd=hwnd,
        )

        @capture.event
        def on_frame_arrived(frame: Frame, capture_control: InternalCaptureControl) -> None:
            try:
                # frame_buffer는 BGRA. 슬라이스만 하면 원본 버퍼를 참조하므로 복사한다 —
                # 콜백이 끝나면 네이티브 버퍼가 무효화된다.
                buf = frame.frame_buffer
                with self._lock:
                    self._latest = np.array(buf[:, :, :3], copy=True)
            except Exception as exc:  # 콜백에서 터지면 캡처 스레드가 죽는다
                self._error = exc
                capture_control.stop()

        @capture.event
        def on_closed() -> None:
            self._closed.set()

        self._capture = capture
        try:
            self._control = capture.start_free_threaded()
        except Exception as exc:
            raise CaptureError(f"WGC 캡처를 시작할 수 없습니다 (hwnd={hwnd}): {exc}") from exc

    def grab(self) -> np.ndarray | None:
        if self._error is not None:
            raise CaptureError(f"WGC 캡처 스레드 오류: {self._error}")
        with self._lock:
            frame = self._latest
        return self._finish(self._crop_to_client(frame))

    def _crop_to_client(self, frame: np.ndarray | None) -> np.ndarray | None:
        """WGC 프레임에서 클라이언트 영역만 잘라낸다.

        **이걸 안 하면 타이틀바와 테두리가 캡처에 섞인다.** 스타레일 실측에서
        WGC는 1922x1112를 주는데 클라이언트는 1920x1080이었다 — 세로로 32px,
        화면 높이의 3%가 어긋난다.

        피해는 이미지가 지저분해지는 정도가 아니다. 입력 훅은 좌표를 **클라이언트
        기준**으로 정규화하는데 이미지가 프레임 기준이면 둘이 어긋나, 클릭 지점에
        있던 UI 요소를 잘못 찾는다. TC 절차가 "[강화하기] 클릭" 대신 엉뚱한
        요소를 집거나 좌표로 폴백한다.

        DXGI/GDI 백엔드는 애초에 ``client_rect``로 크롭하므로 이 문제가 없다.
        """
        if frame is None:
            return None
        fh, fw = frame.shape[:2]
        cl, ct, cw, ch = self.target.window.client_rect
        if cw <= 0 or ch <= 0 or (fw, fh) == (cw, ch):
            return frame

        bounds = _extended_frame_bounds(self.target.window.hwnd)
        if bounds is not None:
            ox, oy = cl - bounds[0], ct - bounds[1]
        else:
            # 폴백: 좌우 테두리가 대칭이고 하단 테두리 = 좌우 테두리라고 가정한다.
            # 실측(1922x1112 → 1920x1080)에서 ox=1, oy=31로 정확히 맞았다.
            ox = max(0, (fw - cw) // 2)
            oy = max(0, fh - ch - ox)

        ox = max(0, min(ox, max(0, fw - cw)))
        oy = max(0, min(oy, max(0, fh - ch)))
        return frame[oy : oy + ch, ox : ox + cw]

    def stop(self) -> None:
        if self._control is not None:
            try:
                self._control.stop()
            except Exception:
                pass
            self._control = None
        self._capture = None
        with self._lock:
            self._latest = None

"""DXGI Desktop Duplication 백엔드 (2순위).

전체화면 배타 모드에서 WGC가 실패할 때의 대비책. 데스크톱 전체를 복제하는 pull 모델이라
**게임 창 영역을 직접 잘라내야 한다** — 이게 WGC와의 결정적 차이다.

``windows-capture``의 :class:`DxgiDuplicationSession`을 1순위로 쓰고, 없으면
``dxcam``으로 폴백한다. 둘 다 같은 DXGI API를 감싸지만 패키지 가용성이 환경마다 다르다.

**모니터 인덱스 주의**: 다중 모니터에서 게임이 보조 모니터에 있으면 기본(0번) 복제로는
잡히지 않는다. 창의 화면 좌표로 어느 모니터인지 판별해 인덱스를 넘긴다.
"""

from __future__ import annotations

import numpy as np

from .base import CaptureBackend, CaptureError, CaptureTarget
from .window import _IS_WINDOWS

try:
    from windows_capture import DxgiDuplicationSession

    _HAS_WC_DXGI = True
except Exception:  # pragma: no cover
    DxgiDuplicationSession = None  # type: ignore[assignment]
    _HAS_WC_DXGI = False

try:
    import dxcam

    _HAS_DXCAM = True
except Exception:  # pragma: no cover
    dxcam = None  # type: ignore[assignment]
    _HAS_DXCAM = False

if not (_HAS_WC_DXGI or _HAS_DXCAM):  # pragma: no cover
    raise ImportError("DXGI 백엔드를 쓸 수 없습니다 (windows-capture / dxcam 모두 없음)")


def _monitor_index_for(x: int, y: int) -> int:
    """주어진 화면 좌표가 속한 모니터의 인덱스. 실패하면 0(주 모니터)."""
    if not _IS_WINDOWS:
        return 0
    try:
        import win32api

        monitors = win32api.EnumDisplayMonitors()
        for idx, (_, _, rect) in enumerate(monitors):
            left, top, right, bottom = rect
            if left <= x < right and top <= y < bottom:
                return idx
    except Exception:
        pass
    return 0


class DxgiBackend(CaptureBackend):
    name = "dxgi"

    def __init__(self, target: CaptureTarget):
        super().__init__(target)
        self._session = None
        self._camera = None
        self._monitor_origin = (0, 0)

    def start(self) -> None:
        left, top, _, _ = self.target.window.client_rect
        mon = _monitor_index_for(left, top)
        self._monitor_origin = self._origin_of_monitor(mon)

        if _HAS_WC_DXGI:
            try:
                self._session = DxgiDuplicationSession(monitor_index=mon)
                return
            except Exception:
                self._session = None
        if _HAS_DXCAM:
            try:
                self._camera = dxcam.create(output_idx=mon, output_color="BGR")
                if self._camera is None:
                    raise CaptureError("dxcam.create()가 None을 반환했습니다")
                return
            except Exception as exc:
                raise CaptureError(f"DXGI 캡처를 시작할 수 없습니다: {exc}") from exc
        raise CaptureError("사용 가능한 DXGI 구현이 없습니다")

    @staticmethod
    def _origin_of_monitor(index: int) -> tuple[int, int]:
        """모니터의 좌상단 화면 좌표. 데스크톱 복제 이미지의 (0,0)이 여기에 해당한다."""
        if not _IS_WINDOWS:
            return (0, 0)
        try:
            import win32api

            monitors = win32api.EnumDisplayMonitors()
            if 0 <= index < len(monitors):
                left, top, _, _ = monitors[index][2]
                return (left, top)
        except Exception:
            pass
        return (0, 0)

    def _desktop_frame(self) -> np.ndarray | None:
        if self._session is not None:
            try:
                frame = self._session.acquire_frame()
            except Exception:
                # 해상도 변경/전체화면 전환 시 복제 세션이 무효화된다. 재생성이 정상 대응.
                try:
                    self._session.recreate()
                except Exception:
                    return None
                return None
            if frame is None:
                return None
            arr = frame.to_numpy()
            if arr is not None and arr.ndim == 3 and arr.shape[2] == 4:
                arr = arr[:, :, :3]
            return arr
        if self._camera is not None:
            return self._camera.grab()
        return None

    def grab(self) -> np.ndarray | None:
        desktop = self._desktop_frame()
        if desktop is None:
            return None
        # 데스크톱 이미지에서 게임 클라이언트 영역만 잘라낸다.
        left, top, w, h = self.target.window.client_rect
        ox, oy = self._monitor_origin
        x0, y0 = left - ox, top - oy
        dh, dw = desktop.shape[:2]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(dw, x0 + w), min(dh, y0 + h)
        if x1 <= x0 or y1 <= y0:
            return None
        return self._finish(desktop[y0:y1, x0:x1])

    def stop(self) -> None:
        self._session = None
        if self._camera is not None:
            try:
                self._camera.release()
            except Exception:
                pass
            self._camera = None

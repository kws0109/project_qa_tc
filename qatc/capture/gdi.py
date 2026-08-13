"""GDI 백엔드 (최후 폴백).

``mss``는 어디서나 설치되고 어디서나 예외 없이 동작하지만, D3D 전체화면 게임에서는
**성공하면서 새까만 이미지를 돌려준다.** 그래서 :func:`qatc.capture.base.probe`가
프레임의 밝기까지 검사한다 — 이 백엔드가 조용히 세션 전체를 망치는 걸 막는 장치다.

에뮬레이터(블루아카이브)나 창모드 게임에서는 충분히 잘 동작한다.
"""

from __future__ import annotations

import numpy as np

from .base import CaptureBackend, CaptureError, CaptureTarget

try:
    import mss
except Exception as exc:  # pragma: no cover
    raise ImportError(f"mss를 사용할 수 없습니다: {exc}") from exc


class GdiBackend(CaptureBackend):
    name = "gdi"

    def __init__(self, target: CaptureTarget):
        super().__init__(target)
        self._sct = None

    def start(self) -> None:
        try:
            self._sct = mss.mss()
        except Exception as exc:
            raise CaptureError(f"GDI 캡처를 시작할 수 없습니다: {exc}") from exc

    def grab(self) -> np.ndarray | None:
        if self._sct is None:
            return None
        left, top, w, h = self.target.window.client_rect
        if w <= 0 or h <= 0:
            return None
        try:
            shot = self._sct.grab({"left": left, "top": top, "width": w, "height": h})
        except Exception:
            return None
        # mss는 BGRA를 준다. base._finish가 알파를 떼어낸다.
        return self._finish(np.asarray(shot, dtype=np.uint8))

    def stop(self) -> None:
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None

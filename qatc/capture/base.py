"""캡처 백엔드 추상화 + 자동 선택.

세 백엔드는 잘 되는 상황이 서로 다르다. 하나만 믿으면 특정 게임에서 검은 화면만 나온다.

============  ==========================  ================================
백엔드         잘 되는 상황                  약점
============  ==========================  ================================
WGC           창모드/테두리없는창/에뮬레이터   Windows 10 1903+ 필요
DXGI          전체화면 배타 모드             모니터 단위라 창을 잘라내야 함
GDI (mss)     어디서나 뜨긴 함              전체화면 D3D 게임에서 검은 화면
============  ==========================  ================================

:func:`select_backend`가 실제로 프레임 한 장을 받아본 뒤 성공한 백엔드를 고른다.
import 성공만으로 판단하지 않는 이유는, 라이브러리가 멀쩡히 로드돼도 해당 게임에서
검은 프레임을 주는 경우가 실제로 흔하기 때문이다.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass

import numpy as np

from .window import WindowInfo


class CaptureError(RuntimeError):
    """백엔드를 시작할 수 없거나 프레임을 얻을 수 없을 때."""


@dataclass
class CaptureTarget:
    """무엇을 캡처할지. 창 정보와 프로파일의 ROI를 합친 결과."""

    window: WindowInfo
    #: 클라이언트 영역 안에서 실제로 잘라낼 영역 (정규화). None이면 전체.
    roi: tuple[float, float, float, float] | None = None

    def crop(self, img: np.ndarray) -> np.ndarray:
        """ROI를 적용한다. 에뮬레이터 툴바를 잘라내는 곳이 여기다."""
        if self.roi is None:
            return img
        h, w = img.shape[:2]
        x, y, rw, rh = self.roi
        x0, y0 = int(x * w), int(y * h)
        x1, y1 = int((x + rw) * w), int((y + rh) * h)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
        if x1 <= x0 or y1 <= y0:
            return img
        return img[y0:y1, x0:x1]


class CaptureBackend(abc.ABC):
    """프레임 공급자. 모두 **BGR uint8 ndarray**를 돌려준다 (OpenCV 관례).

    :meth:`grab`은 논블로킹이어야 한다. 레코더의 링버퍼 루프가 정해진 주기로
    호출하는데 여기서 블로킹되면 입력 이벤트 대응이 늦어진다.
    """

    name: str = "base"

    def __init__(self, target: CaptureTarget):
        self.target = target

    @abc.abstractmethod
    def start(self) -> None: ...

    @abc.abstractmethod
    def grab(self) -> np.ndarray | None:
        """최신 프레임(BGR). 아직 준비되지 않았으면 None."""

    @abc.abstractmethod
    def stop(self) -> None: ...

    def __enter__(self) -> CaptureBackend:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- 공통 헬퍼 ---------------------------------------------------

    def _finish(self, img: np.ndarray | None) -> np.ndarray | None:
        """알파 제거 + ROI 적용. 각 백엔드의 grab() 마지막에서 호출한다."""
        if img is None or img.size == 0:
            return None
        if img.ndim == 3 and img.shape[2] == 4:
            img = img[:, :, :3]
        return self.target.crop(img)


def probe(backend_cls: type[CaptureBackend], target: CaptureTarget, tries: int = 12) -> bool:
    """백엔드가 **유효하고 크기가 맞는** 프레임을 주는지 시험한다.

    두 가지를 함께 본다.

    **밝기** — D3D 전체화면 게임에서 GDI 캡처는 예외 없이 성공하면서 새까만
    이미지를 돌려준다. 그걸 통과시키면 세션 전체가 무의미해진다.

    **크기 정합** — 프레임이 기대 크기와 다르면 좌표계가 어긋난 것이다.
    입력 훅은 클라이언트 기준으로 정규화하므로, 이미지가 다른 기준이면 클릭
    지점의 UI 요소를 잘못 찾는다. WGC가 타이틀바를 포함해 돌려주던 버그를
    조용히 통과시킨 전례가 있어 검사에 넣었다.
    """
    import time

    try:
        backend = backend_cls(target)
    except Exception:
        return False
    try:
        backend.start()
        for _ in range(tries):
            frame = backend.grab()
            if frame is not None and frame.size and float(frame.max()) > 8.0:
                return _size_matches(frame, target)
            time.sleep(0.08)
        return False
    except Exception:
        return False
    finally:
        try:
            backend.stop()
        except Exception:
            pass


def _size_matches(frame: np.ndarray, target: CaptureTarget, tol: int = 2) -> bool:
    """프레임 크기가 기대 크기(ROI 적용 후 클라이언트 영역)와 맞는지."""
    _, _, cw, ch = target.window.client_rect
    if cw <= 0 or ch <= 0:
        return True
    if target.roi is not None:
        cw = int(cw * target.roi[2])
        ch = int(ch * target.roi[3])
    fh, fw = frame.shape[:2]
    return abs(fw - cw) <= tol and abs(fh - ch) <= tol


def available_backends() -> list[type[CaptureBackend]]:
    """import 가능한 백엔드를 우선순위 순으로. 실패한 것은 조용히 빠진다."""
    out: list[type[CaptureBackend]] = []
    try:
        from .wgc import WgcBackend

        out.append(WgcBackend)
    except Exception:
        pass
    try:
        from .dxgi import DxgiBackend

        out.append(DxgiBackend)
    except Exception:
        pass
    try:
        from .gdi import GdiBackend

        out.append(GdiBackend)
    except Exception:
        pass
    return out


def select_backend(
    target: CaptureTarget, preferred: str | None = None
) -> tuple[CaptureBackend, str]:
    """실제 프레임을 받아본 뒤 쓸 수 있는 백엔드를 고른다.

    :param preferred: "wgc" / "dxgi" / "gdi" 중 하나를 강제. 실패하면 자동 선택으로 되돌아간다.
    :returns: (시작되지 않은 백엔드 인스턴스, 백엔드 이름)
    :raises CaptureError: 어떤 백엔드도 유효한 프레임을 주지 못한 경우
    """
    candidates = available_backends()
    if preferred:
        candidates.sort(key=lambda c: c.name != preferred)

    tried: list[str] = []
    for cls in candidates:
        tried.append(cls.name)
        if probe(cls, target):
            return cls(target), cls.name

    raise CaptureError(
        "사용 가능한 캡처 백엔드가 없습니다. 시도한 백엔드: "
        + (", ".join(tried) or "(없음)")
        + "\n게임을 창모드 또는 테두리없는창으로 실행하면 성공률이 올라갑니다."
    )

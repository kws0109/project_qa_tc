"""캡처 좌표계 테스트.

**세 개의 좌표계가 존재한다**는 것이 이 계층의 핵심 함정이다.

* ``GetWindowRect`` — DWM 그림자 포함. 스타레일 실측 1936x1119
* DWM 확장 프레임 — WGC가 실제로 캡처하는 영역. 실측 1922x1112
* 클라이언트 영역 — 게임 콘텐츠만. 실측 1920x1080

**모든 좌표는 클라이언트 영역 기준이어야 한다.** 입력 훅이 클릭을 클라이언트
기준으로 정규화하기 때문이다. 캡처 이미지가 다른 기준이면 클릭 지점의 UI 요소를
잘못 찾고, TC 절차가 "[강화하기] 클릭" 대신 엉뚱한 요소를 집는다.

실제로 WGC 백엔드가 타이틀바를 포함해 돌려주던 버그가 있었고
(세로 32px = 화면 높이의 3% 오차), 이 테스트들이 그 회귀를 막는다.
"""

from __future__ import annotations

import numpy as np
import pytest

from qatc.capture.base import CaptureTarget, _size_matches
from qatc.capture.window import WindowInfo

#: 스타레일 실측값
CLIENT_W, CLIENT_H = 1920, 1080
WGC_W, WGC_H = 1922, 1112       # DWM 확장 프레임
TITLEBAR_H = 31                  # 실측 세로 오프셋


def _window(w: int = CLIENT_W, h: int = CLIENT_H) -> WindowInfo:
    return WindowInfo(
        hwnd=0, title="테스트", process_name="Test.exe",
        client_rect=(471, 186, w, h), is_foreground=True, is_minimized=False,
    )


def _frame(w: int, h: int) -> np.ndarray:
    """세로 위치를 식별할 수 있게 행마다 다른 값을 넣은 프레임."""
    frame = np.zeros((h, w, 3), np.uint8)
    for y in range(h):
        frame[y, :, :] = y % 256
    return frame


# ---------------------------------------------------------------- 크기 검사


def test_size_matches_accepts_exact_client_size():
    assert _size_matches(_frame(CLIENT_W, CLIENT_H), CaptureTarget(window=_window()))


def test_size_matches_rejects_titlebar_inclusion():
    """WGC 원본 프레임(타이틀바 포함)은 통과하면 안 된다."""
    assert not _size_matches(_frame(WGC_W, WGC_H), CaptureTarget(window=_window()))


def test_size_matches_tolerates_rounding():
    assert _size_matches(_frame(CLIENT_W + 1, CLIENT_H), CaptureTarget(window=_window()))
    assert not _size_matches(_frame(CLIENT_W + 8, CLIENT_H), CaptureTarget(window=_window()))


def test_size_matches_accounts_for_roi():
    """에뮬레이터처럼 ROI로 잘라낸 경우 기대 크기가 달라진다."""
    target = CaptureTarget(window=_window(), roi=(0.0, 0.045, 0.955, 0.955))
    expected = _frame(int(CLIENT_W * 0.955), int(CLIENT_H * 0.955))
    assert _size_matches(expected, target)
    assert not _size_matches(_frame(CLIENT_W, CLIENT_H), target)


# ---------------------------------------------------------------- ROI 크롭


def test_roi_crop_removes_emulator_chrome():
    """블루아카이브 프로파일이 BlueStacks 툴바를 잘라내는 경로."""
    target = CaptureTarget(window=_window(1000, 1000), roi=(0.0, 0.1, 1.0, 0.9))
    out = target.crop(_frame(1000, 1000))
    assert out.shape[0] == 900
    assert out[0, 0, 0] == 100  # 상단 10%가 제거됐다


def test_roi_none_is_passthrough():
    frame = _frame(640, 480)
    assert CaptureTarget(window=_window()).crop(frame) is frame


def test_roi_degenerate_is_ignored():
    """잘못된 ROI가 빈 이미지를 만들어 파이프라인을 죽이면 안 된다."""
    target = CaptureTarget(window=_window(), roi=(0.9, 0.9, 0.0, 0.0))
    out = target.crop(_frame(100, 100))
    assert out.size > 0


# ---------------------------------------------------------------- WGC 크롭


@pytest.fixture()
def wgc_backend():
    wgc = pytest.importorskip("qatc.capture.wgc", reason="windows-capture 미설치")
    return wgc.WgcBackend


def test_wgc_crops_titlebar_via_fallback(wgc_backend, monkeypatch):
    """DWM 조회가 실패해도 대칭 테두리 가정으로 정확히 잘라내야 한다.

    실측(1922x1112 → 1920x1080)에서 좌우 1px, 상단 31px이 나온다.
    """
    import qatc.capture.wgc as wgc_mod

    monkeypatch.setattr(wgc_mod, "_extended_frame_bounds", lambda _hwnd: None)
    backend = wgc_backend(CaptureTarget(window=_window()))
    out = backend._crop_to_client(_frame(WGC_W, WGC_H))

    assert out.shape[:2] == (CLIENT_H, CLIENT_W)
    # 첫 행이 원본의 31행이어야 한다 = 타이틀바가 정확히 제거됐다
    assert out[0, 0, 0] == TITLEBAR_H


def test_wgc_crops_titlebar_via_dwm(wgc_backend, monkeypatch):
    """DWM 확장 프레임 경계를 쓸 수 있으면 그쪽이 정확하다."""
    import qatc.capture.wgc as wgc_mod

    # 클라이언트가 (471,186), 확장 프레임이 (470,155) → 오프셋 (1, 31)
    monkeypatch.setattr(wgc_mod, "_extended_frame_bounds", lambda _hwnd: (470, 155, WGC_W, WGC_H))
    backend = wgc_backend(CaptureTarget(window=_window()))
    out = backend._crop_to_client(_frame(WGC_W, WGC_H))

    assert out.shape[:2] == (CLIENT_H, CLIENT_W)
    assert out[0, 0, 0] == TITLEBAR_H


def test_wgc_passthrough_when_already_client_sized(wgc_backend):
    """이미 크기가 맞으면 손대지 않는다 (전체화면 등)."""
    backend = wgc_backend(CaptureTarget(window=_window()))
    frame = _frame(CLIENT_W, CLIENT_H)
    assert backend._crop_to_client(frame) is frame


def test_wgc_crop_clamps_bad_offsets(wgc_backend, monkeypatch):
    """DWM이 엉뚱한 값을 줘도 범위를 벗어나 빈 배열이 나오면 안 된다."""
    import qatc.capture.wgc as wgc_mod

    monkeypatch.setattr(wgc_mod, "_extended_frame_bounds", lambda _hwnd: (99999, 99999, 10, 10))
    backend = wgc_backend(CaptureTarget(window=_window()))
    out = backend._crop_to_client(_frame(WGC_W, WGC_H))
    assert out.size > 0
    assert out.shape[:2] == (CLIENT_H, CLIENT_W)


def test_wgc_crop_handles_none(wgc_backend):
    backend = wgc_backend(CaptureTarget(window=_window()))
    assert backend._crop_to_client(None) is None


# ---------------------------------------------------------------- 좌표 정규화


def test_normalized_coords_are_client_relative():
    """클릭 좌표계와 캡처 이미지 좌표계가 같은 기준이어야 한다."""
    window = _window()
    left, top, w, h = window.client_rect
    # 클라이언트 정중앙을 클릭
    norm = window.to_normalized(left + w // 2, top + h // 2)
    assert norm is not None
    assert norm[0] == pytest.approx(0.5, abs=0.001)
    assert norm[1] == pytest.approx(0.5, abs=0.001)


def test_clicks_outside_client_area_are_dropped():
    """타이틀바 클릭이나 창 밖 클릭은 게임 입력이 아니다."""
    window = _window()
    left, top, _, _ = window.client_rect
    assert window.to_normalized(left - 5, top + 10) is None      # 좌측 밖
    assert window.to_normalized(left + 10, top - 20) is None     # 타이틀바

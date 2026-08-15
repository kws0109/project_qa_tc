"""게임 창을 찾아 찍는다.

**고르는 규칙과 찍는 일을 가른다.** 규칙(`select_window`)은 창 목록을 인자로
받는 순수 함수라 실제 창 없이 전부 검사할 수 있고, 찍는 일은 얇은 OS 어댑터로
남겨 라우트 테스트에서 스텁한다. 둘을 붙여 두면 실제 게임이 떠 있지 않은 한
한 줄도 검사할 수 없다.

이 모듈은 지식 DB 를 전혀 모른다. `qatc/app/` 아래에 두지 않는 이유이기도
하다 — 그 폴더는 무쓰기 가드가 이름 대조로 감시하는 영역이고, 캡처는 앱
전용이 아니라 게임 도메인 기능이다.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Sequence

from PIL import Image, ImageGrab

from .profiles import GameProfile


class CaptureError(Exception):
    """캡처 실패. `kind` 는 코드, `message` 는 완성된 한국어 문장이다.

    **소비자는 `kind` 만 본다.** 렌더링된 한국어를 다시 뒤져 분기하면 문구를
    고치는 순간 조용히 깨진다 — 이 프로젝트가 `unknown_session` 판정에서 이미
    겪은 실패다.
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class WindowInfo:
    """창 하나. `rect` 는 (left, top, right, bottom), 물리 픽셀."""

    handle: int
    title: str
    process: str
    rect: tuple[int, int, int, int]
    minimized: bool = False

    @property
    def area(self) -> int:
        left, top, right, bottom = self.rect
        return max(0, right - left) * max(0, bottom - top)


_NO_CONFIG_MSG = (
    "이 게임의 창을 찾을 단서가 없습니다. "
    "profiles/<게임>.yaml 의 window.process 에 실행 파일 이름을 넣어 주세요."
)
_NOT_RUNNING_MSG = (
    "게임 창을 찾지 못했습니다. 게임이 실행 중인지 확인한 뒤 다시 눌러 주세요."
)
_MINIMIZED_MSG = (
    "게임 창이 최소화되어 있습니다. 창을 복원한 뒤 다시 눌러 주세요."
)


def select_window(candidates: Sequence[WindowInfo], profile: GameProfile) -> WindowInfo:
    """프로파일이 가리키는 창 하나를 고른다.

    규칙을 전부 못 박는다 — 하나라도 "적당히" 두면 사용자는 어떤 창이 찍힐지
    예측할 수 없고, 예측할 수 없는 캡처는 근거로 쓸 수 없다.
    """
    if not profile.window_process and not profile.window_title_regex:
        raise CaptureError("no_window_config", _NO_CONFIG_MSG)

    matched = [w for w in candidates if _matches(w, profile)]
    if not matched:
        raise CaptureError("not_running", _NOT_RUNNING_MSG)

    usable = [w for w in matched if not w.minimized]
    if not usable:
        # 최소화된 창은 사각형이 화면 밖(음수 좌표)이라 스크랩이 무의미하다.
        # 다만 "안 떠 있다" 와는 다음 조치가 다르므로 다른 코드로 알린다.
        raise CaptureError("minimized", _MINIMIZED_MSG)

    # 게임은 런처·오버레이 같은 작은 보조 창을 함께 띄운다. `max` 는 동률에서
    # 먼저 나온 것을 돌려주므로 결과가 결정적이다.
    return max(usable, key=lambda w: w.area)


def _matches(window: WindowInfo, profile: GameProfile) -> bool:
    """단서가 **주어진 것만** 본다. 둘 다 주어지면 둘 다 맞아야 한다."""
    if profile.window_process:
        if window.process.lower() != profile.window_process.lower():
            return False
    if profile.window_title_regex:
        try:
            found = re.search(profile.window_title_regex, window.title)
        except re.error as exc:
            # window_title_regex 는 사용자가 profiles/*.yaml 을 손으로 고쳐
            # 넣는 값이다 - 문법이 깨지면 re.search 가 CaptureError 가 아닌
            # re.error 를 던진다. 여기서 안 잡으면 라우트의
            # `except CaptureError` 를 비껴가 Flask 가 그대로 500 트레이스백
            # 으로 바꾼다("사용자는 트레이스백을 안 본다" 는 이 앱의 불변식).
            raise CaptureError(
                "no_window_config",
                f"'{profile.key}' 프로파일의 창 정규식이 올바르지 않습니다 ({exc}). "
                f"profiles/{profile.key}.yaml 의 window.title_regex 값을 고쳐 주세요.",
            ) from exc
        if not found:
            return False
    return True


#: 긴 변 상한. 첨부는 한 장 8MB 인데 5120x1440 이나 4K 창을 그대로 PNG 로
#: 만들면 그 벽에 부딪히고, 사용자에게는 빨간 줄만 보인다. 2560 이면 UI
#: 글자가 읽히면서 상한 안에 들어온다.
MAX_EDGE = 2560

#: 단색 판정 문턱. 실측: 성공한 캡처의 최소 고유색이 576, 실패가 1이었다.
#: 그 사이가 넓어 보수적으로 잡아도 오판이 없다.
_BLANK_UNIQUE_COLORS = 8

_OCCLUDED_MSG = (
    "게임 창이 다른 창에 가려져 있습니다. "
    "게임 창을 앞으로 꺼내거나 서로 겹치지 않게 놓고 다시 눌러 주세요."
)
_BLANK_MSG = (
    "게임 화면을 읽지 못했습니다(빈 화면). "
    "전체화면 배타 모드는 캡처되지 않습니다 - 테두리 없는 창 모드로 바꾸거나, "
    "Win+Shift+S 로 찍어 채팅창에 붙여넣어 주세요."
)


def _is_blank(img: Image.Image) -> bool:
    """캡처가 사실상 아무것도 못 담았는지. 축소본의 고유색으로 본다."""
    small = img.convert("RGB").resize((160, 90))
    colors = small.getcolors(maxcolors=160 * 90)
    return colors is not None and len(colors) <= _BLANK_UNIQUE_COLORS


def _fit(img: Image.Image) -> Image.Image:
    """긴 변이 `MAX_EDGE` 를 넘으면 비율을 유지해 줄인다."""
    width, height = img.size
    longest = max(width, height)
    if longest <= MAX_EDGE:
        return img
    scale = MAX_EDGE / longest
    return img.resize((max(1, round(width * scale)), max(1, round(height * scale))))


def _to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def grab_window(info: WindowInfo, *, printer=None, scraper=None, occluded=None) -> bytes:
    """창 하나를 찍어 PNG 바이트로 돌려준다.

    `printer`/`scraper`/`occluded` 는 테스트가 갈아끼우는 이음매다. 기본값은
    진짜 OS 함수이며, 이 셋을 주입할 수 있어야 결정 트리 전체를 실제 창 없이
    검사할 수 있다.
    """
    printer = printer or _print_window
    scraper = scraper or _screen_grab
    occluded = occluded or _is_occluded

    shot = printer(info)
    if shot is not None and not _is_blank(shot):
        return _to_png(_fit(shot))

    # 여기서 곧바로 스크랩하면 **가려진 경우 가린 창을 찍어 첨부한다.**
    # 사용자는 게임을 보냈다고 믿는다 - 이 기능의 최악의 실패 모양이다.
    if occluded(info):
        raise CaptureError("occluded", _OCCLUDED_MSG)

    shot = scraper(info)
    if _is_blank(shot):
        raise CaptureError("blank", _BLANK_MSG)
    return _to_png(_fit(shot))


import ctypes
from ctypes import wintypes

_PW_RENDERFULLCONTENT = 0x00000002
_dpi_ready = False

# 핸들을 돌려주는 함수의 restype 을 지정하지 않으면 ctypes 가 32비트 부호
# 있는 정수로 해석한다 - 64비트 Windows 에서는 핸들 값이 잘리거나 부호
# 확장되어, 다음 호출에 엉뚱한 객체를 넘기고도 예외 없이 조용히 틀린
# 결과를 낸다. GDI 핸들(HDC/HBITMAP)은 실측에서 상위 비트가 채워진
# 부호 확장 값으로 돌아왔다 - restype 만 고치고 argtypes 를 안 맞추면
# 그 값을 다음 호출에 넘길 때 ctypes 가 기본값인 32비트 c_int 로 다시
# 잘못 해석해 OverflowError 가 난다. 그래서 반환뿐 아니라 그 핸들을
# 인자로 받는 함수 쪽도 함께 지정한다. 모듈이 로드될 때 한 번만 하면 된다.
_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32
_kernel32 = ctypes.windll.kernel32
_psapi = ctypes.windll.psapi

_user32.GetWindowDC.restype = wintypes.HDC
_user32.GetWindowDC.argtypes = [wintypes.HWND]

_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]

_gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]

_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]

_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
_gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                              wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
                              wintypes.UINT]

_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_psapi.GetModuleBaseNameW.argtypes = [wintypes.HANDLE, wintypes.HMODULE,
                                        wintypes.LPWSTR, wintypes.DWORD]

_user32.WindowFromPoint.restype = wintypes.HWND
_user32.WindowFromPoint.argtypes = [wintypes.POINT]
_user32.GetAncestor.restype = wintypes.HWND
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]


def _ensure_dpi_aware() -> None:
    """`SetProcessDPIAware` 를 첫 캡처 때 한 번만 부른다.

    안 부르면 `GetWindowRect` 가 논리 픽셀을 돌려줘 125% 배율에서 오른쪽·아래가
    잘린다. 이 호출은 **프로세스 전역이고 되돌릴 수 없으므로** import 시점이
    아니라 여기서 부른다 - 캡처를 한 번도 안 쓰는 실행(테스트 스위트 전체가
    그렇다)의 전역 상태를 바꾸지 않기 위해서다.
    """
    global _dpi_ready
    if _dpi_ready:
        return
    ctypes.windll.user32.SetProcessDPIAware()
    _dpi_ready = True


def _process_name(hwnd: int) -> str:
    """창을 소유한 프로세스의 실행 파일 이름. 못 얻으면 빈 문자열."""
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = ctypes.windll.kernel32.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        got = ctypes.windll.psapi.GetModuleBaseNameW(handle, None, buf, 260)
        return buf.value if got else ""
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def list_windows() -> list[WindowInfo]:
    """지금 보이는 창 목록. 제목이 없는 창은 뺀다(작업표시줄 등 껍데기)."""
    _ensure_dpi_aware()
    user32 = ctypes.windll.user32
    out: list[WindowInfo] = []
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        out.append(WindowInfo(
            handle=int(hwnd),
            title=title.value,
            process=_process_name(hwnd),
            rect=(rect.left, rect.top, rect.right, rect.bottom),
            minimized=bool(user32.IsIconic(hwnd)),
        ))
        return True

    user32.EnumWindows(proc_type(visit), 0)
    return out


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def _print_window(info: WindowInfo):
    """창 자신에게 자기 내용을 그리게 한다. 실패하면 `None`.

    가려져 있어도 찍히는 것이 이 경로의 값이다. 가속 렌더링 창(일부 게임)은
    rc=0 이거나 검은 화면을 돌려준다 - 호출자가 그걸 보고 폴백한다.
    """
    _ensure_dpi_aware()
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    left, top, right, bottom = info.rect
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return None

    window_dc = user32.GetWindowDC(info.handle)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(info.handle, memory_dc, _PW_RENDERFULLCONTENT):
            return None
        header = _BitmapInfoHeader()
        header.biSize = ctypes.sizeof(_BitmapInfoHeader)
        header.biWidth = width
        header.biHeight = -height          # 음수 = 위에서 아래로
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0
        buf = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(memory_dc, bitmap, 0, height, buf, ctypes.byref(header), 0)
        return Image.frombuffer("RGBA", (width, height), buf, "raw", "BGRA", 0, 1)
    finally:
        # 선택된 채로는 DeleteObject 가 실패한다(FALSE, 안 지워짐) - 지우기
        # 전에 원래 비트맵으로 되돌려야 한다. 안 그러면 호출마다 HBITMAP 이
        # 하나씩 새고, 오래 떠 있는 서버는 GDI 핸들 상한(기본 10,000)에
        # 부딪혀 캡처뿐 아니라 프로세스 전체의 GDI 호출이 죽는다.
        gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(info.handle, window_dc)


def _screen_grab(info: WindowInfo) -> Image.Image:
    """창 사각형 영역의 **화면**을 긁는다. 가려져 있으면 가린 것이 찍힌다."""
    _ensure_dpi_aware()
    return ImageGrab.grab(bbox=info.rect, all_screens=True)


def _is_occluded(info: WindowInfo) -> bool:
    """창 사각형 안 9개 지점에서 최상위 창이 대상인지 본다.

    한 점만 보면 투명 영역·둥근 모서리에서 오판한다. `WindowFromPoint` 는
    자식 컨트롤을 돌려주므로 `GetAncestor(GA_ROOT)` 로 루트까지 올라가 비교한다.
    """
    _ensure_dpi_aware()
    user32 = ctypes.windll.user32
    left, top, right, bottom = info.rect
    for n in (1, 2, 3):
        for m in (1, 2, 3):
            x = left + (right - left) * n // 4
            y = top + (bottom - top) * m // 4
            hit = user32.WindowFromPoint(wintypes.POINT(x, y))
            root = user32.GetAncestor(hit, 2)      # GA_ROOT
            if int(root or 0) == info.handle:
                return False        # 한 점이라도 보이면 가려지지 않은 것
    return True

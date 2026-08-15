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

import re
from dataclasses import dataclass
from typing import Sequence

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
        if not re.search(profile.window_title_regex, window.title):
            return False
    return True

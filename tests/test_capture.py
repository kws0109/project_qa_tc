"""창 선택 규칙. 실제 창 없이 전부 검사한다."""

import pytest

from qatc.capture import CaptureError, WindowInfo, select_window
from qatc.profiles import GameProfile


def _w(handle=1, title="붕괴: 스타레일", process="StarRail.exe",
       rect=(0, 0, 1920, 1080), minimized=False):
    return WindowInfo(handle=handle, title=title, process=process,
                      rect=rect, minimized=minimized)


def _p(process="StarRail.exe", title_regex=""):
    return GameProfile(key="starrail", name="붕괴 스타레일",
                       window_process=process, window_title_regex=title_regex)


def test_a_profile_without_any_clue_is_refused():
    """단서가 없으면 아무거나 고르면 안 된다 — 엉뚱한 창을 찍는 것이 최악이다."""
    with pytest.raises(CaptureError) as e:
        select_window([_w()], _p(process="", title_regex=""))
    assert e.value.kind == "no_window_config"
    assert "profiles" in e.value.message      # 어디를 고쳐야 하는지 알린다


def test_the_process_name_matches_case_insensitively():
    """Windows 의 파일 이름은 대소문자를 구분하지 않는다."""
    assert select_window([_w(process="starrail.EXE")], _p()).handle == 1


def test_the_title_regex_is_used_when_there_is_no_process():
    got = select_window(
        [_w(handle=7, title="Honkai: Star Rail", process="")],
        _p(process="", title_regex="^(Honkai: Star Rail)$"))
    assert got.handle == 7


def test_both_clues_must_match_when_both_are_given():
    """런처와 본 게임이 같은 실행 파일인 경우가 있다."""
    windows = [_w(handle=1, title="StarRail Launcher"),
               _w(handle=2, title="붕괴: 스타레일")]
    got = select_window(windows, _p(title_regex="^(붕괴: 스타레일)$"))
    assert got.handle == 2


def test_no_match_says_the_game_is_not_running():
    with pytest.raises(CaptureError) as e:
        select_window([_w(process="chrome.exe")], _p())
    assert e.value.kind == "not_running"
    assert "실행" in e.value.message


def test_a_minimized_only_match_is_its_own_error():
    """"안 떠 있다" 와 "최소화됐다" 는 사용자가 할 일이 다르다."""
    with pytest.raises(CaptureError) as e:
        select_window([_w(minimized=True)], _p())
    assert e.value.kind == "minimized"
    assert "복원" in e.value.message


def test_the_largest_window_wins_when_several_match():
    """게임은 런처·오버레이 같은 작은 보조 창을 함께 띄운다."""
    windows = [_w(handle=1, rect=(0, 0, 400, 300)),
               _w(handle=2, rect=(0, 0, 1920, 1080)),
               _w(handle=3, rect=(0, 0, 800, 600))]
    assert select_window(windows, _p()).handle == 2


def test_a_tie_is_broken_by_list_order():
    """결정적이지 않으면 테스트도 실사용도 재현이 안 된다."""
    windows = [_w(handle=5, rect=(0, 0, 100, 100)),
               _w(handle=6, rect=(10, 10, 110, 110))]
    assert select_window(windows, _p()).handle == 5

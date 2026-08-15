"""창 선택 규칙. 실제 창 없이 전부 검사한다."""

import io

import pytest
from PIL import Image

from qatc.capture import CaptureError, WindowInfo, select_window
from qatc.capture import MAX_EDGE, grab_window, _is_occluded
from qatc.profiles import GameProfile

PNG_MAGIC = bytes([137, 80, 78, 71, 13, 10, 26, 10])


def _w(handle=1, title="붕괴: 스타레일", process="StarRail.exe",
       rect=(0, 0, 1920, 1080), minimized=False):
    return WindowInfo(handle=handle, title=title, process=process,
                      rect=rect, minimized=minimized)


def _p(process="StarRail.exe", title_regex=""):
    return GameProfile(key="starrail", name="붕괴 스타레일",
                       window_process=process, window_title_regex=title_regex)


def _img(size=(400, 300), color=(10, 120, 200), noisy=True):
    """단색이 아닌 그림. `noisy=False` 면 완전 단색(캡처 실패의 모양)."""
    im = Image.new("RGB", size, color)
    if noisy:
        for x in range(0, size[0], 7):
            for y in range(0, size[1], 5):
                im.putpixel((x, y), ((x * 7) % 256, (y * 3) % 256, 90))
    return im


def _png_size(raw):
    return Image.open(io.BytesIO(raw)).size


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


def test_a_malformed_title_regex_is_a_korean_capture_error_not_a_traceback():
    """`window_title_regex` 는 사용자가 YAML 을 손으로 고친 값이다 - 문법이
    깨지면 `re.search` 가 `re.error` 를 던진다. 그것이 `CaptureError` 로
    바뀌지 않으면 라우트의 `except CaptureError` 를 비껴가 500 트레이스백이
    된다."""
    with pytest.raises(CaptureError) as e:
        select_window([_w()], _p(process="", title_regex="(unclosed["))
    assert e.value.kind == "no_window_config"
    assert "starrail" in e.value.message      # 어느 프로파일 파일인지
    assert "title_regex" in e.value.message


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


def test_print_window_result_is_used_and_the_screen_is_not_scraped():
    """가려져 있어도 찍히는 경로다 — 되면 여기서 끝나야 한다."""
    scraped = []
    raw = grab_window(_w(), printer=lambda i: _img(),
                      scraper=lambda i: scraped.append(i) or _img(),
                      occluded=lambda i: False)
    assert raw[:8] == PNG_MAGIC
    assert scraped == [], "PrintWindow 가 됐는데도 화면을 긁었습니다"


def test_a_failed_print_window_falls_back_to_the_screen():
    """실측: 5개 중 1개가 rc=0 이었다. 폴백은 반드시 타는 경로다."""
    raw = grab_window(_w(), printer=lambda i: None,
                      scraper=lambda i: _img(size=(320, 240)),
                      occluded=lambda i: False)
    assert _png_size(raw) == (320, 240)


def test_a_blank_print_window_result_also_falls_back():
    """rc=1 인데 검은 화면이 나오는 창이 있다 — 반환값만 믿으면 안 된다."""
    raw = grab_window(_w(), printer=lambda i: _img(noisy=False),
                      scraper=lambda i: _img(size=(320, 240)),
                      occluded=lambda i: False)
    assert _png_size(raw) == (320, 240)


def test_an_occluded_window_is_refused_instead_of_scraped():
    """조용히 브라우저를 찍어 첨부하는 것이 이 기능의 최악의 실패다."""
    scraped = []
    with pytest.raises(CaptureError) as e:
        grab_window(_w(), printer=lambda i: None,
                    scraper=lambda i: scraped.append(i) or _img(),
                    occluded=lambda i: True)
    assert e.value.kind == "occluded"
    assert scraped == [], "가려져 있는데 화면을 긁었습니다"
    assert "가려" in e.value.message


def test_a_blank_screen_grab_is_reported_as_blank():
    """가려지지도 않았는데 단색이면 전체화면 배타 모드다 — 다음 조치가 다르다."""
    with pytest.raises(CaptureError) as e:
        grab_window(_w(), printer=lambda i: None,
                    scraper=lambda i: _img(noisy=False),
                    occluded=lambda i: False)
    assert e.value.kind == "blank"
    assert "전체화면" in e.value.message


def test_a_huge_capture_is_downscaled():
    """5120x1440 을 그대로 PNG 로 만들면 첨부 8MB 상한에 부딪힌다."""
    raw = grab_window(_w(), printer=lambda i: _img(size=(5120, 1440)),
                      scraper=lambda i: _img(), occluded=lambda i: False)
    assert _png_size(raw) == (2560, 720), "비율이 유지되지 않았습니다"
    assert max(_png_size(raw)) == MAX_EDGE


def test_a_small_capture_is_left_alone():
    """작은 창까지 손대면 글자만 흐려진다."""
    raw = grab_window(_w(), printer=lambda i: _img(size=(800, 600)),
                      scraper=lambda i: _img(), occluded=lambda i: False)
    assert _png_size(raw) == (800, 600)


# --- `_is_occluded` 의 합치는 규칙 ---------------------------------------


def test_all_nine_points_on_target_means_not_occluded():
    """9곳 전부가 대상 창이면 안 가려진 것 - 성공 경로가 막히면 안 된다."""
    window = _w()
    assert _is_occluded(window, root_at=lambda x, y: window.handle) is False


def test_one_point_off_target_means_occluded():
    """8/9 이 대상이어도 나머지 1곳이 다른 창이면 가려진 것으로 본다.

    "한 점이라도 보이면 안 가려짐" 이던 예전 규칙이 정확히 이 경우를
    놓쳤다 - 버튼을 누르는 순간 사용자는 브라우저 안에 있으므로 창 대부분이
    가려진 채로 찍히는 것이 예외가 아니라 일상이다. 거짓 "안 가려짐" 은
    엉뚱한 화면을 조용히 사용자에게 보낸다 - 거짓 "가려짐"(헛걸음 한 번,
    안내문 하나)보다 훨씬 비싸다.
    """
    window = _w()
    # 9번째로 계산되는 점(마지막 n=3, m=3)만 다른 창을 돌려준다.
    left, top, right, bottom = window.rect
    miss = (left + (right - left) * 3 // 4, top + (bottom - top) * 3 // 4)

    def root_at(x, y):
        return 999 if (x, y) == miss else window.handle

    assert _is_occluded(window, root_at=root_at) is True


def test_all_points_off_target_means_occluded():
    """가장 흔한 경우 - 창이 완전히 덮여 있다."""
    window = _w()
    assert _is_occluded(window, root_at=lambda x, y: 999) is True


def test_exactly_nine_distinct_points_inside_the_rect_are_sampled():
    """표본이 늘거나 줄면 이 판정의 보수성 근거(9곳)가 조용히 바뀐다."""
    window = _w(rect=(0, 0, 1920, 1080))
    seen = []

    def root_at(x, y):
        seen.append((x, y))
        return window.handle       # 전부 대상이어야 조기 반환 없이 9곳 다 돈다

    assert _is_occluded(window, root_at=root_at) is False
    assert len(seen) == 9
    assert len(set(seen)) == 9, "9개 지점이 서로 달라야 한다"
    left, top, right, bottom = window.rect
    assert all(left <= x <= right and top <= y <= bottom for x, y in seen)

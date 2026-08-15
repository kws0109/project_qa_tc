# 게임 창 캡처 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 채팅창의 `[촬영]` 버튼 하나로 프로파일에 못 박힌 게임 창을 찍어, 기존 첨부 스트립에 넣는다.

**Architecture:** 창을 **고르는 규칙**(순수 함수)과 **찍는 일**(OS 어댑터)을 가른다. 규칙은 전부 단위 테스트하고, OS 경로는 주입 가능한 이음매로 만들어 라우트 테스트에서 스텁한다. 진짜 Win32 경로는 라이브 확인이 본다. 캡처 결과는 곧바로 전송되지 않고 첨부 스트립을 거친다.

**Tech Stack:** Python 3.11+ · Flask · ctypes(user32/gdi32) · Pillow · pytest

**Spec:** [../specs/2026-08-15-window-capture-design.md](../specs/2026-08-15-window-capture-design.md)

## Global Constraints

- Windows 전용. 경로는 `pathlib.Path`.
- 콘솔 출력은 `qatc/console.py` 의 `_p()` / `_p(msg, err=True)`. 맨 `print()` 금지.
- 테스트: `.venv/Scripts/python.exe -m pytest` — `-q` 를 더 붙이면 `-qq` 가 되어 개수 줄이 사라진다.
- **백엔드는 지식 DB 에 쓰지 않는다.** 캡처는 지식 루트를 전혀 건드리지 않는다.
- `qatc/app/` 안의 어떤 파일도 지식 DB 쓰기 메서드 이름(`add_testcase` · `set_slot` · `init_content` · `replace_generated` · `add_slot` · `update_testcase_row`)을 담지 않는다 (주석·도크스트링 포함).
- 사용자·화면에 보이는 문자열은 한국어. 오류는 **다음 조치**를 함께 알린다.
- 오류 분기는 **문구가 아니라 코드**(`CaptureError.kind`)로 한다. 렌더링된 한국어를 다시 뒤져 분기하면 문구를 고칠 때 조용히 깨진다 — 이 프로젝트가 `unknown_session` 판정에서 이미 겪었다.
- 작업 트리는 **전부 CRLF** 다 (git 은 `core.autocrlf=true` 로 LF 를 저장한다). 편집 후 바이트와 줄바꿈을 각각 확인할 것. 미추적 파일은 `git diff` 가 아무것도 안 보여준다.
- 커밋 메시지는 한국어. 마지막 줄에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- 시작 테스트 수: **556 passed**.
- **새 테스트는 전부 뮤테이션으로 검증한다.** 구현을 깨뜨려 그 테스트가 실패하는지 확인하고 **에디터로** 복원한다. `git checkout`/`stash`/`reset` 금지.
- **실제 `claude` 턴을 돌리지 않는다.** 이 계획은 캡처만 다룬다 — 라이브 확인도 `/api/capture` 응답까지만 본다.

## 실측 — 이 계획의 전제

설계 단계에서 이 기계로 직접 확인했다.

| 확인 | 결과 |
|---|---|
| 창 열거·제목·사각형·프로세스명 | 동작 (보이는 창 9개) |
| `PrintWindow(PW_RENDERFULLCONTENT)` | 5개 중 4개 rc=1·내용 있음, 1개 **rc=0·고유색 1** |
| 창 사각형 스크랩 | 2574x1399, 고유색 5223 |
| 데스크톱 | 5120x1440 |

**폴백은 반드시 타는 경로다.** 성공 사례 최소 고유색 576 / 실패 1 — 그 사이가 넓어 단색 문턱을 보수적으로 잡아도 오판이 없다.

---

### Task 1: 프로파일이 창 정보를 읽는다

**Files:**
- Modify: `qatc/profiles.py`
- Modify: `profiles/starrail.yaml`, `profiles/genshin.yaml`, `profiles/wuwa.yaml`, `profiles/bluearchive.yaml` (주석만)
- Modify: `tests/test_profiles.py`

**Interfaces:**
- Produces: `GameProfile.window_process: str` · `GameProfile.window_title_regex: str` (없으면 `""`)

**왜 새 설정 형식을 만들지 않는가.** `profiles/*.yaml` 에 이미 네 게임의 `window.process` / `window.title_regex` 가 실측값으로 들어 있다. 녹화 파이프라인이 삭제되면서 `GameProfile` 이 `name` 만 읽게 돼 죽은 값이 됐을 뿐이다. 파일에 달린 "코드에서 읽지 않는다" 주석은 이 작업 뒤 **거짓이 되므로** 함께 고친다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_profiles.py` 에 추가한다. 파일 맨 위에 `ROOT` 가 없으면 `ROOT = Path(__file__).resolve().parents[1]` 를 함께 더한다.

```python
def test_profile_reads_the_window_process_from_disk():
    """실제 프로파일 파일과 대조한다.

    산문이 아니라 파일에서 읽으므로, 누가 `window` 블록을 지우면 여기서 걸린다.
    """
    p = GameProfile.load(ROOT / "profiles" / "starrail.yaml")
    assert p.window_process == "StarRail.exe"
    assert "Star Rail" in p.window_title_regex


def test_a_profile_without_a_window_block_is_not_an_error(tmp_path):
    """`window` 가 없는 프로파일도 있다 — 그때는 빈 문자열이지 예외가 아니다."""
    f = tmp_path / "x.yaml"
    f.write_text("name: 이름만 있는 게임", encoding="utf-8")
    p = GameProfile.load(f)
    assert p.window_process == ""
    assert p.window_title_regex == ""


def test_a_malformed_window_block_does_not_kill_the_loader(tmp_path):
    """`window` 가 매핑이 아니면(리스트·문자열) 그 파일 하나가 모든 게임을 죽인다.

    최상위 매핑 검사와 같은 이유다 — 여기도 방어한다.
    """
    f = tmp_path / "y.yaml"
    f.write_text("name: 게임" + chr(10) + "window: 이건 매핑이 아니다", encoding="utf-8")
    p = GameProfile.load(f)
    assert p.window_process == ""


def test_every_bundled_profile_still_loads():
    """네 게임 전부 로드되는지 — 주석을 고치다 YAML 을 깨뜨리는 것을 잡는다."""
    files = sorted((ROOT / "profiles").glob("*.yaml"))
    assert len(files) >= 4
    for f in files:
        assert GameProfile.load(f).name
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_profiles.py`
Expected: 네 건 FAIL — `AttributeError: 'GameProfile' object has no attribute 'window_process'`

- [ ] **Step 3: 구현**

`qatc/profiles.py` 의 `GameProfile` 을 고친다:

```python
@dataclass
class GameProfile:
    key: str    # 파일명 기반 식별자 (예: "genshin")
    name: str   # 표시 이름 (예: "원신")
    #: 창을 찾는 두 단서. 없으면 빈 문자열 — 그 게임은 캡처를 쓸 수 없다.
    window_process: str = ""
    window_title_regex: str = ""

    @classmethod
    def from_dict(cls, key: str, d: dict[str, Any]) -> GameProfile:
        window = d.get("window")
        if not isinstance(window, dict):
            # 매핑이 아니면(리스트·문자열·None) 단서가 없는 것으로 본다.
            # 여기서 터지면 파일 하나가 모든 게임의 명령을 죽인다 — 최상위
            # 매핑을 검사하는 것과 같은 이유다.
            window = {}
        return cls(
            key=key,
            name=d.get("name", key),
            window_process=str(window.get("process") or ""),
            window_title_regex=str(window.get("title_regex") or ""),
        )
```

모듈 도크스트링의 "창 탐색 ... 더 이상 코드에서 읽지 않는다" 문장을 고친다 — 이제 `window` 는 읽는다.

- [ ] **Step 4: 프로파일 주석 정정**

네 파일 모두의 `# 아래는 현재 코드에서 읽지 않는다` 블록을 고친다. 정확한 서술은 "`window` 는 창 캡처가 읽는다. `capture_roi`·`static_ignore`·`key_hints`·`input` 은 여전히 읽지 않는다" 이다.

- [ ] **Step 5: 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest`
Expected: 556 + 4 = **560 passed**

- [ ] **Step 6: 뮤테이션 검증**

| # | 뮤테이션 | 죽어야 하는 테스트 |
|---|---|---|
| M1 | `window.get("process")` 대신 항상 `""` | `test_profile_reads_the_window_process_from_disk` |
| M2 | `isinstance(window, dict)` 방어 제거 | `test_a_malformed_window_block_does_not_kill_the_loader` |
| M3 | `starrail.yaml` 의 `window` 블록 삭제 | `test_profile_reads_the_window_process_from_disk` |

- [ ] **Step 7: 커밋**

```bash
git commit -m "프로파일의 창 정보를 되살린다 — window.process / title_regex"
```

---

### Task 2: 창 선택 규칙 (순수 함수)

**Files:**
- Create: `qatc/capture.py`
- Create: `tests/test_capture.py`

**Interfaces:**
- Consumes: `GameProfile.window_process` · `GameProfile.window_title_regex` (Task 1)
- Produces:
  - `WindowInfo(handle: int, title: str, process: str, rect: tuple[int, int, int, int], minimized: bool = False)` · 읽기 전용 `.area` 프로퍼티
  - `CaptureError(kind: str, message: str)` — `kind` 는 `no_window_config` · `not_running` · `minimized` · `occluded` · `blank` 중 하나
  - `select_window(candidates: Sequence[WindowInfo], profile: GameProfile) -> WindowInfo`

**이 파일이 `qatc/app/` 아래가 아닌 이유.** 그 폴더는 무쓰기 가드가 **이름 대조로** 감시하고, 캡처는 앱 전용이 아니라 게임 도메인 기능이다. 그리고 Flask 없이 단위 테스트할 수 있어야 한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_capture.py` 를 새로 만든다:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_capture.py`
Expected: 8건 FAIL — `ModuleNotFoundError: No module named 'qatc.capture'`

- [ ] **Step 3: 구현**

`qatc/capture.py` 를 새로 만든다:

```python
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
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest`
Expected: 560 + 8 = **568 passed**

- [ ] **Step 5: 뮤테이션 검증**

| # | 뮤테이션 | 죽어야 하는 테스트 |
|---|---|---|
| M4 | 단서가 없어도 첫 창을 돌려줌 | `test_a_profile_without_any_clue_is_refused` |
| M5 | `.lower()` 두 개 제거 | `test_the_process_name_matches_case_insensitively` |
| M6 | 둘 다 있을 때 프로세스만 봄 | `test_both_clues_must_match_when_both_are_given` |
| M7 | `minimized` 를 `not_running` 으로 뭉갬 | `test_a_minimized_only_match_is_its_own_error` |
| M8 | `max` 를 `min` 으로 | `test_the_largest_window_wins_when_several_match` |
| M9 | 최소화 창을 후보에 남김 | `test_a_minimized_only_match_is_its_own_error` |

- [ ] **Step 6: 커밋**

```bash
git commit -m "게임 창을 고르는 규칙을 순수 함수로 못 박는다"
```

---

### Task 3: 캡처 어댑터 — PrintWindow 우선, 실패하면 스크랩

**Files:**
- Modify: `qatc/capture.py`
- Modify: `tests/test_capture.py`

**Interfaces:**
- Consumes: `WindowInfo` · `CaptureError` (Task 2)
- Produces:
  - `grab_window(info: WindowInfo, *, printer=None, scraper=None, occluded=None) -> bytes` — PNG 바이트
  - `list_windows() -> list[WindowInfo]` — OS 에서 현재 창 목록
  - `MAX_EDGE: int = 2560`

**결정 순서와 그 이유.** `PrintWindow` 를 1순위로 두는 이유는 **가려져 있어도 창 자신의 내용을 그리기 때문**이다. 실측에서 5개 중 4개가 성공했고 실패한 1개는 `rc=0` 으로 드러났다. 실패했을 때 곧바로 화면을 스크랩하면 **가려진 경우 브라우저를 찍어 첨부하게 된다** — 사용자는 게임을 보냈다고 믿는다. 이 기능의 최악의 실패 모양이라, 스크랩 전에 반드시 가려짐을 본다.

**주입 가능한 이음매.** `printer`/`scraper`/`occluded` 를 인자로 받는 이유는 결정 트리 전체를 실제 창 없이 검사하기 위해서다. 기본값은 진짜 OS 함수다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_capture.py` 에 추가:

```python
import io

from PIL import Image

from qatc.capture import MAX_EDGE, grab_window

PNG_MAGIC = bytes([137, 80, 78, 71, 13, 10, 26, 10])


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
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_capture.py`
Expected: 새 7건 FAIL — `ImportError: cannot import name 'grab_window'`

- [ ] **Step 3: 순수 부분 구현**

`qatc/capture.py` 에 추가:

```python
import io

from PIL import Image, ImageGrab

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
```

- [ ] **Step 4: OS 어댑터 구현**

같은 파일에 이어서. 이 부분은 단위 테스트가 아니라 **라이브 확인**이 본다.

```python
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
```

- [ ] **Step 5: 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest`
Expected: 568 + 7 = **575 passed**

- [ ] **Step 6: 뮤테이션 검증**

| # | 뮤테이션 | 죽어야 하는 테스트 |
|---|---|---|
| M10 | `printer` 결과를 무시하고 항상 스크랩 | `test_print_window_result_is_used_and_the_screen_is_not_scraped` |
| M11 | printer 쪽 `_is_blank` 검사 제거 | `test_a_blank_print_window_result_also_falls_back` |
| M12 | 가려짐 검사 제거 | `test_an_occluded_window_is_refused_instead_of_scraped` |
| M13 | 스크랩 결과의 단색 검사 제거 | `test_a_blank_screen_grab_is_reported_as_blank` |
| M14 | `_fit` 이 그대로 통과시킴 | `test_a_huge_capture_is_downscaled` |
| M15 | `_fit` 이 항상 축소함 | `test_a_small_capture_is_left_alone` |

`_is_occluded` 의 9점 검사는 단위 테스트 대상이 아니다 — **라이브 확인**에서 본다 (완료 기준 참고). 한 점만 보게 줄이는 변이는 실제 창 배치로만 드러난다.

- [ ] **Step 7: 커밋**

```bash
git commit -m "창을 찍는다 - PrintWindow 우선, 실패하면 가려짐을 보고 스크랩"
```

---

### Task 4: `POST /api/capture`

**Files:**
- Modify: `qatc/app/server.py`
- Modify: `tests/test_app_server.py`

**Interfaces:**
- Consumes: `select_window` · `grab_window` · `list_windows` · `CaptureError` (Task 2·3), `load_profiles(profiles_dir) -> dict[str, GameProfile]` (기존)
- Produces: `POST /api/capture` — 요청 `{"game": "<키>"}`, 성공 200 `{"data": "<base64 PNG>", "media_type": "image/png"}`

**응답 모양이 `/api/chat` 의 `images[]` 원소와 같다.** 프런트가 캡처 결과를 붙여넣은 이미지와 구분 없이 다룰 수 있고, 백엔드의 `decode_shots` 검증(매직 바이트·크기)도 그대로 한 번 더 지난다. 폭·높이는 싣지 않는다 — 화면에 이미 썸네일이 보이고, 아무도 안 쓰는 필드는 다음 사람에게 "이걸 봐야 하나" 라는 질문만 남긴다.

**검증은 `/api/chat` 의 관문을 나눠 쓴다.** 이 프로젝트가 결함을 낸 자리가 정확히 "검증 없는 새 POST" 였다. 이 엔드포인트는 돈이 들지는 않지만 **화면 내용을 돌려주므로** 교차 출처 차단이 오히려 더 중요하다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_app_server.py` 에 추가. 캡처 자체는 스텁한다 — 실제 창은 라이브 확인이 본다.

```python
def _stub_capture(monkeypatch, *, raw=None, error=None):
    """`/api/capture` 가 부르는 세 함수를 갈아끼운다.

    라우트가 하는 일(프로파일 찾기·오류를 상태 코드로 옮기기·base64 로 싣기)만
    남기고 OS 를 뺀다. OS 경로는 CI 로 재현할 수 없으므로 라이브 확인이 본다.
    """
    import qatc.app.server as server_mod

    monkeypatch.setattr(server_mod, "list_windows", lambda: ["창"])

    def fake_select(candidates, profile):
        return "선택된 창"

    def fake_grab(window):
        if error is not None:
            raise error
        return raw if raw is not None else _png()

    monkeypatch.setattr(server_mod, "select_window", fake_select)
    monkeypatch.setattr(server_mod, "grab_window", fake_grab)


def test_capture_returns_a_base64_png(app, monkeypatch, tmp_path):
    import base64

    (tmp_path / "p").mkdir(parents=True, exist_ok=True)
    (tmp_path / "p" / "starrail.yaml").write_text(
        "name: 붕괴 스타레일" + chr(10) + "window:" + chr(10) + "  process: StarRail.exe",
        encoding="utf-8")
    _stub_capture(monkeypatch, raw=_png())

    r = app.test_client().post("/api/capture", json={"game": "starrail"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["media_type"] == "image/png"
    assert base64.b64decode(body["data"])[:4] == bytes([137, 80, 78, 71])


def test_the_capture_response_matches_the_chat_image_shape(app, monkeypatch, tmp_path):
    """캡처 결과가 붙여넣은 이미지와 같은 모양이어야 프런트가 한 경로로 다룬다."""
    (tmp_path / "p").mkdir(parents=True, exist_ok=True)
    (tmp_path / "p" / "starrail.yaml").write_text(
        "name: 스타레일" + chr(10) + "window:" + chr(10) + "  process: StarRail.exe",
        encoding="utf-8")
    _stub_capture(monkeypatch)

    body = app.test_client().post("/api/capture", json={"game": "starrail"}).get_json()
    assert set(body) == {"data", "media_type"}
    # 그 모양 그대로 /api/chat 에 넣어도 통과해야 한다.
    from qatc.app.chat import decode_shots
    images, refusal = decode_shots([body])
    assert refusal is None and len(images) == 1


_CAPTURE_FAILURES = [
    ("no_window_config", 400), ("not_running", 404),
    ("minimized", 409), ("occluded", 409), ("blank", 409),
]


@pytest.mark.parametrize("kind,status", _CAPTURE_FAILURES, ids=[c[0] for c in _CAPTURE_FAILURES])
def test_capture_errors_map_to_status_codes(app, monkeypatch, tmp_path, kind, status):
    """상태 코드가 원인마다 달라야 화면이 다른 안내를 보여줄 수 있다."""
    from qatc.capture import CaptureError

    (tmp_path / "p").mkdir(parents=True, exist_ok=True)
    (tmp_path / "p" / "starrail.yaml").write_text(
        "name: 스타레일" + chr(10) + "window:" + chr(10) + "  process: StarRail.exe",
        encoding="utf-8")
    _stub_capture(monkeypatch, error=CaptureError(kind, "한국어 사유 문장"))

    r = app.test_client().post("/api/capture", json={"game": "starrail"})
    assert r.status_code == status
    assert r.get_json()["error"] == "한국어 사유 문장"
    assert "CaptureError" not in r.get_data(as_text=True)


def test_a_cross_origin_capture_is_refused(app, monkeypatch):
    """이 엔드포인트는 **화면 내용**을 돌려준다 - 교차 출처 차단이 더 중요하다."""
    called = []
    import qatc.app.server as server_mod
    monkeypatch.setattr(server_mod, "list_windows", lambda: called.append(1) or [])

    r = app.test_client().post("/api/capture", json={"game": "starrail"},
                               headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert called == [], "교차 출처 요청이 화면을 읽었습니다"


def test_a_capture_without_a_game_never_touches_the_screen(app, monkeypatch):
    called = []
    import qatc.app.server as server_mod
    monkeypatch.setattr(server_mod, "list_windows", lambda: called.append(1) or [])

    r = app.test_client().post("/api/capture", json={})
    assert r.status_code == 400
    assert called == []
    assert "트리" in r.get_json()["error"]      # 다음 조치


def test_an_unknown_game_is_a_korean_404(app, monkeypatch):
    import qatc.app.server as server_mod
    monkeypatch.setattr(server_mod, "list_windows", lambda: [])

    r = app.test_client().post("/api/capture", json={"game": "없는게임"})
    assert r.status_code == 404
    assert "프로파일" in r.get_json()["error"]
```

`app` 픽스처의 `profiles_dir` 은 `tmp_path / "p"` 다 — 위 테스트가 그 폴더에 프로파일을 만든다.

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app_server.py -k capture`
Expected: 10건 FAIL — 404 (라우트 없음)

- [ ] **Step 3: 공통 관문을 떼어낸다**

`_reject_bad_chat_request` 의 앞 두 검사(출처·콘텐츠 타입·본문이 dict 인가)는 두 라우트가 똑같이 필요하다. 복사하지 말고 나눠 쓴다 — 갈라지면 한쪽만 뚫린다.

```python
def _reject_bad_local_request(req):
    """로컬 전용 POST 라우트가 공통으로 보는 것. 괜찮으면 `None`.

    1. **출처.** 교차 출처 요청은 거절한다. CORS 응답 헤더로는 부족하다 -
       `text/plain` 같은 단순 요청은 preflight 가 없어서 브라우저가 응답을
       **읽지 못할 뿐 요청은 이미 실행된다.** `/api/chat` 은 그때 이미 돈이
       쓰였고, `/api/capture` 는 그때 이미 화면을 읽었다.
    2. **콘텐츠 타입 · 본문.** JSON 객체여야 한다.
    """
    origin = req.headers.get("Origin") or req.headers.get("Referer")
    if origin and urlsplit(origin).netloc != req.host:
        return jsonify({"error": "이 앱은 로컬에서만 씁니다. "
                                 "브라우저에서 http://127.0.0.1 주소로 다시 여세요."}), 403
    if not req.is_json:
        return jsonify({"error": "요청 형식이 올바르지 않습니다. "
                                 "브라우저를 새로고침한 뒤 다시 보내세요."}), 400
    if not isinstance(req.get_json(silent=True), dict):
        return jsonify({"error": "요청 내용을 읽을 수 없습니다. "
                                 "브라우저를 새로고침한 뒤 다시 보내세요."}), 400
    return None
```

`_reject_bad_chat_request` 는 첫 줄에서 이것을 부르고 `message`·`content` 검사만 남긴다. **기존 `/api/chat` 테스트가 전부 그대로 통과해야 한다** — 통과하지 않으면 관문이 갈라진 것이다.

- [ ] **Step 4: 라우트 구현**

`qatc/app/server.py` 상단에 더한다:

```python
import base64

from ..capture import CaptureError, grab_window, list_windows, select_window
from ..profiles import load_profiles
```

`create_app` 안:

```python
    #: 캡처 실패 코드 -> HTTP 상태. 원인마다 상태가 달라야 화면이 다른 안내를
    #: 보여줄 수 있다. 모르는 코드는 409 로 떨어뜨린다(요청은 멀쩡했고 지금
    #: 상태가 문제라는 뜻이므로).
    capture_status = {
        "no_window_config": 400, "not_running": 404,
        "minimized": 409, "occluded": 409, "blank": 409,
    }

    @app.post("/api/capture")
    def api_capture():
        rejection = _reject_bad_local_request(request)
        if rejection is not None:
            return rejection
        payload = request.get_json(silent=True) or {}
        game = payload.get("game")
        if not isinstance(game, str) or not game.strip():
            return jsonify({"error": "어느 게임의 창을 찍을지 알 수 없습니다. "
                                     "왼쪽 트리에서 컨텐츠를 고른 뒤 다시 눌러 주세요."}), 400
        # `game` 은 요청 본문에서 온다. `resolve_db_path` 와 같은 원칙으로,
        # 이름의 생김새를 보지 않고 **실재하는 프로파일 목록과 대조한다.**
        profile = load_profiles(cfg.profiles_path).get(game)
        if profile is None:
            return jsonify({"error": f"'{game}' 프로파일이 없습니다. "
                                     f"왼쪽 트리에서 게임을 다시 고르세요."}), 404
        try:
            raw = grab_window(select_window(list_windows(), profile))
        except CaptureError as exc:
            # 클래스 이름이 아니라 완성된 한국어 문장을 그대로 보낸다.
            return jsonify({"error": exc.message}), capture_status.get(exc.kind, 409)
        return jsonify({"data": base64.b64encode(raw).decode("ascii"),
                        "media_type": "image/png"})
```

- [ ] **Step 5: 무쓰기 가드에 새 라우트를 태운다**

`test_no_read_endpoint_changes_a_single_byte_of_the_knowledge_root` 에 한 줄 더한다. 새 엔드포인트는 이 가드를 지나야 한다 — 지식 루트를 건드리지 않는다는 것이 이 앱의 중심 성질이다.

```python
    # 캡처도 지식 루트를 건드리지 않는다. OS 는 스텁한다.
    monkeypatch.setattr("qatc.app.server.list_windows", lambda: [])
    c.post("/api/capture", json={"game": "starrail"}).get_data()
```

- [ ] **Step 6: 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest`
Expected: 575 + 10 = **585 passed**

- [ ] **Step 7: 뮤테이션 검증**

| # | 뮤테이션 | 죽어야 하는 테스트 |
|---|---|---|
| M17 | 출처 검사 제거 | `test_a_cross_origin_capture_is_refused` · `test_a_cross_origin_chat_request_is_refused` |
| M18 | `game` 검사 제거 | `test_a_capture_without_a_game_never_touches_the_screen` |
| M19 | 프로파일 목록 대조 없이 `GameProfile(key=game, name=game)` 을 만들어 씀 | `test_an_unknown_game_is_a_korean_404` |
| M20 | 모든 `CaptureError` 를 500 으로 | `test_capture_errors_map_to_status_codes[not_running]` |
| M21 | `exc.message` 대신 `str(type(exc))` | `test_capture_errors_map_to_status_codes[minimized]` |
| M22 | 응답에 `width`/`height` 를 더함 | `test_the_capture_response_matches_the_chat_image_shape` |

- [ ] **Step 8: 커밋**

```bash
git commit -m "화면을 돌려주는 라우트를 연다 - POST /api/capture"
```

---

### Task 5: `[촬영]` 버튼

**Files:**
- Modify: `qatc/app/static/index.html`, `qatc/app/static/app.js`, `qatc/app/static/app.css`
- Modify: `tests/test_app_server.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `POST /api/capture` (Task 4), 기존 `addImageFiles(fileList)`

**캡처 전용 업로드 경로를 만들지 않는다.** 받은 base64 를 `File` 로 만들어 **기존 `addImageFiles()` 에 넣는다** — 4장/8MB 검증·썸네일·`x` 제거·전송이 전부 재사용된다. 두 경로가 생기면 한쪽에만 검증이 붙는다.

**캡처 결과도 곧바로 전송되지 않는다.** 첨부 스트립을 거치므로 사용자가 보고 뺄 수 있다. 화면에 있는 것이 그대로 넘어가는 기능이라 고를 기회가 반드시 있어야 한다 (Task 2 의 개인정보 원칙과 같다).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_app_server.py` 에 추가. **주석을 먼저 걷어낸다** — 직전 작업에서 함수 본문을 비우는 변이가 그 함수의 주석 때문에 살아남았다.

```python
def test_the_capture_button_exists_and_says_what_it_does(app):
    html = app.test_client().get("/").get_data(as_text=True)
    assert 'id="capture-btn"' in html
    assert "촬영" in html


def test_the_capture_handler_feeds_the_existing_attachment_path(app):
    """캡처 전용 업로드 경로가 생기면 검증이 한쪽에만 붙는다."""
    import re

    js = re.sub("//[^" + chr(10) + "]*", "",
                app.test_client().get("/static/app.js").get_data(as_text=True))
    m = re.search(r"async function captureShot\([^)]*\)\s*\{([\s\S]*?)" + chr(10) + r"\}", js)
    assert m, "captureShot 을 찾을 수 없습니다"
    body = m.group(1)
    assert "/api/capture" in body, "캡처 엔드포인트를 부르지 않습니다"
    assert "addImageFiles" in body, "기존 첨부 경로로 넣지 않습니다"


def test_the_capture_button_is_disabled_while_capturing(app):
    """연타하면 4장 상한이 순식간에 차고, 그 안내가 오히려 사용자를 헷갈리게 한다."""
    import re

    js = re.sub("//[^" + chr(10) + "]*", "",
                app.test_client().get("/static/app.js").get_data(as_text=True))
    m = re.search(r"async function captureShot\([^)]*\)\s*\{([\s\S]*?)" + chr(10) + r"\}", js)
    body = m.group(1)
    assert "disabled = true" in body and "disabled = false" in body


def test_the_stylesheet_draws_the_capture_button(app):
    css = app.test_client().get("/static/app.css").get_data(as_text=True)
    assert "#capture-btn" in css
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app_server.py -k capture_button or capture_handler`
Expected: 4건 FAIL

- [ ] **Step 3: 구현**

`index.html` — 첨부 스트립 줄에 버튼을 놓는다:

```html
    <div id="chat-attachments" hidden></div>
    <div id="chat-tools">
      <button id="capture-btn" type="button" title="게임 창을 찍어 첨부합니다">게임 화면 촬영</button>
    </div>
```

`app.js` — 첨부 절 안에 더한다:

```javascript
// 게임 창을 찍어 붙인다. **캡처 전용 업로드 경로를 만들지 않는다** - 받은
// 이미지를 붙여넣기와 똑같이 `addImageFiles` 에 넣어, 4장/8MB 검증과 썸네일과
// `x` 제거를 그대로 쓴다. 두 경로가 생기면 한쪽에만 검증이 붙는다.
//
// 찍은 것도 곧바로 전송되지 않는다 - 첨부 스트립을 거치므로 사용자가 보고 뺄
// 수 있다. 화면에 있는 것이 그대로 넘어가는 기능이라 고를 기회가 있어야 한다.
async function captureShot() {
  const game = state.selectedGame;
  if (!game) {
    appendErrorMessage("먼저 왼쪽 트리에서 컨텐츠를 고르세요 - 어느 게임의 창을 찍을지 알아야 합니다.");
    return;
  }
  const btn = document.getElementById("capture-btn");
  btn.disabled = true;
  try {
    const resp = await fetch("/api/capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ game }),
    });
    let body = null;
    try {
      body = await resp.json();
    } catch (err) {
      body = null;
    }
    if (!resp.ok) {
      appendErrorMessage(
        body && body.error ? body.error : `촬영에 실패했습니다 (${resp.status}).`);
      return;
    }
    const raw = atob(body.data);
    const buf = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) buf[i] = raw.charCodeAt(i);
    await addImageFiles([new File([buf], "capture.png", { type: body.media_type })]);
  } catch (err) {
    appendErrorMessage("서버와 통신하지 못했습니다. 다시 시도하세요.");
  } finally {
    btn.disabled = false;
  }
}
```

`bindAttachments()` 끝에 연결한다:

```javascript
  document.getElementById("capture-btn").addEventListener("click", captureShot);
```

`app.css` — 첨부 절에 더한다:

```css
#chat-tools { padding: 8px 20px 0; flex: 0 0 auto; }

#capture-btn {
  font-size: 0.8rem;
  padding: 5px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: #fff;
  cursor: pointer;
}

#capture-btn:disabled { opacity: 0.6; cursor: not-allowed; }
```

- [ ] **Step 4: README**

`qatc app` 절의 "스크린샷 붙이기" 소절 아래에 이 문단을 그대로 넣는다:

```markdown
### 게임 화면 촬영

`[게임 화면 촬영]` 을 누르면 게임 창을 찾아 한 장 찍어 첨부합니다. **어느 창을
찍을지는 `profiles/<게임>.yaml` 의 `window.process` 가 정합니다** — 예:
`process: StarRail.exe`. 게임 업데이트로 실행 파일 이름이 바뀌면 그 값만
고치면 되고, 창 제목으로 찾고 싶으면 `window.title_regex` 를 씁니다 (둘 다
있으면 둘 다 맞아야 합니다). 트리에서 컨텐츠를 먼저 골라야 어느 게임인지
정해집니다.

찍은 이미지는 붙여넣은 것과 똑같이 첨부 스트립에 들어갑니다 — 보내기 전에
`x` 로 뺄 수 있습니다.

가려진 창도 대개 찍히지만(창 자신에게 그리게 합니다), 안 되는 창은 "가려져
있습니다" 로 알려 줍니다 — 그때는 게임 창을 앞으로 꺼내거나 겹치지 않게
놓으세요. **전체화면 배타 모드는 캡처되지 않습니다.** 테두리 없는 창 모드로
바꾸거나, `Win+Shift+S` 로 찍어 채팅창에 붙여넣으세요.
```

- [ ] **Step 5: 통과 확인**

Run: `.venv/Scripts/python.exe -m pytest`
Expected: 585 + 4 = **589 passed**

- [ ] **Step 6: 뮤테이션 검증**

| # | 뮤테이션 | 죽어야 하는 테스트 |
|---|---|---|
| M23 | 버튼 라벨을 지움 | `test_the_capture_button_exists_and_says_what_it_does` |
| M24 | `addImageFiles` 대신 자체 업로드 경로 | `test_the_capture_handler_feeds_the_existing_attachment_path` |
| M25 | 버튼 비활성화 제거 | `test_the_capture_button_is_disabled_while_capturing` |
| M26 | `#capture-btn` 스타일 규칙 제거 | `test_the_stylesheet_draws_the_capture_button` |

- [ ] **Step 7: 커밋**

```bash
git commit -m "채팅창에서 게임 화면을 한 번에 찍는다"
```

---

## 완료 기준

- 전체 스위트 **589 passed** (숫자가 달라지면 사유를 보고한다)
- 뮤테이션 26종 전부 검출 (M16 은 라이브 확인 항목으로 대체 — 아래 참고)
- `git status --porcelain` 비어 있음 · 작업 트리 전부 CRLF
- **라이브 확인 (필수).** 단위 테스트는 OS 경로를 하나도 보지 않는다. 직전 작업에서 555건이 전부 초록인 채 기능이 죽어 있었고 그것을 잡은 것이 라이브 확인이었다. 앱을 띄우고 게임(또는 대용으로 아무 창이든 프로파일에 넣어)을 실행한 뒤 확인한다:
  1. 게임 창이 **보이는 상태**에서 `[촬영]` -> 첨부 스트립에 게임 화면이 뜬다
  2. 게임 창을 브라우저로 **가린 뒤** `[촬영]` -> 브라우저가 찍히면 **실패**다. `PrintWindow` 가 되면 게임 화면이 나오고, 안 되면 "가려져 있습니다" 가 나와야 한다 (M16 이 여기서 검증된다)
  3. 게임 창을 **최소화**한 뒤 `[촬영]` -> "최소화되어 있습니다"
  4. 게임을 **끈 뒤** `[촬영]` -> "실행 중인지 확인"
  5. 찍힌 이미지의 크기가 긴 변 2560 이하인지
- `<repo>/knowledge` 의 기존 `로그인` 데이터가 손상되지 않았는지 확인 (slots 10 · filled 8 · TC 23)

## 열려 있는 항목 (명세 11절)

- `profiles/bluearchive.yaml` 은 `process` 가 빈 문자열이고 `title_regex` 가 넓다
  (BlueStacks·LDPlayer·MuMu·Nox). 에뮬레이터 창이 여러 개 떠 있으면 **가장 큰
  창** 규칙이 엉뚱한 것을 고를 수 있다. 지금 추측으로 좁히지 않는다 — 그 게임으로
  실제 인터뷰할 때 확인하고, 필요하면 그 파일의 `process` 를 채운다.
- 게임 창이 두 모니터에 걸쳐 있을 때 `ImageGrab.grab(all_screens=True)` 가 온전히
  뜨는지. 라이브 확인에서 본다.

## 만들지 않는 것

- 영역 선택(크롭) UI. 창 전체를 찍는다 — 크롭이 필요하면 `Win+Shift+S` 가 이미 있고 붙여넣기로 들어온다.
- 전체 데스크톱 캡처. 소유자가 개인정보를 이유로 거부했다.
- 배타 전체화면 우회(DXGI Desktop Duplication). 안내로 처리한다 — 창 모드 전환이 훨씬 싸다.
- 캡처 단축키. 버튼 하나로 충분한지 먼저 써 보고 판단한다.

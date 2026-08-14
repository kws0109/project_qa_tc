# `--game` 검증·기본 게임 + xlsx 잠김 처리 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `--game` 오타가 유령 DB를 만드는 것을 막고, 게임 이름을 게임당 한 번만 치게 하고, Excel이 파일을 열고 있을 때 export가 파이썬 예외 이름으로 죽는 것을 고친다.

**Architecture:** `--game` 을 **명시했을 때만** `profiles/` 와 대조한다 (컨텐츠 이름으로 DB를 역추적하는 경로는 검증하지 않는다 — 이미 만들어진 DB의 읽기를 막으면 안 된다). 기본 게임은 `AppConfig` 에 저장하고 `slot init`·`knowledge` 의 `--game` 을 선택 인자로 바꾼다. xlsx 잠김은 `export_tc_excel` 이 도메인 예외로 바꿔 던지고, CLI와 (나중의) 앱이 같은 메시지를 쓴다.

**Tech Stack:** Python 3.11+ · stdlib · `pyyaml` (프로파일) · `openpyxl` (xlsx) · `pytest`

## Global Constraints

- Windows 전용. 경로는 `pathlib.Path` 로만 다룬다.
- 콘솔 출력은 반드시 `qatc/console.py` 의 `_p()` 를 쓴다. 맨 `print()` 금지.
- 테스트 실행: `.venv\Scripts\python.exe -m pytest`
- 새 코드는 Anthropic API를 호출하지 않는다.
- 사용자에게 보이는 문자열은 한국어.
- **오류는 다음 조치를 항상 함께 알린다** (`qatc/cli_knowledge.py:9` 의 계약). 파이썬 예외 이름을 그대로 노출하지 않는다.
- `TCOrigin` 의 기존 문자열 값은 고정 — 엑셀 색 매핑과 기존 DB가 의존한다.
- `--family` 에 argparse `choices` 를 붙이지 않는다.
- 커밋 메시지는 한국어. 마지막 줄에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- 시작 시점 테스트 수: **353 passed**. 각 태스크가 끝날 때 새 수를 보고하고 증감을 설명한다.
- **새 테스트는 전부 뮤테이션으로 검증한다** — 구현을 깨뜨려 그 테스트가 실패하는지 확인하고 원문을 에디터로 다시 써서 복원한다. `git checkout`/`stash`/`reset` 금지. 빈 문자열로 치환하는 뮤테이션 금지(복원 도구가 `str.count("")` 에서 깨진다).

---

## File Structure

| 파일 | 변경 | 책임 |
|---|---|---|
| `qatc/games.py` | **신규** | `--game` 검증 한 곳 (`validate_game`, `known_games`) |
| `tests/conftest.py` | 수정 | `cfg_env` 가 `APPDATA` 를 격리하고 실제 설정 파일·프로파일을 갖게 |
| `qatc/config.py` | 수정 | `AppConfig.default_game` 추가, 영속화 |
| `qatc/cli.py` | 수정 | `qatc config --game <이름>` 세터 |
| `qatc/cli_knowledge.py` | 수정 | `resolve_game`, `slot init`·`knowledge` 배선, `cmd_export` 의 잠김 처리 |
| `qatc/export/tc_excel.py` | 수정 | `ExportBlocked` 예외, `wb.save` 감싸기 |
| `tests/test_games.py` | **신규** | 검증 단위 테스트 |
| `tests/test_config.py` | **신규** | `default_game` 왕복 |
| `tests/test_cli_slot.py` | 수정 | CLI 경계에서의 검증·기본값 |
| `tests/test_cli_knowledge_cmd.py` | 수정 | `config --game`, export 잠김 |
| `tests/test_tc_excel.py` | 수정 | `ExportBlocked` |

`qatc/games.py` 를 새로 만드는 이유 — 검증 규칙이 `cli_knowledge.py`(slot init, knowledge)와 `cli.py`(config --game) 양쪽에서 필요한데, 둘 중 하나에 두면 다른 쪽이 그 모듈을 import 하게 되어 이전 브랜치 Task 6에서 겪은 순환 import 가 재발한다. `console.py` 를 분리했던 것과 같은 이유다.

## ★ 태스크 순서가 중요한 이유 — `cfg_env` 를 먼저 고쳐야 한다

현재 `tests/conftest.py` 의 `cfg_env` 픽스처(테스트 54개가 사용)에는 이 계획을 그대로
막는 성질이 셋 있다. **Task 2 에서 먼저 고치지 않으면 이후 CLI 테스트가 전부 실패한다.**

| 성질 | 이 계획에 미치는 영향 |
|---|---|
| `AppConfig.load` 를 monkeypatch 해 저장 파일을 읽지 않는다 | `config --game` 으로 저장해도 `load().default_game` 이 항상 `""` — 기본 게임 테스트가 원리적으로 통과 불가 |
| `APPDATA` 를 격리하지 않는다 | 테스트 안의 `cfg.save()` 가 **개발자의 실제 `%APPDATA%\qatc\config.json` 을 덮어쓴다.** 지금까지는 아무 테스트도 `save()` 를 부르지 않아 드러나지 않았다 |
| `profiles_dir` 가 존재하지 않는 폴더를 가리킨다 | `validate_game` 이 "프로파일 0개"로 보고 검증을 건너뛴다 — 오타 거부 테스트가 통과 불가 |

---

### Task 1: `--game` 프로파일 대조

**Files:**
- Create: `qatc/games.py`
- Create: `tests/test_games.py`

**Interfaces:**
- Consumes: `qatc.config.AppConfig`, `qatc.profiles.load_profiles`, `qatc.console._p`
- Produces:
  - `validate_game(cfg: AppConfig, game: str) -> None` — 등록되지 않은 이름이면 `SystemExit(문자열)`. 프로파일이 하나도 없으면 경고를 찍고 통과시킨다.
  - `known_games(cfg: AppConfig) -> list[str]` — 정렬된 프로파일 키 목록

**설계 판단 두 가지 (구현자는 이대로 따를 것):**

1. **프로파일이 0개면 검증을 건너뛴다.** 무조건 거부하면 `profiles/` 가 없거나 비었을 때 모든 게임 이름이 막혀 도구 전체가 벽돌이 된다. 건너뛰되 `_p()` 로 눈에 보이게 알린다.
2. **`--game` 을 명시했을 때만 검증한다.** 컨텐츠 이름으로 DB를 역추적하는 `resolve_store` 경로는 검증하지 않는다 — 오타는 생성 시점에 들어오고, 이미 존재하는 DB의 읽기를 막으면 그 데이터에 접근할 방법이 사라진다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_games.py`:

```python
"""`--game` 이 등록된 게임인지 대조한다."""

import pytest

from qatc.config import AppConfig
from qatc.games import known_games, validate_game


def _cfg(tmp_path, profile_names):
    """프로파일 YAML 을 만들고 그것을 가리키는 AppConfig 를 준다."""
    pdir = tmp_path / "profiles"
    pdir.mkdir()
    for key in profile_names:
        (pdir / f"{key}.yaml").write_text(f"name: {key} 표시이름\n", encoding="utf-8")
    return AppConfig(knowledge_root=str(tmp_path / "k"), profiles_dir=str(pdir))


def test_known_games_lists_profile_keys_sorted(tmp_path):
    cfg = _cfg(tmp_path, ["starrail", "genshin"])
    assert known_games(cfg) == ["genshin", "starrail"]


def test_registered_game_passes_without_the_skip_warning(tmp_path, capsys):
    """등록된 이름은 통과한다 — 그리고 **검증을 건너뛴 것이 아니어야 한다.**

    단언 없이 `validate_game(cfg, "starrail")` 만 부르면 검증을 통째로 없앤
    뮤테이션에서도 통과한다. 경고가 없다는 것이 "실제로 대조했다"의 증거다.
    """
    cfg = _cfg(tmp_path, ["starrail"])
    assert validate_game(cfg, "starrail") is None
    assert "건너뜁니다" not in capsys.readouterr().out


def test_typo_is_rejected_and_lists_the_valid_names(tmp_path):
    cfg = _cfg(tmp_path, ["starrail", "genshin"])
    with pytest.raises(SystemExit) as e:
        validate_game(cfg, "starrial")
    msg = str(e.value)
    assert "starrial" in msg                     # 무엇이 틀렸는지
    assert "genshin" in msg and "starrail" in msg  # 무엇을 쓸 수 있는지
    assert str(cfg.profiles_path) in msg          # 어디서 고치는지


def test_no_profiles_at_all_skips_validation_loudly(tmp_path, capsys):
    """프로파일이 0개면 통과시킨다 — 안 그러면 도구가 통째로 벽돌이 된다."""
    cfg = _cfg(tmp_path, [])
    validate_game(cfg, "아무거나")       # 예외 없음
    out = capsys.readouterr().out
    assert "검증" in out                # 건너뛴 사실이 화면에 남는다


def test_missing_profiles_dir_also_skips(tmp_path, capsys):
    cfg = AppConfig(knowledge_root=str(tmp_path / "k"),
                    profiles_dir=str(tmp_path / "없는폴더"))
    validate_game(cfg, "아무거나")
    assert "검증" in capsys.readouterr().out
```

- [ ] **Step 2: 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_games.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qatc.games'`

- [ ] **Step 3: 구현**

`qatc/games.py`:

```python
"""`--game` 이 등록된 게임인지 대조한다.

`cli_knowledge.py`(slot init·knowledge)와 `cli.py`(config --game)가 모두 이
규칙을 쓴다. 둘 중 하나에 두면 다른 쪽이 그 모듈을 import 하게 되어 순환이
생기므로 별도 모듈로 둔다 (`console.py` 를 분리했던 것과 같은 이유).

**검증 시점.** `--game` 을 명시했을 때만 대조한다. 컨텐츠 이름으로 DB를
역추적하는 `resolve_store` 경로는 대조하지 않는다 — 오타는 생성 시점에
들어오고, 이미 존재하는 DB의 읽기를 막으면 그 데이터에 접근할 방법이 없어진다.
"""

from __future__ import annotations

from .config import AppConfig
from .console import _p
from .profiles import load_profiles


def known_games(cfg: AppConfig) -> list[str]:
    """등록된 게임 키 목록 (정렬됨)."""
    return sorted(load_profiles(cfg.profiles_path))


def validate_game(cfg: AppConfig, game: str) -> None:
    """`game` 이 등록된 게임이 아니면 멈춘다.

    :raises SystemExit: 등록되지 않은 이름일 때. `main()` 이 문자열 코드를
        `오류: …` + rc=1 로 바꾼다 (`resolve_store` 와 같은 관용구).

    프로파일이 **하나도 없으면 통과시킨다.** 무조건 거부하면 `profiles/` 가
    없거나 비었을 때 모든 게임 이름이 막혀 도구 전체가 벽돌이 된다. 대신
    건너뛴 사실을 화면에 남긴다 — 조용히 넘기면 검증이 도는 줄 알게 된다.
    """
    names = known_games(cfg)
    if not names:
        _p(f"[경고] {cfg.profiles_path} 에 프로파일이 없어 --game 검증을 건너뜁니다.")
        return
    if game not in names:
        raise SystemExit(
            f"'{game}'은(는) 등록된 게임이 아닙니다. "
            f"사용 가능: {', '.join(names)}. "
            f"프로파일은 {cfg.profiles_path} 에 있습니다."
        )
```

- [ ] **Step 4: 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_games.py -v`
Expected: 5 passed

- [ ] **Step 5: 뮤테이션 검증**

각각 적용 → 전체 스위트 실행 → 지정된 테스트가 실패하는지 확인 → 에디터로 원문 복원 → 358 통과 재확인.

| # | 뮤테이션 | 죽어야 하는 테스트 |
|---|---|---|
| M1 | `if game not in names:` → `if False:` | `test_typo_is_rejected_and_lists_the_valid_names` |
| M2 | `if not names:` 블록 삭제 | `test_no_profiles_at_all_skips_validation_loudly`, `test_missing_profiles_dir_also_skips` |
| M3 | 메시지에서 `', '.join(names)` 를 `'...'` 로 | `test_typo_is_rejected_and_lists_the_valid_names` |
| M4 | `sorted(...)` → `list(...)` | `test_known_games_lists_profile_keys_sorted` |

- [ ] **Step 6: 커밋**

```bash
git add qatc/games.py tests/test_games.py
git commit -m "A: --game 을 profiles 와 대조한다 (프로파일 0개면 건너뛴다)"
```

---

### Task 2: `cfg_env` 픽스처를 실제 설정 파일 기반으로 바꾼다

**Files:**
- Modify: `tests/conftest.py` (`cfg_env`)

**Interfaces:**
- Produces: `cfg_env` 가 **그대로 knowledge 루트 `Path` 를 반환한다** (기존과 동일).
  달라지는 것은 세 가지 — `APPDATA` 가 임시 폴더로 격리되고, 진짜 설정 파일이
  하나 저장되며, `profiles/` 에 `starrail.yaml`·`genshin.yaml` 이 생긴다.

**이 태스크는 프로덕션 코드를 건드리지 않는다.** 테스트 인프라만 바꾼다.

- [ ] **Step 1: 현재 상태 확인**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: 358 passed (Task 1 이후)

Run: `.venv\Scripts\python.exe -c "import os,pathlib;print(pathlib.Path(os.environ['APPDATA'])/'qatc'/'config.json')"`
이 경로의 파일이 지금 존재하는지, 존재하면 내용을 적어 둔다. **작업 중 이 파일이
바뀌면 안 된다** — 마지막에 대조한다.

- [ ] **Step 2: 픽스처를 바꾼다**

`tests/conftest.py` 의 `cfg_env` 를 다음으로 교체한다. docstring 을 이렇게 쓰는 이유는
다음 사람이 왜 진짜 파일을 쓰는지 알아야 하기 때문이다.

```python
@pytest.fixture()
def cfg_env(tmp_path, monkeypatch):
    """`qatc` 가 임시 디렉터리만 보게 만든다. 반환값은 knowledge 루트.

    **진짜 `AppConfig.load()` 를 쓴다.** 예전에는 `load` 를 monkeypatch 해서
    고정된 경로를 돌려줬는데, 그러면 `qatc config --game` 이 저장한 값을
    되읽을 수 없어 기본 게임을 테스트할 방법이 없다.

    `APPDATA` 를 임시 폴더로 돌리는 것이 **필수**다. `AppConfig.config_file()`
    이 `%APPDATA%\\qatc\\config.json` 이므로, 격리하지 않으면 테스트 안의
    `cfg.save()` 가 개발자의 실제 설정 파일을 덮어쓴다.

    프로파일 YAML 두 개를 실제로 만든다. `validate_game` 은 프로파일이 0개면
    검증을 건너뛰므로, 파일이 없으면 "오타를 거부한다"는 테스트가 아무것도
    검증하지 못한다.
    """
    appdata = tmp_path / "appdata"
    kroot = tmp_path / "knowledge"
    pdir = tmp_path / "profiles"
    pdir.mkdir(parents=True, exist_ok=True)
    for key, name in (("starrail", "붕괴 스타레일"), ("genshin", "원신")):
        (pdir / f"{key}.yaml").write_text(f"name: {name}\n", encoding="utf-8")

    monkeypatch.setenv("APPDATA", str(appdata))
    AppConfig(knowledge_root=str(kroot), profiles_dir=str(pdir)).save()
    return kroot
```

- [ ] **Step 3: 전체 스위트를 돌리고 낙진을 정리한다**

Run: `.venv\Scripts\python.exe -m pytest -q`

기존 테스트 일부가 깨질 수 있다. 예상되는 것 — `qatc config` 출력을 단언하는
테스트는 이제 프로파일이 0개가 아니라 2개로 나온다. 깨진 테스트마다:

1. 깨진 이유가 **픽스처 변경의 정당한 결과**인지, **진짜 회귀**인지 판정한다.
2. 정당한 결과면 단언을 새 현실에 맞춘다. 회귀면 픽스처를 고친다.
3. 보고서에 각 건을 한 줄로 기록한다 — 어느 테스트가 왜 바뀌었는지.

`tests/test_cli_knowledge_cmd.py:352-357` 부근에 `APPDATA` 를 직접 설정하는
테스트가 있다. `cfg_env` 도 같은 변수를 설정하므로 상호작용을 확인한다 —
`monkeypatch.setenv` 는 나중 호출이 이기므로 그 테스트가 `cfg_env` 를 쓴다면
자기 값이 이긴다. 실제로 그런지 실행으로 확인한다.

- [ ] **Step 4: 격리를 증명한다**

`tests/test_cli_knowledge_cmd.py` 에 추가한다:

```python
def test_cfg_env_isolates_the_real_user_config(cfg_env, capsys):
    """픽스처 안에서 저장해도 개발자의 실제 설정 파일은 건드리지 않는다.

    이 테스트가 없으면 `save()` 를 부르는 테스트가 하나 늘어날 때마다
    개발자의 `%APPDATA%\\qatc\\config.json` 이 조용히 덮어써진다.
    """
    import os
    from pathlib import Path

    from qatc.config import AppConfig

    here = AppConfig.config_file()
    assert str(cfg_env.parent) in str(here), f"설정 파일이 임시 폴더 밖이다: {here}"
    assert Path(os.environ["APPDATA"]).is_relative_to(cfg_env.parent)
```

- [ ] **Step 5: 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: 358 + 1 = 359 passed (기존 테스트 단언을 고쳤다면 그 수도 보고한다)

- [ ] **Step 6: 실제 사용자 설정 파일이 그대로인지 대조**

Step 1 에서 적어 둔 내용과 대조한다. 달라졌으면 **멈추고 보고한다** — 격리가 안 된 것이다.

- [ ] **Step 7: 뮤테이션 검증**

| # | 뮤테이션 | 죽어야 하는 테스트 |
|---|---|---|
| M5 | `monkeypatch.setenv("APPDATA", ...)` 줄 삭제 | `test_cfg_env_isolates_the_real_user_config` |

- [ ] **Step 8: 커밋**

```bash
git add tests/conftest.py tests/test_cli_knowledge_cmd.py
git commit -m "테스트 픽스처가 APPDATA 를 격리하고 실제 설정 파일을 쓴다"
```

---

### Task 3: 기본 게임을 config 에 저장

**Files:**
- Modify: `qatc/config.py:24-91` (`AppConfig`)
- Modify: `qatc/cli.py` (`cmd_config`, 파서에 `--game`)
- Create: `tests/test_config.py`
- Modify: `tests/test_cli_knowledge_cmd.py`

**Interfaces:**
- Consumes: `qatc.games.validate_game`
- Produces:
  - `AppConfig.default_game: str = ""` — 빈 문자열이면 "설정 안 됨"
  - `qatc config --game <이름>` — 검증 후 저장. 잘못된 이름은 rc=1
  - `qatc config` (인자 없음) — 기존대로 확인만. **쓰지 않는다**

**주의 — 기존 동작을 깨뜨리지 말 것.** `cmd_config` 는 최근에 "확인은 쓰지 않는다"로
고쳐졌고 그것을 지키는 테스트가 있다. `--game` 을 줬을 때만 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_config.py`:

```python
"""`AppConfig.default_game` 왕복."""

import json

from qatc.config import AppConfig


def test_default_game_is_empty_when_unset():
    assert AppConfig().default_game == ""


def test_default_game_survives_save_and_load(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    cfg = AppConfig(knowledge_root=str(tmp_path / "k"),
                    profiles_dir=str(tmp_path / "p"),
                    default_game="starrail")
    cfg.save()
    assert AppConfig.load().default_game == "starrail"


def test_saved_file_actually_contains_the_key(tmp_path, monkeypatch):
    """load() 가 기본값으로 채워서 통과하는 것을 배제한다."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    AppConfig(knowledge_root=str(tmp_path / "k"),
              profiles_dir=str(tmp_path / "p"),
              default_game="genshin").save()
    raw = json.loads(AppConfig.config_file().read_text(encoding="utf-8"))
    assert raw["default_game"] == "genshin"


def test_config_without_default_game_key_still_loads(tmp_path, monkeypatch):
    """예전 설정 파일 호환 — 키가 없어도 깨지지 않는다."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    AppConfig.config_file().write_text(
        json.dumps({"knowledge_root": "kk", "profiles_dir": "pp"}), encoding="utf-8")
    cfg = AppConfig.load()
    assert cfg.default_game == ""
    assert cfg.knowledge_root == "kk"
```

`tests/test_cli_knowledge_cmd.py` 에 추가 (기존 `cfg_env` 픽스처를 쓴다):

```python
def test_config_game_sets_the_default(cfg_env, capsys):
    assert main(["config", "--game", "starrail"]) == 0
    capsys.readouterr()
    from qatc.config import AppConfig
    assert AppConfig.load().default_game == "starrail"


def test_config_game_rejects_an_unregistered_name(cfg_env, capsys):
    rc = main(["config", "--game", "starrial"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "starrial" in out
    assert "starrail" in out          # 쓸 수 있는 이름을 알려준다
    from qatc.config import AppConfig
    assert AppConfig.load().default_game == ""   # 잘못된 값이 저장되지 않았다


def test_plain_config_still_does_not_write(cfg_env, capsys):
    """인자 없는 config 는 확인만 한다 — 기존 계약."""
    assert main(["config", "--game", "starrail"]) == 0
    capsys.readouterr()
    before = AppConfig.config_file().read_bytes()
    assert main(["config"]) == 0
    capsys.readouterr()
    assert AppConfig.config_file().read_bytes() == before
```

- [ ] **Step 2: 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_cli_knowledge_cmd.py -v`
Expected: FAIL — `TypeError: AppConfig() got an unexpected keyword argument 'default_game'`

- [ ] **Step 3: `AppConfig` 에 필드 추가**

`qatc/config.py` — `AppConfig` 를 다음으로 바꾼다 (기존 docstring 은 그대로 둔다):

```python
@dataclass
class AppConfig:
    knowledge_root: str = ""
    profiles_dir: str = ""
    default_game: str = ""      # 빈 문자열이면 "설정 안 됨"
```

`load()` 의 반환을 바꾼다:

```python
        return cls(
            knowledge_root=raw.get("knowledge_root", ""),
            profiles_dir=raw.get("profiles_dir", ""),
            default_game=raw.get("default_game", ""),
        )
```

`save()` 의 딕셔너리를 바꾼다:

```python
        d = {
            "knowledge_root": self.knowledge_root,
            "profiles_dir": self.profiles_dir,
            "default_game": self.default_game,
        }
```

- [ ] **Step 4: `qatc config --game` 세터 구현**

`qatc/cli.py` 의 `cmd_config` 맨 앞(현재 `path = AppConfig.config_file()` 위)에 넣는다:

```python
    game = getattr(args, "game", None)
    if game:
        from .games import validate_game

        validate_game(cfg, game)        # 잘못된 이름이면 여기서 SystemExit
        cfg.default_game = game
        saved = cfg.save()
        _p(f"✓ 기본 게임 = {game}  ({saved})")
        return 0
```

파서에서 `config` 하위명령에 인자를 추가한다 (`build_parser()` 안, `config` 서브파서를 만드는 자리):

```python
    cf.add_argument("--game", "-g", help="기본 게임을 설정한다 (이후 --game 생략 가능)")
```

- [ ] **Step 5: 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: 359 + 7 = 366 passed

- [ ] **Step 6: 뮤테이션 검증**

| # | 뮤테이션 | 죽어야 하는 테스트 |
|---|---|---|
| M6 | `save()` 의 딕셔너리에서 `default_game` 줄 삭제 | `test_saved_file_actually_contains_the_key` |
| M7 | `load()` 의 `default_game=raw.get(...)` 삭제 | `test_default_game_survives_save_and_load` |
| M8 | `validate_game(cfg, game)` 호출 삭제 | `test_config_game_rejects_an_unregistered_name` |
| M9 | `validate_game` 를 `cfg.save()` **뒤로** 이동 | `test_config_game_rejects_an_unregistered_name` (저장 안 됨 단언) |
| M10 | `raw.get("default_game", "")` → `raw["default_game"]` | `test_config_without_default_game_key_still_loads` |

- [ ] **Step 7: 커밋**

```bash
git add qatc/config.py qatc/cli.py tests/test_config.py tests/test_cli_knowledge_cmd.py
git commit -m "D: 기본 게임을 설정에 저장한다 (config --game)"
```

---

### Task 4: `slot init`·`knowledge` 가 검증과 기본값을 쓴다

**Files:**
- Modify: `qatc/cli_knowledge.py` (`cmd_slot_init`, `cmd_knowledge`, `register`)
- Modify: `tests/test_cli_slot.py`
- Modify: `tests/test_cli_knowledge_cmd.py`

**Interfaces:**
- Consumes: `qatc.games.validate_game`, `AppConfig.default_game`
- Produces: `slot init` 과 `knowledge` 의 `--game` 이 **선택 인자**가 된다.
  값이 없고 기본 게임도 없으면 rc=1 로 다음 조치를 알린다.

**순서 주의.** `cmd_slot_init` 은 이미 맨 앞에서 `is_blank(args.content)` 를 검사한다.
게임 해석은 **그 뒤**에 온다 — 이름이 비었으면 게임이 맞든 틀리든 만들 게 없다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_cli_slot.py` 에 추가:

```python
def test_slot_init_rejects_an_unregistered_game(cfg_env, capsys):
    rc = main(["slot", "init", "테스트", "--game", "starrial"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "starrial" in out and "starrail" in out
    assert not list((cfg_env / "starrial.db").parent.glob("starrial.db")), \
        "거부했는데 DB 가 생겼다"


def test_slot_init_uses_the_default_game_when_flag_is_omitted(cfg_env, capsys):
    assert main(["config", "--game", "starrail"]) == 0
    capsys.readouterr()
    assert main(["slot", "init", "파티편성"]) == 0
    capsys.readouterr()
    assert (cfg_env / "starrail.db").exists()


def test_explicit_game_wins_over_the_default(cfg_env, capsys):
    assert main(["config", "--game", "starrail"]) == 0
    capsys.readouterr()
    assert main(["slot", "init", "상점", "--game", "genshin"]) == 0
    capsys.readouterr()
    assert (cfg_env / "genshin.db").exists()
    assert not (cfg_env / "starrail.db").exists()


def test_slot_init_without_game_or_default_says_what_to_do(cfg_env, capsys):
    rc = main(["slot", "init", "파티편성"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "--game" in out
    assert "config" in out           # 기본 게임을 정하는 방법도 알려준다


def test_blank_content_is_rejected_before_the_game_is_resolved(cfg_env, capsys):
    """이름이 비었으면 게임 얘기를 꺼내지 않는다 — 만들 게 없다."""
    rc = main(["slot", "init", "", "--game", "starrial"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "이름이 비어" in out
    assert "등록된 게임이 아닙니다" not in out
```

`tests/test_cli_knowledge_cmd.py` 에 추가:

```python
def test_knowledge_uses_the_default_game(cfg_env, capsys):
    assert main(["config", "--game", "starrail"]) == 0
    capsys.readouterr()
    assert main(["slot", "init", "파티편성", "--game", "starrail"]) == 0
    capsys.readouterr()
    assert main(["knowledge"]) == 0
    assert "파티편성" in capsys.readouterr().out
```

- [ ] **Step 2: 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli_slot.py -v`
Expected: FAIL — `--game` 이 아직 required 라 argparse 가 rc=2 로 죽는다

- [ ] **Step 3: 게임 해석 헬퍼 추가**

`qatc/cli_knowledge.py` 의 `resolve_store` 아래에 넣는다:

```python
def resolve_game(cfg: AppConfig, game: str | None) -> str:
    """`--game` 값이나 설정의 기본 게임을 돌려준다. 둘 다 없으면 멈춘다.

    :raises SystemExit: 어느 쪽도 없을 때, 또는 이름이 등록되지 않았을 때.

    `resolve_store` 와 다르다 — 저쪽은 **읽기**라 컨텐츠 이름으로 DB를
    역추적할 수 있지만, 이쪽은 **생성**이라 어느 DB에 넣을지 사람이 정해야 한다.
    """
    from .games import validate_game

    chosen = game or cfg.default_game
    if not chosen:
        raise SystemExit(
            "어느 게임인지 알 수 없습니다. --game <게임> 을 주거나, "
            "'qatc config --game <게임>' 으로 기본 게임을 정하세요."
        )
    validate_game(cfg, chosen)
    return chosen
```

- [ ] **Step 4: `cmd_slot_init` 을 고친다**

`is_blank` 검사 **뒤**, `types = ...` **앞**에 넣고, 이어지는 두 줄에서 `args.game` 을 `game` 으로 바꾼다:

```python
    game = resolve_game(cfg, args.game)

    types = [t.strip() for t in (args.types or "").split(",") if t.strip()]
    store = KnowledgeStore(cfg.knowledge_path / f"{game}.db").open()
    try:
        content = store.init_content(args.content, game=game, types=types)
```

- [ ] **Step 5: `cmd_knowledge` 를 고친다**

`cmd_knowledge` 가 `args.game` 을 쓰는 첫 자리 앞에 넣고, 이후 `args.game` 을 `game` 으로 바꾼다:

```python
    game = resolve_game(cfg, args.game)
```

- [ ] **Step 6: 파서에서 `required=True` 를 뗀다**

`register(sub)` 안의 두 줄을 바꾼다:

```python
    it.add_argument("--game", "-g")      # 기존: required=True
    kn.add_argument("--game", "-g")      # 기존: required=True
```

- [ ] **Step 7: 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: 366 + 6 = 372 passed

- [ ] **Step 8: 뮤테이션 검증**

| # | 뮤테이션 | 죽어야 하는 테스트 |
|---|---|---|
| M11 | `chosen = game or cfg.default_game` → `chosen = game` | `test_slot_init_uses_the_default_game_when_flag_is_omitted` |
| M12 | `chosen = game or cfg.default_game` → `chosen = cfg.default_game or game` | `test_explicit_game_wins_over_the_default` |
| M13 | `validate_game(cfg, chosen)` 삭제 | `test_slot_init_rejects_an_unregistered_game` |
| M14 | `if not chosen:` → `if False:` | `test_slot_init_without_game_or_default_says_what_to_do` |
| M15 | `resolve_game` 호출을 `is_blank` 검사 **앞**으로 이동 | `test_blank_content_is_rejected_before_the_game_is_resolved` |

- [ ] **Step 9: 커밋**

```bash
git add qatc/cli_knowledge.py tests/test_cli_slot.py tests/test_cli_knowledge_cmd.py
git commit -m "A+D 연결: slot init·knowledge 가 검증과 기본 게임을 쓴다"
```

---

### Task 5: xlsx 잠김을 사람 말로 알린다

**Files:**
- Modify: `qatc/export/tc_excel.py:138-159` (`export_tc_excel`)
- Modify: `qatc/cli_knowledge.py` (`cmd_export`)
- Modify: `tests/test_tc_excel.py`
- Modify: `tests/test_cli_knowledge_cmd.py`

**Interfaces:**
- Produces:
  - `ExportBlocked(OSError)` — `qatc.export.tc_excel` 에 정의. 메시지는 완성된 한국어 문장이라 호출자는 `str(exc)` 를 그대로 쓰면 된다.
  - `export_tc_excel(...)` 이 `PermissionError` 대신 `ExportBlocked` 를 던진다.

**왜 CLI 가 아니라 함수에서 잡는가.** 앞으로 만들 앱이 `export_tc_excel` 을 직접
호출한다 (CLI 를 자식 프로세스로 부르지 않는다). 메시지를 함수에 두면 CLI 와 앱이
같은 문장을 쓴다. CLI 에서만 잡으면 앱이 같은 메시지를 다시 쓰게 되고 둘이 갈라진다.

**실측 (고치기 전):**
```
오류: PermissionError: [Errno 13] Permission denied: '...starrail_잠김_TC.xlsx'
rc=1
```

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_tc_excel.py` 에 추가:

```python
import os
import stat

import pytest

from qatc.export.tc_excel import ExportBlocked, export_tc_excel


def test_locked_target_raises_export_blocked_with_next_action(tmp_path):
    """Excel 이 열어둔 파일에 쓰려 할 때. 읽기 전용으로 같은 조건을 만든다."""
    out = tmp_path / "잠김.xlsx"
    export_tc_excel("컨텐츠", [], [], out)          # 1차 — 정상
    os.chmod(out, stat.S_IREAD)
    try:
        with pytest.raises(ExportBlocked) as e:
            export_tc_excel("컨텐츠", [], [], out)  # 2차 — 막힘
        msg = str(e.value)
        assert str(out) in msg                       # 어느 파일인지
        assert "Excel" in msg and "닫" in msg        # 다음 조치
        assert "PermissionError" not in msg          # 예외 이름을 노출하지 않는다
    finally:
        os.chmod(out, stat.S_IWRITE)                 # 다른 테스트를 위해 되돌린다


def test_export_blocked_is_an_oserror():
    """호출자가 OSError 로도 잡을 수 있어야 한다."""
    assert issubclass(ExportBlocked, OSError)
```

`tests/test_cli_knowledge_cmd.py` 에 추가:

```python
def test_export_on_a_locked_file_tells_the_user_to_close_excel(cfg_env, capsys):
    import os
    import stat

    assert main(["slot", "init", "잠김", "--game", "starrail"]) == 0
    assert main(["slot", "set", "잠김", "core_action",
                 "--status", "filled", "--value", "동작"]) == 0
    capsys.readouterr()
    assert main(["export", "잠김"]) == 0
    out1 = capsys.readouterr().out
    path = out1.split("✓")[1].split("(")[0].strip()

    os.chmod(path, stat.S_IREAD)
    try:
        rc = main(["export", "잠김"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "Excel" in out and "닫" in out
        assert "PermissionError" not in out
    finally:
        os.chmod(path, stat.S_IWRITE)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_tc_excel.py -v`
Expected: FAIL — `ImportError: cannot import name 'ExportBlocked'`

- [ ] **Step 3: 예외를 정의하고 `save` 를 감싼다**

`qatc/export/tc_excel.py` — `export_tc_excel` 정의 **위**에 넣는다:

```python
class ExportBlocked(OSError):
    """대상 파일에 쓸 수 없다 — 보통 Excel 이 그 파일을 열어 두고 있다.

    메시지는 완성된 한국어 문장이라 호출자는 ``str(exc)`` 를 그대로 쓰면 된다.
    CLI 와 앱이 같은 문장을 쓰게 하려고 CLI 가 아니라 여기서 만든다.
    """
```

`export_tc_excel` 안의 `wb.save(path)` 를 바꾼다:

```python
    try:
        wb.save(path)
    except PermissionError as exc:
        raise ExportBlocked(
            f"파일에 쓸 수 없습니다 — {path}. "
            f"Excel에서 이 파일을 닫고 다시 시도하세요."
        ) from exc
    return path
```

- [ ] **Step 4: `cmd_export` 가 잡게 한다**

`qatc/cli_knowledge.py` 의 `cmd_export` 에서 import 줄을 바꾸고:

```python
    from .export.tc_excel import ExportBlocked, export_tc_excel
```

`path = export_tc_excel(...)` 을 감싼다:

```python
    try:
        path = export_tc_excel(args.content, cases, skipped, out, withdrawn)
    except ExportBlocked as exc:
        _p(f"오류: {exc}")
        return 1
```

- [ ] **Step 5: 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: 372 + 3 = 375 passed

- [ ] **Step 6: 뮤테이션 검증**

| # | 뮤테이션 | 죽어야 하는 테스트 |
|---|---|---|
| M16 | `except PermissionError` 블록 삭제 (맨 `wb.save(path)` 로) | `test_locked_target_raises_export_blocked_with_next_action`, `test_export_on_a_locked_file_tells_the_user_to_close_excel` |
| M17 | 메시지에서 `{path}` 를 `파일` 로 | `test_locked_target_raises_export_blocked_with_next_action` |
| M18 | 메시지에서 `Excel에서 이 파일을 닫고 다시 시도하세요.` 삭제 | 위 두 테스트 |
| M19 | `class ExportBlocked(OSError)` → `class ExportBlocked(Exception)` | `test_export_blocked_is_an_oserror` |
| M20 | `cmd_export` 의 `except ExportBlocked` 블록 삭제 | `test_export_on_a_locked_file_tells_the_user_to_close_excel` |

- [ ] **Step 7: 커밋**

```bash
git add qatc/export/tc_excel.py qatc/cli_knowledge.py tests/test_tc_excel.py tests/test_cli_knowledge_cmd.py
git commit -m "xlsx 잠김을 사람 말로 알린다 (Excel 이 열고 있을 때)"
```

---

### Task 6: 문서를 실제 동작에 맞춘다

**Files:**
- Modify: `README.md`
- Modify: `.claude/skills/interview/SKILL.md`
- Modify: `docs/superpowers/specs/2026-08-14-interview-driven-tc-design.md`
- Modify: `tests/test_interview_skill.py`

**이 태스크가 필요한 이유.** `README.md:66-67` 은 지금 *"`--game` 은 컨텐츠 이름이 한
게임의 지식 저장소에서만 발견되면 생략할 수 있습니다"* 라고만 한다. Task 3·4 이후
`slot init` 에서도 생략 가능해졌고 방법이 다르다(기본 게임). `SKILL.md` 는 신규 컨텐츠
분기에서 `--game <게임>` 을 요구하는데, 기본 게임이 있으면 물어볼 필요가 없다 —
그대로 두면 모델이 매번 게임을 되묻는다.

- [ ] **Step 1: 실제 출력을 확보한다**

Run:
```bash
.venv\Scripts\qatc.exe config --game starrail
.venv\Scripts\qatc.exe slot init 확인용
.venv\Scripts\qatc.exe slot init 확인용2 --game 없는게임
```
세 출력을 그대로 받아 적어 둔다. 문서에 인용할 문구는 **추측하지 말고 실행 결과를 쓴다.**

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_interview_skill.py` 에 추가:

```python
def test_skill_tells_the_model_about_the_default_game():
    """기본 게임이 있으면 게임을 되묻지 않아야 한다.

    이 문장이 없으면 모델은 신규 컨텐츠마다 `--game` 을 물어보고, 사용자는
    `config --game` 을 설정한 보람이 없어진다.
    """
    text = SKILL.read_text(encoding="utf-8")
    assert "qatc config --game" in text
    assert "기본 게임" in text


def test_readme_documents_the_default_game():
    from pathlib import Path

    readme = Path(__file__).resolve().parent.parent / "README.md"
    text = readme.read_text(encoding="utf-8")
    assert "config --game" in text
    assert "기본 게임" in text
```

- [ ] **Step 3: 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests/test_interview_skill.py -v`
Expected: FAIL — 두 테스트 모두 AssertionError

- [ ] **Step 4: `README.md` 를 고친다**

"지금 동작하는 명령" 목록에 한 줄을 넣는다:

```
qatc config --game starrail                              # 기본 게임 설정 (이후 --game 생략 가능)
```

그 아래 `--game` 설명 문단을 다음으로 바꾼다:

```markdown
`--game` 은 두 가지 방법으로 생략할 수 있습니다. **읽기 명령**(`slot status`,
`tc plan`, `export` 등)은 컨텐츠 이름이 한 게임에서만 발견되면 알아서 찾습니다.
**생성 명령**(`slot init`, `knowledge`)은 `qatc config --game <게임>` 으로 정해 둔
기본 게임을 씁니다. 여러 게임에 같은 이름의 컨텐츠가 있으면 명시해야 합니다.

게임 이름은 `profiles/` 의 프로파일과 대조되므로 오타는 거부됩니다.
```

- [ ] **Step 5: `SKILL.md` 를 고친다**

신규 컨텐츠 분기에서 게임을 묻는 대목에, Step 1 에서 실측한 문구를 근거로 다음 취지를
넣는다 — **`slot init` 을 `--game` 없이 먼저 시도하고, "어느 게임인지 알 수 없습니다"
오류가 나올 때만 사용자에게 묻는다.** 기본 게임이 설정돼 있으면 묻지 않게 된다.

- [ ] **Step 6: 설계 문서에 정정 주석을 단다**

`docs/superpowers/specs/2026-08-14-interview-driven-tc-design.md` 의 `--game` 을 다루는
자리에, 기존 문장을 지우지 말고 날짜가 붙은 정정 블록을 덧붙인다 (이 저장소의 관례):

```markdown
> **2026-08-14 정정:** 이 절이 쓰일 당시 `--game` 은 어떤 검증도 받지 않았고
> `slot init` 에서 필수였다. 지금은 `profiles/` 와 대조되며, `qatc config --game`
> 으로 기본 게임을 정하면 생략할 수 있다.
```

- [ ] **Step 7: 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: 375 + 2 = 377 passed

- [ ] **Step 8: 뮤테이션 검증**

| # | 뮤테이션 | 죽어야 하는 테스트 |
|---|---|---|
| M21 | `README.md` 에서 `기본 게임` 문단 삭제 | `test_readme_documents_the_default_game` |
| M22 | `SKILL.md` 에서 `qatc config --game` 언급 삭제 | `test_skill_tells_the_model_about_the_default_game` |

- [ ] **Step 9: 커밋**

```bash
git add README.md .claude/skills/interview/SKILL.md docs/superpowers/specs/2026-08-14-interview-driven-tc-design.md tests/test_interview_skill.py
git commit -m "문서를 --game 검증·기본 게임에 맞춘다"
```

---

## 완료 기준

- 전체 스위트 **377 passed**
- `slot init 테스트 --game starrial` → rc=1, 유효한 게임 목록 표시, DB 미생성
- `qatc config --game starrail` 후 `slot init 파티편성` → rc=0
- Excel 이 연 xlsx 에 `qatc export` → rc=1, "Excel에서 이 파일을 닫고" 안내
- `git status --porcelain` 비어 있음
- 뮤테이션 22종 전부 검출

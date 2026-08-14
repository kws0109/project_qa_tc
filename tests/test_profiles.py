"""프로파일 로더 — 손으로 만든 깨짐을 어디까지 견디는가.

배경(최종 검토 F1): `load_profiles` 의 도크스트링은 "깨진 파일은 건너뛰되
이유를 남긴다" 를 약속한다. 그런데 잡는 예외가
`(yaml.YAMLError, KeyError, ValueError, OSError)` 뿐이라, **문법은 맞지만
최상위가 매핑이 아닌** YAML 은 `GameProfile.from_dict` 의 `d.get(...)` 에서
`AttributeError` 를 내며 그 그물을 통과했다. 그 예외는 `cli.py` 의 포괄
핸들러까지 올라가 `오류: AttributeError: 'list' object has no attribute 'get'`
로 나온다 — 이 브랜치가 없애기로 한 바로 그 출력 형태다.

트리거가 "리스트를 붙여넣었을 때" 보다 넓다는 점이 중요하다. `profiles/` 에
남긴 **콜론 없는 메모 파일 한 장**이면 YAML 은 그것을 스칼라로 읽고, 같은
`AttributeError` 가 난다. 그리고 파일 하나가 **모든 게임**의 명령을 막는다.
"""

from __future__ import annotations

import pytest

from qatc.cli import main
from qatc.profiles import load_profiles


#: 문법은 맞지만 최상위가 매핑이 아닌 것들. 사람이 실제로 만드는 형태다.
#:
#: 아래 절반(`falsy` 무리)이 따로 필요한 이유 — `GameProfile.load` 가
#: `yaml.safe_load(...) or {}` 를 **`isinstance` 검사보다 먼저** 하는 동안,
#: 거짓값인 최상위는 전부 `{}` 로 정규화돼 가드에 닿지도 못했다. 그 파일들은
#: 건너뛰어지는 대신 **파일명을 키로 한 프로파일로 조용히 등록**됐다
#: (실측: `[]` 만 든 `empty-list.yaml` → `profiles=['empty-list','starrail']`).
#: 위 절반은 전부 참값이라 그 구멍을 한 번도 밟지 않는다 — 매개변수 표가
#: 넓어 보이는데 정작 갈라지는 지점을 비껴간 모양이다.
NON_MAPPING_YAML = [
    # --- 참값: `or {}` 를 그냥 통과해 가드에 닿았던 것들 ---
    ("- starrail\n- genshin\n", "list", "게임 목록을 붙여넣은 것"),
    ("starrail\n", "str", "콜론 없는 메모 한 줄"),
    ("42\n", "int", "숫자만 있는 파일"),
    ("[a, b]\n", "list", "인라인 리스트"),
    # --- 거짓값: `or {}` 가 먼저 삼켜 가드에 닿지 못했던 것들 ---
    ("[]\n", "list", "빈 리스트 — 항목을 다 지운 목록"),
    ("0\n", "int", "숫자 0"),
    ("''\n", "str", "빈 문자열"),
    ("false\n", "bool", "불리언 false"),
]
NON_MAPPING_IDS = ["block-list", "bare-scalar", "number", "flow-list",
                   "empty-list", "zero", "empty-string", "false"]

#: 최상위가 **비어 있는** 것들. YAML 은 셋 다 `None` 으로 읽는다.
EMPTY_YAML = ["", "null\n", "# 메모만 적어 둔 파일\n"]
EMPTY_IDS = ["empty-file", "explicit-null", "comment-only"]


@pytest.mark.parametrize("text,typename,_why", NON_MAPPING_YAML, ids=NON_MAPPING_IDS)
def test_non_mapping_profile_is_skipped_by_name(tmp_path, capsys, text, typename, _why):
    """최상위가 매핑이 아니면 **건너뛴다** — 읽을 수 없는 파일과 같은 대접."""
    (tmp_path / "starrail.yaml").write_text("name: 붕괴 스타레일\n", encoding="utf-8")
    (tmp_path / "메모.yaml").write_text(text, encoding="utf-8")

    profiles = load_profiles(tmp_path)

    # 멀쩡한 파일은 살아남는다 — 한 장이 전부를 막지 않는다
    assert set(profiles) == {"starrail"}

    err = capsys.readouterr().err
    assert "메모.yaml" in err          # 어느 파일인지
    assert "건너뜁니다" in err          # 무엇을 했는지
    assert typename in err             # 왜 (무엇이 들어 있었는지)


@pytest.mark.parametrize("text", EMPTY_YAML, ids=EMPTY_IDS)
def test_an_empty_profile_file_is_still_a_valid_profile(tmp_path, capsys, text):
    """빈 파일 · `null` · 주석뿐인 파일 → `{}` → **유효한 프로파일**.

    위 건너뛰기의 **반대쪽 경계**다. `None` 을 `{}` 로 받아 주는 것은 실수가
    아니라 오래된 의도다 — `name` 은 없어도 되고(파일명이 키이자 표시 이름),
    `profiles/` 의 YAML 은 지금 코드가 읽지 않는 옛 필드를 잔뜩 달고 있어서
    전부 주석 처리한 파일이 남아 있을 수 있다. 그런 파일까지 건너뛰면 등록돼
    있던 게임이 목록에서 사라지고, 그 게임의 생성 명령이 통째로 거부된다.

    거짓값 건너뛰기를 넣을 때 `data = yaml.safe_load(...) or {}` 를 그냥
    `if not isinstance(data, dict)` 앞뒤로 옮기기만 하면 이 경계가 함께
    무너진다 (`None` 도 매핑이 아니다). `None` 만 따로 `{}` 로 받아야 한다.
    """
    (tmp_path / "starrail.yaml").write_text(text, encoding="utf-8")

    profiles = load_profiles(tmp_path)

    assert set(profiles) == {"starrail"}
    assert profiles["starrail"].name == "starrail"     # name 이 없으면 키가 대신한다
    assert capsys.readouterr().err == ""               # 건너뛴 것이 아니다


def test_a_stray_note_file_does_not_brick_every_game(cfg_env, capsys, tmp_path):
    """CLI 레벨 — 프로파일 폴더의 메모 한 장이 명령 전체를 죽이면 안 된다.

    실측 재현(수정 전): `slot init 새컨텐츠 --game starrail` 이
    `오류: AttributeError: 'list' object has no attribute 'get'` rc=1 이었다.
    프로파일이 멀쩡한 `genshin` 의 명령도 같이 죽었다.
    """
    (tmp_path / "profiles" / "메모.yaml").write_text("- starrail\n", encoding="utf-8")

    assert main(["slot", "init", "새컨텐츠", "--game", "starrail", "--types", "편성"]) == 0
    cap = capsys.readouterr()
    assert "슬롯" in cap.out
    # 이 계획의 계약: 파이썬 예외 이름을 그대로 노출하지 않는다
    assert "AttributeError" not in cap.out + cap.err
    assert "오류:" not in cap.out

    # 다른 게임도 살아 있어야 한다 — 파일 하나가 전부를 막지 않는다
    assert main(["slot", "init", "다른컨텐츠", "--game", "genshin"]) == 0
    assert "AttributeError" not in capsys.readouterr().out


def test_a_stray_note_file_still_leaves_typo_rejection_working(cfg_env, capsys, tmp_path):
    """건너뛴 뒤에도 **검증은 계속 돈다** — 메모 한 장이 검증을 끄면 안 된다.

    건너뛰기를 "프로파일 0개" 로 잘못 처리하면 검증이 통째로 꺼지고 오타가
    통과한다. 남은 프로파일로 계속 대조하는지 확인한다.
    """
    (tmp_path / "profiles" / "메모.yaml").write_text("starrail\n", encoding="utf-8")

    assert main(["slot", "init", "새컨텐츠", "--game", "starrial"]) == 1
    out = capsys.readouterr().out
    assert "등록된 게임이 아닙니다" in out
    assert "starrail" in out and "genshin" in out

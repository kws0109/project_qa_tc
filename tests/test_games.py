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

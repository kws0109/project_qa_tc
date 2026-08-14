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


def test_save_on_a_locked_config_says_what_to_do(tmp_path, monkeypatch):
    """설정 파일이 잠겼을 때 파이썬 예외 이름이 아니라 사람 말이 나와야 한다.

    실측: config.json 이 잠긴 상태에서 `qatc config --game starrail` 이
    `오류: PermissionError: [Errno 13] Permission denied: ...` 로 죽었다.
    Task 5 가 xlsx 에 대해 고친 것과 같은 결함이고, 이 경로는 Task 3 이
    새로 만든 것이다. 편집기로 열어두거나 클라우드 동기화가 잠그면 실제로 난다.
    """
    import os
    import stat

    import pytest

    monkeypatch.setenv("APPDATA", str(tmp_path))
    cfg = AppConfig(knowledge_root=str(tmp_path / "k"), profiles_dir=str(tmp_path / "p"))
    cfg.save()
    target = AppConfig.config_file()
    os.chmod(target, stat.S_IREAD)
    try:
        with pytest.raises(SystemExit) as e:
            cfg.default_game = "starrail"
            cfg.save()
        msg = str(e.value)
        assert str(target) in msg                 # 어느 파일인지
        assert "닫" in msg or "권한" in msg        # 다음 조치
        assert "PermissionError" not in msg       # 예외 이름을 노출하지 않는다
    finally:
        os.chmod(target, stat.S_IWRITE)

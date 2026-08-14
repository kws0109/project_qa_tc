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

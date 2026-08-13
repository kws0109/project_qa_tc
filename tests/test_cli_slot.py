import json

import pytest

from qatc.cli import main
from qatc.config import AppConfig
from qatc.knowledge.store import KnowledgeStore


@pytest.fixture()
def cfg_env(tmp_path, monkeypatch):
    """AppConfig.load() 가 임시 knowledge 디렉터리를 쓰게 만든다."""
    kroot = tmp_path / "knowledge"
    original = AppConfig.load

    def patched(cls=AppConfig):
        c = original()
        c.knowledge_root = str(kroot)
        return c

    monkeypatch.setattr(AppConfig, "load", staticmethod(patched))
    return kroot


def test_init_creates_db_and_slots(cfg_env, capsys):
    assert main(["slot", "init", "파티편성", "--game", "starrail", "--types", "편성"]) == 0
    with KnowledgeStore(cfg_env / "starrail.db") as s:
        keys = {x.key for x in s.slots("파티편성")}
    assert "core_action" in keys
    assert "편성.정원" in keys


def test_init_unknown_type_fails_with_message(cfg_env, capsys):
    rc = main(["slot", "init", "던전", "--game", "starrail", "--types", "로그라이크"])
    assert rc == 1
    assert "알 수 없는 컨텐츠 유형" in capsys.readouterr().out


def test_status_json_lists_open_slots(cfg_env, capsys):
    main(["slot", "init", "파티편성", "--game", "starrail"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다
    assert main(["slot", "status", "파티편성", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["content"] == "파티편성"
    assert data["filled"] == 0
    assert data["total"] == 10
    assert any(s["key"] == "core_action" for s in data["open"])


def test_set_then_status_reflects_it(cfg_env, capsys):
    main(["slot", "init", "파티편성", "--game", "starrail"])
    rc = main(["slot", "set", "파티편성", "core_action", "--status", "filled",
               "--value", "파티를 짠다"])
    assert rc == 0
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다
    main(["slot", "status", "파티편성", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["filled"] == 1
    assert all(s["key"] != "core_action" for s in data["open"])


def test_na_slot_leaves_open_list(cfg_env, capsys):
    main(["slot", "init", "파티편성", "--game", "starrail"])
    main(["slot", "set", "파티편성", "cost", "--status", "na"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다
    main(["slot", "status", "파티편성", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert all(s["key"] != "cost" for s in data["open"])


def test_set_unknown_key_fails_and_lists_keys(cfg_env, capsys):
    main(["slot", "init", "파티편성", "--game", "starrail"])
    rc = main(["slot", "set", "파티편성", "없는키", "--status", "filled", "--value", "x"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "core_action" in out


def test_set_on_missing_content_fails(cfg_env, capsys):
    main(["slot", "init", "다른컨텐츠", "--game", "starrail"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다
    rc = main(["slot", "set", "없는것", "core_action", "--status", "filled", "--value", "x"])
    assert rc == 1
    assert "없는것" in capsys.readouterr().out


def test_add_slot_appends(cfg_env, capsys):
    main(["slot", "init", "파티편성", "--game", "starrail"])
    rc = main(["slot", "add", "파티편성", "네트워크",
               "--hint", "통신이 끊기면", "--family", "중단"])
    assert rc == 0
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다
    main(["slot", "status", "파티편성", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["total"] == 11


def test_game_is_inferred_when_only_one_db_has_the_content(cfg_env):
    main(["slot", "init", "파티편성", "--game", "starrail"])
    # --game 없이도 찾아낸다
    assert main(["slot", "status", "파티편성"]) == 0


def test_ambiguous_content_across_games_fails(cfg_env, capsys):
    main(["slot", "init", "공통", "--game", "starrail"])
    main(["slot", "init", "공통", "--game", "genshin"])
    rc = main(["slot", "status", "공통"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "--game" in out

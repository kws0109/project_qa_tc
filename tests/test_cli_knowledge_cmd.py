import json

import pytest

from qatc.cli import build_parser, main
from qatc.config import AppConfig


@pytest.fixture()
def cfg_env(tmp_path, monkeypatch):
    kroot = tmp_path / "knowledge"
    original = AppConfig.load

    def patched(cls=AppConfig):
        c = original()
        c.knowledge_root = str(kroot)
        return c

    monkeypatch.setattr(AppConfig, "load", staticmethod(patched))
    return kroot


def test_knowledge_lists_contents_with_coverage(cfg_env, capsys):
    main(["slot", "init", "파티편성", "--game", "starrail"])
    main(["slot", "set", "파티편성", "core_action", "--status", "filled", "--value", "v"])
    main(["slot", "init", "워프", "--game", "starrail", "--types", "가챠"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다

    assert main(["knowledge", "--game", "starrail", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    by_name = {c["content"]: c for c in data["contents"]}
    assert by_name["파티편성"]["filled"] == 1
    assert by_name["파티편성"]["total"] == 10
    assert by_name["워프"]["total"] == 14


def test_knowledge_on_missing_game_fails(cfg_env, capsys):
    rc = main(["knowledge", "--game", "없는게임"])
    assert rc == 1
    assert "없는게임" in capsys.readouterr().out


def test_legacy_commands_are_hidden_without_flag():
    parser = build_parser()
    # argparse 는 등록된 하위명령만 받는다
    with pytest.raises(SystemExit):
        parser.parse_args(["record", "--profile", "starrail"])


def test_legacy_commands_available_with_flag():
    parser = build_parser(legacy=True)
    args = parser.parse_args(["analyze", "some-session"])
    assert args.command == "analyze"


def test_new_commands_available_without_flag():
    parser = build_parser()
    args = parser.parse_args(["tc", "plan", "파티편성"])
    assert args.tc_command == "plan"

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


def test_legacy_config_dispatches_through_main(monkeypatch):
    """main(["--legacy", "config"]) 가 성공하려면 두 가지가 다 맞아야 한다:

    (1) main() 이 argv 에서 '--legacy' 를 argparse 에 넘기기 전에 떼어내고,
    (2) 그 결과로 만든 legacy 파서에 'config' 가 등록돼 있어야 한다.
    실제 설정 파일에 쓰거나 API 키를 묻지 않도록 cmd_config 자체는 스텁으로
    교체해 디스패치 배선만 검증한다.
    """
    import qatc.cli as cli

    calls = []

    def fake_cmd_config(args, cfg):
        calls.append(args)
        return 0

    monkeypatch.setattr(cli, "cmd_config", fake_cmd_config)

    assert cli.main(["--legacy", "config"]) == 0
    assert len(calls) == 1

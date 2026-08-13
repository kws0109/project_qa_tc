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


def test_knowledge_filled_counts_only_filled_not_unknown_or_na(cfg_env, capsys):
    """`knowledge` 의 커버리지 분자도 FILLED 만 센다.

    `cmd_knowledge` 는 `slot status` 와 같은 집계를 따로 한 번 더 계산한다.
    한쪽만 고치면 같은 게임에 대해 두 명령이 다른 숫자를 말한다.
    """
    main(["slot", "init", "파티편성", "--game", "starrail"])
    main(["slot", "set", "파티편성", "core_action", "--status", "filled", "--value", "v"])
    main(["slot", "set", "파티편성", "cost", "--status", "unknown"])
    main(["slot", "set", "파티편성", "failure", "--status", "na"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다

    assert main(["knowledge", "--game", "starrail", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    row = {c["content"]: c for c in data["contents"]}["파티편성"]
    # 슬롯 3개가 닫혔지만 근거는 1개다 — is_closed 로 세면 3이 나온다
    assert row["filled"] == 1
    assert row["total"] == 10


def test_knowledge_on_missing_game_fails(cfg_env, capsys):
    rc = main(["knowledge", "--game", "없는게임"])
    assert rc == 1
    assert "없는게임" in capsys.readouterr().out


def test_record_command_not_registered():
    """녹화 파이프라인은 삭제됐다 — 'record'는 더 이상 어떤 형태로도 등록되지 않는다."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["record", "--profile", "starrail"])


def test_new_commands_available():
    parser = build_parser()
    args = parser.parse_args(["tc", "plan", "파티편성"])
    assert args.tc_command == "plan"


def test_config_dispatches_through_main(monkeypatch):
    """main(["config"]) 가 cmd_config 로 디스패치되는지 확인한다.

    실제 설정 파일에 쓰지 않도록 cmd_config 자체는 스텁으로 교체해
    디스패치 배선만 검증한다.
    """
    import qatc.cli as cli

    calls = []

    def fake_cmd_config(args, cfg):
        calls.append(args)
        return 0

    monkeypatch.setattr(cli, "cmd_config", fake_cmd_config)

    assert cli.main(["config"]) == 0
    assert len(calls) == 1

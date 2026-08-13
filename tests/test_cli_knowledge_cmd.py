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
    out = capsys.readouterr().out
    assert "없는게임" in out
    # 계약: 오류는 다음 조치를 항상 함께 알린다 (모듈 도크스트링)
    assert "qatc slot init" in out


class _StdIn:
    def __init__(self, text: str):
        self._text = text

    def read(self) -> str:
        return self._text


_VALID_TC = '{"testcases":[{"title":"t","steps":["s"],"expected":["e"]}]}'


@pytest.mark.parametrize("argv", [
    ["slot", "status", "없는컨텐츠", "--game", "starrail"],
    ["tc", "plan", "없는컨텐츠", "--game", "starrail"],
    ["tc", "list", "없는컨텐츠", "--game", "starrail"],
    ["export", "없는컨텐츠", "--game", "starrail"],
    ["tc", "add", "없는컨텐츠", "--family", "정상 경로",
     "--origin", "interview", "--json", "-", "--game", "starrail"],
], ids=["slot-status", "tc-plan", "tc-list", "export", "tc-add"])
def test_missing_content_error_always_names_next_action(cfg_env, capsys, monkeypatch, argv):
    """같은 조건을 알리는 다섯 명령이 모두 다음 조치를 함께 말해야 한다.

    `slot status` 만 `'qatc slot init'을 먼저 실행하세요.` 를 붙였고 나머지 넷은
    "없습니다." 에서 끝났다. 이 출력의 1차 독자는 인터뷰를 진행하는 모델이라,
    다음 조치가 없으면 무엇을 할지 추측하게 된다. 태스크 단위 리뷰가 구조적으로
    못 보는 결함이라(각각은 자기 태스크 안에서 일관돼 보인다) 다섯을 한 테스트로 묶는다.
    """
    main(["slot", "init", "다른컨텐츠", "--game", "starrail"])
    monkeypatch.setattr("sys.stdin", _StdIn(_VALID_TC))
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다

    assert main(argv) == 1
    out = capsys.readouterr().out
    assert "없는컨텐츠" in out
    assert "qatc slot init" in out


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

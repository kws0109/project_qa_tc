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


def test_set_filled_without_value_is_rejected(cfg_env, capsys):
    """`--status filled` 를 `--value` 없이 부르면 거부해야 한다.

    게이트는 `status is FILLED` 만 보고 계열을 계획한다. 빈 근거가 FILLED 로
    통과하면 "근거 없는 TC는 만들어지지 않는다" 가 플래그 하나 빠뜨리는 것만으로
    무너진다 — 인터뷰를 진행하는 모델이 가장 하기 쉬운 실수다.
    """
    main(["slot", "init", "파티편성", "--game", "starrail"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다
    rc = main(["slot", "set", "파티편성", "core_action", "--status", "filled"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "--value" in out
    # 다음 조치를 알린다 — 모르면 unknown, 해당 없으면 na
    assert "--status unknown" in out
    assert "--status na" in out

    # 거부됐으면 슬롯이 실제로 안 바뀌어야 한다
    main(["slot", "status", "파티편성", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["filled"] == 0
    assert any(s["key"] == "core_action" for s in data["open"])


def test_set_filled_with_whitespace_value_is_rejected(cfg_env, capsys):
    """공백만 있는 `--value` 도 근거가 아니다."""
    main(["slot", "init", "파티편성", "--game", "starrail"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다
    rc = main(["slot", "set", "파티편성", "core_action",
               "--status", "filled", "--value", "   "])
    assert rc == 1
    assert "--value" in capsys.readouterr().out

    main(["slot", "status", "파티편성", "--json"])
    assert json.loads(capsys.readouterr().out)["filled"] == 0


def test_unknown_na_empty_still_accept_missing_value(cfg_env, capsys):
    """"모른다"·"해당 없음"·되돌리기는 값이 없는 것이 정상 사용법이다.

    C1 검증이 이 셋까지 막으면 인터뷰가 진행되지 않는다.
    """
    main(["slot", "init", "파티편성", "--game", "starrail"])
    for status in ("unknown", "na", "empty"):
        assert main(["slot", "set", "파티편성", "cost", "--status", status]) == 0, status


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


def test_int_coded_system_exit_still_propagates(cfg_env, monkeypatch):
    """main() 의 SystemExit 가드는 문자열 코드만 rc=1 로 바꿔야 한다.

    문자열 케이스는 test_set_on_missing_content_fails /
    test_ambiguous_content_across_games_fails 가 이미 고정한다 (resolve_store
    가 안내 문구와 함께 SystemExit 를 던지고, main() 이 그걸 표준출력 + rc=1 로
    바꾼다). 여기서는 반대쪽 경계 — 정수 코드는 그대로 통과해야 한다 — 를 고정한다.
    가드를 `except SystemExit: return 1` 처럼 뭉뚱그리면 이 테스트가 깨진다.
    """
    import qatc.cli_knowledge as ck

    def _raise_int_exit(args, cfg):
        raise SystemExit(3)

    monkeypatch.setattr(ck, "cmd_slot_status", _raise_int_exit)
    with pytest.raises(SystemExit) as exc_info:
        main(["slot", "status", "아무거나"])
    assert exc_info.value.code == 3

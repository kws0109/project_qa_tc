import json

import pytest

from qatc.cli import main
from qatc.knowledge.store import KnowledgeStore
from conftest import INVISIBLE_IDS, INVISIBLE_VALUES


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


def test_status_filled_counts_only_filled_not_unknown_or_na(cfg_env, capsys):
    """`filled` 는 FILLED 만 센다 — UNKNOWN·NA 는 근거가 아니다.

    이 설계 전체가 "FILLED 만 근거다" 위에 서 있는데, 그 구분이 게이트에서는
    봉인돼 있고 **사용자·모델이 실제로 보는 진척 표시에서는 안 봉인돼 있었다.**
    `s.status is SlotStatus.FILLED` 를 `s.is_closed` 로 바꿔도 109개가 전부
    통과했다 (뮤테이션 M30 생존). 그 상태에서는 절반이 "모름"인 컨텐츠가
    `15/15 채움` 으로 보고된다.
    """
    main(["slot", "init", "파티편성", "--game", "starrail"])
    main(["slot", "set", "파티편성", "core_action", "--status", "filled",
          "--value", "파티를 짠다"])
    main(["slot", "set", "파티편성", "cost", "--status", "unknown"])
    main(["slot", "set", "파티편성", "failure", "--status", "na"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다

    main(["slot", "status", "파티편성", "--json"])
    data = json.loads(capsys.readouterr().out)
    # 닫힌 슬롯은 3개지만 근거는 1개다 — is_closed 로 세면 3이 나온다
    assert len(data["closed"]) == 3
    assert data["filled"] == 1
    assert data["total"] == 10


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


def test_add_slot_rejects_unregistered_family(cfg_env, capsys):
    """오타 계열은 `slot add` 에서 막는다.

    `--family 중단됨`(등록된 `중단` 의 오타)이 통과하면 `tc plan` 이 그 계열을
    계획하고 `FAMILY_META` 폴백으로 **의도한 INTERRUPT 대신 HAPPY_PATH / Medium
    을 조용히 배정**한다. rc=0 이라 아무도 눈치채지 못한다.
    """
    main(["slot", "init", "파티편성", "--game", "starrail"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다
    rc = main(["slot", "add", "파티편성", "네트워크",
               "--hint", "통신이 끊기면", "--family", "중단됨"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "중단됨" in out
    assert "중단" in out          # 유효한 계열 목록을 알려준다 (다음 조치)

    # 거부됐으면 분모가 늘지 않아야 한다
    main(["slot", "status", "파티편성", "--json"])
    assert json.loads(capsys.readouterr().out)["total"] == 10


def test_add_slot_rejects_empty_family(cfg_env, capsys):
    """`--family ""` 는 어떤 계열도 만들지 못하는 죽은 슬롯을 만든다.

    `tc plan` 의 skipped 에도 안 뜨고 (`tc_family` 가 비면 게이트가 양쪽에서
    제외한다) `slot status` 의 `total` 분모만 늘린다 — 실측 10 → 11.
    """
    main(["slot", "init", "파티편성", "--game", "starrail"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다
    rc = main(["slot", "add", "파티편성", "죽은슬롯", "--hint", "h", "--family", ""])
    assert rc == 1
    assert "정의된 계열" in capsys.readouterr().out   # 유효값을 알려준다

    main(["slot", "status", "파티편성", "--json"])
    assert json.loads(capsys.readouterr().out)["total"] == 10


def test_added_interrupt_slot_plans_as_interrupt(cfg_env, capsys):
    """`중단` 은 `slot add` 로 도달하라고 FAMILY_META 에 있는 계열이다.

    검증을 넣으면서 이 경로가 막히면 안 된다 — 등록된 이름을 쓰면 폴백이 아니라
    의도한 INTERRUPT 가 배정돼야 한다.
    """
    main(["slot", "init", "파티편성", "--game", "starrail"])
    assert main(["slot", "add", "파티편성", "네트워크",
                 "--hint", "통신이 끊기면", "--family", "중단"]) == 0
    main(["slot", "set", "파티편성", "네트워크", "--status", "filled",
          "--value", "전투 중 통신이 끊긴다"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다

    main(["tc", "plan", "파티편성", "--json"])
    data = json.loads(capsys.readouterr().out)
    row = {p["family"]: p for p in data["planned"]}["중단"]
    assert row["kind"] == "중단"        # TCKind.INTERRUPT — 폴백이면 "정상" 이 된다
    assert row["priority"] == "Medium"


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


def test_add_slot_rejects_whitespace_only_family(cfg_env, capsys):
    """공백만 있는 `--family` 도 죽은 슬롯을 만든다 (Minor 19 후속).

    라운드 1a 가 빈 문자열을 막았지만, `--value` 와 달리 공백만 있는 경우는
    확인되지 않았다. `"  "` 는 `FAMILY_META` 의 키가 아니므로 같은 검사에
    걸려야 하고, 그 사실을 여기서 고정한다.
    """
    main(["slot", "init", "파티편성", "--game", "starrail"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다
    rc = main(["slot", "add", "파티편성", "죽은슬롯", "--hint", "h", "--family", "  "])
    assert rc == 1
    assert "정의된 계열" in capsys.readouterr().out

    main(["slot", "status", "파티편성", "--json"])
    assert json.loads(capsys.readouterr().out)["total"] == 10


# --- 보이지 않는 문자 (BL1) ----------------------------------------------


@pytest.mark.parametrize("value, label", INVISIBLE_VALUES, ids=INVISIBLE_IDS)
def test_set_filled_with_invisible_only_value_is_rejected(cfg_env, capsys, value, label):
    """제로폭·BOM·제어문자만 있는 `--value` 는 근거가 아니다.

    실측(수정 전): `slot set 중복 cost --status filled --value <U+200B>` 가
    `✓ cost = filled` rc=0 으로 끝나고, 이어서 `tc plan` 이 `재화 부족` 을
    **생성 대상 계열로 계획했다.** 보이지 않는 문자 하나가 계열을 연 것이다.
    라운드 1a 가 Critical 로 막은 구멍이 `strip()` 의 한계(= `isspace()` 인
    문자만 지운다)를 통해 그대로 다시 열려 있었다.
    """
    main(["slot", "init", "파티편성", "--game", "starrail"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다

    rc = main(["slot", "set", "파티편성", "cost", "--status", "filled", "--value", value])
    assert rc == 1, label
    out = capsys.readouterr().out
    assert "--value" in out, label
    # 다음 조치를 알린다 — 모르면 unknown, 해당 없으면 na
    assert "--status unknown" in out, label
    assert "--status na" in out, label

    # 근거로 인정되지 않았으니 계열도 열리지 않아야 한다
    main(["slot", "status", "파티편성", "--json"])
    assert json.loads(capsys.readouterr().out)["filled"] == 0, label
    main(["tc", "plan", "파티편성", "--json"])
    planned = [p["family"] for p in json.loads(capsys.readouterr().out)["planned"]]
    assert planned == [], f"{label}: 보이지 않는 문자가 계열을 열었다 — {planned}"


@pytest.mark.parametrize("value", ["소모하지 않는다", "4", "0"],
                         ids=["korean", "digit", "zero"])
def test_set_filled_accepts_short_but_real_value(cfg_env, capsys, value):
    """반대쪽 경계 — 뜻이 있는 값은 짧아도 그대로 근거가 된다.

    `"4"`(정원 4명) 같은 정당한 한 글자 답변까지 막으면 인터뷰가 진행되지 않는다.
    """
    main(["slot", "init", "파티편성", "--game", "starrail"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다

    assert main(["slot", "set", "파티편성", "cost",
                 "--status", "filled", "--value", value]) == 0
    capsys.readouterr()          # 기록 확인 문구를 버린다
    main(["slot", "status", "파티편성", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["filled"] == 1
    assert {"key": "cost", "status": "filled", "value": value} in data["closed"]
    main(["tc", "plan", "파티편성", "--json"])
    planned = [p["family"] for p in json.loads(capsys.readouterr().out)["planned"]]
    assert planned == ["재화 부족"]


@pytest.mark.parametrize("status", ["unknown", "na", "empty"])
@pytest.mark.parametrize("value", ["\u200b", "\ufeff", "\x07", "   "],
                         ids=["zwsp", "bom", "bel", "spaces"])
def test_unknown_na_empty_ignore_the_invisible_check(cfg_env, capsys, status, value):
    """보이지 않는 문자 검사가 FILLED 밖으로 새면 인터뷰가 멈춘다.

    `--status unknown` 은 값이 없는 것이 정상 사용법이고, 모델이 습관적으로
    `--value ""` 를 함께 보내기도 한다.
    """
    main(["slot", "init", "파티편성", "--game", "starrail"])
    assert main(["slot", "set", "파티편성", "cost",
                 "--status", status, "--value", value]) == 0

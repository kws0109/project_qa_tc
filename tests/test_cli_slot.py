import json

import pytest

from conftest import INVISIBLE_IDS, INVISIBLE_VALUES
from qatc.cli import main
from qatc.knowledge.gate import FAMILY_META
from qatc.knowledge.store import KnowledgeStore


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
    """오타 계열은 `slot add` 에서 막고, **유효한 이름 전부와 다음 조치**를 알린다.

    `--family 중단됨`(등록된 `중단` 의 오타)이 통과하면 `tc plan` 이 그 계열을
    계획하고 `FAMILY_META` 폴백으로 **의도한 INTERRUPT 대신 HAPPY_PATH / Medium
    을 조용히 배정**한다. rc=0 이라 아무도 눈치채지 못한다.

    예전 판의 셋째 단언은 `"중단" in out` 이었는데, 이는 둘째 단언
    (`"중단됨" in out`)의 **부분문자열이라 공짜였다** — 메시지가 계열 목록과
    다음 조치를 통째로 잃어도 초록으로 남았다. 이 명령의 호출자는 인터뷰를
    진행하는 모델이고, 목록이 없으면 무엇으로 고쳐야 할지 알 수 없다. 그래서
    `FAMILY_META` 를 진실 원천으로 삼아 정렬된 전체 목록과 다음 조치를 고정한다.
    """
    main(["slot", "init", "파티편성", "--game", "starrail"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다
    rc = main(["slot", "add", "파티편성", "네트워크",
               "--hint", "통신이 끊기면", "--family", "중단됨"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "등록되지 않은 계열 '중단됨'" in out       # 무엇이 틀렸는지
    # 정의된 계열 **전부**를 알려준다. 부분문자열로 통과하지 못하도록 한 덩어리로 본다.
    assert f"정의된 계열: {', '.join(sorted(FAMILY_META))}" in out
    assert "--family 에 쓰세요" in out                # 다음 조치

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

    단, `line-separator`(U+2028) 파라미터에는 이 설명이 해당하지 않는다 —
    그것만 `isspace()` 라 `strip()` 가드도 이미 잡았다. 사정은
    `conftest.INVISIBLE_VALUES` 의 해당 항목 주석에 적혀 있다.
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


@pytest.mark.parametrize("value", ["소모하지 않는다", "4", "0",
                                   "\uac00\u0301", "\u2764\ufe0f", "🔥획득"],
                         ids=["korean", "digit", "zero",
                              "hangul-accent", "emoji-vs16", "emoji-with-text"])
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


# --- slot status 가 실제로 보여주는 것 (T2 · M17 · M18) -------------------


def _fill_three_ways(capsys):
    """FILLED 하나 · UNKNOWN 하나 · NA 하나 — 나머지는 비어 있다."""
    main(["slot", "init", "파티편성", "--game", "starrail"])
    main(["slot", "set", "파티편성", "core_action", "--status", "filled",
          "--value", "파티를 짜고 적용한다"])
    main(["slot", "set", "파티편성", "cost", "--status", "unknown"])
    main(["slot", "set", "파티편성", "failure", "--status", "na"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다


def test_status_text_lists_the_open_slots_and_does_not_claim_completion(cfg_env, capsys):
    """빈 슬롯이 남아 있는데 "모든 항목이 채워졌습니다" 라고 말하면 안 된다 (M18).

    이 화면은 사람이 인터뷰 진척을 보는 곳이다. `if payload["open"]:` 를
    뒤집어도 스위트가 전부 초록이었고(뮤테이션 M18 생존), 그 상태에서는 슬롯
    7개가 빈 채로 완료가 선언된다 — 같은 출력이 `1/10 채움` 이라고 말하면서
    동시에 다 됐다고 말하는 자기모순이다.
    """
    _fill_three_ways(capsys)

    assert main(["slot", "status", "파티편성"]) == 0
    out = capsys.readouterr().out

    assert "[파티편성] starrail · 1/10 채움" in out   # 근거는 1개다 (닫힌 것은 3개)
    assert "남은 항목:" in out
    assert "모든 항목이 채워졌습니다" not in out
    # 남은 항목은 키와 물음을 함께 보여준다 — 키만 있으면 무엇을 물을지 모른다
    assert "entry" in out
    assert "어디서 어떻게 들어가는가" in out
    # 닫힌 슬롯은 남은 항목에 없다
    remaining = out.split("남은 항목:", 1)[1]
    for closed in ("core_action", "cost", "failure"):
        assert closed not in remaining, closed


def test_status_text_says_it_is_done_only_when_nothing_is_open(cfg_env, capsys):
    """반대쪽 경계 — 정말 다 닫혔으면 완료와 다음 조치를 말해야 한다."""
    main(["slot", "init", "파티편성", "--game", "starrail"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다
    main(["slot", "status", "파티편성", "--json"])
    keys = [s["key"] for s in json.loads(capsys.readouterr().out)["open"]]
    for key in keys:
        main(["slot", "set", "파티편성", key, "--status", "na"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다

    assert main(["slot", "status", "파티편성"]) == 0
    out = capsys.readouterr().out
    assert "모든 항목이 채워졌습니다" in out
    assert "qatc tc plan" in out          # 다음 조치
    assert "남은 항목:" not in out


def test_status_json_planned_families_matches_what_can_be_built(cfg_env, capsys):
    """`planned_families` 는 게이트가 계산한 결과여야 한다 (M17a).

    이 배열이 상수여도 스위트가 초록이었다. 인터뷰를 진행하는 모델은 이걸 보고
    "이제 TC를 만들 수 있다" 를 판단하므로, 틀리면 `tc add` 가 거부할 계열을
    작성하느라 턴을 버린다.
    """
    _fill_three_ways(capsys)

    main(["slot", "status", "파티편성", "--json"])
    status = json.loads(capsys.readouterr().out)
    main(["tc", "plan", "파티편성", "--json"])
    plan = json.loads(capsys.readouterr().out)

    assert status["planned_families"] == ["정상 경로"]
    # 두 명령이 같은 답을 해야 한다 — 갈라지면 어느 쪽을 믿을지 알 수 없다
    assert status["planned_families"] == [p["family"] for p in plan["planned"]]


def test_status_json_skipped_status_distinguishes_empty_unknown_and_na(cfg_env, capsys):
    """`skipped_families[].status` 를 `empty` 로 고정해도 초록이었다 (M17b).

    그 상태에서는 사용자가 이미 `unknown`/`na` 로 **닫은** 슬롯이 "다시 물어라"
    로 보고된다 — 4상태 설계가 막으려던 재질문 루프 그 자체다
    ("이건 재화 안 써요" 라고 답했는데 도구가 계속 재화를 묻는 상황).
    """
    _fill_three_ways(capsys)

    main(["slot", "status", "파티편성", "--json"])
    skipped = {s["family"]: s for s in json.loads(capsys.readouterr().out)["skipped_families"]}

    assert skipped["재화 부족"] == {"family": "재화 부족", "slot": "cost", "status": "unknown"}
    assert skipped["실패 경로"] == {"family": "실패 경로", "slot": "failure", "status": "na"}
    assert skipped["진입 경로"] == {"family": "진입 경로", "slot": "entry", "status": "empty"}
    # 근거가 있는 계열은 제외 목록에 없다
    assert "정상 경로" not in skipped


# --- slot init 의 컨텐츠 이름 (C1) ---------------------------------------


@pytest.mark.parametrize("name, label", [("", "빈 문자열"), ("   ", "공백만"),
                                         ("\t", "탭만")] + INVISIBLE_VALUES,
                         ids=["empty", "spaces", "tab"] + INVISIBLE_IDS)
def test_init_rejects_a_content_name_with_no_content(cfg_env, capsys, name, label):
    """이름이 없는 컨텐츠는 만들지 않는다.

    실측(수정 전): `slot init "" --game X` 가 `[] 슬롯 10개 준비됨 (유형: 없음)`
    rc=0 으로 끝났다. 이 명령의 호출자는 인자를 조립하는 **모델**이라 빈
    문자열이 실제로 넘어오고, rc=0 이면 정상 생성으로 읽는다. 그러면
    `knowledge` 커버리지 표에 이름 없는 행이 남고, 진짜 컨텐츠는 만들어지지
    않은 채로 인터뷰가 계속된다.

    판정은 근거 검사와 같은 `is_blank` 를 쓴다 — 보이지 않는 문자로 된 이름도
    사람이 다시 찾을 수 없기는 마찬가지다.
    """
    rc = main(["slot", "init", name, "--game", "starrail"])
    assert rc == 1, label
    out = capsys.readouterr().out
    assert "컨텐츠 이름" in out, label
    assert "qatc slot init" in out, label          # 다음 조치 (올바른 호출 형태)

    # 거부됐으면 DB 파일 자체가 만들어지지 않아야 한다
    assert not (cfg_env / "starrail.db").exists(), label


def test_init_rejecting_a_blank_name_leaves_no_ghost_content(cfg_env, capsys):
    """거부됐으면 커버리지 표에 이름 없는 행이 남지 않아야 한다."""
    main(["slot", "init", "파티편성", "--game", "starrail"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다

    assert main(["slot", "init", "", "--game", "starrail"]) == 1
    capsys.readouterr()
    assert main(["knowledge", "--game", "starrail", "--json"]) == 0
    contents = [c["content"] for c in json.loads(capsys.readouterr().out)["contents"]]
    assert contents == ["파티편성"]


def test_init_still_accepts_short_and_unusual_but_real_names(cfg_env, capsys):
    """반대쪽 경계 — 짧거나 특이해도 뜻이 있는 이름은 그대로 만들어야 한다."""
    for name in ("1", "워프", "A/B 테스트", "v2"):
        assert main(["slot", "init", name, "--game", "starrail"]) == 0, name
    capsys.readouterr()
    assert main(["knowledge", "--game", "starrail", "--json"]) == 0
    contents = {c["content"] for c in json.loads(capsys.readouterr().out)["contents"]}
    assert contents == {"1", "워프", "A/B 테스트", "v2"}


# --- --game 검증과 기본값 (T4) --------------------------------------------


def test_slot_init_rejects_an_unregistered_game(cfg_env, capsys):
    rc = main(["slot", "init", "테스트", "--game", "starrial"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "starrial" in out and "starrail" in out
    assert not list((cfg_env / "starrial.db").parent.glob("starrial.db")), \
        "거부했는데 DB 가 생겼다"


def test_slot_init_uses_the_default_game_when_flag_is_omitted(cfg_env, capsys):
    assert main(["config", "--game", "starrail"]) == 0
    capsys.readouterr()
    assert main(["slot", "init", "파티편성"]) == 0
    capsys.readouterr()
    assert (cfg_env / "starrail.db").exists()


def test_explicit_game_wins_over_the_default(cfg_env, capsys):
    assert main(["config", "--game", "starrail"]) == 0
    capsys.readouterr()
    assert main(["slot", "init", "상점", "--game", "genshin"]) == 0
    capsys.readouterr()
    assert (cfg_env / "genshin.db").exists()
    assert not (cfg_env / "starrail.db").exists()


def test_slot_init_without_game_or_default_says_what_to_do(cfg_env, capsys):
    rc = main(["slot", "init", "파티편성"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "--game" in out
    assert "config" in out           # 기본 게임을 정하는 방법도 알려준다


def test_blank_content_is_rejected_before_the_game_is_resolved(cfg_env, capsys):
    """이름이 비었으면 게임 얘기를 꺼내지 않는다 — 만들 게 없다."""
    rc = main(["slot", "init", "", "--game", "starrial"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "이름이 비어" in out
    assert "등록된 게임이 아닙니다" not in out


# --- 컨텐츠 코드 (`--code`) ------------------------------------------------


def test_slot_init_takes_a_code(cfg_env, capsys):
    from qatc.knowledge.store import KnowledgeStore

    assert main(["slot", "init", "로그인", "--game", "starrail", "--code", "LOGIN"]) == 0
    with KnowledgeStore(cfg_env / "starrail.db") as st:
        assert st.content_code("로그인") == "LOGIN"


def test_a_lowercase_or_symbolic_code_is_refused(cfg_env, capsys):
    rc = main(["slot", "init", "로그인", "--game", "starrail", "--code", "log-in"])
    assert rc == 1
    assert "영문 대문자" in capsys.readouterr().out


def test_a_duplicate_code_in_the_same_game_is_refused(cfg_env, capsys):
    main(["slot", "init", "로그인", "--game", "starrail", "--code", "LOGIN"])
    rc = main(["slot", "init", "로그인보상", "--game", "starrail", "--code", "LOGIN"])
    assert rc == 1
    assert "이미" in capsys.readouterr().out


def test_a_duplicate_code_does_not_leave_a_phantom_content_behind(cfg_env, capsys):
    """새 컨텐츠 생성이 코드 중복으로 실패하면, 이름만 있고 코드 없는 유령
    컨텐츠도 남지 않아야 한다 (Bug C).

    `KnowledgeStore.close()` 는 예외와 무관하게 항상 커밋한다 — `init_content`
    가 컨텐츠 행을 먼저 넣고 코드 검사를 나중에 하면, 검사가 실패해 명령이
    rc=1 로 끝나도 그 행은 이미 커밋돼 있다. 검사를 행 삽입보다 먼저 해야
    이 유령이 안 생긴다.
    """
    from qatc.knowledge.store import KnowledgeStore

    main(["slot", "init", "로그인", "--game", "starrail", "--code", "LOGIN"])
    rc = main(["slot", "init", "로그인보상", "--game", "starrail", "--code", "LOGIN"])
    assert rc == 1

    with KnowledgeStore(cfg_env / "starrail.db") as st:
        assert st.get_content("로그인보상") is None


def test_changing_the_code_of_an_existing_content_is_refused(cfg_env, capsys):
    main(["slot", "init", "로그인", "--game", "starrail", "--code", "LOGIN"])
    rc = main(["slot", "init", "로그인", "--game", "starrail", "--code", "SIGNIN"])
    assert rc == 1
    assert "이미 발급" in capsys.readouterr().out

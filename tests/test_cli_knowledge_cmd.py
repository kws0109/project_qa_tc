import json
import sqlite3
from pathlib import Path

import pytest

from qatc.cli import build_parser, main
from qatc.cli_knowledge import _safe_filename_part
from qatc.console import display_width
from qatc.knowledge.store import is_locked_error


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


def test_knowledge_on_unregistered_game_fails(cfg_env, capsys):
    """T4 이후: 등록되지 않은 게임 이름은 DB 존재 여부를 보기도 전에 막힌다.

    `cmd_knowledge` 가 이제 `resolve_game` 을 맨 앞에서 호출하므로, 오타 게임
    이름은 "지식 DB가 없습니다" 가 아니라 `validate_game` 의 "등록된 게임이
    아닙니다" 로 거부된다 — 이 태스크가 만들려는 바로 그 동작이다.
    """
    rc = main(["knowledge", "--game", "없는게임"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "없는게임" in out
    # 계약: 오류는 다음 조치를 항상 함께 알린다 (모듈 도크스트링) — 여기서는
    # 쓸 수 있는 게임 이름과 프로파일 위치가 그 역할을 한다.
    assert "starrail" in out and "genshin" in out
    assert "등록된 게임이 아닙니다" in out


def test_knowledge_on_missing_db_for_a_registered_game_fails(cfg_env, capsys):
    """등록은 됐지만 아직 DB가 없는 게임은 "지식 DB가 없습니다" 로 안내한다.

    `resolve_game` 통과 **뒤**에 있는 이 분기는 `validate_game` 이 막지 않는다
    — `genshin` 은 `cfg_env` 가 등록한 프로파일이지만 `slot init` 을 한 번도
    부르지 않아 DB 파일 자체가 없다.
    """
    rc = main(["knowledge", "--game", "genshin"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "genshin" in out
    # 계약: 오류는 다음 조치를 항상 함께 알린다 (모듈 도크스트링)
    assert "qatc slot init" in out


_VALID_TC = '{"testcases":[{"title":"t","steps":["s"],"expected":["e"]}]}'


@pytest.mark.parametrize("argv", [
    ["slot", "status", "없는컨텐츠", "--game", "starrail"],
    ["tc", "plan", "없는컨텐츠", "--game", "starrail"],
    ["tc", "list", "없는컨텐츠", "--game", "starrail"],
    ["export", "없는컨텐츠", "--game", "starrail"],
    ["tc", "add", "없는컨텐츠", "--family", "정상 경로",
     "--origin", "interview", "--json", "-", "--game", "starrail"],
], ids=["slot-status", "tc-plan", "tc-list", "export", "tc-add"])
def test_missing_content_error_always_names_next_action(stdin_text, cfg_env, capsys, monkeypatch, argv):
    """같은 조건을 알리는 다섯 명령이 모두 다음 조치를 함께 말해야 한다.

    `slot status` 만 `'qatc slot init'을 먼저 실행하세요.` 를 붙였고 나머지 넷은
    "없습니다." 에서 끝났다. 이 출력의 1차 독자는 인터뷰를 진행하는 모델이라,
    다음 조치가 없으면 무엇을 할지 추측하게 된다. 태스크 단위 리뷰가 구조적으로
    못 보는 결함이라(각각은 자기 태스크 안에서 일관돼 보인다) 다섯을 한 테스트로 묶는다.
    """
    main(["slot", "init", "다른컨텐츠", "--game", "starrail"])
    stdin_text(_VALID_TC)
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


# --- 표 정렬 (Minor 11) ---------------------------------------------------


def test_knowledge_table_columns_line_up_for_korean_names(cfg_env, capsys):
    """한글 이름과 ASCII 이름이 섞여도 숫자 칸이 같은 열에서 시작해야 한다.

    `f"{'컨텐츠':<14}"` 는 한글을 폭 1로 세므로 실제 콘솔(폭 2)에서는 한글
    이름 행만 오른쪽으로 밀린다 — 커버리지 표를 눈으로 훑을 수 없게 된다.
    """
    main(["slot", "init", "파티편성", "--game", "starrail"])
    main(["slot", "init", "warp", "--game", "starrail"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다

    assert main(["knowledge", "--game", "starrail"]) == 0
    rows = [ln for ln in capsys.readouterr().out.splitlines()
            if ln.startswith("  ") and "/" in ln and "-" not in ln]
    assert len(rows) == 2, rows
    starts = {display_width(ln[: ln.index("/")]) for ln in rows}
    assert len(starts) == 1, f"열이 어긋난다: {starts}"


# --- export 기본 파일명 (Minor 12) ---------------------------------------


@pytest.mark.parametrize("name", ["파티/편성", "전투:보스", "물음표?", "별*표"])
def test_export_default_filename_sanitizes_forbidden_characters(cfg_env, capsys, name):
    """컨텐츠 이름을 파일명에 그대로 넣으면 엉뚱한 하위 폴더가 생긴다.

    실측: `slot init "파티/편성"` 후 `export` →
    `.../starrail_파티\편성_TC.xlsx` 가 되고 `mkdir(parents=True)` 덕에
    rc=0 이라 아무도 눈치채지 못한 채 `starrail_파티` 폴더가 남는다.
    """
    main(["slot", "init", name, "--game", "starrail"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다

    assert main(["export", name, "--game", "starrail"]) == 0
    out = capsys.readouterr().out.strip()
    path = Path(out.split("✓ ", 1)[1].split("  (", 1)[0])

    assert path.exists()
    assert path.parent == cfg_env               # 하위 폴더를 만들지 않았다
    assert not any(c in path.name for c in '\\/:*?"<>|')
    assert [d for d in cfg_env.iterdir() if d.is_dir()] == []


def test_export_out_option_is_used_verbatim(cfg_env, capsys, tmp_path):
    """`--out` 은 사용자가 명시한 경로다 — 마음대로 고치지 않는다."""
    main(["slot", "init", "파티편성", "--game", "starrail"])
    target = tmp_path / "내가 정한 폴더" / "결과.xlsx"
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다
    assert main(["export", "파티편성", "--out", str(target)]) == 0
    assert target.exists()


# --- DB 잠금 (Minor 13, 스펙 §7) -----------------------------------------


def _raise(exc):
    def _fn(args, cfg):
        raise exc
    return _fn


@pytest.mark.parametrize("message", [
    "database is locked",
    "database table is locked: slots",
    "database is busy",
], ids=["locked", "table-locked", "busy"])
def test_locked_db_says_another_qatc_process_is_running(cfg_env, capsys, monkeypatch, message):
    """스펙 §7 이 요구하는 문구가 실제로 나와야 한다.

    `timeout=30.0` 만 있어 진짜 잠금은
    `오류: OperationalError: database is locked` 로 새어나왔다 — 인터뷰를
    진행하는 모델에게 "무엇을 하라"가 전혀 없는 메시지다.

    `busy` 갈래는 미검증이었다 (M15). `is_locked_error` 에서 `or "busy" in msg`
    를 지워도 초록이었는데, sqlite 는 `SQLITE_BUSY` 를 같은 `OperationalError`
    로 던지므로 그 갈래가 빠지면 Minor 13 이 고친 "다음 조치 없는 오류" 가
    그대로 되살아난다.

    **`table-locked` 를 중복으로 보고 지우지 말 것.** `locked` 와 같은 갈래를
    타는 것은 맞지만, 판정이 **부분문자열이 아니라 등호**로 바뀌는 변이
    (`"locked" in msg` → `msg == "database is locked"`) 를 스위트에서 유일하게
    잡는 파라미터다 (실측: 그 변이의 실패 1건이 바로 이것). sqlite 의 잠금
    문구는 `database table is locked: <표>` 처럼 표 이름을 달고 오는 형태가
    실제로 있어서, 등호 판정은 그 경우를 통째로 놓친다.
    """
    import qatc.cli_knowledge as ck

    monkeypatch.setattr(ck, "cmd_slot_status",
                        _raise(sqlite3.OperationalError(message)))
    assert main(["slot", "status", "아무거나"]) == 1
    out = capsys.readouterr().out
    assert "다른 qatc 프로세스가 실행 중" in out
    assert "OperationalError" not in out


@pytest.mark.parametrize("message, locked", [
    ("database is locked", True),
    ("database is busy", True),
    ("Database Is Locked", True),
    ("no such table: slots", False),
    ("attempt to write a readonly database", False),
], ids=["locked", "busy", "mixed-case", "no-such-table", "readonly"])
def test_lock_classifier_separates_lock_from_other_db_errors(message, locked):
    """분류기 자체를 직접 고정한다 — CLI 배선과 판정 규칙을 따로 봉인한다.

    `mixed-case` 가 `is_locked_error` 의 `str(exc).lower()` 를 봉인한다. 나머지
    파라미터가 전부 소문자라 `.lower()` 를 지워도 스위트 전체가 초록이었다
    (실측: 실패 0건). sqlite 자신은 소문자로 던지지만 이 함수가 받는 것은 어느
    층에서든 다시 감싸지고 다시 포매팅될 수 있는 `OperationalError` 라,
    대소문자에 기대는 판정은 조용히 빗나간다 — 그러면 잠금이 Minor 13 이전의
    "다음 조치가 없는 오류" 로 되돌아간다.
    """
    assert is_locked_error(sqlite3.OperationalError(message)) is locked


def test_non_lock_operational_error_still_shows_the_real_cause(cfg_env, capsys, monkeypatch):
    """잠금이 아닌 DB 오류까지 "다른 프로세스" 로 뭉개면 진단이 불가능해진다."""
    import qatc.cli_knowledge as ck

    monkeypatch.setattr(ck, "cmd_slot_status",
                        _raise(sqlite3.OperationalError("no such table: slots")))
    assert main(["slot", "status", "아무거나"]) == 1
    out = capsys.readouterr().out
    assert "no such table" in out
    assert "다른 qatc 프로세스" not in out


# --- 픽스처 격리 (이월 #14 · Minor 7) ------------------------------------


def test_cfg_env_isolates_the_real_user_config(cfg_env, capsys):
    """픽스처 안에서 저장해도 개발자의 실제 설정 파일은 건드리지 않는다.

    이 테스트가 없으면 `save()` 를 부르는 테스트가 하나 늘어날 때마다
    개발자의 `%APPDATA%\\qatc\\config.json` 이 조용히 덮어써진다.
    """
    import os
    from pathlib import Path

    from qatc.config import AppConfig

    here = AppConfig.config_file()
    assert str(cfg_env.parent) in str(here), f"설정 파일이 임시 폴더 밖이다: {here}"
    assert Path(os.environ["APPDATA"]).is_relative_to(cfg_env.parent)


# --- 파일명 살균 규칙 (T2 · M3 · M16) ------------------------------------


@pytest.mark.parametrize("ch", list('\\/:*?"<>|') + ["\x00", "\x07", "\x1f"],
                         ids=["backslash", "slash", "colon", "star", "question",
                              "quote", "lt", "gt", "pipe", "nul", "bel", "unit-sep"])
def test_safe_filename_part_replaces_every_forbidden_character(ch):
    """금지문자 11종 중 4종만 검증돼 있었다 (M3 생존).

    `\\` `"` `<` `>` `|` 와 제어범위가 비어 있었다. 그중 `\\` 는 실제 피해가
    가장 큰 문자다 — Windows 경로 구분자라 `mkdir(parents=True)` 가 rc=0 으로
    엉뚱한 하위 폴더를 만들고 사용자는 xlsx 를 찾지 못한다.
    """
    got = _safe_filename_part(f"앞{ch}뒤")
    assert got == "앞_뒤", ascii(got)


def test_safe_filename_part_keeps_ordinary_names_untouched():
    """반대쪽 경계 — 멀쩡한 이름을 건드리면 안 된다."""
    for name in ("파티편성", "warp", "전투 보스", "v1.2 업데이트"):
        assert _safe_filename_part(name) == name, name


@pytest.mark.parametrize("name, want", [
    ("보스전.", "보스전"),
    ("보스전 ", "보스전"),
    ("보스전. . ", "보스전"),
    ("보스전...", "보스전"),
], ids=["dot", "space", "mixed", "dots"])
def test_safe_filename_part_strips_trailing_dots_and_spaces(name, want):
    """끝의 마침표·공백은 Windows 가 파일명으로 만들 수 없다 (M16a 미검증).

    지금은 살균 결과가 `{게임}_{컨텐츠}_TC.xlsx` 의 **가운데**에 들어가므로
    당장 실패하지는 않지만, 이 함수의 계약은 "파일명 조각으로 안전하게 만든다"
    이고 조각이 파일명 끝에 오는 호출자가 생기면 그때 조용히 깨진다.
    """
    assert _safe_filename_part(name) == want


@pytest.mark.parametrize("name", ["", "   ", "...", ". . .", " . "],
                         ids=["empty", "spaces", "dots", "dots-spaces", "space-dot-space"])
def test_safe_filename_part_falls_back_to_a_name_when_nothing_is_left(name):
    """전부 지워지면 빈 파일명이 된다 — `이름없음` 폴백이 미검증이었다 (M16b).

    폴백이 없으면 `starrail__TC.xlsx` 처럼 조각이 사라지거나, 조각이 파일명
    전체인 호출자에서는 이름 없는 경로가 만들어진다. (금지문자는 `_` 로
    치환되므로 여기 오지 않는다 — `.rstrip('. ')` 로만 빌 수 있다.)
    """
    assert _safe_filename_part(name) == "이름없음"


def test_export_default_filename_survives_a_backslash_in_the_content_name(cfg_env, capsys):
    """역슬래시가 든 컨텐츠 이름으로도 하위 폴더가 생기면 안 된다 (종단 확인).

    파라미터라이즈된 위 테스트는 `/ : ? *` 만 실제 `export` 로 확인했다.
    역슬래시는 Windows 경로 구분자라 같은 결함 중 피해가 가장 크다.
    """
    name = "전투\\보스"
    main(["slot", "init", name, "--game", "starrail"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다

    assert main(["export", name, "--game", "starrail"]) == 0
    out = capsys.readouterr().out.strip()
    path = Path(out.split("✓ ", 1)[1].split("  (", 1)[0])

    assert path.exists()
    assert path.parent == cfg_env
    # 파일명 자체도 본다. `parent` 비교만으로는 **Windows 에서만** 판별이
    # 된다 — POSIX 에서 역슬래시는 평범한 글자라 정화를 지워도 상위 폴더가
    # 그대로여서 통과한다. 이 프로젝트는 Windows 전용이지만, 테스트가 어디서
    # 도는지에 따라 판별력이 사라지는 것은 그 자체로 결함이다.
    assert "\\" not in path.name
    assert [d for d in cfg_env.iterdir() if d.is_dir()] == []


# --- qatc config 는 "확인" 이다 (C2) -------------------------------------


@pytest.fixture()
def real_config(tmp_path, monkeypatch):
    """`cmd_config`·`AppConfig.load()` 자체의 파일 처리를 시험할 **빈 자리**를 만든다.

    `cfg_env` 도 이제 진짜 `save()` 를 태우므로 설정 파일 자체는 그쪽으로도
    들여다볼 수 있다 (`test_cfg_env_isolates_the_real_user_config` 가 그렇게
    한다). 그런데 `cfg_env` 는 픽스처 실행 시점에 **이미 유효한** 설정을
    (고정된 knowledge_root·profiles_dir 로) 저장해 둔다 — slot·tc·export 같은
    명령이 바로 쓸 수 있는 "이미 갖춰진 앱" 을 주는 게 목적이다.

    여기 아래 테스트들은 정반대를 원한다: 파일이 아직 없는 상태(첫 실행 생성
    문구 확인), 이미 있는 파일을 절대 다시 쓰지 않는지, 문법이 깨진 JSON, 최상위가
    객체가 아닌 JSON — 즉 파일의 **부재·내용을 테스트가 직접 통제**해야 한다.
    `project_root()` 도 함께 돌리는 이유는 파일이 없을 때 `AppConfig()` 의
    기본값이 거길 참조하기 때문이다 — `cfg_env` 는 생성자에 경로를 항상 명시로
    넘기므로 이 기본값 경로를 아예 타지 않는다. 그래서 `real_config` 는
    `APPDATA` 와 `project_root()` 만 임시 폴더로 돌리고 파일 자체는 건드리지
    않은 채 남겨 둔다.
    """
    import qatc.config as config

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(config, "project_root", lambda: tmp_path / "proj")
    return tmp_path / "appdata" / "qatc" / "config.json"


def _write_config(path: Path, knowledge_root: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"knowledge_root": str(knowledge_root), "profiles_dir": ""},
                   ensure_ascii=False),
        encoding="utf-8",
    )


def test_config_does_not_rewrite_an_existing_config_file(real_config, tmp_path, capsys):
    """`config` 는 README·`--help` 가 약속한 대로 **확인**이어야 한다 (C2).

    예전 `cmd_config` 의 첫 문장이 `cfg.save()` 였다. README 는 이 파일을 손으로
    고치라고 안내하면서 위치를 이 명령으로 찾으라고 하는데, 찾는 행위가 파일을
    덮어썼다.
    """
    _write_config(real_config, tmp_path / "내지식")
    before = real_config.read_bytes()

    assert main(["config"]) == 0
    assert real_config.read_bytes() == before, "확인 명령이 설정 파일을 고쳤다"

    out = capsys.readouterr().out
    assert str(real_config) in out
    assert str(tmp_path / "내지식") in out      # 파일에 적힌 값이 그대로 보인다


def test_config_does_not_create_the_knowledge_folder(real_config, tmp_path, capsys):
    """확인만 했는데 폴더가 생기면 그것도 쓰기다.

    `cfg.knowledge_path` 프로퍼티는 `mkdir(parents=True)` 를 한다. 출력용으로
    그걸 부르면 오타 난 경로에 빈 폴더가 조용히 만들어진다.
    """
    kroot = tmp_path / "아직 없는 폴더"
    _write_config(real_config, kroot)

    assert main(["config"]) == 0
    assert not kroot.exists()
    out = capsys.readouterr().out
    assert str(kroot) in out
    assert "아직 없습니다" in out               # 없다는 사실을 말해준다


def test_config_creates_the_default_file_on_first_run_and_says_so(real_config, capsys):
    """첫 실행에는 설정 파일이 없다 — 만들어 주되 **만들었다고 말해야** 한다.

    말하지 않으면 사용자는 화면의 경로가 자기 파일 내용인지 기본값인지 알 수 없다.
    """
    assert not real_config.exists()

    assert main(["config"]) == 0
    assert real_config.exists()                # 첫 실행 생성 경로는 남긴다
    out = capsys.readouterr().out
    assert "새로 만들었습니다" in out
    assert str(real_config) in out


def test_broken_config_is_a_visible_error_and_is_not_overwritten(real_config, capsys):
    """깨진 설정을 조용히 기본값으로 되돌리면 안 된다 (C2).

    재검증자가 실제로 당한 경로다 — 스크래치 설정의 이스케이프가 깨져 있었고,
    `qatc config` 한 번에 설정이 기본값으로 덮어써졌으며 출력은 그게 파일
    내용인지 기본값인지 구분해 주지 않았다. 사용자의 `knowledge_root` 가
    말없이 저장소 기본값으로 바뀌면 쌓아둔 지식 DB가 사라진 것처럼 보인다.
    """
    real_config.parent.mkdir(parents=True, exist_ok=True)
    # README 가 안내하는 손편집에서 가장 흔한 실수 — Windows 경로의 역슬래시
    broken = '{"knowledge_root": "C:' + chr(92) + '내지식"}'
    real_config.write_text(broken, encoding="utf-8")

    assert main(["config"]) == 1
    out = capsys.readouterr().out
    assert str(real_config) in out              # 어느 파일인지
    assert "읽을 수 없습니다" in out
    assert "지우면" in out                       # 다음 조치
    assert real_config.read_text(encoding="utf-8") == broken, "깨진 설정이 덮어써졌다"


@pytest.mark.parametrize("body, kind", [
    ("[]", "list"),
    ("42", "int"),
    ('"x"', "str"),
    ("null", "NoneType"),
], ids=["list", "int", "string", "null"])
def test_config_whose_top_level_is_not_an_object_stops_and_is_not_overwritten(
    real_config, capsys, body, kind,
):
    """문법은 맞지만 **최상위가 객체가 아닌** 설정도 소리 내어 멈춰야 한다 (C2).

    위아래 두 테스트는 둘 다 문법이 깨진 JSON 을 먹이므로 `AppConfig.load()` 의
    `JSONDecodeError` 갈래만 지난다. `isinstance(raw, dict)` 갈래는 한 번도
    실행되지 않아, 그 `raise SystemExit` 을 `return cls()` 로 바꿔도 전체
    스위트가 초록이었다 (실측).

    이 갈래가 조용해지면 피해는 문법 오류 때와 똑같다 — `knowledge_root` 가
    말없이 저장소 기본값으로 돌아가고, 그 기본값이 사용자 파일을 덮어쓴다.
    그리고 이건 억지 입력이 아니다: README 는 이 파일을 손으로 고치라고
    안내하는데, 바깥 중괄호를 지우거나 예시 목록을 통째로 붙여 넣으면 문법은
    멀쩡한 채 최상위만 `list`·`int`·`str`·`null` 이 된다.

    타입 이름까지 고정한다 — "무엇이 잘못됐는가" 가 빠지면 사용자는 문법 오류와
    구분할 수 없어 어디를 고쳐야 할지 모른다.
    """
    real_config.parent.mkdir(parents=True, exist_ok=True)
    real_config.write_text(body, encoding="utf-8")

    assert main(["config"]) == 1
    out = capsys.readouterr().out
    assert str(real_config) in out              # 어느 파일인지
    assert "읽을 수 없습니다" in out
    assert kind in out                          # 무엇이 잘못됐는지
    assert "지우면" in out                       # 다음 조치
    assert real_config.read_text(encoding="utf-8") == body, "깨진 설정이 덮어써졌다"


def test_broken_config_stops_every_command_not_just_config(real_config, capsys):
    """설정이 깨진 채로 다른 명령이 진행되면 **다른 지식 폴더**를 보게 된다.

    조용한 기본값 복귀는 `config` 만의 문제가 아니다 — `slot status` 가 엉뚱한
    폴더를 보고 "컨텐츠가 없습니다" 라고 답하면, 사용자는 자기 인터뷰가 사라진
    줄 안다.
    """
    real_config.parent.mkdir(parents=True, exist_ok=True)
    real_config.write_text("{깨진 JSON", encoding="utf-8")

    assert main(["slot", "status", "파티편성"]) == 1
    out = capsys.readouterr().out
    assert str(real_config) in out
    assert "읽을 수 없습니다" in out


def test_config_game_sets_the_default(cfg_env, capsys):
    assert main(["config", "--game", "starrail"]) == 0
    out = capsys.readouterr().out
    from qatc.config import AppConfig
    assert AppConfig.load().default_game == "starrail"

    # 계약은 "설정하고 멈춘다" — 확인 한 줄이 게임과 저장 파일을 알려주고 끝나야
    # 한다. "starrail" 만 찾으면 아래 조회 경로의 프로파일 목록에도 그 글자가
    # 나오므로 (진단됨) 구분이 안 된다 — 확인 메시지에만 있는 어구로 잡는다.
    assert "기본 게임 = starrail" in out
    assert str(AppConfig.config_file()) in out

    # 뒤이어 평범한 조회 경로(전체 설정 덤프)로 흘러 들어가면 안 된다 — 그
    # 경로에만 나오는 표식으로 확인한다.
    assert "사용 가능한 프로파일" not in out    # 조회 경로의 프로파일 표 제목
    assert "지식 폴더" not in out               # 조회 경로에만 찍히는 줄


def test_config_game_rejects_an_unregistered_name(cfg_env, capsys):
    rc = main(["config", "--game", "starrial"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "starrial" in out
    assert "starrail" in out          # 쓸 수 있는 이름을 알려준다
    from qatc.config import AppConfig
    assert AppConfig.load().default_game == ""   # 잘못된 값이 저장되지 않았다


def test_plain_config_still_does_not_write(cfg_env, capsys):
    """인자 없는 config 는 확인만 한다 — 기존 계약."""
    from qatc.config import AppConfig
    assert main(["config", "--game", "starrail"]) == 0
    capsys.readouterr()
    before = AppConfig.config_file().read_bytes()
    assert main(["config"]) == 0
    capsys.readouterr()
    assert AppConfig.config_file().read_bytes() == before


# --- --game 검증과 기본값 (T4) --------------------------------------------


def test_knowledge_uses_the_default_game(cfg_env, capsys):
    assert main(["config", "--game", "starrail"]) == 0
    capsys.readouterr()
    assert main(["slot", "init", "파티편성", "--game", "starrail"]) == 0
    capsys.readouterr()
    assert main(["knowledge"]) == 0
    assert "파티편성" in capsys.readouterr().out


def test_knowledge_default_game_names_itself_in_the_text_header(cfg_env, capsys):
    """기본 게임 경로에서도 텍스트 헤더가 **해석된** 게임을 말해야 한다.

    `cmd_knowledge` 의 헤더 자리(`f"[{game}] ..."`)를 `args.game` 으로 되돌려도
    스위트가 초록일 수 있다 — `--game` 이 없으면 `args.game` 은 `None` 이라
    헤더는 `[None] 컨텐츠 1개` 를 찍지만, DB는 여전히 (resolve_game 이 고른)
    starrail.db 를 정확히 열어 컨텐츠 행은 그대로 옳게 나온다. "컨텐츠 이름이
    출력에 있는가" 만 보는 검사로는 그 절반만 고쳐진 상태 — 한 게임을
    보고하면서 다른 게임을 여는 것 — 를 잡을 수 없다. 그래서 헤더 줄 자체를
    등호로 본다 (아래 표에도 게임 이름이 나올 수 있으므로 전체 출력이 아니라
    첫 줄만 본다).
    """
    assert main(["config", "--game", "starrail"]) == 0
    capsys.readouterr()
    assert main(["slot", "init", "파티편성", "--game", "starrail"]) == 0
    capsys.readouterr()

    assert main(["knowledge"]) == 0
    header = capsys.readouterr().out.splitlines()[0]
    assert header == "[starrail] 컨텐츠 1개"


def test_knowledge_default_game_appears_in_the_json_payload(cfg_env, capsys):
    """`--json` 의 `game` 필드도 헤더와 같은 이유로 해석된 값이어야 한다.

    `args.game` 으로 되돌리면 이 필드는 `null` 이 된다. 페이로드 전체에서
    "starrail" 을 부분문자열로 찾으면 안 된다 — 컨텐츠 행 이름에 게임 이름이
    우연히 들어갈 수 있어 그 검사는 `game` 키가 `null` 이어도 통과한다.
    그래서 JSON 을 파싱해 `game` 키의 값만 본다.
    """
    assert main(["config", "--game", "starrail"]) == 0
    capsys.readouterr()
    assert main(["slot", "init", "파티편성", "--game", "starrail"]) == 0
    capsys.readouterr()

    assert main(["knowledge", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["game"] == "starrail"


def test_export_on_a_locked_file_tells_the_user_to_close_excel(cfg_env, capsys):
    import os
    import stat

    assert main(["slot", "init", "잠김", "--game", "starrail"]) == 0
    assert main(["slot", "set", "잠김", "core_action",
                 "--status", "filled", "--value", "동작"]) == 0
    capsys.readouterr()
    assert main(["export", "잠김"]) == 0
    out1 = capsys.readouterr().out
    path = out1.split("✓")[1].split("(")[0].strip()

    os.chmod(path, stat.S_IREAD)
    try:
        rc = main(["export", "잠김"])
        out = capsys.readouterr().out
        assert rc == 1
        # 예외 클래스 이름을 하나씩 나열해 부재를 확인하는 방식은 이름 목록에
        # 없는 예외(예: cmd_export 안의 다른 버그가 낸 UnboundLocalError)가
        # cli.py 의 범용 폴백을 타면 그대로 새어나가도 잡아내지 못한다 —
        # 실제로 그런 뮤테이션이 기존의 이름 나열 단언 4개를 전부 통과시켰다.
        # 진짜 불변 조건은 "실패한 명령은 원인이 무엇이든 오류 줄을 정확히
        # 하나만 찍는다" 이므로 그것을 구조적으로 확인한다.
        assert out.count("오류:") == 1
        assert "Excel" in out and "닫" in out
        assert "PermissionError" not in out
        assert "ExportBlocked" not in out
    finally:
        os.chmod(path, stat.S_IWRITE)


# --- `--json` 의 표준출력은 JSON 하나여야 한다 (최종 검토 F/M2) ---------------
#
# 회귀 배경: T4 가 `cmd_knowledge` 앞에 `resolve_game` 을 넣으면서 이 명령이
# 처음으로 `load_profiles` 를 거치게 됐다. 프로파일이 0개면 `[경고] …` 가,
# 깨진 YAML 이 있으면 `[프로파일] …` 여러 줄이 **JSON 앞 stdout 에** 붙어
# `json.loads` 가 죽는데 **rc 는 0** 이다 — 호출자(인터뷰 모델)는 성공했다고
# 믿고 빈 손으로 진행한다. 진단은 지우지 않고 stderr 로 보낸다.


def _json_commands() -> list[list[str]]:
    """표준출력에 JSON 을 내는 명령 전부. 새 명령이 생기면 여기 추가한다."""
    return [
        ["slot", "status", "파티편성", "--json"],
        ["tc", "plan", "파티편성", "--json"],
        ["knowledge", "--game", "starrail", "--json"],
    ]


@pytest.mark.parametrize("argv", _json_commands(), ids=lambda a: " ".join(a[:2]))
def test_json_stdout_stays_parseable_when_a_profile_is_broken(
        cfg_env, capsys, tmp_path, argv):
    """깨진 프로파일 YAML 이 있어도 `--json` 의 stdout 은 JSON 하나다."""
    assert main(["slot", "init", "파티편성", "--game", "starrail"]) == 0
    capsys.readouterr()
    (tmp_path / "profiles" / "깨짐.yaml").write_text("name: [깨진\n", encoding="utf-8")

    assert main(argv) == 0
    cap = capsys.readouterr()
    json.loads(cap.out)          # 진단이 섞였다면 JSONDecodeError 로 죽는다


@pytest.mark.parametrize("argv", _json_commands(), ids=lambda a: " ".join(a[:2]))
def test_json_stdout_stays_parseable_when_there_are_no_profiles(
        cfg_env, capsys, tmp_path, argv):
    """프로파일이 0개여도 `--json` 의 stdout 은 JSON 하나다.

    이쪽은 `validate_game` 이 **의도적으로** 내는 경고다 (프로파일 0개면 검증을
    건너뛴다 — 무조건 거부하면 도구가 벽돌이 된다). 의도된 경고라도 기계가 읽는
    채널에 섞이면 안 된다는 것이 이 테스트의 요지다.
    """
    assert main(["slot", "init", "파티편성", "--game", "starrail"]) == 0
    capsys.readouterr()
    for f in (tmp_path / "profiles").glob("*.yaml"):
        f.unlink()

    assert main(argv) == 0
    json.loads(capsys.readouterr().out)


def test_broken_profile_notice_goes_to_stderr_not_stdout(cfg_env, capsys, tmp_path):
    """진단을 **지우지 않는다** — 사람이 볼 수 있게 stderr 로 옮길 뿐이다.

    억제해 버리면 왜 게임 이름이 거부되는지 아무도 모르게 된다. 파일 이름이
    stderr 에 남아야 어느 파일을 고칠지 알 수 있다.
    """
    assert main(["slot", "init", "파티편성", "--game", "starrail"]) == 0
    capsys.readouterr()
    (tmp_path / "profiles" / "깨짐.yaml").write_text("name: [깨진\n", encoding="utf-8")

    assert main(["knowledge", "--game", "starrail", "--json"]) == 0
    cap = capsys.readouterr()
    assert "깨짐.yaml" in cap.err
    assert "깨짐.yaml" not in cap.out


def test_no_profile_warning_goes_to_stderr_not_stdout(cfg_env, capsys, tmp_path):
    """프로파일 0개 경고도 마찬가지다 — 보이되 stdout 은 더럽히지 않는다."""
    assert main(["slot", "init", "파티편성", "--game", "starrail"]) == 0
    capsys.readouterr()
    for f in (tmp_path / "profiles").glob("*.yaml"):
        f.unlink()

    assert main(["knowledge", "--game", "starrail", "--json"]) == 0
    cap = capsys.readouterr()
    assert "검증을 건너뜁니다" in cap.err
    assert "검증을 건너뜁니다" not in cap.out


# --- 읽기 명령의 --game 오타가 유령 DB 를 만들지 않는다 (최종 검토 3절 1번) ---
#
# `resolve_store` 는 `KnowledgeStore(root / f"{game}.db").open()` 인데, sqlite 는
# **여는 순간 파일을 만든다.** 그래서 `slot status 파티편성 --game starrial` 은
# rc=1 로 끝나면서 `starrial.db` 를 남겼다 — README·SKILL.md·games.py 도크스트링이
# 모두 "오타는 거부된다, 새 DB 가 조용히 생기지 않는다" 를 약속하는데도.
#
# **`resolve_store` 에 `validate_game` 을 넣어 고치지 않는다.** 그건 이 브랜치의
# 설계 결정(읽기 경로는 미검증)을 뒤집어, 게임이 `profiles/` 에서 사라지면 그
# DB 에 쌓인 인터뷰 데이터에 접근할 방법이 없어진다. 대신 **없는 것을 만들지
# 않는** 쪽으로 고친다: 명시된 게임의 DB 파일이 없으면 만들지 말고 멈춘다.

_READ_COMMANDS_WITH_GAME = [
    ["slot", "status", "파티편성"],
    ["slot", "set", "파티편성", "core_action", "--status", "unknown"],
    ["tc", "plan", "파티편성"],
    ["tc", "list", "파티편성"],
    ["export", "파티편성"],
]


@pytest.mark.parametrize("argv", _READ_COMMANDS_WITH_GAME,
                         ids=lambda a: " ".join(a[:2]))
def test_explicit_game_typo_makes_no_ghost_db(cfg_env, capsys, argv):
    """오타 난 `--game` 으로 읽으면 그 이름의 DB 가 **생기지 않는다.**"""
    assert main(["slot", "init", "파티편성", "--game", "starrail"]) == 0
    capsys.readouterr()

    assert main(argv + ["--game", "starrial"]) == 1
    out = capsys.readouterr().out

    assert not (cfg_env / "starrial.db").exists(), (
        f"유령 DB 가 생겼다: {sorted(p.name for p in cfg_env.glob('*.db'))}"
    )
    assert "starrial" in out                       # 무엇이 문제인지
    assert "slot init" in out                      # 다음 조치
    assert out.count("오류:") == 1                  # 파이썬 예외가 아니라 우리 문장
    assert "Error" not in out


def test_the_missing_db_message_names_the_file(cfg_env, capsys):
    """어느 파일을 찾다 실패했는지 말한다 — 경로가 없으면 오타인지 폴더 문제인지 모른다."""
    assert main(["slot", "init", "파티편성", "--game", "starrail"]) == 0
    capsys.readouterr()

    assert main(["slot", "status", "파티편성", "--game", "starrial"]) == 1
    out = capsys.readouterr().out
    assert str(cfg_env / "starrial.db") in out


def test_an_existing_db_still_opens_with_an_explicit_game(cfg_env, capsys):
    """반대 방향 — 있는 DB 는 그대로 열린다. 위 가드가 정상 경로를 막으면 안 된다."""
    assert main(["slot", "init", "파티편성", "--game", "starrail"]) == 0
    capsys.readouterr()

    assert main(["slot", "status", "파티편성", "--game", "starrail", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["game"] == "starrail"


def test_slot_init_still_creates_the_db_for_a_registered_game(cfg_env, capsys):
    """생성 경로는 여전히 만든다 — 가드는 `resolve_store`(읽기)에만 있다.

    `slot init` 까지 "없으면 멈춘다" 가 되면 새 게임을 영영 시작할 수 없다.
    """
    assert not (cfg_env / "genshin.db").exists()
    assert main(["slot", "init", "파티편성", "--game", "genshin"]) == 0
    capsys.readouterr()
    assert (cfg_env / "genshin.db").exists()


# --- 설계 결정 둘을 테스트로 못박는다 (최종 검토 M5 · M8) ---------------------
#
# 둘 다 변이 생존이었다 — 뒤집어도 스위트가 초록이라, 나중 기여자가 어느 쪽으로
# 고쳐도 "고쳤다" 는 신호를 받는다. 결정의 **이유**는 코드 주석이 아니라
# 실패하는 테스트가 지켜야 한다.


def test_the_read_path_stays_unvalidated_so_data_never_gets_stranded(
        cfg_env, capsys, tmp_path):
    """게임이 `profiles/` 를 떠나도 그 DB 는 계속 읽힌다 (결정 #3).

    `resolve_store` 의 명시-게임 분기에 `validate_game` 을 넣으면 전체 스위트가
    그대로 초록이었다 (M5). 그러나 그 변이는 프로파일 YAML 한 장이 지워지거나
    이름이 바뀐 순간 **그 게임에 쌓아둔 인터뷰 전부를 좌초시킨다** — 읽기도,
    `tc plan` 도, `export` 도 막힌다. 되살릴 방법은 프로파일을 복원하는 것뿐인데,
    사용자는 왜 막혔는지 알 방법이 없다.

    그래서 생성 경로와 읽기 경로를 **같은 테스트 안에서 대조**한다. 한쪽만
    보면 "검증이 도는가" 와 "데이터가 살아 있는가" 중 하나를 놓친다.
    """
    assert main(["slot", "init", "파티편성", "--game", "genshin"]) == 0
    assert main(["slot", "set", "파티편성", "core_action",
                 "--status", "filled", "--value", "파티를 적용한다"]) == 0
    capsys.readouterr()

    (tmp_path / "profiles" / "genshin.yaml").unlink()      # 게임이 목록을 떠났다

    # 생성 경로는 이제 이 이름을 거부한다 — 검증이 실제로 돌고 있다는 대조군
    assert main(["slot", "init", "새컨텐츠", "--game", "genshin"]) == 1
    assert "등록된 게임이 아닙니다" in capsys.readouterr().out

    # 그러나 **읽기는 그대로다.** 이 세 줄이 M5 변이를 잡는다.
    assert main(["slot", "status", "파티편성", "--game", "genshin", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["filled"] == 1

    assert main(["tc", "plan", "파티편성", "--game", "genshin", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["planned"]

    assert main(["export", "파티편성", "--game", "genshin"]) == 0
    assert "✓" in capsys.readouterr().out

    # `--game` 없이 컨텐츠 이름으로 역추적하는 경로도 마찬가지
    assert main(["slot", "status", "파티편성", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["game"] == "genshin"


def test_a_stored_default_game_that_left_the_profiles_is_rejected(
        cfg_env, capsys, tmp_path):
    """저장된 기본 게임도 대조한다 (결정 #1의 나머지 절반).

    `resolve_game` 에 `if game:` 가드를 넣어 **명시된 플래그만** 검증하게 해도
    전체 스위트가 초록이었다 (M8). 기존 테스트가 전부
    `main(["config","--game","starrail"])` 로 기본값을 넣는데 그 경로가 이미
    검증하므로, 저장된 값은 **구성상 항상 등록된 상태**라 가드가 시험되지 않았다.

    실제로는 설정 파일이 오래 살아남는다 — 프로파일 이름이 바뀌거나 파일이
    지워지면 저장된 기본값만 남는다. 그때 검증이 꺼져 있으면 `slot init` 이
    등록되지 않은 이름으로 **새 DB 를 만들고** rc=0 을 낸다. 그래서 여기서는
    검증을 통과한 기본값을 저장한 **뒤에** 프로파일을 지운다.
    """
    assert main(["config", "--game", "genshin"]) == 0
    capsys.readouterr()

    (tmp_path / "profiles" / "genshin.yaml").unlink()

    assert main(["slot", "init", "새컨텐츠", "--types", "편성"]) == 1
    out = capsys.readouterr().out
    assert "genshin" in out                        # 무엇이 문제인지
    assert "등록된 게임이 아닙니다" in out
    assert "starrail" in out                       # 무엇을 쓸 수 있는지
    assert not (cfg_env / "genshin.db").exists(), "거부해 놓고 DB 를 만들었다"


def test_the_stored_default_is_validated_on_knowledge_too(cfg_env, capsys, tmp_path):
    """`knowledge` 도 같은 기본값을 쓴다 — 한 명령만 고치면 다른 쪽이 샌다."""
    assert main(["config", "--game", "genshin"]) == 0
    capsys.readouterr()

    (tmp_path / "profiles" / "genshin.yaml").unlink()

    assert main(["knowledge"]) == 1
    assert "등록된 게임이 아닙니다" in capsys.readouterr().out


def test_config_game_on_a_locked_file_is_a_korean_sentence_not_an_exception(
        cfg_env, capsys):
    """CLI 레벨 — 잠긴 설정 파일에 `config --game` 을 하면 사람 말이 나온다.

    이 경로는 **이 계획이 만들었다.** `qatc config` 는 확인 전용이었는데
    `--game` 이 붙으면서 쓰기 명령이 됐다. 편집기로 열어두거나 클라우드
    동기화가 잠그면 실제로 난다.

    짝이 되는 단위 테스트(`tests/test_config.py` 의
    `test_save_on_a_locked_config_says_what_to_do`)는 `AppConfig.save()` 를 직접
    감싸므로 **CLI 가 그 `SystemExit` 를 어떻게 보여주는지**는 보지 못한다.
    실측: `SystemExit` 를
    다른 예외 타입으로 바꾸면 단위 테스트 하나만 실패하고, CLI 출력은
    `오류: RuntimeError: 설정 파일에 쓸 수 없습니다 — …` 가 되어 이 계획이
    없애기로 한 형태로 되돌아간다. 잠긴 xlsx 는 같은 이유로 CLI 레벨 쌍둥이를
    이미 갖고 있는데 이쪽만 없었다.
    """
    import os
    import stat

    from qatc.config import AppConfig

    target = AppConfig.config_file()
    assert target.exists()                     # 픽스처가 이미 만들어 둔다
    os.chmod(target, stat.S_IREAD)
    try:
        rc = main(["config", "--game", "genshin"])
        out = capsys.readouterr().out
        assert rc == 1
        # 실패한 명령은 원인이 무엇이든 오류 줄을 정확히 하나만 찍는다
        # (잠긴 xlsx 테스트와 같은 구조적 단언 — 예외 이름을 나열해 부재를
        # 확인하는 방식은 목록에 없는 예외가 새어도 잡지 못한다).
        assert out.count("오류:") == 1
        assert str(target) in out              # 어느 파일인지
        assert "닫" in out and "권한" in out     # 다음 조치 두 갈래
        # 파이썬 예외 이름이 그대로 나오면 안 된다. 클래스 이름은 관용적으로
        # `…Error` 로 끝나므로 접미사로 통째로 막는다 — 이름을 하나씩 나열하면
        # 목록에 없는 예외가 새어 나가도 통과한다.
        assert "Error" not in out
    finally:
        os.chmod(target, stat.S_IWRITE)

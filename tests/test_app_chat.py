"""`claude` 자식 프로세스. 진짜 API 는 부르지 않는다."""

import json
import sys
import textwrap
from pathlib import Path

import pytest

from qatc.app.chat import ChatEvent, ClaudeMissing, session_id_for, stream_turn
from qatc.config import AppConfig, project_root


@pytest.fixture()
def cfg(tmp_path):
    return AppConfig(knowledge_root=str(tmp_path / "k"),
                     profiles_dir=str(tmp_path / "p"))


def _fake_claude(tmp_path, lines, *, record_to=None, stderr_text=None, exit_code=0):
    """정해진 stream-json 줄을 뱉는 가짜 실행 파일을 만든다.

    `record_to` 를 주면 실행 시점에 자신이 받은 `argv` 와 `cwd` 를 그 경로에
    JSON 으로 남긴다 — 이 가짜는 원래 자기 인자를 무시하므로, 실행 계약
    (cwd·세션 id·플래그)을 검사하려면 그 인자를 어딘가 남겨야 테스트가 읽을
    수 있다. `stderr_text` 를 주면 표준오류에 그 문자열을 쓰고 `exit_code`
    로 끝난다 — 비정상 종료 시 stderr 가 진단으로 전달되는지 확인하는 데 쓴다.
    """
    script = tmp_path / "fake_claude.py"
    script.write_text(textwrap.dedent(f"""
        import json
        import os
        import sys

        record_to = {(str(record_to) if record_to else None)!r}
        if record_to:
            with open(record_to, "w", encoding="utf-8") as f:
                json.dump({{"argv": sys.argv, "cwd": os.getcwd()}}, f, ensure_ascii=False)

        for line in {lines!r}:
            sys.stdout.write(line + "\\n")
            sys.stdout.flush()

        stderr_text = {stderr_text!r}
        if stderr_text:
            sys.stderr.write(stderr_text)
            sys.stderr.flush()

        sys.exit({exit_code!r})
    """), encoding="utf-8")
    return [sys.executable, str(script)]


def _skill_text() -> str:
    return (project_root() / ".claude" / "skills" / "interview" / "SKILL.md").read_text(
        encoding="utf-8")


def _distinctive_skill_line() -> str:
    """SKILL.md 를 실제로 읽어, 이 파일에만 있을 최상위 제목을 뽑는다.

    문장을 하드코딩하면 SKILL.md 가 바뀔 때마다 이 테스트도 손으로 고쳐야
    한다. 파일에서 직접 뽑으면 내용이 바뀌어도 테스트는 계속 유효하다.
    """
    for line in _skill_text().splitlines():
        if line.startswith("# "):
            return line
    raise AssertionError("SKILL.md 에 최상위 제목이 없습니다")


OK_LINES = [
    json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "안녕"}]}}, ensure_ascii=False),
    json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash",
         "input": {"command": "qatc tc add 파티편성 ..."}}]}}, ensure_ascii=False),
    json.dumps({"type": "result", "subtype": "success", "is_error": False},
               ensure_ascii=False),
]

AUTH_LINES = [
    json.dumps({"type": "result", "subtype": "success", "is_error": True,
                "api_error_status": 401,
                "result": "Failed to authenticate. API Error: 401 OAuth access token has expired."},
               ensure_ascii=False),
]


def test_text_arrives_as_delta_events(cfg, tmp_path):
    evs = list(stream_turn(cfg, "안녕", None, claude=_fake_claude(tmp_path, OK_LINES)))
    deltas = [e for e in evs if e.kind == "delta"]
    assert "".join(d.data["text"] for d in deltas) == "안녕"


def test_tool_use_becomes_a_tool_event(cfg, tmp_path):
    evs = list(stream_turn(cfg, "x", None, claude=_fake_claude(tmp_path, OK_LINES)))
    tools = [e for e in evs if e.kind == "tool"]
    assert len(tools) == 1
    assert tools[0].data["name"] == "Bash"
    assert "qatc tc add" in tools[0].data["summary"]


def test_a_turn_ends_with_exactly_one_done(cfg, tmp_path):
    evs = list(stream_turn(cfg, "x", None, claude=_fake_claude(tmp_path, OK_LINES)))
    assert [e.kind for e in evs].count("done") == 1
    assert evs[-1].kind == "done"


def test_expired_token_becomes_an_auth_error_not_a_done(cfg, tmp_path):
    """401 은 유일하게 *성공한 것처럼 보이는* 실패라 1순위다.

    조용히 지나가면 사용자는 앱이 고장난 줄 안다.
    """
    evs = list(stream_turn(cfg, "x", None, claude=_fake_claude(tmp_path, AUTH_LINES)))
    errs = [e for e in evs if e.kind == "error"]
    assert len(errs) == 1
    assert errs[0].data["kind"] == "auth"
    assert "재인증" in errs[0].data["message"]
    assert "claude" in errs[0].data["message"]      # 무엇을 실행할지
    assert not [e for e in evs if e.kind == "done"]  # 성공으로 끝내지 않는다


def test_unparseable_line_does_not_kill_the_turn(cfg, tmp_path):
    """stream-json 이 아닌 줄이 섞여도 나머지를 계속 읽는다."""
    lines = ["이건 JSON 이 아니다"] + OK_LINES
    evs = list(stream_turn(cfg, "x", None, claude=_fake_claude(tmp_path, lines)))
    assert evs[-1].kind == "done"


def test_missing_executable_says_what_to_do(cfg):
    with pytest.raises(ClaudeMissing) as e:
        list(stream_turn(cfg, "x", None, claude=["존재하지않는실행파일"]))
    msg = str(e.value)
    assert "claude" in msg
    assert "설치" in msg or "찾을 수 없" in msg
    assert "FileNotFoundError" not in msg


def test_session_id_is_stable_per_content(cfg):
    a = session_id_for(cfg, "파티편성")
    assert session_id_for(cfg, "파티편성") == a
    assert session_id_for(cfg, "상점") != a


def test_session_id_survives_a_restart(cfg):
    a = session_id_for(cfg, "파티편성")
    assert (cfg.knowledge_path / "sessions.json").exists()
    assert session_id_for(cfg, "파티편성") == a


def test_no_content_uses_the_default_session(cfg):
    """컨텐츠를 아직 안 고른 대화도 이어져야 한다."""
    assert session_id_for(cfg, None) == session_id_for(cfg, None)
    assert session_id_for(cfg, None) != session_id_for(cfg, "파티편성")


# --- 401 이 아닌 다른 오류 상태 코드가 "auth" 로 둔갑하지 않는다 ------------
#
# 브리프 표의 M10 이 지키려는 위험: `api_error_status == 401` 검사를 빼고
# `is_error` 만 보면, 401 이 아닌 다른 실패도 재인증 오류로 오분류된다.
# 위 `test_expired_token_...` 은 401 입력만 쓰므로 이 위험을 못 잡는다 —
# 401 만 넣으면 두 구현이 똑같은 결과를 내기 때문이다. 다른 상태 코드를
# 직접 넣어 구분해야 한다.
OTHER_ERROR_LINES = [
    json.dumps({"type": "result", "subtype": "success", "is_error": True,
                "api_error_status": 500, "result": "internal error"},
               ensure_ascii=False),
]


def test_a_non_401_error_is_not_mislabeled_as_auth(cfg, tmp_path):
    evs = list(stream_turn(cfg, "x", None, claude=_fake_claude(tmp_path, OTHER_ERROR_LINES)))
    errs = [e for e in evs if e.kind == "error"]
    assert len(errs) == 1
    assert errs[0].data["kind"] != "auth"
    assert not [e for e in evs if e.kind == "done"]


# --- 가짜 claude 는 자기 argv 를 무시한다 — 실행 계약 자체를 검사한다 -------
#
# 리뷰 지적: 위 테스트들은 전부 가짜의 *출력* 만 검사하고, 그 가짜가 실제로
# 어떤 인자·어떤 cwd 로 실행됐는지는 아무도 보지 않는다. `cwd` 를 빠뜨리면
# `.claude/settings.json` 의 allowlist 가 안 먹어 헤드리스 턴이 권한 승인
# 프롬프트에 조용히 멈추고, `--append-system-prompt` 를 빠뜨리면 모델이
# 인터뷰 방법을 아예 모른다 — 둘 다 이 태스크가 막으려던 실패인데, 실사용
# 에서만 드러나고 기존 테스트는 하나도 못 잡는다. 그래서 가짜가 자신이 받은
# `argv`·`cwd` 를 파일에 기록하게 하고, 그 기록을 읽어 계약을 직접 검사한다.


def test_system_prompt_carries_the_real_skill_text(cfg, tmp_path):
    """`--append-system-prompt` 값에 SKILL.md 의 실제 문장이 실려야 한다.

    플래그가 있는지만 보면 값이 비어 있거나 다른 내용이어도 통과한다 —
    SKILL.md 를 실제로 읽어 그 파일에만 있는 문장으로 값 자체를 검사한다.
    """
    record = tmp_path / "record.json"
    evs = list(stream_turn(cfg, "x", None, claude=_fake_claude(
        tmp_path, OK_LINES, record_to=record)))
    assert evs
    argv = json.loads(record.read_text(encoding="utf-8"))["argv"]
    prompt = argv[argv.index("--append-system-prompt") + 1]
    assert _distinctive_skill_line() in prompt


def test_child_process_cwd_is_the_project_root(cfg, tmp_path, monkeypatch):
    """`.claude/settings.json` 의 Bash allowlist 는 그 디렉터리 기준으로 매칭된다.

    cwd 가 어긋나면 헤드리스 실행이 권한 승인 프롬프트에 조용히 멈춘다 —
    답할 UI 가 없으니 사용자는 그 사실조차 알 수 없다.

    `Popen` 에 `cwd` 를 아예 안 주면 자식은 **호출자(이 테스트 프로세스)의**
    cwd 를 물려받는다. 이 스위트를 늘 저장소 루트에서 돌리면 그 물려받은
    값도 우연히 `project_root()` 와 같아져, `cwd=` 인자를 빠뜨리는 뮤테이션이
    감지되지 않는다(실측: 처음에는 그랬다). 그래서 pytest 프로세스 자체의
    cwd 를 임시 디렉터리로 옮겨 놓고 검사한다 — 이러면 "명시적으로
    `project_root()` 를 준다" 와 "그냥 물려받는다" 가 서로 다른 값을 내어
    구분된다.
    """
    monkeypatch.chdir(tmp_path)
    record = tmp_path / "record.json"
    evs = list(stream_turn(cfg, "x", None, claude=_fake_claude(
        tmp_path, OK_LINES, record_to=record)))
    assert evs
    recorded_cwd = json.loads(record.read_text(encoding="utf-8"))["cwd"]
    assert Path(recorded_cwd).resolve() == project_root().resolve()
    assert Path(recorded_cwd).resolve() != tmp_path.resolve()


def test_session_id_flag_matches_session_id_for(cfg, tmp_path):
    """`--session-id` 는 `session_id_for` 가 그 컨텐츠에 돌려주는 값이어야 한다.

    다른(예: 매번 새로) id 를 넘기면 세션이 매번 새로 열려, 영속시킨 의미가
    없어진다.
    """
    expected = session_id_for(cfg, "파티편성")
    record = tmp_path / "record.json"
    evs = list(stream_turn(cfg, "x", "파티편성", claude=_fake_claude(
        tmp_path, OK_LINES, record_to=record)))
    assert evs
    argv = json.loads(record.read_text(encoding="utf-8"))["argv"]
    assert argv[argv.index("--session-id") + 1] == expected


def test_stream_json_format_flags_are_passed_both_ways(cfg, tmp_path):
    """`--output-format`·`--input-format` 이 둘 다 `stream-json` 이어야 한다.

    입력 형식이 다르면(예: `text`) `claude` 가 우리가 stdin 으로 보내는
    stream-json 페이로드를 못 읽고, 출력 형식이 다르면 우리가 이 모듈에서
    파싱하는 줄 단위 JSON 을 못 받는다.
    """
    record = tmp_path / "record.json"
    evs = list(stream_turn(cfg, "x", None, claude=_fake_claude(
        tmp_path, OK_LINES, record_to=record)))
    assert evs
    argv = json.loads(record.read_text(encoding="utf-8"))["argv"]
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--input-format") + 1] == "stream-json"


# --- stderr 는 열어만 놓고 아무도 안 읽으면 안 된다 -------------------------
#
# 리뷰 지적: `stderr=PIPE` 로 열어 놓고 아무도 읽지 않으면 (1) 진짜 실패
# 원인이 버려지고("응답 없이 종료" 라는 문구만으로는 충돌과 멈춤을 구분할 수
# 없다), (2) 자식이 stderr 버퍼를 채울 만큼 많이 쓰면 자식이 그 쓰기에서
# 영원히 막혀 데드락이 난다.


def test_stderr_reason_reaches_the_error_event_on_a_crash(cfg, tmp_path):
    """done/error 없이 죽은 자식의 stderr 내용이 오류 메시지에 실려야 한다.

    원인 없이 "응답 없이 종료" 라고만 하면, 진짜 충돌(이 테스트)과 그냥
    느린 것을 사용자가 구분할 수 없다.
    """
    evs = list(stream_turn(cfg, "x", None, claude=_fake_claude(
        tmp_path, [], stderr_text="FATAL: something broke badly", exit_code=1)))
    errs = [e for e in evs if e.kind == "error"]
    assert len(errs) == 1
    assert "FATAL: something broke badly" in errs[0].data["message"]

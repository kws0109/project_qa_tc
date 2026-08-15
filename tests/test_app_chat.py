"""`claude` 자식 프로세스. 진짜 API 는 부르지 않는다."""

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from qatc.app import chat as chat_module
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


# --- `--verbose` — 라이브 스모크 테스트가 잡은 결함 -------------------------
#
# 실측: 473개 테스트가 전부 통과한 채로 앱을 실제로 띄워 메시지 하나를
# 보냈더니 3초 만에 죽었다. 진짜 `claude` 실행 파일은 `--print`(`-p`) 와
# `--output-format=stream-json` 을 같이 받으면 `--verbose` 없이는 즉시
# 거부한다 ("Error: When using --print, --output-format=stream-json requires
# --verbose"). 가짜 `claude` 는 자기 인자를 전혀 검증하지 않으므로 이 조합은
# 기존 테스트 473개 중 어느 것도 잡을 수 없었다 — 그래서 여기 하나를 붙여
# `--output-format stream-json` 이 있으면 `--verbose` 도 반드시 같이 있게
# 고정한다.


def test_verbose_flag_accompanies_stream_json_output(cfg, tmp_path):
    """`--output-format` 이 `stream-json` 이면 `--verbose` 도 반드시 같이 있어야 한다.

    이 하나가 빠지면 실제 `claude` 는 헤드리스 턴을 시작조차 못 하고
    거부하는데, 가짜 `claude` 는 그 거부를 흉내 내지 않으므로 다른 어떤
    테스트도 이 결함을 못 잡는다.
    """
    record = tmp_path / "record.json"
    evs = list(stream_turn(cfg, "x", None, claude=_fake_claude(
        tmp_path, OK_LINES, record_to=record)))
    assert evs
    argv = json.loads(record.read_text(encoding="utf-8"))["argv"]
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv


# --- 실제 스트림의 시작 모양 — 가짜가 한 번도 낸 적 없는 프레임들 -----------
#
# 라이브 스모크 테스트로 실측한 진짜 `claude --output-format stream-json
# --verbose` 출력은 `assistant`/`result` 가 아니라 `system` 타입 프레임
# (`hook_started` 등) 으로 시작한다. `_events_from` 은 모르는 `type` 은 그냥
# 건너뛰므로(빈 제너레이터를 돌려주므로) 파싱 루프는 이미 이걸 견딘다 —
# 이 테스트는 그 관대함이 실제로 성립함을 고정하고, 가짜의 스트림 모양을
# 실측에 맞춘다.

SYSTEM_OPENING_LINES = [
    json.dumps({"type": "system", "subtype": "hook_started",
                "hook_id": "h1", "hook_name": "SessionStart:startup"},
               ensure_ascii=False),
    json.dumps({"type": "system", "subtype": "hook_finished",
                "hook_id": "h1", "hook_name": "SessionStart:startup"},
               ensure_ascii=False),
    json.dumps({"type": "system", "subtype": "init", "session_id": "abc",
                "model": "claude-opus-4", "tools": ["Bash"]},
               ensure_ascii=False),
]

REALISTIC_OPENING_LINES = SYSTEM_OPENING_LINES + OK_LINES


def test_unknown_system_frames_before_the_turn_are_ignored(cfg, tmp_path):
    """`system`/`hook_started` 류로 시작하는 실제 스트림 모양을 견뎌야 한다.

    이 프레임들이 섞여도 뒤이은 delta·tool·done 은 평소와 똑같이 나와야
    한다 — 말줄임 없이, 순서도 그대로.
    """
    evs = list(stream_turn(cfg, "x", None, claude=_fake_claude(
        tmp_path, REALISTIC_OPENING_LINES)))
    assert [e.kind for e in evs] == ["delta", "tool", "done"]
    assert "".join(e.data["text"] for e in evs if e.kind == "delta") == "안녕"


# --- `--session-id` 는 create-only — 첫 턴은 생성, 다음 턴부터는 재개 ------
#
# 라이브 스모크 테스트가 잡은 결함: `session_id_for` 가 컨텐츠당 uuid 하나를
# 영속시키고, `stream_turn` 은 매 턴 그 값을 그대로 `--session-id` 로
# 넘겼다. 진짜 `claude` 는 `--session-id` 를 생성 전용으로 다뤄서, 이미 그
# id 로 연 적이 있으면 다음 턴에서 "Error: Session ID ... is already in
# use." 로 거부한다(실측) — 인터뷰가 여러 턴 이어져야 한다는 이 앱의 전제를
# 정면으로 깬다. 가짜 `claude` 는 이 create-vs-resume 구분을 전혀 흉내 내지
# 않으므로 기존 스위트는 이 결함을 못 잡았다.


def test_first_turn_for_a_content_uses_session_id_not_resume(cfg, tmp_path):
    """컨텐츠의 첫 턴은 `--session-id` 로 세션을 만들어야 한다(`--resume` 아님)."""
    record = tmp_path / "record.json"
    evs = list(stream_turn(cfg, "x", "파티편성", claude=_fake_claude(
        tmp_path, OK_LINES, record_to=record)))
    assert evs
    argv = json.loads(record.read_text(encoding="utf-8"))["argv"]
    assert "--session-id" in argv
    assert "--resume" not in argv


def test_second_turn_for_the_same_content_resumes_with_the_same_id(cfg, tmp_path):
    """같은 컨텐츠의 둘째 턴부터는 `--resume` 을 **같은** id 로 써야 한다.

    라이브 스모크 테스트가 실제로 재현한 실패 모양: 매 턴 `--session-id` 를
    다시 주면 진짜 `claude` 는 "이미 쓰이고 있다" 며 거부한다 — 두 번째
    턴부터 대화가 절대 못 이어진다는 뜻이다.
    """
    record1 = tmp_path / "record1.json"
    list(stream_turn(cfg, "x", "파티편성", claude=_fake_claude(
        tmp_path, OK_LINES, record_to=record1)))
    argv1 = json.loads(record1.read_text(encoding="utf-8"))["argv"]
    created_id = argv1[argv1.index("--session-id") + 1]

    record2 = tmp_path / "record2.json"
    evs = list(stream_turn(cfg, "y", "파티편성", claude=_fake_claude(
        tmp_path, OK_LINES, record_to=record2)))
    assert evs
    argv2 = json.loads(record2.read_text(encoding="utf-8"))["argv"]
    assert "--session-id" not in argv2
    assert argv2[argv2.index("--resume") + 1] == created_id


def test_a_known_content_still_resumes_after_a_restart(cfg, tmp_path):
    """앱을 껐다 켜도(새 객체, 같은 `sessions.json`) 시작된 대화는 재개돼야 한다.

    "생성됨" 여부가 메모리가 아니라 파일에 저장되는지 확인한다 — 메모리에만
    있었다면 재시작한 프로세스는 이 컨텐츠를 처음 보는 줄 알고 다시
    `--session-id` 를 시도해 똑같은 "이미 쓰이고 있다" 오류로 죽는다.
    """
    record1 = tmp_path / "record1.json"
    list(stream_turn(cfg, "x", "파티편성", claude=_fake_claude(
        tmp_path, OK_LINES, record_to=record1)))
    created_id = session_id_for(cfg, "파티편성")

    # 새 프로세스를 흉내 낸다 — 같은 경로를 가리키는 새 `AppConfig` 객체.
    # `chat.py` 는 세션 상태를 메모리에 캐시하지 않고 매번 파일을 다시
    # 읽으므로, 이 객체가 "이전 실행"의 어떤 상태도 물려받지 않는다는 점이
    # 검사의 핵심이다.
    restarted_cfg = AppConfig(knowledge_root=cfg.knowledge_root, profiles_dir=cfg.profiles_dir)
    record2 = tmp_path / "record2.json"
    evs = list(stream_turn(restarted_cfg, "y", "파티편성", claude=_fake_claude(
        tmp_path, OK_LINES, record_to=record2)))
    assert evs
    argv2 = json.loads(record2.read_text(encoding="utf-8"))["argv"]
    assert "--session-id" not in argv2
    assert argv2[argv2.index("--resume") + 1] == created_id


def test_no_content_chosen_yet_follows_the_same_create_then_resume_path(cfg, tmp_path):
    """`content=None`("아직 컨텐츠 안 고름") 의 첫 턴도 일반 컨텐츠와 같은 규칙을 따른다.

    내부적으로 다른 키(`__default__`)를 쓸 뿐, 생성→재개 판정 자체는 별도
    분기 없이 같은 코드를 탄다는 것을 고정한다.
    """
    record1 = tmp_path / "record1.json"
    list(stream_turn(cfg, "x", None, claude=_fake_claude(
        tmp_path, OK_LINES, record_to=record1)))
    argv1 = json.loads(record1.read_text(encoding="utf-8"))["argv"]
    assert "--session-id" in argv1
    assert "--resume" not in argv1
    created_id = argv1[argv1.index("--session-id") + 1]

    record2 = tmp_path / "record2.json"
    list(stream_turn(cfg, "y", None, claude=_fake_claude(
        tmp_path, OK_LINES, record_to=record2)))
    argv2 = json.loads(record2.read_text(encoding="utf-8"))["argv"]
    assert "--session-id" not in argv2
    assert argv2[argv2.index("--resume") + 1] == created_id


# --- `sessions.json` 이 있다고 진짜 `claude` 도 그 세션을 안다는 보장은 없다 -
#
# 재검토(진짜 `claude` 로 확인): 알 수 없는 `--resume` 대상은 프레임 없이
# 죽지 않는다 — **정상적인 JSON `result` 프레임**으로 `errors: ["No
# conversation found with session ID: ..."]` 를 담아 온다. (`--session-id`
# 를 두 번 보내는 "이미 쓰이고 있다" 오류만 프레임 없이 stderr 로 죽는데, 이
# 수정 자체가 정상 흐름에서 그 경로를 없앴다.) 그래서 복구는 프레임 유무가
# 아니라 그 문자열이 오류 메시지 안에 있는지로 좁게 판단한다.


def _unknown_session_error_line(session_id: str) -> str:
    return json.dumps({
        "type": "result", "subtype": "error_during_execution", "is_error": True,
        "num_turns": 0, "total_cost_usd": 0,
        "errors": [f"No conversation found with session ID: {session_id}"],
    }, ensure_ascii=False)


def _fake_claude_session_aware(tmp_path, *, ok_lines, error_line, resume_record, create_record):
    """`--resume` 이면 주어진 오류 프레임을 내고, `--session-id` 면 성공(`ok_lines`)한다.

    어느 쪽으로 불렸는지에 따라 argv 를 다른 파일에 기록해, 복구가 실제로
    새 세션으로(즉 `--session-id` 로) 재시도했는지 구분해서 검사할 수 있게
    한다. `error_line` 을 파라미터로 받아, 실측 문구가 있는 프레임뿐 아니라
    문구가 다른(구조적 특징만 같은) 프레임도 같은 헬퍼로 검사할 수 있다.
    """
    script = tmp_path / "fake_claude_session_aware.py"
    script.write_text(textwrap.dedent(f"""
        import json
        import os
        import sys

        argv = sys.argv
        resumed = "--resume" in argv
        record_path = {str(resume_record)!r} if resumed else {str(create_record)!r}
        with open(record_path, "w", encoding="utf-8") as f:
            json.dump({{"argv": argv, "cwd": os.getcwd()}}, f, ensure_ascii=False)

        if resumed:
            sys.stdout.write({error_line!r} + "\\n")
            sys.stdout.flush()
            sys.exit(0)

        for line in {ok_lines!r}:
            sys.stdout.write(line + "\\n")
            sys.stdout.flush()
        sys.exit(0)
    """), encoding="utf-8")
    return [sys.executable, str(script)]


def test_unknown_resume_target_recovers_with_a_fresh_session(cfg, tmp_path):
    """`--resume` 대상을 못 찾는다는 정상 JSON 오류 프레임이 오면, 새 세션으로
    한 번 다시 시도해 턴을 살린다.

    그렇지 않으면 이 컨텐츠는 사용자가 다시는 대화할 수 없는 상태로 영영
    막힌다 — 세션 하나 잃는 것보다 훨씬 나쁘다.
    """
    list(stream_turn(cfg, "x", "파티편성", claude=_fake_claude(
        tmp_path, OK_LINES, record_to=tmp_path / "record0.json")))
    stale_id = session_id_for(cfg, "파티편성")

    resume_record = tmp_path / "resume_record.json"
    create_record = tmp_path / "create_record.json"
    claude_cmd = _fake_claude_session_aware(
        tmp_path, ok_lines=OK_LINES, error_line=_unknown_session_error_line(stale_id),
        resume_record=resume_record, create_record=create_record)

    evs = list(stream_turn(cfg, "y", "파티편성", claude=claude_cmd))

    # 사용자에게 실패한 재개 시도는 **오류로** 보이지 않는다. 대신 복구가
    # 일어났다는 `notice` 하나가 앞서고, 그 뒤로 재시도의 결과가 정상적인
    # 턴 모양(OK_LINES)으로 도착한다.
    assert [e.kind for e in evs] == ["notice", "delta", "tool", "done"]

    resume_argv = json.loads(resume_record.read_text(encoding="utf-8"))["argv"]
    assert resume_argv[resume_argv.index("--resume") + 1] == stale_id

    create_argv = json.loads(create_record.read_text(encoding="utf-8"))["argv"]
    new_id = create_argv[create_argv.index("--session-id") + 1]
    assert new_id != stale_id

    # 다음 턴은 이 새 id 를 재개해야 한다 — 낡은 id 로 되돌아가면 다시 막힌다.
    assert session_id_for(cfg, "파티편성") == new_id


# --- 3차 재검토: 분류는 문구가 아니라 프레임에 — 그리고 못 박힌 곳 4군데 --
#
# 매 턴 실측 문구(영어 원문)를 렌더링된 한국어 메시지 문자열 안에서 다시
# 찾는 방식이었을 때, 그 문구가 바뀌거나(claude 쪽 업데이트) 메시지가
# 잘리면(stderr 꼬리 자르기) 복구가 조용히 멈춘다 — 아무 것도 실패하지
# 않으면서 그 컨텐츠는 영원히 낡은 id 로 --resume 을 시도한다. 이제
# `_result_events` 가 프레임을 파싱하는 자리에서 `data["kind"] =
# "unknown_session"` 이라는 코드로 분류를 끝내고, 그 코드가 안 맞을
# 때(문구 드리프트)를 대비한 구조적 폴백(턴 진행 0·비용 0·401 아님)도
# 같이 둔다.


def test_structural_fallback_recovers_even_when_the_wording_has_drifted(cfg, tmp_path):
    """실측 문구가 바뀌어도, 구조적 특징(턴 진행 0·비용 0·401 아님)만으로 복구해야 한다.

    문구 하나에만 기대면 그 문구가 바뀌는 날 복구가 통째로 멈추고, 매 턴이
    이미 죽은 id 로 `--resume` 을 계속 시도해 컨텐츠가 영영 막힌다. 폴백이
    있으면 최악의 경우가 "비용 0인 턴을 하나 헛되이 더 시도한다" 로 끝난다.
    """
    list(stream_turn(cfg, "x", "파티편성", claude=_fake_claude(
        tmp_path, OK_LINES, record_to=tmp_path / "record0.json")))
    stale_id = session_id_for(cfg, "파티편성")

    drifted_line = json.dumps({
        "type": "result", "subtype": "error_during_execution", "is_error": True,
        "num_turns": 0, "total_cost_usd": 0,
        "errors": ["완전히 다른 문구로 바뀐 미래의 claude 오류 메시지"],
    }, ensure_ascii=False)

    resume_record = tmp_path / "resume_record.json"
    create_record = tmp_path / "create_record.json"
    claude_cmd = _fake_claude_session_aware(
        tmp_path, ok_lines=OK_LINES, error_line=drifted_line,
        resume_record=resume_record, create_record=create_record)

    evs = list(stream_turn(cfg, "y", "파티편성", claude=claude_cmd))
    assert [e.kind for e in evs] == ["notice", "delta", "tool", "done"]

    create_argv = json.loads(create_record.read_text(encoding="utf-8"))["argv"]
    new_id = create_argv[create_argv.index("--session-id") + 1]
    assert new_id != stale_id
    assert session_id_for(cfg, "파티편성") == new_id


DONE_ONLY_LINES = [
    json.dumps({"type": "result", "subtype": "success", "is_error": False}, ensure_ascii=False),
]


def test_retry_success_with_no_assistant_text_still_persists_the_new_session(cfg, tmp_path):
    """재시도가 델타/도구 없이 곧바로 `done` 으로 끝나도 새 id 를 저장해야 한다.

    저장 안 하면 다음 턴이 여전히 낡은 id 로 `--resume` 을 시도해 복구가
    매번 다시 발동하고, 매 턴 자식 프로세스를 두 번씩 태우면서 방금 막
    만든 세션을 고아로 만든다(재검토 지적: `("delta","tool")` 로만 좁히면
    이 경로가 절대 저장되지 않는다).
    """
    list(stream_turn(cfg, "x", "파티편성", claude=_fake_claude(
        tmp_path, OK_LINES, record_to=tmp_path / "record0.json")))
    stale_id = session_id_for(cfg, "파티편성")

    resume_record = tmp_path / "resume_record.json"
    create_record = tmp_path / "create_record.json"
    claude_cmd = _fake_claude_session_aware(
        tmp_path, ok_lines=DONE_ONLY_LINES, error_line=_unknown_session_error_line(stale_id),
        resume_record=resume_record, create_record=create_record)

    evs = list(stream_turn(cfg, "y", "파티편성", claude=claude_cmd))
    assert [e.kind for e in evs] == ["notice", "done"]

    create_argv = json.loads(create_record.read_text(encoding="utf-8"))["argv"]
    new_id = create_argv[create_argv.index("--session-id") + 1]
    assert session_id_for(cfg, "파티편성") == new_id


TOOL_ONLY_THEN_DONE_LINES = [
    json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Skill",
         "input": {"skill": "interview", "args": "파티편성"}}]}}, ensure_ascii=False),
    json.dumps({"type": "result", "subtype": "success", "is_error": False}, ensure_ascii=False),
]


def test_retry_persists_the_new_session_even_when_it_opens_with_a_tool_call(cfg, tmp_path):
    """재시도의 첫 이벤트가 델타가 아니라 도구 호출이어도 새 id 를 저장해야 한다.

    실제 라이브 증명에서 정확히 이 모양이 나왔다 — 복구된 턴이 델타 없이
    `Skill` 도구 호출로 시작했다. 저장 조건을 `("delta",)` 로만 좁히면
    이 경로(재검토 지적: `tool` 갈래가 테스트된 적이 없다)가 저장을
    건너뛴다.
    """
    list(stream_turn(cfg, "x", "파티편성", claude=_fake_claude(
        tmp_path, OK_LINES, record_to=tmp_path / "record0.json")))
    stale_id = session_id_for(cfg, "파티편성")

    resume_record = tmp_path / "resume_record.json"
    create_record = tmp_path / "create_record.json"
    claude_cmd = _fake_claude_session_aware(
        tmp_path, ok_lines=TOOL_ONLY_THEN_DONE_LINES,
        error_line=_unknown_session_error_line(stale_id),
        resume_record=resume_record, create_record=create_record)

    evs = list(stream_turn(cfg, "y", "파티편성", claude=claude_cmd))
    assert [e.kind for e in evs] == ["notice", "tool", "done"]

    create_argv = json.loads(create_record.read_text(encoding="utf-8"))["argv"]
    new_id = create_argv[create_argv.index("--session-id") + 1]
    assert session_id_for(cfg, "파티편성") == new_id


def test_first_turn_never_recovers_even_if_the_frame_looks_like_unknown_session(cfg, tmp_path):
    """생성 턴(첫 턴)은 재개가 아니므로, 프레임이 "세션 없음" 모양이어도 재시도하면 안 된다.

    `_result_events`/`_looks_like_unknown_session` 자체는 `resume` 여부를
    모른다(프레임만 본다) — "재개 턴에서만" 이라는 제약은 `stream_turn` 의
    `ref.resume` 조건이 혼자 지킨다. 그 조건을 빼면 생성 턴의 어떤 오류든
    "혹시 세션 문제인가" 로 오분류돼 원인과 무관하게 재시도로 새고, 실제
    턴 하나를 헛되이 더 태운다.

    재시도가 일어나면 원래 시도와 같은 모양(같은 오류)의 자식 프로세스가
    한 번 더 뜬다 — argv 파일은 두 시도가 같은 경로(생성 모드)로 겹쳐서
    덮어써지므로 구분이 안 된다. 그래서 호출 횟수 자체를 로그 파일에
    남겨 직접 센다.
    """
    call_log = tmp_path / "calls.log"
    error_line = _unknown_session_error_line("아무-id")
    script = tmp_path / "fake_claude_counts_calls.py"
    script.write_text(textwrap.dedent(f"""
        import sys
        with open({str(call_log)!r}, "a", encoding="utf-8") as f:
            f.write("call\\n")
        sys.stdout.write({error_line!r} + "\\n")
        sys.stdout.flush()
        sys.exit(0)
    """), encoding="utf-8")
    claude_cmd = [sys.executable, str(script)]

    evs = list(stream_turn(cfg, "x", "파티편성", claude=claude_cmd))

    assert [e.kind for e in evs] == ["error"]
    assert call_log.read_text(encoding="utf-8").count("call") == 1


MULTI_ERROR_LINES = [
    json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True,
                "total_cost_usd": 0,
                "errors": ["첫 번째 진짜 원인", "두 번째 무관한 꼬리 메모"]},
               ensure_ascii=False),
]


def test_first_error_text_picks_the_first_element_not_the_last(cfg, tmp_path):
    """`errors` 배열이 여러 개면 **첫** 원소를 써야 한다 — 마지막이 아니다.

    지금까지 쓴 가짜는 전부 원소 하나짜리 배열이라 `[0]` 과 `[-1]` 이
    우연히 같은 값을 냈다(재검토 지적) — 이 테스트만 둘을 갈라놓는다.
    """
    evs = list(stream_turn(cfg, "x", None, claude=_fake_claude(
        tmp_path, MULTI_ERROR_LINES)))
    errs = [e for e in evs if e.kind == "error"]
    assert len(errs) == 1
    assert "첫 번째 진짜 원인" in errs[0].data["message"]
    assert "두 번째 무관한 꼬리 메모" not in errs[0].data["message"]


def test_stderr_is_not_closed_until_the_drain_thread_has_finished(cfg, tmp_path, monkeypatch):
    """`proc.stderr` 는 드레인 스레드가 다 읽은 **뒤에만** 닫혀야 한다.

    `_drain_stderr` 를, 끝나기 직전에 `proc.stderr.closed` 를 확인하고 잠깐
    쉬었다 끝나는 가짜로 바꿔 끼운다. 순서가 맞으면(join 뒤에 닫으면) 메인
    스레드는 이 가짜가 끝날 때까지 `join()` 에서 블로킹돼 있으므로, 가짜가
    확인하는 시점엔 항상 아직 안 닫혀 있어야 한다. 순서가 뒤바뀌면(닫고
    나서 join) 메인 스레드가 먼저 닫아 버릴 수 있다.
    """
    closed_before_finish = []

    def spy_drain_stderr(proc, sink):
        time.sleep(0.1)
        closed_before_finish.append(proc.stderr.closed if proc.stderr else None)

    monkeypatch.setattr(chat_module, "_drain_stderr", spy_drain_stderr)

    list(stream_turn(cfg, "x", None, claude=_fake_claude(
        tmp_path, [], stderr_text="some stderr", exit_code=1)))
    assert closed_before_finish == [False]


def _fake_claude_always_unknown_session(tmp_path, mentioned_id):
    """`--resume`·`--session-id` 상관없이 항상 "세션 없음" 오류로 끝나는 가짜.

    재시도조차 실패하는 상황(원래 시도와 재시도 둘 다 실패)을 흉내내, 그
    실패가 `sessions.json` 을 훼손하지 않는지 검사하는 데 쓴다.
    """
    script = tmp_path / "fake_claude_always_unknown.py"
    error_line = _unknown_session_error_line(mentioned_id)
    script.write_text(textwrap.dedent(f"""
        import sys
        sys.stdout.write({error_line!r} + "\\n")
        sys.stdout.flush()
        sys.exit(0)
    """), encoding="utf-8")
    return [sys.executable, str(script)]


def test_a_second_consecutive_failure_does_not_discard_the_original_session_id(cfg, tmp_path):
    """재시도조차 델타/도구 없이 실패하면, `sessions.json` 은 원래 id 를 그대로 지켜야 한다.

    안 지키면 실패할 때마다 uuid 를 하나씩 태워버려, 몇 번만 연속 실패해도
    원래 살아있었을 세션 id 조차 기록에서 사라진다 — 재검토가 실측한 연쇄
    붕괴("세 번 연속 실패가 uuid 세 개를 버렸다").
    """
    list(stream_turn(cfg, "x", "파티편성", claude=_fake_claude(
        tmp_path, OK_LINES, record_to=tmp_path / "record0.json")))
    stale_id = session_id_for(cfg, "파티편성")

    always_unknown = _fake_claude_always_unknown_session(tmp_path, stale_id)
    evs = list(stream_turn(cfg, "y", "파티편성", claude=always_unknown))

    # 복구를 **시도했다**는 사실은 알린다(`notice`). 그 재시도까지 실패했으니
    # 그 실패는 `error` 로 이어진다 — 알림이 실패를 덮지 않는다.
    assert [e.kind for e in evs] == ["notice", "error"]
    # sessions.json 은 원래 id 그대로다 — 재시도가 실패한 새 id 로 덮이지
    # 않았다.
    assert session_id_for(cfg, "파티편성") == stale_id


# --- 뮤테이션: 좁은 신호를 "아무 오류" 로 넓히면 안 된다 -------------------


def test_a_401_on_a_resume_turn_does_not_trigger_recovery(cfg, tmp_path):
    """세션을 못 찾는다는 신호가 없는 오류(예: 401)는 재시도로 이어지면 안 된다.

    재시도로 이어지면 원인과 무관하게 실제 턴을 한 번 더 태워 비용이 두
    배가 된다 — 인증 문제와 세션 문제는 원인이 다르다.
    """
    list(stream_turn(cfg, "x", "파티편성", claude=_fake_claude(
        tmp_path, OK_LINES, record_to=tmp_path / "record0.json")))

    record = tmp_path / "resume_record.json"
    evs = list(stream_turn(cfg, "y", "파티편성", claude=_fake_claude(
        tmp_path, AUTH_LINES, record_to=record)))

    assert [e.kind for e in evs] == ["error"]
    assert evs[0].data["kind"] == "auth"
    # 재시도가 없었다 — 기록된 argv 가 여전히 --resume 이다. 재시도였다면
    # 이 같은 경로(record 파일)가 --session-id 로 다시 기록되어 덮였을
    # 것이다.
    argv = json.loads(record.read_text(encoding="utf-8"))["argv"]
    assert "--resume" in argv
    assert "--session-id" not in argv


MID_TURN_UNKNOWN_SESSION_LINES = [
    json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "부분 응답"}]}}, ensure_ascii=False),
    json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True,
                "total_cost_usd": 0,
                "errors": ["No conversation found with session ID: deadbeef"]},
               ensure_ascii=False),
]


def test_emitted_real_content_blocks_recovery_even_with_the_unknown_session_marker(cfg, tmp_path):
    """이미 델타가 나온 뒤에 그 신호가 떠도 재시도하지 않는다.

    재시도하면 이미 보여준 응답과 겹치는 턴을 통째로 다시 태워 비용이 두
    배가 된다. 이 프레임은 `result` 없이 `errors` 배열만 담고 있으므로,
    사용자에게 보이는 메시지가 "원인을 알 수 없습니다" 로 뭉개지지 않는지도
    함께 확인한다(§ `result` 가 없는 오류 프레임도 원인을 보여줘야 한다).
    """
    list(stream_turn(cfg, "x", "파티편성", claude=_fake_claude(
        tmp_path, OK_LINES, record_to=tmp_path / "record0.json")))

    record = tmp_path / "resume_record.json"
    evs = list(stream_turn(cfg, "y", "파티편성", claude=_fake_claude(
        tmp_path, MID_TURN_UNKNOWN_SESSION_LINES, record_to=record)))

    assert [e.kind for e in evs] == ["delta", "error"]
    assert "No conversation found with session ID" in evs[-1].data["message"]
    assert "원인을 알 수 없습니다" not in evs[-1].data["message"]

    argv = json.loads(record.read_text(encoding="utf-8"))["argv"]
    assert "--resume" in argv
    assert "--session-id" not in argv


# --- 뮤테이션: 옛 형식(문자열 하나) sessions.json 도 재개로 해석해야 한다 ---


def test_legacy_string_session_entry_still_resumes(cfg, tmp_path):
    """`sessions.json` 의 옛 형식(문자열 하나만)도 재개로 해석해야 한다.

    새 `{"id":..., "created":...}` 형식이 생기기 전에 만들어진 파일이 있을
    수 있다 — 그 id 를 `created=False` 로 잘못 해석해 다시 `--session-id`
    로 보내면(이미 존재하는 세션에) "이미 쓰이고 있다" 오류가 똑같이
    재현된다.
    """
    legacy_id = "11111111-1111-1111-1111-111111111111"
    sessions_path = cfg.knowledge_path / "sessions.json"
    sessions_path.write_text(
        json.dumps({"파티편성": legacy_id}, ensure_ascii=False), encoding="utf-8")

    record = tmp_path / "record.json"
    evs = list(stream_turn(cfg, "x", "파티편성", claude=_fake_claude(
        tmp_path, OK_LINES, record_to=record)))
    assert evs
    argv = json.loads(record.read_text(encoding="utf-8"))["argv"]
    assert "--session-id" not in argv
    assert argv[argv.index("--resume") + 1] == legacy_id


# --- `result` 가 없는 오류 프레임도 원인을 보여줘야 한다 --------------------
#
# 재검토: 세션을 못 찾는 오류가 정확히 이 모양(`result` 없음, `errors` 배열
# 만 있음)으로 온다. `obj["result"]` 만 보면 "원인을 알 수 없습니다" 로
# 뭉개져 사용자가 진짜 원인을 못 본다.

ERRORS_ARRAY_LINES = [
    json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True,
                "total_cost_usd": 0, "errors": ["some other failure detail"]},
               ensure_ascii=False),
]


def test_error_message_falls_back_to_the_errors_array_when_result_is_absent(cfg, tmp_path):
    """`result` 키가 없는 오류 프레임(`errors` 배열만 있음)도 원인을 그대로 보여줘야 한다.

    복구가 안 먹혔을 때(위 401 테스트, 연속 실패 테스트) 이 메시지가
    사용자가 보는 유일한 설명이라 특히 중요하다.
    """
    evs = list(stream_turn(cfg, "x", None, claude=_fake_claude(
        tmp_path, ERRORS_ARRAY_LINES)))
    errs = [e for e in evs if e.kind == "error"]
    assert len(errs) == 1
    assert "some other failure detail" in errs[0].data["message"]
    assert "원인을 알 수 없습니다" not in errs[0].data["message"]


# --- 소비자가 스트림을 도중에 끊어도 죽지 않고, 안 죽은 자식은 정리된다 -----
#
# 재검토가 실측: `gen.close()` 를(Werkzeug 가 SSE 연결이 끊길 때 하는 것과
# 같은 모양으로) 델타 두 개 받은 뒤 부르면 "RuntimeError: generator ignored
# GeneratorExit" 로 죽었다 — `_attempt_turn` 의 `finally` 가 `settled=False`
# 인 채로 마지막 오류를 다시 `yield` 하려 했기 때문이다. 게다가 그때의
# `_close` 는 시간제한 없이 `proc.wait()` 를 불러, 자식이 살아있으면 12초가
# 지나도 안 끝났다.


def _fake_claude_that_lingers(tmp_path, lines):
    """몇 줄을 쓰고 나서 곧바로 끝나지 않고 오래 붙어 있는 가짜.

    소비자가 스트림을 일찍 끊었을 때 `_close` 가 실제로 강제 종료하는지
    검사하는 데 쓴다 — 빨리 끝나 버리면 "죽지 않은 자식을 정리하는지" 를
    검사할 수 없다.
    """
    script = tmp_path / "fake_claude_lingers.py"
    script.write_text(textwrap.dedent(f"""
        import sys
        import time

        for line in {lines!r}:
            sys.stdout.write(line + "\\n")
            sys.stdout.flush()
        time.sleep(30)
    """), encoding="utf-8")
    return [sys.executable, str(script)]


def test_closing_the_generator_early_does_not_raise_and_kills_the_child(cfg, tmp_path, monkeypatch):
    """소비자가 스트림을 일찍 끊어도(Werkzeug 가 SSE 연결 끊김에 하는 것과
    같은 모양) `RuntimeError` 없이 정리되고, 안 죽은 자식은 빠르게 강제
    종료된다.

    `_attempt_turn` 을 직접 닫는다(`stream_turn` 을 통해 간접적으로 닫지
    않는다) — `stream_turn` 의 `for` 루프가 대신 닫아 주면, 안에서 난
    `RuntimeError` 가 이 테스트 프레임까지 동기적으로 전파되지 않고
    `sys.unraisablehook` 경고로만 새 나가 `assert`/`pytest.raises` 로 못
    잡을 수 있다(실측: 뮤테이션 검증 중 확인 — 테스트가 "통과" 로 잘못
    보고됐다). 고치려는 `except GeneratorExit` 는 `_attempt_turn` 안에
    있으므로, 거기를 직접 닫아야 뮤테이션이 확실히 걸린다.
    """
    monkeypatch.setattr(chat_module, "_CLOSE_WAIT_TIMEOUT", 0.2)

    lines = [json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "hi"}]}}, ensure_ascii=False)]
    gen = chat_module._attempt_turn(
        cfg, "x", "any-session-id", False, _fake_claude_that_lingers(tmp_path, lines))
    ev = next(gen)
    assert ev.kind == "delta"

    start = time.monotonic()
    gen.close()          # RuntimeError 가 나면 여기서 실패한다
    elapsed = time.monotonic() - start
    # 몽키패치한 0.2 초 남짓이어야 한다 — 30 초 sleep 이 끝나길 기다렸다면
    # 강제 종료가 안 먹힌 것이다.
    assert elapsed < 5.0


@pytest.mark.filterwarnings("error")
def test_stream_turn_can_also_be_closed_early_without_an_unraisable_warning(cfg, tmp_path, monkeypatch):
    """실제 소비 경로(`stream_turn`, `_attempt_turn` 을 간접적으로 닫는 경로)도
    조용히 끊긴다 — 경고조차 남기지 않는다.

    `filterwarnings("error")` 로 이 테스트 안의 어떤 경고도 실패로 바꾼다 —
    안 그러면 `_attempt_turn` 을 간접적으로 닫을 때 나는
    `RuntimeError: generator ignored GeneratorExit` 가 `sys.unraisablehook`
    경고로만 새 나가 이 테스트가 조용히 "통과" 로 보고된다(위 직접 테스트의
    docstring에 적은 바로 그 함정).
    """
    monkeypatch.setattr(chat_module, "_CLOSE_WAIT_TIMEOUT", 0.2)

    lines = [json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "hi"}]}}, ensure_ascii=False)]
    gen = stream_turn(cfg, "x", None, claude=_fake_claude_that_lingers(tmp_path, lines))
    ev = next(gen)
    assert ev.kind == "delta"
    gen.close()


class _NeverExitingProc:
    """`.wait()` 가 `kill()` 전까지 항상 timeout 나는 가짜 프로세스.

    `_close` 가 실제로 `kill()` 을 부르는지, 진짜 자식 프로세스 없이 빠르고
    결정적으로 검사한다.
    """

    def __init__(self):
        self.stdout = None
        self.killed = False

    def wait(self, timeout=None):
        if not self.killed:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0)
        return 0

    def kill(self):
        self.killed = True


def test_close_kills_a_child_that_never_exits(monkeypatch):
    monkeypatch.setattr(chat_module, "_CLOSE_WAIT_TIMEOUT", 0.01)
    proc = _NeverExitingProc()
    chat_module._close(proc)
    assert proc.killed


# --- `claude` 실행 파일 해석 (최종 리뷰 확정 결함 2) -------------------------


def _cmd_shim(tmp_path):
    """npm 식 `.cmd` 셰임을 흉내 낸다 — Windows 설치본의 실제 모양."""
    d = tmp_path / "shimbin"
    d.mkdir()
    (d / "fakeclaude.cmd").write_text("@echo off\r\necho SHIM\r\n", encoding="utf-8")
    return d


def test_health_and_launch_cannot_disagree_about_a_cmd_shim(tmp_path, monkeypatch):
    """`shutil.which` 와 `Popen` 은 Windows 에서 갈린다 — 그 틈이 배지를 거짓말시켰다.

    `which` 는 `PATHEXT` 를 존중해 `.cmd`/`.ps1` 셰임까지 찾지만
    `CreateProcess` 는 확장자 없는 이름에 `.exe` 만 붙인다. 헬스가 `which`
    로 보고 실행이 `Popen(["claude"])` 로 하던 동안, npm 식 `.cmd`
    설치본에서는 배지가 **연결됨** 인데 모든 턴이 `[WinError 2]` 로 죽었다.

    이 테스트는 그 틈이 **실재함**을 먼저 실행으로 보이고(맨 이름 `Popen`
    은 실패한다), `resolve_claude` 가 돌려준 형태는 실제로 실행된다는 것을
    보인다. 그래서 헬스와 실행이 같은 함수를 쓰는 한 갈릴 수 없다.
    """
    d = _cmd_shim(tmp_path)
    monkeypatch.setenv("PATH", str(d) + os.pathsep + os.environ["PATH"])

    resolved = chat_module.resolve_claude("fakeclaude")
    assert resolved is not None
    assert Path(resolved[0]).suffix.lower() == ".cmd"

    # (1) 맨 이름은 CreateProcess 가 못 찾는다 — 이 틈이 결함의 원인이었다
    with pytest.raises(FileNotFoundError):
        subprocess.Popen(["fakeclaude"], stdout=subprocess.PIPE, shell=False)

    # (2) 해석된 전체 경로는 실제로 실행된다 — 그래서 `.cmd` 설치본이 산다
    proc = subprocess.Popen(resolved, stdout=subprocess.PIPE, shell=False, text=True)
    assert "SHIM" in proc.communicate()[0]


def test_a_missing_claude_is_reported_before_any_process_is_launched(cfg, monkeypatch):
    """찾지 못하면 `ClaudeMissing` — `Popen` 이 던지는 `FileNotFoundError` 가 아니다.

    예전엔 `Popen` 을 부른 뒤 `FileNotFoundError` 를 받아 번역했다. 이제는
    헬스와 같은 해석기가 먼저 판정하므로, `Popen` 이 아예 불리지 않아도
    같은 한국어 문장이 나와야 한다.
    """
    def explode(*a, **kw):
        raise AssertionError("실행 파일이 없는데 자식 프로세스를 띄웠다")

    monkeypatch.setattr(subprocess, "Popen", explode)
    with pytest.raises(ClaudeMissing) as e:
        list(stream_turn(cfg, "x", None, claude=["없는실행파일이름"]))
    assert "claude" in str(e.value)


# --- 세션 복구 알림 (소유자 승인 기능) --------------------------------------


#: 화면에 그대로 나가는 문장. 여기 하드코딩하는 것이 요점이다 — 구현에서
#: 문자열을 가져다 비교하면 문구가 바뀌어도 테스트가 따라 바뀌어 초록이다.
RECOVERY_NOTICE_TEXT = (
    "대화가 새로 이어졌어요 — 방금 이야기한 내용에 대한 답이라면 한 번만 더 말씀해 주세요"
)


def test_recovery_tells_the_user_in_words_they_can_act_on(cfg, tmp_path):
    """복구는 조용히 일어나면 안 된다 — 그러면 "모델이 방금 한 질문을 또 한다" 로만 보인다.

    지금까지 유일한 흔적은 `_p(..., err=True)` 로 나가는 서버 콘솔 줄인데,
    로컬 앱 사용자는 그 창을 보지 않는다.
    """
    list(stream_turn(cfg, "x", "파티편성", claude=_fake_claude(
        tmp_path, OK_LINES, record_to=tmp_path / "record0.json")))
    stale_id = session_id_for(cfg, "파티편성")

    claude_cmd = _fake_claude_session_aware(
        tmp_path, ok_lines=OK_LINES, error_line=_unknown_session_error_line(stale_id),
        resume_record=tmp_path / "r.json", create_record=tmp_path / "c.json")
    evs = list(stream_turn(cfg, "y", "파티편성", claude=claude_cmd))

    notices = [e for e in evs if e.kind == "notice"]
    assert len(notices) == 1
    assert notices[0].data["message"] == RECOVERY_NOTICE_TEXT


def test_the_recovery_notice_is_not_an_error_and_the_turn_still_completes(cfg, tmp_path):
    """복구된 턴은 **성공한 턴**이다 — `error` 로 보내면 정반대를 말하게 된다.

    프런트는 `error` 를 "이 턴은 실패했다" 로 읽어 트리·검토 갱신을 건너뛴다
    (`app.js` 의 갱신 규칙). 복구 뒤 턴은 실제로 슬롯·TC 를 바꿀 수 있으므로,
    이 알림이 `error` 로 나가면 화면이 낡은 채로 남는다 — 이 브랜치가 세
    번 싸운 "성공처럼 보이는데 아닌" 결함을 그대로 뒤집은 모양이다.
    """
    list(stream_turn(cfg, "x", "파티편성", claude=_fake_claude(
        tmp_path, OK_LINES, record_to=tmp_path / "record0.json")))
    stale_id = session_id_for(cfg, "파티편성")

    claude_cmd = _fake_claude_session_aware(
        tmp_path, ok_lines=OK_LINES, error_line=_unknown_session_error_line(stale_id),
        resume_record=tmp_path / "r.json", create_record=tmp_path / "c.json")
    evs = list(stream_turn(cfg, "y", "파티편성", claude=claude_cmd))

    assert not [e for e in evs if e.kind == "error"]
    assert evs[-1].kind == "done"
    assert evs[0].kind == "notice"      # 응답보다 먼저 — 뒤늦게 알리면 늦다


def test_a_normal_turn_carries_no_notice(cfg, tmp_path):
    """복구가 없었으면 알림도 없어야 한다 — 매 턴 뜨면 곧 무시된다."""
    evs = list(stream_turn(cfg, "x", None, claude=_fake_claude(tmp_path, OK_LINES)))
    assert not [e for e in evs if e.kind == "notice"]


# --- 뮤테이션 보강 (최종 리뷰 M2 · M19 · M6 · M5) ---------------------------


def test_headless_print_and_model_flags_are_passed(cfg, tmp_path):
    """`-p` 와 `--model opus` 를 지워도 스위트가 초록이었다 (M2 · M19).

    `-p` 가 빠지면 헤드리스가 아니라 대화형으로 떠서 자식이 영영 안 끝나고,
    `--model` 이 빠지면 이 도구가 전제한 모델이 아닌 것으로 인터뷰가 돈다.
    argv 를 읽는 테스트가 이미 넷 있었는데 이 두 자리만 안 봤다.
    """
    record = tmp_path / "record.json"
    list(stream_turn(cfg, "x", None, claude=_fake_claude(
        tmp_path, OK_LINES, record_to=record)))
    argv = json.loads(record.read_text(encoding="utf-8"))["argv"]
    assert "-p" in argv
    assert argv[argv.index("--model") + 1] == "opus"


def test_the_created_flag_lives_on_disk_not_in_process_memory(cfg, tmp_path):
    """"생성했다" 표시를 모듈 전역 dict 에 담아도 스위트가 초록이었다 (M6).

    같은 프로세스 안에서는 dict 도 파일처럼 동작하므로 재시작을 흉내 낸
    테스트조차 속는다. 진짜 재시작이면 `--session-id` 를 다시 써서 "already
    in use" 로 죽는다 — `f9d919b` 가 고친 바로 그 결함이다. 그러니 파일
    내용을 직접 읽어 확인한다.
    """
    list(stream_turn(cfg, "x", "파티편성", claude=_fake_claude(tmp_path, OK_LINES)))
    saved = json.loads((cfg.knowledge_path / "sessions.json").read_text(encoding="utf-8"))
    assert saved["파티편성"]["created"] is True
    assert saved["파티편성"]["id"] == session_id_for(cfg, "파티편성")


def test_a_real_failure_is_not_labelled_as_a_missing_session(cfg, tmp_path):
    """`_looks_like_unknown_session` 을 무조건 참으로 만들어도 초록이었다 (M5).

    참이 되면 모든 실패가 "세션 없음" 으로 분류돼 매 실패마다 턴을 한 번 더
    태우고(비용 두 배), 사용자에게는 엉뚱한 복구 알림이 뜬다.
    """
    lines = [json.dumps({
        "type": "result", "subtype": "success", "is_error": True,
        "num_turns": 3, "total_cost_usd": 0.37,
        "result": "디스크가 가득 찼습니다",
    }, ensure_ascii=False)]
    evs = list(stream_turn(cfg, "x", None, claude=_fake_claude(tmp_path, lines)))
    assert [e.kind for e in evs] == ["error"]
    assert evs[0].data["kind"] == "error"

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
# 사용자가 `~/.claude` 상태를 지웠거나 세션이 오래돼 사라졌으면, `--resume`
# 은 실측된 "이미 쓰이고 있다" 오류와 같은 모양(정상 JSON 프레임 하나 없이
# stderr 문구만 남기고 곧바로 종료)으로 죽을 것으로 본다 — 둘 다 "그 세션
# id 를 claude 가 지금 받아들이지 않는다" 는 같은 종류의 인자 단계 거부이기
# 때문이다. 이 가정을 실측으로 검증하지는 않았다(따로 값을 태워야 해서
# 라이브 런의 예산 밖). 그 자리에서 그냥 포기하면 그 컨텐츠는 다시는 대화할
# 수 없는 상태로 영영 막힌다 — 그래서 새 세션 id 로 딱 한 번 다시 시도한다.


def _fake_claude_session_aware(tmp_path, *, ok_lines, resume_stderr, resume_record, create_record):
    """`--resume` 이면 즉시 실패(빈 stdout, stderr 만), `--session-id` 면 성공.

    어느 쪽으로 불렸는지에 따라 argv 를 다른 파일에 기록해, 복구가 실제로
    새 세션으로(즉 `--session-id` 로) 재시도했는지 구분해서 검사할 수 있게
    한다.
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
            sys.stderr.write({resume_stderr!r})
            sys.stderr.flush()
            sys.exit(1)

        for line in {ok_lines!r}:
            sys.stdout.write(line + "\\n")
            sys.stdout.flush()
        sys.exit(0)
    """), encoding="utf-8")
    return [sys.executable, str(script)]


def test_unknown_resume_target_recovers_with_a_fresh_session(cfg, tmp_path):
    """`--resume` 대상이 사라졌으면, 새 세션으로 한 번 다시 시도해 턴을 살린다.

    그렇지 않으면 이 컨텐츠는 사용자가 다시는 대화할 수 없는 상태로 영영
    막힌다 — 세션 하나 잃는 것보다 훨씬 나쁘다.
    """
    list(stream_turn(cfg, "x", "파티편성", claude=_fake_claude(
        tmp_path, OK_LINES, record_to=tmp_path / "record0.json")))
    stale_id = session_id_for(cfg, "파티편성")

    resume_record = tmp_path / "resume_record.json"
    create_record = tmp_path / "create_record.json"
    claude_cmd = _fake_claude_session_aware(
        tmp_path, ok_lines=OK_LINES,
        resume_stderr=f"Error: No conversation found with session ID: {stale_id}",
        resume_record=resume_record, create_record=create_record)

    evs = list(stream_turn(cfg, "y", "파티편성", claude=claude_cmd))

    # 사용자에게는 실패한 재개 시도가 전혀 안 보이고, 재시도의 결과만
    # 정상적인 턴 모양(OK_LINES)으로 도착해야 한다.
    assert [e.kind for e in evs] == ["delta", "tool", "done"]

    resume_argv = json.loads(resume_record.read_text(encoding="utf-8"))["argv"]
    assert resume_argv[resume_argv.index("--resume") + 1] == stale_id

    create_argv = json.loads(create_record.read_text(encoding="utf-8"))["argv"]
    new_id = create_argv[create_argv.index("--session-id") + 1]
    assert new_id != stale_id

    # 다음 턴은 이 새 id 를 재개해야 한다 — 낡은 id 로 되돌아가면 다시 막힌다.
    assert session_id_for(cfg, "파티편성") == new_id

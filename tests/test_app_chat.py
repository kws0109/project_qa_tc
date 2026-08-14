"""`claude` 자식 프로세스. 진짜 API 는 부르지 않는다."""

import json
import sys
import textwrap

import pytest

from qatc.app.chat import ChatEvent, ClaudeMissing, session_id_for, stream_turn
from qatc.config import AppConfig


@pytest.fixture()
def cfg(tmp_path):
    return AppConfig(knowledge_root=str(tmp_path / "k"),
                     profiles_dir=str(tmp_path / "p"))


def _fake_claude(tmp_path, lines):
    """정해진 stream-json 줄을 뱉는 가짜 실행 파일을 만든다."""
    script = tmp_path / "fake_claude.py"
    script.write_text(textwrap.dedent(f"""
        import sys
        for line in {lines!r}:
            sys.stdout.write(line + "\\n")
            sys.stdout.flush()
    """), encoding="utf-8")
    return [sys.executable, str(script)]


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

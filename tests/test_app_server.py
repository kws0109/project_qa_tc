"""라우트·SSE·오류. Flask test client 로만 돈다."""

import json

import pytest

from qatc.app.server import create_app
from qatc.config import AppConfig
from qatc.knowledge.models import SlotStatus
from qatc.knowledge.store import KnowledgeStore


@pytest.fixture()
def app(tmp_path):
    cfg = AppConfig(knowledge_root=str(tmp_path / "k"),
                    profiles_dir=str(tmp_path / "p"))
    with KnowledgeStore(cfg.knowledge_path / "starrail.db") as st:
        st.init_content("파티편성", game="starrail", types=["편성"])
        st.set_slot("파티편성", "core_action", SlotStatus.FILLED, "편성한다")
    a = create_app(cfg)
    a.config["TESTING"] = True
    return a


def test_index_is_served(app):
    r = app.test_client().get("/")
    assert r.status_code == 200
    assert b"<html" in r.data.lower()


def test_tree_returns_json(app):
    r = app.test_client().get("/api/tree")
    assert r.status_code == 200
    assert r.get_json()["games"][0]["game"] == "starrail"


def test_content_returns_json(app):
    r = app.test_client().get("/api/content?game=starrail&name=파티편성")
    assert r.status_code == 200
    assert r.get_json()["name"] == "파티편성"


def test_missing_content_is_a_korean_error_not_a_traceback(app):
    r = app.test_client().get("/api/content?game=starrail&name=없는것")
    assert r.status_code == 404
    body = r.get_json()
    assert "없는것" in body["error"]
    assert "ContentNotFound" not in body["error"]
    assert "Traceback" not in body["error"]


def test_health_reports_claude_state(app):
    body = app.test_client().get("/api/health").get_json()
    assert body["claude"] in {"ok", "missing", "unauthenticated"}
    assert "knowledge_root" in body


def test_chat_streams_server_sent_events(app, monkeypatch):
    from qatc.app import chat as chat_mod

    def fake_stream(cfg, message, content, **kw):
        yield chat_mod.ChatEvent("delta", {"text": "안"})
        yield chat_mod.ChatEvent("done", {"changed": True})

    monkeypatch.setattr("qatc.app.server.stream_turn", fake_stream)
    r = app.test_client().post("/api/chat", json={"message": "x", "content": None})
    assert r.status_code == 200
    assert r.mimetype == "text/event-stream"
    text = r.get_data(as_text=True)
    assert "event: delta" in text
    assert "event: done" in text


def test_chat_auth_error_reaches_the_browser(app, monkeypatch):
    from qatc.app import chat as chat_mod

    def fake_stream(cfg, message, content, **kw):
        yield chat_mod.ChatEvent("error", {"kind": "auth", "message": "재인증이 필요합니다."})

    monkeypatch.setattr("qatc.app.server.stream_turn", fake_stream)
    text = app.test_client().post("/api/chat", json={"message": "x"}).get_data(as_text=True)
    assert "event: error" in text
    assert "재인증" in text


def test_sse_frame_ends_with_a_blank_line(app, monkeypatch):
    """프레임은 정확히 `event: <kind>\\ndata: <json>\\n\\n` 모양이어야 한다.

    브라우저의 `EventSource`는 빈 줄(연속된 개행 두 번)을 프레임 경계로
    본다 — 그 경계가 없으면 텍스트는 도착해도 이벤트로 분배되지 않는다.
    `"event: delta" in text` 처럼 부분 문자열만 보는 검사로는 종결자가
    `\\n\\n`에서 `\\n`으로 뭉개져도 여전히 통과한다(이 회귀를 놓친 자리).
    이벤트를 하나만 흘려서 전체 응답 바이트를 통째로 비교한다.
    """
    from qatc.app import chat as chat_mod

    def fake_stream(cfg, message, content, **kw):
        yield chat_mod.ChatEvent("delta", {"text": "안"})

    monkeypatch.setattr("qatc.app.server.stream_turn", fake_stream)
    r = app.test_client().post("/api/chat", json={"message": "x", "content": None})
    text = r.get_data(as_text=True)
    expected_data = json.dumps({"text": "안"}, ensure_ascii=False)
    assert text == f"event: delta\ndata: {expected_data}\n\n"


def test_export_writes_a_real_xlsx_and_returns_its_path(app, tmp_path, monkeypatch):
    """성공 경로 — 실제 xlsx 파일이 생기고, 그 경로가 응답에 그대로 실린다.

    `os.startfile`은 진짜로 부르면 Excel 창을 띄우므로 가짜로 바꿔 둔다.
    """
    import qatc.app.server as server_mod
    from qatc.models import Priority, TCKind, TCOrigin, TestCase

    db = tmp_path / "k" / "starrail.db"
    with KnowledgeStore(db) as st:
        tc = TestCase(id="", category_minor="정상 경로", title="TC",
                      steps=["1"], expected=["e"], priority=Priority.HIGH,
                      kind=TCKind.HAPPY_PATH, origin=TCOrigin.INTERVIEW)
        st.add_testcase("파티편성", "정상 경로", tc, ["core_action"])

    started = {}
    monkeypatch.setattr(server_mod.os, "startfile",
                         lambda p: started.setdefault("path", p), raising=False)

    r = app.test_client().post("/api/export", json={"game": "starrail", "content": "파티편성"})
    assert r.status_code == 200
    body = r.get_json()
    from pathlib import Path
    path = Path(body["path"])
    assert path.exists()
    assert path.suffix == ".xlsx"
    assert Path(started["path"]) == path


def test_export_blocked_file_reports_the_korean_message_not_a_class_name(app, monkeypatch):
    """잠긴 파일 경로 — `ExportBlocked`의 한국어 문장이 그대로, 예외 이름 없이."""
    from qatc.export.tc_excel import ExportBlocked

    msg = ("파일에 쓸 수 없습니다 — C:\\아무경로\\starrail_파티편성_TC.xlsx. "
           "Excel에서 이 파일을 닫고 다시 시도하세요.")

    def fake_export(*args, **kwargs):
        raise ExportBlocked(msg)

    monkeypatch.setattr("qatc.app.server.export_tc_excel", fake_export)
    r = app.test_client().post("/api/export", json={"game": "starrail", "content": "파티편성"})
    assert r.status_code == 409
    body = r.get_json()
    assert body["error"] == msg
    assert "ExportBlocked" not in body["error"]


def test_the_backend_never_writes_to_the_knowledge_db(app):
    """앱이 게이트를 우회할 수 없다는 것이 이 설계의 중심 성질이다.

    `qatc/app/` 안에서 쓰기 메서드를 부르면 채팅→CLI→게이트 경로 밖에
    두 번째 쓰기 경로가 생긴다. 선행 브랜치에서 이 불변식이 세 번 다시
    뚫렸고, 매번 "닫았다"고 판단한 다음 라운드에서 열렸다.
    """
    import pathlib

    src = "\n".join(
        p.read_text(encoding="utf-8")
        for p in pathlib.Path("qatc/app").rglob("*.py")
    )
    for writer in ("add_testcase", "set_slot", "init_content",
                   "replace_generated", "add_slot", "update_testcase_row"):
        assert writer not in src, f"앱이 쓰기 메서드를 부른다: {writer}"

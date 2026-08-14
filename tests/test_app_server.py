"""라우트·SSE·오류. Flask test client 로만 돈다.

파일 끝의 `qatc app` 절만 예외 — CLI 진입점(포트 찾기·브라우저 열기)을 다루므로
Flask test client 를 쓰지 않는다.
"""

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

    `qatc/app/` 안에서, 그리고 `qatc/cli.py`(`qatc app`의 진입점) 안에서
    쓰기 메서드를 부르면 채팅→CLI→게이트 경로 밖에 두 번째 쓰기 경로가
    생긴다. 선행 브랜치에서 이 불변식이 세 번 다시 뚫렸고, 매번 "닫았다"고
    판단한 다음 라운드에서 열렸다.

    `qatc/cli.py`를 넣는 이유: Task 5가 `cmd_app`·`_find_open_port`를 그
    파일에 추가하면서 앱의 진입점 코드가 옛 `qatc/app/**/*.py` 글롭 밖으로
    나갔다 — 이 가드가 막으려던 바로 그 종류의 우회를, 이 가드를 넓히지
    않은 채로는 Task 5 자신이 놓칠 뻔했다(리뷰에서 실측: `cmd_app` 맨 위에
    `KnowledgeStore(...).init_content(...)`를 심어도 옛 버전은 계속 통과했다).

    `qatc/cli_knowledge.py`는 **일부러** 넣지 않는다 — `slot`·`tc` 같은
    인터뷰 명령이 정당하게 쓰기를 하는 유일한 경로라서, 거기까지 넣으면
    이 테스트가 항상 실패한다.
    """
    import pathlib

    write_methods = ("add_testcase", "set_slot", "init_content",
                      "replace_generated", "add_slot", "update_testcase_row")
    guarded_files = [*pathlib.Path("qatc/app").rglob("*.py"), pathlib.Path("qatc/cli.py")]

    for path in guarded_files:
        src = path.read_text(encoding="utf-8")
        for writer in write_methods:
            assert writer not in src, f"{path} 가 쓰기 메서드를 부른다: {writer}"


def test_index_has_the_three_panes(app):
    html = app.test_client().get("/").get_data(as_text=True)
    for pane in ("tree", "chat", "review"):
        assert f'id="{pane}"' in html, f"{pane} 패널이 없습니다"


def test_static_assets_are_served(app):
    c = app.test_client()
    for path in ("/static/app.css", "/static/app.js"):
        assert c.get(path).status_code == 200, path


def test_the_page_loads_nothing_from_the_network(app):
    """로컬 앱이다. 외부 CDN 을 물면 오프라인에서 죽는다."""
    html = app.test_client().get("/").get_data(as_text=True)
    assert "http://" not in html
    assert "https://" not in html


# ---------------------------------------------------------------- `qatc app`


def test_app_command_is_registered():
    from qatc.cli import build_parser
    assert "app" in build_parser()._subparsers._group_actions[0].choices


def test_app_launches_the_server_and_opens_the_browser_at_the_requested_port(
    monkeypatch, tmp_path, capsys
):
    """기본 포트가 비어 있으면 그 포트 그대로 서버를 띄우고 브라우저를 연다.

    `qatc.app.server.run` 과 `webbrowser.open` 을 둘 다 스텁으로 바꾼다 —
    진짜 `run` 은 포그라운드에서 영원히 블로킹하는 개발 서버고, 진짜
    `webbrowser.open` 은 실제 브라우저 창을 띄운다. 둘 다 테스트에서 그대로
    부르면 안 된다.
    """
    from qatc.cli import build_parser, cmd_app

    calls = {}
    monkeypatch.setattr("webbrowser.open", lambda url: calls.setdefault("browser_url", url))
    monkeypatch.setattr("qatc.app.server.run",
                         lambda cfg, port: calls.setdefault("run_port", port))

    cfg = AppConfig(knowledge_root=str(tmp_path / "k"), profiles_dir=str(tmp_path / "p"))
    args = build_parser().parse_args(["app", "--port", "8765"])
    assert cmd_app(args, cfg) == 0

    assert calls["run_port"] == 8765
    assert calls["browser_url"] == "http://127.0.0.1:8765"
    assert "8765" in capsys.readouterr().out


def test_app_falls_back_to_the_next_port_when_the_default_is_taken(
    monkeypatch, tmp_path, capsys
):
    """8765 가 이미 열려 있으면 8766 으로 넘어가고, **그 사실과 실제 주소를 알린다.**

    사용자에게 8765 라고 말해 놓고 실제로는 다른 포트에서 뜨면, 브라우저에
    아무것도 안 뜨는 걸 보고 "고장났다" 고 오해한다 — 이 테스트가 지키는
    불변식이다. 포트가 쓰이고 있는지는 진짜 소켓으로 점유한다 — bind 를
    스텁으로 흉내 내면 `_find_open_port` 가 실제 OS 오류를 어떻게 다루는지는
    검증하지 못한다.
    """
    import socket

    from qatc.cli import build_parser, cmd_app

    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind(("127.0.0.1", 8765))
    occupied.listen(1)
    try:
        calls = {}
        monkeypatch.setattr("webbrowser.open", lambda url: calls.setdefault("browser_url", url))
        monkeypatch.setattr("qatc.app.server.run",
                             lambda cfg, port: calls.setdefault("run_port", port))

        cfg = AppConfig(knowledge_root=str(tmp_path / "k"), profiles_dir=str(tmp_path / "p"))
        args = build_parser().parse_args(["app", "--port", "8765"])
        assert cmd_app(args, cfg) == 0

        assert calls["run_port"] == 8766
        assert calls["browser_url"] == "http://127.0.0.1:8766"
        out = capsys.readouterr().out
        assert "8765" in out and "8766" in out
    finally:
        occupied.close()

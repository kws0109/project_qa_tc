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


# --- 무쓰기 불변식을 **행동으로** 지킨다 (최종 리뷰 확정 결함 5 · M4) --------
#
# 이름 대조 grep 은 전제가 틀렸다. `KnowledgeStore(path).open()` 은 여섯 개
# 이름 어디에도 안 걸리면서 `executescript(_SCHEMA)` + `commit()` 을 돌린다 —
# **경로를 여는 것 자체가 쓰기다** (확정 결함 3 이 그것으로 지식 루트 밖
# 파일을 초기화했다). 스토어 자신의 `_db()` 로 얻은 원시 커넥션도 마찬가지다
# (M4: `api_export` 안에 `UPDATE slots ...` + commit 을 심어도 494 전부 통과).
#
# 그래서 이름이 아니라 **디스크**를 본다: 읽기 경로를 적대적 입력까지 포함해
# 전부 태운 뒤, 지식 루트의 파일 이름·크기·mtime·내용 해시가 하나도 안 바뀌
# 었음을 단언한다. 어떤 새 우회 수단이 나와도 이 단언은 자동으로 막는다.


def _snapshot(root):
    """지식 루트의 모든 파일을 (크기, mtime_ns, 내용 해시) 로 찍는다."""
    import hashlib
    import pathlib

    root = pathlib.Path(root)
    out = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file():
            raw = p.read_bytes()
            out[str(p.relative_to(root))] = (
                len(raw), p.stat().st_mtime_ns, hashlib.sha256(raw).hexdigest())
    return out


def _describe_delta(before, after):
    """무엇이 생겼는지·바뀌었는지 **파일 이름을 대며** 말한다."""
    lines = []
    for name in sorted(set(after) - set(before)):
        lines.append(f"  + 새로 생김: {name} ({after[name][0]}바이트)")
    for name in sorted(set(before) - set(after)):
        lines.append(f"  - 사라짐: {name}")
    for name in sorted(set(before) & set(after)):
        if before[name] != after[name]:
            b, a = before[name], after[name]
            lines.append(f"  ~ 바뀜: {name} "
                         f"({b[0]}→{a[0]}바이트, sha {b[2][:12]}→{a[2][:12]})")
    return "\n".join(lines) or "  (차이를 설명하지 못했습니다)"


#: 지식 루트를 벗어나거나 없는 것을 가리키려는 질의들.
_HOSTILE_CONTENT_QUERIES = [
    {"game": "../victimF", "name": "x"},
    {"game": "..\\victimF", "name": "x"},
    {"game": "./../victimF", "name": "x"},
    {"game": "sub/starrail", "name": "x"},
    {"game": "..", "name": "x"},
    {"game": "", "name": ""},
    {"game": "starrail", "name": "없는것"},
    {"game": "없는게임", "name": "x"},
    {"game": "starrail", "name": "../탈출"},
]


def test_no_read_endpoint_changes_a_single_byte_of_the_knowledge_root(app, tmp_path):
    """읽기 경로 전체를 적대적 입력까지 태워도 지식 루트가 그대로여야 한다."""
    root = tmp_path / "k"
    before = _snapshot(root)
    assert before, "픽스처가 지식 DB 를 하나도 안 만들었습니다"

    c = app.test_client()
    c.get("/")
    c.get("/api/tree")
    c.get("/api/health")
    c.get("/api/content", query_string={"game": "starrail", "name": "파티편성"})
    for query in _HOSTILE_CONTENT_QUERIES:
        c.get("/api/content", query_string=query)

    after = _snapshot(root)
    assert after == before, (
        "읽기 경로가 지식 루트를 바꿨습니다 — 백엔드는 지식 DB 에 쓰지 않습니다:\n"
        + _describe_delta(before, after)
    )


def test_a_traversing_game_never_touches_a_file_outside_the_knowledge_root(app, tmp_path):
    """라우트 자리에서도 재현한다 — 남의 SQLite 파일이 바이트 하나 안 바뀐다.

    실측(최종 리뷰): `GET /api/content?game=../../victimF&name=x` 가 **404 를
    돌려주면서** 지식 루트 밖 파일을 8192 → 32768 바이트로 키우고
    `contents`·`slots`·`testcases` 를 심었다. 404 라서 아무 일도 없었던 것처럼
    보이는 것이 이 결함의 핵심이다.
    """
    import hashlib
    import sqlite3

    victim = tmp_path / "victimF.db"
    con = sqlite3.connect(victim)
    con.execute("CREATE TABLE notes(id INTEGER PRIMARY KEY, body TEXT)")
    con.execute("INSERT INTO notes(body) VALUES ('남의 소중한 메모')")
    con.commit()
    con.close()
    before = victim.read_bytes()

    c = app.test_client()
    stem = str(victim)[: -len(".db")]
    for game in ["../victimF", "..\\victimF", stem, stem.replace("\\", "/")]:
        r = c.get("/api/content", query_string={"game": game, "name": "x"})
        assert r.status_code == 404
        assert "ContentNotFound" not in r.get_data(as_text=True)
        now = victim.read_bytes()
        assert now == before, (
            f"{game!r} 가 지식 루트 밖 파일을 바꿨습니다 — "
            f"{len(before)}→{len(now)}바이트, "
            f"sha {hashlib.sha256(before).hexdigest()[:12]}→"
            f"{hashlib.sha256(now).hexdigest()[:12]}"
        )


def test_export_writes_the_xlsx_and_leaves_every_db_untouched(app, tmp_path, monkeypatch):
    """`/api/export` 가 만들어도 되는 것은 xlsx 하나뿐이다 — DB 는 불변.

    M4(실측): `api_export` 안에 스토어 자신의 원시 커넥션으로 슬롯 상태를
    바꾸고 commit 하는 줄을 심어도 494 전부 통과했다 — **무쓰기 가드까지
    포함해서.** 그 커넥션은 이름 대조 목록 어디에도 안 걸리기 때문이다.
    이름이 아니라 바이트를 보면 그 우회도 그냥 막힌다.
    """
    import qatc.app.server as server_mod
    from qatc.models import Priority, TCKind, TCOrigin, TestCase

    root = tmp_path / "k"
    with KnowledgeStore(root / "starrail.db") as st:
        tc = TestCase(id="", category_minor="정상 경로", title="TC",
                      steps=["1"], expected=["e"], priority=Priority.HIGH,
                      kind=TCKind.HAPPY_PATH, origin=TCOrigin.INTERVIEW)
        st.add_testcase("파티편성", "정상 경로", tc, ["core_action"])

    monkeypatch.setattr(server_mod.os, "startfile", lambda p: None, raising=False)
    before = {k: v for k, v in _snapshot(root).items() if k.endswith(".db")}

    r = app.test_client().post("/api/export",
                               json={"game": "starrail", "content": "파티편성"})
    assert r.status_code == 200

    after = {k: v for k, v in _snapshot(root).items() if k.endswith(".db")}
    assert after == before, (
        "엑셀 내보내기가 지식 DB 를 바꿨습니다:\n" + _describe_delta(before, after)
    )


# --- /api/chat 검증 (최종 리뷰 확정 결함 4) ---------------------------------


def _spy_stream(monkeypatch):
    """`stream_turn` 을 감시자로 갈아끼우고, 받은 인자를 담을 목록을 돌려준다."""
    from qatc.app import chat as chat_mod

    seen = []

    def fake(cfg, message, content, **kw):
        seen.append({"message": message, "content": content})
        yield chat_mod.ChatEvent("done", {})

    monkeypatch.setattr("qatc.app.server.stream_turn", fake)
    return seen


#: (라벨, test_client 인자) — 전부 턴을 띄우면 안 되는 요청들.
_REJECTED_CHAT_REQUESTS = [
    ("본문 없는 text/plain", dict(data=b"", content_type="text/plain")),
    ("JSON 을 위장한 text/plain (단순 요청이라 preflight 가 없다)",
     dict(data='{"message":"공격"}', content_type="text/plain")),
    ("빈 JSON 객체", dict(json={})),
    ("message 가 null", dict(json={"message": None})),
    ("message 가 공백뿐", dict(json={"message": "   "})),
    ("message 가 숫자", dict(json={"message": 123})),
    ("message 가 배열", dict(json={"message": ["공격"]})),
    ("본문이 JSON 배열", dict(json=["공격"])),
    ("content 가 숫자", dict(json={"message": "안녕", "content": 7})),
]


@pytest.mark.parametrize("label,kwargs", _REJECTED_CHAT_REQUESTS,
                         ids=[c[0] for c in _REJECTED_CHAT_REQUESTS])
def test_a_malformed_chat_request_never_burns_a_paid_turn(app, monkeypatch, label, kwargs):
    """한 턴은 실제로 돈이 든다 — 검증 없이 아무 본문에나 띄우면 안 된다.

    비용만의 문제가 아니다. `message` 는 완전히 요청자 제어이고, 그 프롬프트는
    `cwd=project_root()` 로 뜬 헤드리스 claude — 즉 지식 DB 로 가는 **유일한
    정당 쓰기 경로**인 그 CLI — 에 그대로 들어간다.
    """
    seen = _spy_stream(monkeypatch)
    r = app.test_client().post("/api/chat", **kwargs)
    assert r.status_code == 400, label
    assert seen == [], f"{label}: 턴이 실행됐습니다"
    assert "error" in r.get_json()
    assert "Traceback" not in r.get_data(as_text=True)


def test_a_non_json_content_type_is_refused_as_a_format_problem(app, monkeypatch):
    """콘텐츠 타입 검사는 본문 검사와 **다른 말**을 해야 한다.

    본문 검사(`payload` 가 dict 인가)만 있어도 거절 자체는 일어난다 — Flask
    는 JSON 이 아닌 콘텐츠 타입을 파싱하지 않으므로 `None` 이 되기 때문이다.
    그래서 이 줄은 겹쳐 놓은 방어선이고, 그 값은 **사용자가 받는 안내가
    정확해지는 것**에 있다: 형식이 틀린 것("새로고침")과 내용이 빈 것("할
    말을 입력")은 다음 조치가 다르다. 지우면 안내가 엉뚱해지므로 여기서
    고정한다.
    """
    seen = _spy_stream(monkeypatch)
    r = app.test_client().post("/api/chat", data={"message": "안녕"})
    assert r.status_code == 400
    assert seen == []
    assert r.get_json()["error"].startswith("요청 형식이 올바르지 않습니다")


def test_a_cross_origin_chat_request_is_refused(app, monkeypatch):
    """CORS 응답 헤더로는 못 막는다 — 단순 요청은 응답을 못 읽을 뿐 **이미 실행된다.**

    즉 브라우저가 결과를 감춰 줘도 돈은 이미 쓰였고 프롬프트는 이미 헤드리스
    claude 에 들어갔다. 로컬 전용 앱이므로 서버가 실행 전에 막는다.
    """
    seen = _spy_stream(monkeypatch)
    r = app.test_client().post("/api/chat", json={"message": "공격"},
                               headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert seen == []


def test_a_same_origin_chat_request_still_works(app, monkeypatch):
    """봉쇄가 정상 화면을 막으면 안 된다."""
    seen = _spy_stream(monkeypatch)
    r = app.test_client().post("/api/chat", json={"message": "안녕", "content": None},
                               headers={"Origin": "http://localhost"})
    assert r.status_code == 200
    assert len(seen) == 1


def test_the_message_the_user_typed_is_what_reaches_the_turn(app, monkeypatch):
    """라우트의 **유일한 일**이 그동안 미관측이었다 (M7).

    세 테스트가 전부 인자를 무시하는 가짜 `stream_turn` 을 심어서, `message`
    와 `content` 키를 맞바꿔도 494 가 통과했다. 그러면 사용자가 친 문장이
    claude 에 도달한다는 계약항이 통째로 무방비다.
    """
    seen = _spy_stream(monkeypatch)
    app.test_client().post("/api/chat",
                           json={"message": "파티편성 설명할게", "content": "파티편성"})
    assert seen == [{"message": "파티편성 설명할게", "content": "파티편성"}]


# --- claude 실행 파일이 없을 때 (최종 리뷰 확정 결함 2) ---------------------


def test_a_missing_claude_reaches_the_browser_as_a_stream_frame_not_a_500(app, monkeypatch):
    """설치 안내가 담긴 한국어 문장이 **채팅 창에** 도달해야 한다.

    `stream_turn` 은 제너레이터라 `ClaudeMissing` 은 첫 `next()` 에서, 즉
    응답 헤더가 나간 뒤에 터진다. 경계가 없으면 Werkzeug 가 `text/html`
    500 으로 바꾸고 프런트는 `요청이 실패했습니다 (500).` 만 렌더한다 —
    처방이 적힌 유일한 문장이 그것을 읽어야 할 유일한 창에 못 간다.
    """
    from qatc.app.chat import _MISSING_MSG, ClaudeMissing

    def boom(cfg, message, content, **kw):
        raise ClaudeMissing(_MISSING_MSG)
        yield       # 제너레이터로 만들기 위한 줄 — 도달하지 않는다

    monkeypatch.setattr("qatc.app.server.stream_turn", boom)
    r = app.test_client().post("/api/chat", json={"message": "안녕"})

    assert r.status_code == 200
    assert r.mimetype == "text/event-stream"
    text = r.get_data(as_text=True)
    assert "event: error" in text
    assert "claude 실행 파일을 찾을 수 없습니다" in text
    assert "ClaudeMissing" not in text


def test_health_resolves_claude_the_same_way_the_turn_launches_it(app, monkeypatch):
    """배지는 실행 경로와 같은 해석기를 봐야 한다 — 아니면 거짓말을 한다.

    실측: `.cmd` 셰임이 PATH 에 있으면 `shutil.which` 는 찾고
    `Popen(["claude"])` 는 `[WinError 2]` 로 실패한다. 그 시절 배지는
    **연결됨** 인데 모든 턴이 죽었다. `qatc/app/chat.py` 의
    `resolve_claude` 하나만 보게 해서 두 자리가 갈릴 수 없게 한다.
    """
    import qatc.app.server as server_mod

    monkeypatch.setattr(server_mod, "resolve_claude", lambda *a, **kw: None)
    assert app.test_client().get("/api/health").get_json()["claude"] == "missing"


# --- 세션 복구 알림이 브라우저까지 간다 (소유자 승인 기능) ------------------


def test_a_notice_reaches_the_browser_as_its_own_event_kind(app, monkeypatch):
    """`notice` 는 `error` 가 아니다 — 프레임 종류가 그대로 실려야 한다."""
    from qatc.app import chat as chat_mod

    def fake(cfg, message, content, **kw):
        yield chat_mod.ChatEvent("notice", {"kind": "session_restarted",
                                            "message": "대화가 새로 이어졌어요"})
        yield chat_mod.ChatEvent("done", {})

    monkeypatch.setattr("qatc.app.server.stream_turn", fake)
    text = app.test_client().post("/api/chat", json={"message": "x"}).get_data(as_text=True)
    assert "event: notice" in text
    assert "대화가 새로 이어졌어요" in text
    assert "event: error" not in text


def test_a_notice_does_not_clear_the_unauthenticated_flag(app, monkeypatch):
    """알림은 턴을 끝내지 않는다 — `done` 만이 인증 표시를 지운다.

    `notice` 를 `done` 과 같은 갈래로 다루면, 401 로 내려간 배지가 알림 하나에
    **연결됨** 으로 되돌아가 실패를 성공으로 덮는다.
    """
    from qatc.app import chat as chat_mod

    def auth_fail(cfg, message, content, **kw):
        yield chat_mod.ChatEvent("error", {"kind": "auth", "message": "재인증"})

    monkeypatch.setattr("qatc.app.server.stream_turn", auth_fail)
    c = app.test_client()
    c.post("/api/chat", json={"message": "x"})
    assert c.get("/api/health").get_json()["claude"] == "unauthenticated"

    def notice_only(cfg, message, content, **kw):
        yield chat_mod.ChatEvent("notice", {"message": "대화가 새로 이어졌어요"})

    monkeypatch.setattr("qatc.app.server.stream_turn", notice_only)
    c.post("/api/chat", json={"message": "y"})
    assert c.get("/api/health").get_json()["claude"] == "unauthenticated"


# --- 화면 스크립트가 실제로 자기 일을 한다 (M15) ----------------------------


def test_the_page_script_still_talks_to_every_endpoint_and_frame_kind(app):
    """`app.js` 를 17969바이트에서 53바이트로 줄이고 외부 CDN 호출을 심어도
    494 가 통과했다 (M15) — 프론트 테스트가 200 응답과 index.html grep 만 봤다.

    한 파일이 화면 전체이므로, 그 파일이 부르는 엔드포인트와 다루는 프레임
    종류를 직접 확인한다. 로컬 앱이니 외부 주소가 없다는 것도 여기서 본다
    (`index.html` 만 검사하면 스크립트 안의 CDN 호출을 못 본다).
    """
    js = app.test_client().get("/static/app.js").get_data(as_text=True)
    for endpoint in ("/api/tree", "/api/content", "/api/chat", "/api/export", "/api/health"):
        assert endpoint in js, f"화면이 {endpoint} 를 부르지 않습니다"
    for kind in ('"delta"', '"tool"', '"done"', '"error"', '"notice"'):
        assert kind in js, f"화면이 {kind} 프레임을 다루지 않습니다"
    assert "msg-notice" in js, "복구 알림을 그리는 자리가 없습니다"
    assert "http://" not in js and "https://" not in js, "화면이 외부 주소를 씁니다"


def test_the_stylesheet_draws_a_notice_differently_from_a_bubble(app):
    """알림이 말풍선과 같은 모양이면 "앱이 하는 말" 과 "모델이 한 말" 이 섞인다.

    규칙이 **존재하는지** 만 부분 문자열로 보면 `.msg-notice-disabled` 처럼
    이름만 살아 있는 규칙도 통과한다(실측: 그 변이를 놓쳤다). 규칙 본문을
    떼어 내 말풍선(`.msg-assistant`)과 실제로 다른 값인지 본다.
    """
    import re

    css = app.test_client().get("/static/app.css").get_data(as_text=True)

    def rule(selector):
        m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
        assert m, f"{selector} 규칙이 없습니다"
        return m.group(1)

    notice, assistant = rule(".msg-notice"), rule(".msg-assistant")
    assert notice != assistant
    assert "align-self: center" in notice, "알림이 말풍선처럼 한쪽에 붙습니다"
    assert "var(--text-muted)" in notice, "알림이 본문과 같은 무게로 그려집니다"

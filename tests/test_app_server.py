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


def _dir_stamps(root):
    """지식 루트와 그 아래 모든 폴더의 mtime.

    `_snapshot` 은 **파일만** 본다. 그래서 "폴더를 만들고 · 그 안에 쓰고 ·
    다시 지운다" 는 지식 루트를 통째로 헤집고도 스냅샷을 하나도 안 바꾼다 —
    정리가 흔적을 지우기 때문이다 (실측: 첨부 이미지를 지식 루트에 쓰도록
    바꾼 변이가 `_snapshot` 비교를 그대로 통과했다. 정리를 잘 하는 코드일수록
    이 가드에 안 걸린다는 뜻이라, 하필 정반대다).

    폴더의 mtime 은 자식이 생기거나 사라질 때 갱신되므로 그 흔적이 남는다.
    """
    import pathlib

    root = pathlib.Path(root)
    if not root.exists():
        return {}
    out = {".": root.stat().st_mtime_ns}
    for p in sorted(root.rglob("*")):
        if p.is_dir():
            out[str(p.relative_to(root))] = p.stat().st_mtime_ns
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


def test_no_read_endpoint_changes_a_single_byte_of_the_knowledge_root(app, monkeypatch, tmp_path):
    """읽기 경로 전체와, `stream_turn` 을 스텁으로 갈아끼운 `/api/chat` 핸들러
    자체를 태워도 지식 루트가 그대로여야 한다.

    `/api/chat` 은 엄밀히는 "읽기" 가 아니다 — 이 앱에서 지식 DB 를 바꾸는
    유일하게 승인된 경로다. 그 쓰기는 반드시 `stream_turn` 이 띄우는
    `claude` 자식 프로세스(그리고 그 프로세스가 스스로 부르는 `qatc` CLI)
    안에서만 일어나야 한다 — **핸들러 자신**이 지식 DB 를 직접 만지면
    게이트가 우회된다. 그런데 이 가드는 지금까지 `/api/chat` 을 전혀 태우지
    않았다: 핸들러 안에 원시 sqlite 쓰기를 심어도 이 테스트는 볼 수 없었다.
    실제 턴(자식 프로세스)이 만드는 쓰기까지 여기서 검증할 수는 없으므로
    (진짜 `claude` 를 부르면 비용이 든다), 자식 프로세스를 띄우는 **안쪽**만
    스텁으로 바꿔 나머지 경로를 전부 실제로 태운다 — 스텁이 아무것도 안 쓰는데
    스냅샷이 바뀐다면 그건 앱 코드가 직접 쓴 것이다.

    **바깥(`server.stream_turn`)이 아니라 안쪽(`chat._stream_turn`)을 스텁하는
    이유.** 첨부 이미지를 임시 파일로 쓰는 코드는 바깥 껍데기에 있다. 예전처럼
    바깥을 통째로 갈아끼우면 그 쓰기가 아예 실행되지 않아, 저장 위치가 지식
    루트로 바뀌어도 이 가드가 못 본다 — 그 결함을 잡는 것이 이 가드의 존재
    이유인데도.
    """
    root = tmp_path / "k"
    before = _snapshot(root)
    before_dirs = _dir_stamps(root)
    assert before, "픽스처가 지식 DB 를 하나도 안 만들었습니다"

    from qatc.app import chat as chat_mod

    def fake_child(cfg, message, content, *, claude=None):
        yield chat_mod.ChatEvent("done", {})

    monkeypatch.setattr(chat_mod, "_stream_turn", fake_child)

    c = app.test_client()
    c.get("/")
    c.get("/api/tree")
    c.get("/api/health")
    c.get("/api/content", query_string={"game": "starrail", "name": "파티편성"})
    for query in _HOSTILE_CONTENT_QUERIES:
        c.get("/api/content", query_string=query)
    c.post("/api/chat", json={"message": "안녕", "content": None}).get_data()
    # 첨부가 붙은 턴도 태운다 — 이미지를 지식 루트에 쓰면 여기서 걸린다.
    c.post("/api/chat", json={
        "message": "이 화면이에요",
        "images": [{"data": _b64(_png()), "media_type": "image/png"}],
    }).get_data()
    # 캡처도 지식 루트를 건드리지 않는다. OS 는 스텁한다.
    monkeypatch.setattr("qatc.app.server.list_windows", lambda: [])
    c.post("/api/capture", json={"game": "starrail"}).get_data()

    after = _snapshot(root)
    assert after == before, (
        "읽기 경로가 지식 루트를 바꿨습니다 — 백엔드는 지식 DB 에 쓰지 않습니다:\n"
        + _describe_delta(before, after)
    )
    assert _dir_stamps(root) == before_dirs, (
        "지식 루트 안에 무언가가 생겼다가 지워졌습니다. 파일은 안 남았지만 "
        "폴더 mtime 이 그 사실을 기억합니다 — 백엔드는 지식 루트에 아무것도 "
        "쓰지 않습니다(임시 파일도 포함)."
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
    """`/api/export` 가 만들어도 되는 것은 xlsx 하나뿐이다 — 그 외 지식 루트는 불변.

    M4(실측): `api_export` 안에 스토어 자신의 원시 커넥션으로 슬롯 상태를
    바꾸고 commit 하는 줄을 심어도 494 전부 통과했다 — **무쓰기 가드까지
    포함해서.** 그 커넥션은 이름 대조 목록 어디에도 안 걸리기 때문이다.
    이름이 아니라 바이트를 보면 그 우회도 그냥 막힌다.

    이전 버전은 비교를 `.db` 확장자로 미리 걸러서 했다 — 그러면 읽기 경로가
    `.db` 가 아닌 엉뚱한 파일을 지식 루트에 떨어뜨려도 이 테스트는 보지
    못한다(필터 자체가 그 결함을 가린다). 그래서 지식 루트 전체를 찍고,
    이 엔드포인트가 실제로 만들어도 되는 xlsx **한 파일**만 허용 목록에서
    빼는 방식으로 바꾼다 — 그 밖의 어떤 파일이 새로 생기거나 바뀌어도 남는다.
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
    before = _snapshot(root)

    r = app.test_client().post("/api/export",
                               json={"game": "starrail", "content": "파티편성"})
    assert r.status_code == 200

    after = _snapshot(root)
    from pathlib import Path as _Path
    expected_new = str(_Path(r.get_json()["path"]).relative_to(root))
    new_files = set(after) - set(before)
    assert new_files == {expected_new}, (
        "export 가 예상 밖의 파일을 만들거나 지웠습니다:\n" + _describe_delta(before, after)
    )
    after_without_export = {k: v for k, v in after.items() if k != expected_new}
    assert after_without_export == before, (
        "엑셀 내보내기가 지식 DB(또는 다른 기존 파일)를 바꿨습니다:\n"
        + _describe_delta(before, after_without_export)
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
    for kind in ('"delta"', '"tool"', '"done"', '"error"', '"notice"', '"progress"'):
        assert kind in js, f"화면이 {kind} 프레임을 다루지 않습니다"
    assert "msg-notice" in js, "복구 알림을 그리는 자리가 없습니다"
    assert "http://" not in js and "https://" not in js, "화면이 외부 주소를 씁니다"
    # 진행 표시는 두 겹이라야 한다 — `progress` 프레임만 다루고 자체 타이머가
    # 없으면, 백엔드가 조용한 구간에서 화면이 그대로 멈춘다(그 구간이 이
    # 기능이 생긴 이유다).
    assert "setInterval" in js, "경과 시간을 갱신하는 자체 타이머가 없습니다"


# --- `선택 해제` 버튼이 이름 그대로 동작한다 (부차 결함, 병합 전 수정) -------
#
# 예전 이름 `새 대화` 는 두 번 틀렸다: 눌러도 채팅 로그가 안 지워지고,
# 서버 호출도 없이 트리 선택만 지운다. 다음 메시지는 `content: null` 로 가서
# `__default__` 세션 키를 그대로 재개한다 — "새 대화" 가 아니라 기존 대화를
# 잇는다. 세션을 리셋하는 새 라우트는 만들지 않는다(검증 없는 새 POST
# 엔드포인트가 정확히 `/api/chat` 결함이 난 자리였다) — 대신 라벨을 실제
# 동작(`선택 해제`)에 맞추고, 로그를 지우는 동작을 실제로 추가한다.


def test_the_selection_clear_button_is_labelled_for_what_it_does(app):
    """버튼 글자가 `새 대화` 로 되돌아가면 다시 두 번 틀린 이름이 된다."""
    html = app.test_client().get("/").get_data(as_text=True)
    assert '<button id="chat-new-btn" type="button">선택 해제</button>' in html
    assert "새 대화" not in html


def test_the_selection_clear_handler_wipes_the_log_and_calls_no_endpoint(app):
    """핸들러가 실제로 로그를 비우고, 서버는 부르지 않는지를 소스에서 직접 본다.

    라벨만 바뀌고 동작이 그대로면(로그가 안 지워짐) 여전히 거짓말이고,
    반대로 이 핸들러가 `fetch` 를 부르기 시작하면 그건 세션을 리셋하는
    새 서버 경로가 몰래 생겼다는 뜻이다 — 둘 다 이 테스트가 잡는다.
    """
    import re

    js = app.test_client().get("/static/app.js").get_data(as_text=True)
    m = re.search(
        r'getElementById\("chat-new-btn"\)\.addEventListener\("click",\s*(\w+)\)', js)
    assert m, "chat-new-btn 에 클릭 핸들러가 연결돼 있지 않습니다"
    handler_name = m.group(1)

    body_match = re.search(
        r"function\s+" + re.escape(handler_name) + r"\s*\([^)]*\)\s*\{([\s\S]*?)\n\}", js)
    assert body_match, f"{handler_name} 함수 본문을 찾을 수 없습니다"
    body = body_match.group(1)

    assert '"chat-log"' in body, "핸들러가 채팅 로그를 건드리지 않습니다"
    assert 'innerHTML = ""' in body, "핸들러가 로그를 비우지 않습니다"
    assert "fetch(" not in body, "핸들러가 서버를 호출합니다 — 새 라우트가 생긴 것으로 보입니다"


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


# --- 진행 표시가 SSE 프레임으로 브라우저까지 간다 ---------------------------


def test_progress_reaches_the_browser_as_its_own_frame(app, monkeypatch):
    from qatc.app import chat as chat_mod

    def fake_stream(cfg, message, content, **kw):
        yield chat_mod.ChatEvent("progress", {"phase": "준비 중"})
        yield chat_mod.ChatEvent("done", {"changed": False})

    monkeypatch.setattr("qatc.app.server.stream_turn", fake_stream)
    text = app.test_client().post("/api/chat", json={"message": "x"}).get_data(as_text=True)
    assert "event: progress" in text
    assert "준비 중" in text


# --- 스크린샷 첨부 ----------------------------------------------------------
#
# 첫 실사용에서 사용자는 스크린샷 3장을 따로 저장해 두고, 그것을 보면서 화면
# 인벤토리를 손으로 옮겨 적었다 — `screen` 슬롯 하나에 전부. 붙일 수 있으면
# 그 옮겨적기가 없어진다.
#
# **핵심은 방향이다.** `claude` 는 파일 경로로 이미지를 읽는다(실측). 그래서
# 백엔드가 임시 파일로 **쓰고** `claude` 는 **읽기만** 한다 — 예전에
# `.qatc-tmp/` 가 거부됐던 것은 `claude` 가 쓰려고 했기 때문이다.


def _png(size=(1, 1)) -> bytes:
    """진짜 PNG. 손으로 박아 넣은 바이트열을 쓰지 않는 이유는, 그러면 매직
    바이트 검사를 통과시키려고 테스트가 거짓말을 하게 되기 때문이다."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


#: 실행 파일(PE)의 시작. `media_type` 은 얼마든지 "image/png" 라고 말할 수
#: 있으므로, 그 말을 믿지 않는다는 것을 이 값으로 검사한다.
_NOT_AN_IMAGE = b"MZ\x90\x00\x03 this is not an image"


def _b64(raw: bytes) -> str:
    import base64

    return base64.b64encode(raw).decode("ascii")


def _attached_paths(message: str) -> list:
    """턴 메시지에 덧붙은 이미지 절대경로들."""
    lines = message.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("[첨부 이미지"):
            return [x.strip() for x in lines[i + 1:] if x.strip()]
    return []


def _spy_child_stream(monkeypatch):
    """자식 프로세스를 띄우는 **안쪽**만 갈아끼운다.

    바깥의 `stream_turn` 은 그대로 둬야 임시 파일 쓰기·경로 덧붙이기·정리가
    실제로 돌고, 이 절이 검사하려는 것이 바로 그것이다. 예전 방식대로
    `server.stream_turn` 을 통째로 스텁하면 그 셋 중 어느 것도 실행되지
    않아 첨부 경로가 통째로 미관측이 된다.

    `existed` 에는 **턴이 도는 그 순간** 실재하던 경로만 담는다 — 나중에
    "지워졌다" 를 볼 때, 애초에 만들어지긴 했는지와 구별하기 위해서다.
    """
    import os

    from qatc.app import chat as chat_mod

    seen = []

    def fake(cfg, message, content, *, claude=None):
        paths = _attached_paths(message)
        seen.append({
            "message": message,
            "paths": paths,
            "existed": [p for p in paths if os.path.exists(p)],
        })
        yield chat_mod.ChatEvent("done", {})

    monkeypatch.setattr(chat_mod, "_stream_turn", fake)
    return seen


def test_attached_image_is_written_outside_the_knowledge_root(app, tmp_path, monkeypatch):
    """지식 루트에 쓰면 무쓰기 가드가 실패한다 — 그게 가드의 요점이다."""
    from pathlib import Path

    seen = _spy_child_stream(monkeypatch)
    r = app.test_client().post("/api/chat", json={
        "message": "이 화면이에요",
        "images": [{"data": _b64(_png()), "media_type": "image/png"}],
    })
    assert r.status_code == 200
    r.get_data()

    assert len(seen) == 1
    paths = seen[0]["paths"]
    assert len(paths) == 1, f"첨부 경로가 메시지에 없습니다: {seen[0]['message']!r}"
    assert seen[0]["existed"] == paths, "턴이 도는 동안 파일이 실재하지 않았습니다"

    root = (tmp_path / "k").resolve()
    written = Path(paths[0]).resolve()
    assert written.is_absolute()
    assert not written.is_relative_to(root), f"지식 루트 안에 썼습니다: {written}"


def test_a_non_image_payload_is_rejected_in_korean(app):
    """media_type 을 믿지 않는다 — 매직 바이트로 판정한다."""
    r = app.test_client().post("/api/chat", json={
        "message": "x",
        "images": [{"data": _b64(_NOT_AN_IMAGE), "media_type": "image/png"}],
    })
    assert r.status_code == 400
    body = r.get_json()
    assert "이미지" in body["error"]
    assert "PNG" in body["error"]           # 무엇이 되는지 알려준다
    assert "Traceback" not in r.get_data(as_text=True)


def test_too_many_images_are_rejected(app, monkeypatch):
    seen = _spy_child_stream(monkeypatch)
    one = {"data": _b64(_png()), "media_type": "image/png"}
    r = app.test_client().post("/api/chat", json={"message": "x", "images": [one] * 5})
    assert r.status_code == 400
    assert "4" in r.get_json()["error"]
    assert seen == [], "상한을 넘겼는데 턴이 실행됐습니다"


def test_an_oversized_image_is_rejected(app, monkeypatch):
    """한 장 8MB 상한. 넘으면 그 자리에서 400 이고 턴은 뜨지 않는다."""
    from qatc.app.chat import _MAX_SHOT_BYTES

    assert _MAX_SHOT_BYTES == 8 * 1024 * 1024
    seen = _spy_child_stream(monkeypatch)
    huge = b"\x89PNG\r\n\x1a\n" + bytes(_MAX_SHOT_BYTES)
    r = app.test_client().post("/api/chat", json={
        "message": "x",
        "images": [{"data": _b64(huge), "media_type": "image/png"}],
    })
    assert r.status_code == 400
    assert "8MB" in r.get_json()["error"]
    assert seen == [], "상한을 넘겼는데 턴이 실행됐습니다"


def test_the_client_cannot_choose_the_filename(app, monkeypatch):
    """이름을 클라이언트가 정하면 그 경로가 통째로 요청자 제어가 된다."""
    from pathlib import Path

    seen = _spy_child_stream(monkeypatch)
    app.test_client().post("/api/chat", json={
        "message": "x",
        "images": [{"data": _b64(_png()), "media_type": "image/png",
                    "filename": "../../지식루트에떨어져라.png"}],
    }).get_data()

    name = Path(seen[0]["paths"][0]).name
    assert "지식루트에떨어져라" not in name
    assert ".." not in name
    assert name.startswith("qatc-shot-") and name.endswith(".png")


def test_the_temp_image_is_deleted_after_the_turn(app, monkeypatch):
    """턴이 끝나면 지운다. 안 지우면 화면에 있던 것이 임시 폴더에 쌓인다."""
    import os

    seen = _spy_child_stream(monkeypatch)
    app.test_client().post("/api/chat", json={
        "message": "x",
        "images": [{"data": _b64(_png()), "media_type": "image/png"}],
    }).get_data()

    paths = seen[0]["paths"]
    assert seen[0]["existed"] == paths      # 턴 중에는 있었고
    still = [p for p in paths if os.path.exists(p)]
    assert still == [], f"턴이 끝났는데 남아 있습니다: {still}"


# --- 화면이 "이 분모는 늘 수 있다" 를 실제로 말한다 -------------------------


def test_the_tree_shows_that_the_denominator_can_grow(app):
    """`/api/tree` 가 `base_total` 을 실어 보내도 화면이 안 쓰면 아무것도 안 바뀐다.

    그래서 값을 **읽는 자리**(늘어난 개수를 계산하는 함수)와 **그리는 자리**
    (그 사실을 적는 문구), 그리고 그것을 다르게 그리는 스타일 규칙까지 본다.
    셋 중 하나만 빠져도 사용자에게는 예전과 똑같은 `8 / 14` 만 보인다.

    **주석을 먼저 걷어낸다.** 실측: `addedSlotCount` 의 본문을 `return 0;` 로
    바꾸는 변이가 이 테스트를 그대로 통과했다 — 그 함수 안의 주석이 아직
    `base_total` 을 언급하고 있었기 때문이다. 주석은 코드가 아니다.
    """
    import re

    def without_comments(text):
        # 이 화면은 외부 주소를 쓰지 않으므로(`test_the_page_loads_nothing_
        # from_the_network`) `//` 를 지워도 URL 을 다치게 할 일이 없다.
        return re.sub(r"//[^\n]*", "", text)

    c = app.test_client()
    js = without_comments(c.get("/static/app.js").get_data(as_text=True))

    m = re.search(r"function\s+addedSlotCount\s*\([^)]*\)\s*\{([\s\S]*?)\n\}", js)
    assert m, "늘어난 슬롯 수를 계산하는 자리가 없습니다"
    assert "content.base_total" in m.group(1), "그 계산이 base_total 을 안 씁니다"

    assert "추가됨" in js, "늘어난 사실을 적는 문구가 없습니다"
    assert "content-added" in c.get("/static/app.css").get_data(as_text=True), (
        "늘어난 표시를 그리는 스타일 규칙이 없습니다")


# --- 화면 캡처 (`POST /api/capture`) -----------------------------------------


def _stub_capture(monkeypatch, *, raw=None, error=None):
    """`/api/capture` 가 부르는 세 함수를 갈아끼운다.

    라우트가 하는 일(프로파일 찾기·오류를 상태 코드로 옮기기·base64 로 싣기)만
    남기고 OS 를 뺀다. OS 경로는 CI 로 재현할 수 없으므로 라이브 확인이 본다.
    """
    import qatc.app.server as server_mod

    monkeypatch.setattr(server_mod, "list_windows", lambda: ["창"])

    def fake_select(candidates, profile):
        return "선택된 창"

    def fake_grab(window):
        if error is not None:
            raise error
        return raw if raw is not None else _png()

    monkeypatch.setattr(server_mod, "select_window", fake_select)
    monkeypatch.setattr(server_mod, "grab_window", fake_grab)


def test_capture_returns_a_base64_png(app, monkeypatch, tmp_path):
    import base64

    (tmp_path / "p").mkdir(parents=True, exist_ok=True)
    (tmp_path / "p" / "starrail.yaml").write_text(
        "name: 붕괴 스타레일" + chr(10) + "window:" + chr(10) + "  process: StarRail.exe",
        encoding="utf-8")
    _stub_capture(monkeypatch, raw=_png())

    r = app.test_client().post("/api/capture", json={"game": "starrail"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["media_type"] == "image/png"
    assert base64.b64decode(body["data"])[:4] == bytes([137, 80, 78, 71])


def test_the_capture_response_matches_the_chat_image_shape(app, monkeypatch, tmp_path):
    """캡처 결과가 붙여넣은 이미지와 같은 모양이어야 프런트가 한 경로로 다룬다."""
    (tmp_path / "p").mkdir(parents=True, exist_ok=True)
    (tmp_path / "p" / "starrail.yaml").write_text(
        "name: 스타레일" + chr(10) + "window:" + chr(10) + "  process: StarRail.exe",
        encoding="utf-8")
    _stub_capture(monkeypatch)

    body = app.test_client().post("/api/capture", json={"game": "starrail"}).get_json()
    assert set(body) == {"data", "media_type"}
    # 그 모양 그대로 /api/chat 에 넣어도 통과해야 한다.
    from qatc.app.chat import decode_shots
    images, refusal = decode_shots([body])
    assert refusal is None and len(images) == 1


_CAPTURE_FAILURES = [
    ("no_window_config", 400), ("not_running", 404),
    ("minimized", 409), ("occluded", 409), ("blank", 409),
]


@pytest.mark.parametrize("kind,status", _CAPTURE_FAILURES, ids=[c[0] for c in _CAPTURE_FAILURES])
def test_capture_errors_map_to_status_codes(app, monkeypatch, tmp_path, kind, status):
    """상태 코드가 원인마다 달라야 화면이 다른 안내를 보여줄 수 있다."""
    from qatc.capture import CaptureError

    (tmp_path / "p").mkdir(parents=True, exist_ok=True)
    (tmp_path / "p" / "starrail.yaml").write_text(
        "name: 스타레일" + chr(10) + "window:" + chr(10) + "  process: StarRail.exe",
        encoding="utf-8")
    _stub_capture(monkeypatch, error=CaptureError(kind, "한국어 사유 문장"))

    r = app.test_client().post("/api/capture", json={"game": "starrail"})
    assert r.status_code == status
    assert r.get_json()["error"] == "한국어 사유 문장"
    assert "CaptureError" not in r.get_data(as_text=True)


def test_a_cross_origin_capture_is_refused(app, monkeypatch):
    """이 엔드포인트는 **화면 내용**을 돌려준다 - 교차 출처 차단이 더 중요하다."""
    called = []
    import qatc.app.server as server_mod
    monkeypatch.setattr(server_mod, "list_windows", lambda: called.append(1) or [])

    r = app.test_client().post("/api/capture", json={"game": "starrail"},
                               headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert called == [], "교차 출처 요청이 화면을 읽었습니다"


def test_a_capture_without_a_game_never_touches_the_screen(app, monkeypatch):
    called = []
    import qatc.app.server as server_mod
    monkeypatch.setattr(server_mod, "list_windows", lambda: called.append(1) or [])

    r = app.test_client().post("/api/capture", json={})
    assert r.status_code == 400
    assert called == []
    assert "트리" in r.get_json()["error"]      # 다음 조치


def test_an_unknown_game_is_a_korean_404(app, monkeypatch):
    import qatc.app.server as server_mod
    monkeypatch.setattr(server_mod, "list_windows", lambda: [])

    r = app.test_client().post("/api/capture", json={"game": "없는게임"})
    assert r.status_code == 404
    assert "프로파일" in r.get_json()["error"]

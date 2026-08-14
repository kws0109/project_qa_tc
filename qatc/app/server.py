"""Flask 서버 — 읽기 API·SSE 채팅·엑셀 내보내기를 라우트로 묶는다.

이 모듈 자신은 지식 DB 에 아무것도 쓰지 않는다. `/api/export` 가 여는
`KnowledgeStore` 도 읽기만 하고, 실제로 새로 만드는 파일은 xlsx 하나뿐이다.
슬롯·TC 를 바꾸는 유일한 경로는 `/api/chat` 이 흘려보내는 `claude` 자식
프로세스(그리고 그 프로세스가 스스로 부르는 `qatc` CLI)다 — 이 파일이 그
경로를 우회하면 "근거 없는 TC는 만들어지지 않는다"는 게이트가 빈 껍데기가
된다.
"""

from __future__ import annotations

import json
import os
import shutil

from flask import Flask, Response, jsonify, request

from ..cli_knowledge import _safe_filename_part
from ..config import AppConfig
from ..console import _p
from ..export.tc_excel import ExportBlocked, export_tc_excel
from ..knowledge.gate import plan_families, withdrawn_families
from ..knowledge.store import KnowledgeStore
from .chat import stream_turn
from .views import ContentNotFound, content_detail, tree


def create_app(cfg: AppConfig) -> Flask:
    """`cfg` 가 가리키는 지식 루트를 읽는 Flask 앱을 만든다."""
    app = Flask(__name__)

    # `claude` 인증 상태는 실제로 한 턴을 태우지 않고는 확실히 알 수 없고,
    # 그렇게 확인하면 턴마다 비용이 든다(실측: 사소한 호출 하나에도
    # $0.369). 그래서 낙관적으로 시작해("ok"), `/api/chat` 이 401 을 만난
    # 시점에만 내려서(`error(kind=auth)` 이후 재확인 — 설계서 §3) "그
    # 프로세스를 실제로 태우지 않고도" 다음 `/api/health` 호출이 정확한
    # 상태를 보고하게 한다. 성공한 턴이 그 표시를 다시 지운다.
    session_state = {"unauthenticated": False}

    @app.get("/")
    def index():
        # 3분할 화면은 정적 파일이다 (Task 4) — 여기서는 그 파일을 그대로
        # 돌려줄 뿐, 화면이 뭘 하는지는 이 모듈이 몰라도 된다.
        return app.send_static_file("index.html")

    @app.get("/api/tree")
    def api_tree():
        return jsonify(tree(cfg))

    @app.get("/api/content")
    def api_content():
        game = request.args.get("game", "")
        name = request.args.get("name", "")
        try:
            return jsonify(content_detail(cfg, game, name))
        except ContentNotFound as exc:
            return jsonify({"error": str(exc)}), 404

    @app.get("/api/health")
    def api_health():
        if shutil.which("claude") is None:
            status = "missing"
        elif session_state["unauthenticated"]:
            status = "unauthenticated"
        else:
            status = "ok"
        return jsonify({"claude": status, "knowledge_root": str(cfg.knowledge_path)})

    @app.post("/api/chat")
    def api_chat():
        payload = request.get_json(silent=True) or {}
        message = payload.get("message", "")
        content = payload.get("content")

        def generate():
            for ev in stream_turn(cfg, message, content):
                if ev.kind == "error" and ev.data.get("kind") == "auth":
                    session_state["unauthenticated"] = True
                elif ev.kind == "done":
                    session_state["unauthenticated"] = False
                data = json.dumps(ev.data, ensure_ascii=False)
                yield f"event: {ev.kind}\ndata: {data}\n\n"

        return Response(generate(), mimetype="text/event-stream")

    @app.post("/api/export")
    def api_export():
        payload = request.get_json(silent=True) or {}
        game = payload.get("game", "")
        content = payload.get("content", "")
        try:
            # 존재 검증과 오류 문구는 `content_detail` 한 곳에서만 만든다 —
            # 여기서 따로 흉내 내면 문구가 갈라질 위험이 있다. 반환값은
            # xlsx 에 필요한 원본 도메인 객체(Enum·category_major 포함)를
            # 담지 않으므로 버리고, 아래에서 저장소를 다시 읽는다.
            content_detail(cfg, game, content)
            with KnowledgeStore(cfg.knowledge_path / f"{game}.db") as st:
                cases = st.testcases(content)
                slots = st.slots(content)
        except ContentNotFound as exc:
            return jsonify({"error": str(exc)}), 404

        _, skipped = plan_families(slots)
        withdrawn = withdrawn_families(slots, {tc.category_minor for tc in cases})
        out = (cfg.knowledge_path
               / f"{_safe_filename_part(game)}_{_safe_filename_part(content)}_TC.xlsx")
        try:
            path = export_tc_excel(content, cases, skipped, out, withdrawn)
        except ExportBlocked as exc:
            return jsonify({"error": str(exc)}), 409
        os.startfile(path)
        return jsonify({"path": str(path)})

    return app


def run(cfg: AppConfig, port: int = 8765) -> None:
    """개발용 로컬 서버를 포그라운드에서 띄운다."""
    app = create_app(cfg)
    _p(f"QATC 앱 — http://127.0.0.1:{port}")
    # threaded=True 가 빠지면 Werkzeug 개발 서버가 한 번에 요청을 하나만
    # 처리한다. `/api/chat` 은 턴이 끝날 때까지(수 초~수십 초) 응답을 계속
    # 흘려보내는 SSE 라서, 그 동안 같은 프로세스로 오는 `/api/tree`·
    # `/api/content`·`/api/health` 요청이 전부 그 스트림이 끝날 때까지
    # 멈춘다 — 채팅 중엔 트리·검토 패널이 죽은 것처럼 보인다. Flask test
    # client 는 동시 요청 없이 동기적으로만 도므로 이 플래그를 빼도 어떤
    # 테스트도 못 잡는다 — 장식처럼 보여도 지우면 이 회귀가 조용히 돌아온다.
    app.run(host="127.0.0.1", port=port, threaded=True)

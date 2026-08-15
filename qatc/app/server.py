"""Flask 서버 — 읽기 API·SSE 채팅·엑셀 내보내기를 라우트로 묶는다.

이 모듈 자신은 지식 DB 에 아무것도 쓰지 않는다. `/api/export` 가 여는
`KnowledgeStore` 도 읽기만 하고, 실제로 새로 만드는 파일은 xlsx 하나뿐이다.
슬롯·TC 를 바꾸는 유일한 경로는 `/api/chat` 이 흘려보내는 `claude` 자식
프로세스(그리고 그 프로세스가 스스로 부르는 `qatc` CLI)다 — 이 파일이 그
경로를 우회하면 "근거 없는 TC는 만들어지지 않는다"는 게이트가 빈 껍데기가
된다.
"""

from __future__ import annotations

import base64
import json
import os
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, request

from ..capture import CaptureError, grab_window, list_windows, select_window
from ..cli_knowledge import _safe_filename_part
from ..config import AppConfig
from ..console import _p
from ..export.tc_excel import ExportBlocked, export_tc_excel
from ..knowledge.gate import plan_families, withdrawn_families
from ..knowledge.store import KnowledgeStore
from ..profiles import load_profiles
from .chat import ClaudeMissing, decode_shots, resolve_claude, stream_turn
from .views import ContentNotFound, content_detail, resolve_db_path, tree


def _reject_bad_local_request(req):
    """로컬 전용 POST 라우트가 공통으로 보는 것. 괜찮으면 `None`.

    `/api/chat` 과 `/api/capture` 둘 다 필요로 하는 앞 두 검사다 — 복사하지
    않고 나눠 쓴다. 갈라지면 한쪽만 뚫린다.

    1. **출처.** 로컬 전용 앱이므로 교차 출처 요청은 그냥 거절한다. CORS
       응답 헤더로 막는 것으로는 부족하다 — `text/plain` 같은 단순 요청은
       preflight 가 없어서, 브라우저가 응답을 **읽지 못할 뿐 요청은 이미
       실행된다.** `/api/chat` 은 그때 이미 돈이 쓰였고, `/api/capture` 는
       그때 이미 화면을 읽었다. 브라우저를 믿지 말고 서버가 실행 전에
       막아야 한다. `Origin` 이 아예 없는 호출(로컬 스크립트·테스트
       클라이언트)은 브라우저가 만든 것이 아니므로 통과시킨다.
    2. **콘텐츠 타입 · 본문.** JSON 객체여야 한다. 이 검사만으로도 위의
       단순 요청 경로가 한 번 더 막힌다(방어선 둘이 서로 독립이다).
    """
    origin = req.headers.get("Origin") or req.headers.get("Referer")
    if origin and urlsplit(origin).netloc != req.host:
        return jsonify({"error": "이 앱은 로컬에서만 씁니다. "
                                 "브라우저에서 http://127.0.0.1 주소로 다시 여세요."}), 403
    if not req.is_json:
        return jsonify({"error": "요청 형식이 올바르지 않습니다. "
                                 "브라우저를 새로고침한 뒤 다시 보내세요."}), 400
    if not isinstance(req.get_json(silent=True), dict):
        return jsonify({"error": "요청 내용을 읽을 수 없습니다. "
                                 "브라우저를 새로고침한 뒤 다시 보내세요."}), 400
    return None


def _reject_bad_chat_request(req):
    """`/api/chat` 이 받아도 되는 요청인지 본다. 괜찮으면 `None`.

    이 라우트는 실제 돈이 드는 턴을 띄우고(실측: 사소한 호출 하나에도
    $0.369), 그 프롬프트는 지식 DB 로 가는 **유일한 정당 쓰기 경로**인
    헤드리스 `claude` 에 그대로 들어간다. 그런데 예전엔 검증이 하나도
    없었다 — 파싱 불가 본문·빈 본문·비-JSON 콘텐츠 타입이 전부
    `message=""` 로 격하돼 턴이 그대로 돌았다.

    출처 · 콘텐츠 타입 · 본문 검사는 `_reject_bad_local_request` 가 대신
    본다(`/api/capture` 와 공유). 여기서는 이 라우트만의 나머지를 본다.

    1. **`message`.** 비어 있지 않은 문자열이어야 한다. 프런트는 이미 그
       모양만 보낸다(`app.js` 가 `trim()` 후 빈 문자열을 막는다) — 서버
       쪽 쌍둥이가 없었을 뿐이다.
    2. **`content`.** 있다면 문자열이어야 한다.

    첨부 이미지(`images`)는 **여기서 보지 않는다.** 판정 기준이 이 파일이
    아니라 그 바이트를 디스크에 쓰는 `chat.py` 에 있기 때문이다
    (`decode_shots`). 라우트가 그 함수를 이 검사 바로 다음에 부른다 — 두
    관문 모두 턴을 띄우기 전이므로, 어느 쪽에 걸리든 돈은 쓰이지 않는다.
    """
    rejection = _reject_bad_local_request(req)
    if rejection is not None:
        return rejection
    payload = req.get_json(silent=True)
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "보낼 내용이 비어 있습니다. "
                                 "할 말을 입력한 뒤 다시 보내세요."}), 400
    content = payload.get("content")
    if content is not None and not isinstance(content, str):
        return jsonify({"error": "컨텐츠 선택을 읽을 수 없습니다. "
                                 "왼쪽 트리에서 컨텐츠를 다시 고르세요."}), 400
    return None


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
        # **`resolve_claude` 여야 한다.** 예전엔 여기서 `shutil.which("claude")`
        # 를 보고 실행은 `Popen(["claude"], shell=False)` 로 했는데, Windows
        # 에서 그 둘은 갈린다 — `which` 는 `PATHEXT` 를 존중해 `.cmd` 셰임까지
        # 찾고 `CreateProcess` 는 `.exe` 만 붙인다. npm 식 설치본에서 배지가
        # **연결됨** 인 채로 모든 턴이 죽었다. 실행과 같은 함수를 부르면 두
        # 자리가 애초에 갈릴 수 없다.
        if resolve_claude() is None:
            status = "missing"
        elif session_state["unauthenticated"]:
            status = "unauthenticated"
        else:
            status = "ok"
        return jsonify({"claude": status, "knowledge_root": str(cfg.knowledge_path)})

    @app.post("/api/chat")
    def api_chat():
        rejection = _reject_bad_chat_request(request)
        if rejection is not None:
            return rejection
        payload = request.get_json(silent=True) or {}
        message = payload["message"].strip()
        content = payload.get("content")
        # 두 번째 관문. 아직 턴을 띄우기 전이므로 거절해도 돈은 안 든다.
        images, refusal = decode_shots(payload.get("images"))
        if refusal is not None:
            return jsonify({"error": refusal}), 400

        def generate():
            try:
                for ev in stream_turn(cfg, message, content, images=images):
                    if ev.kind == "error" and ev.data.get("kind") == "auth":
                        session_state["unauthenticated"] = True
                    elif ev.kind == "done":
                        session_state["unauthenticated"] = False
                    data = json.dumps(ev.data, ensure_ascii=False)
                    yield f"event: {ev.kind}\ndata: {data}\n\n"
            except ClaudeMissing as exc:
                # `stream_turn` 은 제너레이터라 이 예외는 첫 `next()` 에서
                # 터진다 — 즉 응답 헤더(`text/event-stream`)가 이미 나간
                # 뒤다. 잡지 않으면 Werkzeug 가 `text/html` 500 으로 바꾸고
                # 프런트는 `요청이 실패했습니다 (500).` 만 렌더한다. 그러면
                # 설치 방법이 적힌 **유일한** 한국어 문장이, 사용자가 그걸
                # 읽어야 할 유일한 창에 도달하지 못한다.
                data = json.dumps({"kind": "missing", "message": str(exc)},
                                  ensure_ascii=False)
                yield f"event: error\ndata: {data}\n\n"

        return Response(generate(), mimetype="text/event-stream")

    #: 캡처 실패 코드 -> HTTP 상태. 원인마다 상태가 달라야 화면이 다른 안내를
    #: 보여줄 수 있다. 모르는 코드는 409 로 떨어뜨린다(요청은 멀쩡했고 지금
    #: 상태가 문제라는 뜻이므로).
    capture_status = {
        "no_window_config": 400, "not_running": 404,
        "minimized": 409, "occluded": 409, "blank": 409,
    }

    @app.post("/api/capture")
    def api_capture():
        rejection = _reject_bad_local_request(request)
        if rejection is not None:
            return rejection
        payload = request.get_json(silent=True) or {}
        game = payload.get("game")
        if not isinstance(game, str) or not game.strip():
            return jsonify({"error": "어느 게임의 창을 찍을지 알 수 없습니다. "
                                     "왼쪽 트리에서 컨텐츠를 고른 뒤 다시 눌러 주세요."}), 400
        # `game` 은 요청 본문에서 온다. `resolve_db_path` 와 같은 원칙으로,
        # 이름의 생김새를 보지 않고 **실재하는 프로파일 목록과 대조한다.**
        profile = load_profiles(cfg.profiles_path).get(game)
        if profile is None:
            return jsonify({"error": f"'{game}' 프로파일이 없습니다. "
                                     f"왼쪽 트리에서 게임을 다시 고르세요."}), 404
        try:
            raw = grab_window(select_window(list_windows(), profile))
        except CaptureError as exc:
            # 클래스 이름이 아니라 완성된 한국어 문장을 그대로 보낸다.
            return jsonify({"error": exc.message}), capture_status.get(exc.kind, 409)
        return jsonify({"data": base64.b64encode(raw).decode("ascii"),
                        "media_type": "image/png"})

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
            with KnowledgeStore(resolve_db_path(cfg, game)) as st:
                cases = st.testcases(content)
                slots = st.slots(content)
        except ContentNotFound as exc:
            return jsonify({"error": str(exc)}), 404

        _, skipped = plan_families(slots)
        withdrawn = withdrawn_families(slots, {tc.category_minor for tc in cases})
        # `game`·`content` 는 둘 다 요청 본문에서 온다. `_safe_filename_part`
        # 가 구분자를 지우지만, 그 함수는 "정리" 지 "봉쇄" 가 아니다 — 정말
        # 지식 폴더 안에 떨어지는지는 만들어진 경로로 직접 확인한다. 결함 3
        # (읽기 경로의 `game` 트래버설)이 정확히 "가운데 함수를 믿었다" 로
        # 생긴 구멍이었다.
        out = (cfg.knowledge_path
               / f"{_safe_filename_part(game)}_{_safe_filename_part(content)}_TC.xlsx")
        if out.resolve().parent != cfg.knowledge_path.resolve():
            return jsonify({"error": "이름을 파일로 만들 수 없습니다. "
                                     "컨텐츠 이름을 확인하고 다시 시도하세요."}), 400
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

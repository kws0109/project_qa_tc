"""`claude` 자식 프로세스 — 채팅 턴을 스트리밍한다.

로컬 `claude` 프로세스를 헤드리스로 띄워 대화를 이어간다. API 를 직접 부르지
않는 이유는 비용이다 — Anthropic API 로 직접 부르면 컨텐츠 하나에 수십 달러가
드는데, 사용자의 `claude` 구독으로 돌리면 이 앱이 공짜에 가깝다.

`qatc/app/views.py` 는 참조하지 않는다 — 이 모듈은 지식 DB 를 전혀 건드리지
않고, 오직 `claude` 자식 프로세스(그리고 그 프로세스가 스스로 부르는 `qatc`
CLI)와만 대화한다. 두 계층은 서로 몰라야 한다.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

from ..config import AppConfig, project_root
from ..console import _p

_DEFAULT_SESSION_KEY = "__default__"

_MISSING_MSG = (
    "claude 실행 파일을 찾을 수 없습니다. "
    "Claude Code CLI가 설치되어 있는지, PATH에 등록되어 있는지 확인한 뒤 다시 시도하세요."
)

_AUTH_ERROR_MSG = (
    "claude 인증이 만료되었습니다. 터미널에서 'claude' 를 실행해 재인증한 뒤 다시 시도하세요."
)

# 실측(재검토, 진짜 claude 로 확인): 알 수 없는 --resume 대상은 프레임 없이
# 죽지 않는다 — 정상적인 JSON `result` 프레임으로 "errors": ["No conversation
# found with session ID: ..."] 를 담아 온다. `_result_events` 가 이 문자열을
# (그리고 문구가 바뀔 경우의 구조적 폴백을) 이 프레임을 파싱하는 자리에서
# 바로 판정해 `ChatEvent.data["kind"] = "unknown_session"` 이라는 코드로
# 내보낸다 — `stream_turn` 은 그 코드만 보고, 렌더링된 메시지 문자열을
# 다시 뒤지지 않는다(`_looks_like_unknown_session` 참고).
_UNKNOWN_SESSION_MARKER = "No conversation found with session ID"

#: 세션 복구가 일어났을 때 화면에 그대로 띄우는 문장.
#:
#: **이것은 오류가 아니다.** 복구된 턴은 끝까지 성공하고 `done` 이 나가며
#: 트리·검토 패널도 정상으로 갱신된다. 그래서 `error` 로 보내면 안 된다 —
#: 프런트가 "턴이 실패했다" 로 읽어, 이 브랜치가 세 번 싸운 "성공처럼
#: 보이는데 아닌" 결함을 정확히 뒤집은 모양이 된다. 대신 `notice` 라는
#: 별도 종류로 내보내고 화면은 옅은 시스템 줄로 그린다.
#:
#: 문구는 일부러 이렇게 골랐다: 기술 용어가 없고, 사용자가 할 일이 정확히
#: 하나이며, 무엇이 사라졌다는 암시가 없다 — 슬롯에 들어간 것은 어떤
#: 경우에도 위험하지 않기 때문이다.
_RECOVERY_NOTICE = (
    "대화가 새로 이어졌어요 — 방금 이야기한 내용에 대한 답이라면 한 번만 더 말씀해 주세요"
)


#: 자식이 이만큼 조용하면 하트비트 진행 표시를 한 번 낸다 (초).
#:
#: 이 값이 진행 표시의 **하한**을 정한다: 백엔드가 아무 말도 못 할 때조차
#: 화면은 이 주기로 움직인다. 테스트는 이 값을 짧게 몽키패치해 침묵 구간을
#: 실제로 만들어 본다.
_HEARTBEAT_SECONDS = 5.0

#: 진행 표시 문구. 화면에 그대로 나가는 한국어이므로 여기 한 곳에서만 정한다.
_PHASE_STARTING = "claude 를 깨우는 중"
_PHASE_PREPARING = "준비 중"
_PHASE_WAITING = "기다리는 중"

#: `_pump_stdout` 이 "더 읽을 것이 없다" 를 알리는 표식. `None` 이나 빈
#: 문자열을 쓰면 자식이 실제로 보낸 빈 줄과 구별할 수 없다.
_STDOUT_EOF = object()


def _progress(phase: str) -> "ChatEvent":
    return ChatEvent("progress", {"phase": phase})


class ClaudeMissing(Exception):
    """`claude` 실행 파일을 찾지 못했다. 메시지는 완성된 한국어 문장이다."""


@dataclass(frozen=True)
class ChatEvent:
    """`stream_turn` 이 흘려보내는 사건 하나.

    `kind` 는 `delta`(텍스트 조각) / `tool`(도구 호출) / `progress`(지금
    무엇을 하는 중인지) / `done`(턴 종료) / `error`(실패) / `notice`(턴은
    성공했지만 사용자가 알아야 할 것) 중 하나다. `notice` 는 `error` 가
    아니다 — 그 턴은 계속 진행되고 `done` 으로 끝난다.

    `progress` 도 `error` 가 아니고 `done` 도 아니다 — 아무 것도 확정하지
    않는 순수한 표시이므로, 소비자는 이 종류로 턴을 끝내거나 패널을 다시
    읽으면 안 된다.
    """

    kind: str
    data: dict


# --- 세션 id ------------------------------------------------------------------
#
# 실측(라이브 스모크 테스트): 진짜 `claude` 는 `--session-id <uuid>` 를
# create-only 로 다룬다 — 이미 그 id 로 대화를 연 적이 있으면 "Error: Session
# ID <uuid> is already in use." 로 거부한다. 기존 대화를 이으려면 `--resume
# <uuid>` 를 써야 한다. 그래서 컨텐츠별로 "이 id 로 생성을 이미 시도했는가"를
# `knowledge_root/sessions.json` 에 함께 저장해 두고, 첫 턴은 `--session-id`,
# 그 다음 턴부터는 `--resume` 을 고른다.


@dataclass(frozen=True)
class SessionRef:
    """컨텐츠 하나의 세션 상태.

    `resume` 이 거짓이면 이 `id` 로 아직 세션을 생성한 적이 없다(이번이 첫
    턴) — `--session-id` 를 써야 한다. 참이면 이미 한 번 생성을 시도했다 —
    `--resume` 으로 이어가야 한다.
    """

    id: str
    resume: bool


def session_id_for(cfg: AppConfig, content: str | None) -> str:
    """컨텐츠별 세션 id 만 필요할 때 쓴다. 생성/재개 구분은 `session_ref_for`."""
    return session_ref_for(cfg, content).id


def session_ref_for(cfg: AppConfig, content: str | None) -> SessionRef:
    """컨텐츠별 세션 상태. `knowledge_root/sessions.json` 에 영속된다.

    컨텐츠를 아직 고르지 않은 대화(`content=None`)도 이어져야 하므로, 실제
    컨텐츠 이름과 겹치지 않는 별도 키(`__default__`)를 쓴다 — 그 대화의 첫
    턴도 나머지와 똑같이 "아직 생성 안 됨" 으로 시작해 `--session-id` 를
    받는다.
    """
    key = content if content is not None else _DEFAULT_SESSION_KEY
    path = _sessions_path(cfg)
    sessions = _load_sessions(path)
    entry = _normalize_entry(sessions.get(key))
    if entry is not None:
        return SessionRef(entry["id"], resume=entry["created"])
    new_id = str(uuid.uuid4())
    sessions[key] = {"id": new_id, "created": False}
    _save_sessions(path, sessions)
    return SessionRef(new_id, resume=False)


def _mark_session_created(cfg: AppConfig, content: str | None, session_id: str) -> None:
    """`session_id` 로 세션 생성을 시도했다고 기록한다 — 다음 턴부터 `--resume`.

    턴이 실제로 성공했는지는 보지 않고 시도 자체를 기록한다. 앱이 죽거나
    `claude` 실행 파일이 없어 시도가 곧바로 실패해도, 이후 재시도는 아래
    `_attempt_turn` 의 복구 경로(알 수 없는 세션이면 새로 만든다)를 타므로
    컨텐츠가 영영 막히지는 않는다.
    """
    key = content if content is not None else _DEFAULT_SESSION_KEY
    path = _sessions_path(cfg)
    sessions = _load_sessions(path)
    sessions[key] = {"id": session_id, "created": True}
    _save_sessions(path, sessions)


def _normalize_entry(entry: object) -> dict | None:
    """`sessions.json` 항목 하나를 `{"id": str, "created": bool}` 로 정규화한다.

    옛 형식(문자열 하나뿐)도 받아들인다 — 그 id 는 이미 최소 한 번은
    `--session-id` 로 쓰였을 것이므로 `created=True` 로 본다(재개 시도).
    형식을 알아볼 수 없으면 `None` — 호출자가 새로 만든다.
    """
    if isinstance(entry, str):
        return {"id": entry, "created": True}
    if isinstance(entry, dict) and isinstance(entry.get("id"), str):
        return {"id": entry["id"], "created": bool(entry.get("created", False))}
    return None


def _sessions_path(cfg: AppConfig) -> Path:
    return cfg.knowledge_path / "sessions.json"


def _load_sessions(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_sessions(path: Path, sessions: dict) -> None:
    path.write_text(json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8")


# --- 첨부 이미지 --------------------------------------------------------------
#
# **방향이 핵심이다.** `claude` 는 파일 경로로 이미지를 읽는다(실측: 합성
# 이미지의 도형 개수와 색을 정확히 서술했다). 그래서 이미지를 대화에 밀어
# 넣는 특별한 프로토콜이 필요 없고, 더 중요하게는 **쓰기 권한 벽을 피한다** —
# 예전에 `.qatc-tmp/` 가 거부됐던 것은 `claude` 가 거기에 **쓰려고** 했기
# 때문이다. 여기서는 백엔드가 쓰고 `claude` 는 읽기만 한다.
#
# 판정(무엇이 이미지인가)과 쓰기를 같은 파일에 두는 이유: 갈라 놓으면
# "media_type 을 믿지 않는다" 가 한쪽에서만 지켜진다.

#: 한 장의 상한. 화면 캡처 한 장이 이보다 클 이유가 없다.
_MAX_SHOT_BYTES = 8 * 1024 * 1024
#: 한 턴의 장수 상한.
_MAX_SHOTS_PER_TURN = 4

#: 첨부를 두는 폴더 (저장소 바로 아래). 턴마다 그 안에 하위 폴더를 판다.
#:
#: **저장소 안이어야 한다.** 자식은 `cwd=project_root()` 로 뜨고, 그 **밖**의
#: 파일은 `Read` 에 승인이 필요하다 — 헤드리스 실행에는 그 창에 답할 주체가
#: 없다. 실측(라이브 확인): `%TEMP%` 에 두었더니 진짜 `claude` 가 그 자리에서
#: 막혔고(`Claude requested permissions to read from ...`), 우회로 `cp` 도
#: Bash 가 거부했다("may only copy files to/from the allowed working
#: directories for this session"). 경로가 메시지에 실려 도착해도 열 수 없으면
#: 첨부는 아무 일도 안 한 것이다 — 그런데 단위 테스트는 전부 초록이었다,
#: 경로가 **닿는지**만 봤기 때문이다.
#:
#: **그리고 지식 루트 밖이어야 한다** (무쓰기 스냅숏 가드). 두 조건은 함께
#: 만족된다: 이 폴더는 `knowledge/` 의 형제다.
#:
#: 예전에 `.qatc-tmp/` 가 거부됐던 것과 헷갈리지 말 것 — 그때는 `claude` 가
#: 거기에 **쓰려고** 했다. 여기서는 백엔드가 쓰고 `claude` 는 읽기만 한다.
_SHOTS_DIRNAME = ".qatc-shots"

#: 덧붙인 경로 앞에 놓는 표시. `claude` 에게 이것이 무엇이고 무엇을 하라는
#: 것인지 한 줄로 말해 준다.
_SHOT_MARKER = "[첨부 이미지 — Read 로 확인할 것]"

#: (매직 바이트, 확장자). 확장자도 **여기서** 정한다 — 클라이언트가 준 이름을
#: 쓰지 않기로 한 이상, 확장자만 클라이언트에서 받아 올 이유가 없다.
_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
)

_SHOT_UNREADABLE_MSG = (
    "붙인 이미지를 읽을 수 없습니다. 화면을 다시 캡처해 붙여넣어 주세요."
)

_SHOT_NOT_AN_IMAGE_MSG = (
    "이미지 파일이 아닙니다 — PNG·JPEG·WebP 만 붙일 수 있습니다. "
    "화면을 다시 캡처해 붙여넣어 주세요."
)


def image_suffix(raw: bytes) -> str | None:
    """실제 바이트로 판정한 확장자. 이미지가 아니면 `None`.

    **`media_type` 을 믿지 않는다.** 그 값은 요청자가 정하는데, 이 경로의
    끝은 디스크에 떨어지는 파일이다 — "image/png" 라고 적힌 실행 파일을
    그대로 써 줄 이유가 없다.
    """
    for magic, suffix in _IMAGE_MAGIC:
        if raw.startswith(magic):
            return suffix
    # WebP 는 앞 네 바이트가 컨테이너 이름(RIFF)이라 그것만으로는 판정할 수
    # 없다 — 8..12 바이트의 형식 이름까지 봐야 한다.
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    return None


def decode_shots(items: object) -> tuple[list[bytes], str | None]:
    """요청이 실어 보낸 이미지를 검증하고 원시 바이트로 푼다.

    두 번째 값이 `None` 이 아니면 그것이 **사용자에게 그대로 보일 한국어
    거절 사유**다. 하나라도 걸리면 그 턴은 통째로 거절한다 — 일부만 붙은
    채로 도는 것이 사용자에겐 가장 헷갈린다(무엇이 빠졌는지 화면에 안
    나온다).
    """
    if items is None:
        return [], None
    if not isinstance(items, list):
        return [], _SHOT_UNREADABLE_MSG
    if len(items) > _MAX_SHOTS_PER_TURN:
        return [], (f"이미지는 한 번에 {_MAX_SHOTS_PER_TURN}장까지 붙일 수 있습니다. "
                    f"몇 장을 빼고 다시 보내세요.")
    out: list[bytes] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("data"), str):
            return [], _SHOT_UNREADABLE_MSG
        try:
            raw = base64.b64decode(item["data"], validate=True)
        except (binascii.Error, ValueError):
            return [], _SHOT_UNREADABLE_MSG
        if len(raw) > _MAX_SHOT_BYTES:
            return [], (f"이미지 한 장은 {_MAX_SHOT_BYTES // (1024 * 1024)}MB까지입니다. "
                        f"더 작게 잘라서 다시 붙여넣어 주세요.")
        if image_suffix(raw) is None:
            return [], _SHOT_NOT_AN_IMAGE_MSG
        out.append(raw)
    return out, None


def _write_shots(images: Sequence[bytes] | None) -> tuple[Path | None, list[Path]]:
    """이미지를 임시 폴더에 쓰고 `(폴더, 경로들)` 을 돌려준다.

    **저장 위치는 지식 루트 밖이다.** 안에 두면 "백엔드는 지식 DB 에 쓰지
    않는다" 를 디스크 바이트로 지키는 스냅숏 가드가 그 자리에서 실패한다 —
    그리고 그게 그 가드의 요점이다. 임시 폴더는 애초에 지식 루트와 겹치지
    않는다.

    턴마다 새 폴더를 판다: 이름이 겹칠 일이 없고, 정리가 폴더 하나 지우기로
    끝나며, 정리에 실패하더라도 다른 턴의 파일을 건드릴 수 없다.

    위치는 `_SHOTS_DIRNAME` 이 정한다 — 자식이 읽을 수 있으면서 지식 루트
    밖인 자리여야 하는 이유는 그쪽에 적었다.
    """
    if not images:
        return None, []
    shots_root = project_root() / _SHOTS_DIRNAME
    shots_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="turn-", dir=str(shots_root)))
    paths: list[Path] = []
    for raw in images:
        # **이름은 서버가 만든다.** 클라이언트가 준 이름을 쓰면 이 경로가
        # 통째로 요청자 제어가 된다. 확장자는 바이트로 판정한 값이다
        # (`decode_shots` 를 통과한 것만 여기 오므로 `None` 일 수 없지만,
        # 그 가정이 깨져도 엉뚱한 확장자를 만들지 않게 기본값을 둔다).
        path = directory / f"qatc-shot-{uuid.uuid4()}{image_suffix(raw) or '.png'}"
        path.write_bytes(raw)
        paths.append(path)
    return directory, paths


def _with_shot_paths(message: str, paths: Sequence[Path]) -> str:
    """사용자 문장 뒤에 첨부 경로를 덧붙인다. 첨부가 없으면 문장 그대로.

    문장이 **먼저** 온다. 경로 목록으로 시작하면 사용자가 실제로 한 말이
    파일 목록에 묻힌다.
    """
    if not paths:
        return message
    return message + "\n\n" + _SHOT_MARKER + "\n" + "\n".join(str(p) for p in paths)


def _discard_shots(directory: Path | None) -> None:
    """임시 폴더를 통째로 지운다. 실패해도 턴을 깨뜨리지 않는다.

    화면에 있던 것이 그대로 들어간 파일이다 — 남겨 두면 사용자가 한 번
    보여주고 끝냈다고 생각한 것이 임시 폴더에 계속 쌓인다.
    """
    if directory is None:
        return
    shutil.rmtree(directory, ignore_errors=True)


# --- 턴 스트리밍 --------------------------------------------------------------


def stream_turn(
    cfg: AppConfig,
    message: str,
    content: str | None,
    *,
    images: Sequence[bytes] | None = None,
    claude: str | list[str] | None = None,
) -> Iterator[ChatEvent]:
    """한 턴. 첨부 이미지가 있으면 임시 파일로 쓰고, 끝나면 반드시 지운다.

    첨부의 수명을 이 얇은 껍데기 하나가 통째로 쥔다. 두 가지 이유로 안쪽이
    아니라 여기다.

    1. 안쪽(`_stream_turn`)은 세션 복구 때문에 자식 프로세스를 **두 번** 띄울
       수 있다. 그 사이에 파일이 사라지면 재시도만 첨부 없이 도는데, 화면에는
       아무 표시도 안 난다.
    2. `finally` 이므로 소비자가 스트림을 도중에 끊어도(브라우저가 SSE 연결을
       끊으면 Werkzeug 가 이 제너레이터를 닫는다) 정리가 돈다. 화면에 있던
       것이 그대로 들어간 파일이라, "실패했을 때만 안 지워진다" 는 남겨 둘
       수 있는 종류의 빈틈이 아니다.
    """
    directory, paths = _write_shots(images)
    try:
        yield from _stream_turn(cfg, _with_shot_paths(message, paths), content,
                                claude=claude)
    finally:
        _discard_shots(directory)


def _stream_turn(
    cfg: AppConfig,
    message: str,
    content: str | None,
    *,
    claude: str | list[str] | None = None,
) -> Iterator[ChatEvent]:
    """`claude` 자식 프로세스로 한 턴을 보내고, 출력을 `ChatEvent` 로 바꿔 흘려보낸다.

    첫 턴은 `--session-id` 로 세션을 만들고, 그 다음 턴부터는 `--resume` 으로
    이어간다(둘 다 같은 id) — `session_ref_for` 참고.

    **진행 표시.** 이 스트림에는 `progress` 이벤트가 섞여 나온다(기동 직후 ·
    `system` 프레임 · 도구 호출 · 무음 하트비트). 아무 것도 확정하지 않는
    표시일 뿐이므로 소비자는 그것으로 턴을 끝내거나 패널을 다시 읽으면 안
    된다. `done` 뒤에는 절대 오지 않는다 — 끝난 턴이 계속 도는 것처럼
    보이면 그 표시는 거짓말이 된다.

    **복구.** `sessions.json` 은 "생성을 시도했다" 만 기억하지, 그 id 를 진짜
    `claude` 가 여전히 알고 있는지는 보장하지 않는다 — 사용자가 `~/.claude`
    상태를 지웠거나 세션이 오래돼 사라졌을 수 있다. 알 수 없는 `--resume`
    대상은 프레임 없이 죽지 않는다 — **정상적인 JSON `result` 프레임**으로
    `errors: ["No conversation found with session ID: ..."]` 를 담아 온다
    (실측: 재검토, 진짜 `claude` 로 확인). 재검토 2차: 그 판단을 렌더링된
    한국어 메시지 문자열 안에서 영어 원문을 다시 찾는 방식으로 하면
    안 된다 — 문구가 바뀌거나(claude 쪽 업데이트) 메시지를 줄이면(예: stderr
    꼬리 자르기) 조용히 깨진다. 그래서 분류는 `_result_events` 가 JSON
    프레임을 파싱하는 그 자리에서 끝내고(`_looks_like_unknown_session`),
    여기서는 `data["kind"] == "unknown_session"` 이라는 **코드** 만 본다.
    `ref.resume` 도 이 조건에 직접 건다 — 생성 턴엔 "재개 대상이 없다" 는
    개념 자체가 없으므로, 재검토를 안 받은 프레임이 우연히 이 모양이어도
    복구로 잘못 흘러가지 않는다. 이미 델타/도구 호출이 나온 뒤라면(재개
    자체는 성공했다는 뜻이므로) 다시 시도하지 않는다 — 이미 보여준 내용과
    겹치는 새 응답을 만드는 게 더 헷갈린다.

    재시도로 만든 새 세션 id 는 그 재시도가 실제 결과(델타·도구 호출·또는
    오류 없는 `done`)를 낼 때만 `sessions.json` 에 적는다 — 재시도조차
    곧바로 실패하면 아무것도 쓰지 않는다. 미리 써 두면, 실패가 연속될
    때마다 uuid 를 하나씩 버리게 되고 그 사이 원래 살아있었을 수도 있는
    id 의 기록까지 지워진다. `done` 을 빼면(델타/도구만 보면) 어시스턴트
    텍스트 없이 곧장 성공하는 재시도(재검토가 지적: `is_error: false` 인데
    델타·도구가 하나도 없는 결과)가 저장을 건너뛰어, 다음 턴이 여전히 낡은
    id 를 재개하려다 또 실패하고 — 매 턴 자식 프로세스를 두 번씩 태우며
    방금 막 만든 세션을 고아로 만든다.
    """
    ref = session_ref_for(cfg, content)
    if not ref.resume:
        _mark_session_created(cfg, content, ref.id)

    emitted_real = False
    stale_error: ChatEvent | None = None
    for ev in _attempt_turn(cfg, message, ref.id, ref.resume, claude):
        if ev.kind in ("delta", "tool"):
            emitted_real = True
        if (ref.resume and not emitted_real and ev.kind == "error"
                and ev.data.get("kind") == "unknown_session"):
            # 이 이벤트는 그 시도의 마지막 이벤트다(도달하면 `_attempt_turn`
            # 이 곧바로 `settled=True` 로 끝난다) — 사용자에게 곧바로 내지
            # 않고 들고 있다가 아래에서 복구할지 정한다. 여기서 `return` 으로
            # 반복을 바로 끊으면 `_attempt_turn` 이 자기 마무리를 끝내기 전에
            # `GeneratorExit` 를 맞아 강제 종료된다 — `continue` 로 이어가
            # 스스로 `StopIteration` 으로 끝나게 둔다.
            stale_error = ev
            continue
        yield ev

    if stale_error is None:
        return

    reason = stale_error.data.get("message", "")
    _p(
        "이어가려던 세션을 찾을 수 없어 새 세션으로 다시 시작합니다"
        + (f" (원인: {reason})" if reason else ""),
        err=True,
    )
    # 위 `_p` 는 서버 콘솔로 간다 — 로컬 앱 사용자는 그 창을 보지 않는다.
    # 알리지 않으면 복구는 "모델이 방금 한 질문을 영문 없이 다시 한다" 로만
    # 나타난다. 그래서 화면에도 같은 사실을 내보낸다.
    yield ChatEvent("notice", {"kind": "session_restarted", "message": _RECOVERY_NOTICE})
    new_id = str(uuid.uuid4())
    retry_persisted = False
    for retry_ev in _attempt_turn(cfg, message, new_id, False, claude):
        if not retry_persisted and retry_ev.kind in ("delta", "tool", "done"):
            _mark_session_created(cfg, content, new_id)
            retry_persisted = True
        yield retry_ev


def _attempt_turn(
    cfg: AppConfig,
    message: str,
    session_id: str,
    resume: bool,
    claude: str | list[str] | None,
) -> Iterator[ChatEvent]:
    """`claude` 자식 프로세스를 한 번 실행하고 그 출력을 `ChatEvent` 로 흘려보낸다.

    `cwd=project_root()` 로 고정한다 — `.claude/settings.json` 의 Bash
    allowlist 는 그 디렉터리 기준으로 매칭되고, 헤드리스 실행에는 권한 승인
    창에 답할 UI 가 없으므로 allowlist 가 빗나가면 턴이 조용히 멈춘다.
    """
    session_flag = ["--resume", session_id] if resume else ["--session-id", session_id]
    base = resolve_claude(claude)
    if base is None:
        raise ClaudeMissing(_MISSING_MSG)
    cmd = base + [
        "-p",
        *session_flag,
        "--output-format", "stream-json",
        "--input-format", "stream-json",
        # 실측(라이브 스모크 테스트, 테스트가 아니라 진짜 `claude` 실행 파일로
        # 확인): `--print` 와 `--output-format=stream-json` 을 같이 주면
        # `--verbose` 없이는 `claude` 가 즉시 이 오류로 거부한다 —
        # "Error: When using --print, --output-format=stream-json requires
        # --verbose". 가짜 `claude` 는 인자를 검증하지 않으므로 테스트
        # 스위트는 이걸 잡을 수 없었다. 옆의 `--output-format` 과 중복처럼
        # 보여도 지우지 말 것 — 지우면 실제 대화가 3초 만에 죽는다.
        "--verbose",
        "--append-system-prompt", _skill_prompt(),
        "--model", "opus",
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=project_root(),
            env=_child_env(),
        )
    except FileNotFoundError as exc:
        raise ClaudeMissing(_MISSING_MSG) from exc

    _send_message(proc, message)

    # stderr 는 별도 스레드로 계속 비운다. 우리가 stdout 만 블로킹으로 읽는
    # 동안 자식이 stderr 파이프 버퍼를 채울 만큼 많이 쓰면, 아무도 안 읽는
    # stderr 에서 자식이 영원히 막혀 stdout 도 더는 진행되지 않는다(데드락).
    # 계속 비워 두면 그 위험이 없고, 덤으로 실패 원인을 진단에 쓸 수 있다.
    stderr_lines: list[str] = []
    stderr_thread = threading.Thread(
        target=_drain_stderr, args=(proc, stderr_lines), daemon=True)
    stderr_thread.start()

    # stdout 도 별도 스레드로 읽는다. 여기서 `for raw_line in proc.stdout` 로
    # 직접 읽으면 자식이 조용한 동안 그 줄에서 무한정 막혀 **하트비트를 낼
    # 수 있는 지점이 아예 없다** — 사용자가 "진행 중인지 멈춘 건지 모르겠다"
    # 고 한 구간이 정확히 그 블로킹이다. 읽기를 스레드로 옮기면 이 루프는
    # 시간제한 있는 큐 대기가 되고, 시간이 다 될 때마다 진행 표시를 낼 수
    # 있다. `proc.stdout` 을 논블로킹으로 바꾸는 방식은 쓰지 않는다 —
    # Windows 의 파이프에서 신뢰할 수 없다.
    stdout_lines: queue.Queue = queue.Queue()
    stdout_thread = threading.Thread(
        target=_pump_stdout, args=(proc.stdout, stdout_lines), daemon=True)
    stdout_thread.start()

    settled = False
    closing = False
    try:
        # 자식이 뜬 직후. 첫 프레임까지 수 초가 걸리므로 그 전에 화면이 한
        # 번 움직여야 한다. 이 `yield` 는 반드시 `try` **안**에 있어야 한다 —
        # 밖에 두면 소비자가 바로 여기서 스트림을 끊었을 때 아래 `finally`
        # 를 거치지 않아 자식 프로세스가 그대로 남는다.
        yield _progress(_PHASE_STARTING)
        while True:
            try:
                item = stdout_lines.get(timeout=_HEARTBEAT_SECONDS)
            except queue.Empty:
                # 침묵이 이어지는 중. 백엔드가 할 말이 없어도 화면은 움직여야
                # 한다 — 이것이 "멈춘 게 아니다" 의 유일한 증거다. 몇 번이든
                # 반복해서 낸다.
                yield _progress(_PHASE_WAITING)
                continue
            if item is _STDOUT_EOF:
                break
            line = item.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                # 한 줄이 stream-json 이 아니어도 턴 전체를 버리지 않는다 —
                # 이 줄만 건너뛰고 계속 읽는다.
                _p(f"claude 출력 중 JSON 이 아닌 줄을 건너뜁니다: {line!r}", err=True)
                continue
            for ev in _events_from(obj):
                yield ev
                if ev.kind in ("done", "error"):
                    settled = True
                    return
    except GeneratorExit:
        # 소비자가 스트림을 도중에 끊었다 — 예: 브라우저가 SSE 연결을 끊어
        # Werkzeug 가 이 제너레이터를 닫는 경우(실측: 재검토, `gen.close()`
        # 로 재현됨). `GeneratorExit` 를 처리하는 중에는 값을 만들어 낼 수
        # 없다 — 만들면 그 자체가 "RuntimeError: generator ignored
        # GeneratorExit" 다. 아래 `finally` 가 `settled=False` 인 채로 마지막
        # 오류를 다시 `yield` 하려던 것이 바로 그 상황이었다. 여기서는
        # 표시만 남기고 그대로 다시 던진다 — 자식 프로세스 정리는 `finally`
        # 가 한다.
        closing = True
        raise
    finally:
        _close(proc)
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        # 여기서 닫는 이유가 "안 그러면 파이프 쓰기 쪽이 아직 열려 있어서"
        # 는 아니다(재검토가 지적: 그 설명은 실제 위험을 과장한다) — 바로
        # 위 `_close(proc)` 가 이미 `proc.wait()` 로 자식을 회수했으므로,
        # 이 시점엔 파이프 쓰기 쪽도 이미 죽어 있고 `_drain_stderr` 는 EOF
        # 를 만나 스스로 끝나는 중이다(그래서 `join` 이 금방 끝난다). `join`
        # **뒤에** 닫는 진짜 이유는, 그 스레드가 아직 같은 파일 객체를
        # 읽고 있는 동안 메인 스레드가 끼어들어 닫지 않기 위해서다 — 안
        # 닫아 두면(어느 순서든) 이 파일 객체가 나중에 GC 로 정리될 때
        # "Exception ignored while finalizing file" 로 새어나간다(실측:
        # 재검토, 소비자가 스트림을 일찍 끊는 시나리오에서 재현됨).
        #
        # **이제 stdout 도 같은 처지다.** 예전엔 이 함수가 stdout 을 직접
        # 읽어서 `_close` 가 곧바로 닫아도 아무도 그 파일 객체를 안 보고
        # 있었지만, 지금은 `_pump_stdout` 스레드가 읽는다 — 그래서 stdout
        # 닫기도 `_close` 에서 여기로 옮겨, 두 파이프 모두 자기 스레드를
        # 거둔 뒤에만 닫는다.
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass
        if not settled and not closing:
            # done 도 error 도 없이 스트림이 끝났다 — 성공처럼 보이며 조용히
            # 멈추는 것이 이 프로젝트가 반복해서 당해 온 실패 모양이므로,
            # 프런트가 무한정 기다리지 않도록 명시적인 오류로 마무리한다.
            # 진짜 충돌(exit code != 0)과 그냥 멈춘 것을 구분할 수 있도록,
            # stderr 에 뭔가 남았으면 그 내용을 원인으로 함께 보여준다.
            detail = "".join(stderr_lines).strip()
            _p(
                "claude 프로세스가 done/error 없이 종료되었습니다."
                + (f" (stderr: {detail})" if detail else ""),
                err=True,
            )
            yield ChatEvent("error", {
                "kind": "error",
                "message": _unsettled_error_msg(detail),
            })


def _child_env() -> dict:
    """자식 프로세스의 표준입출력 인코딩을 UTF-8로 강제한다.

    Windows 콘솔은 기본 코드페이지(한국어 환경은 cp949)를 쓴다. 부모 쪽은
    `encoding="utf-8"` 로 디코딩을 고정했지만, 자식이 파이썬 프로세스라면
    (테스트의 가짜 `claude` 가 그렇다) 자기 표준출력을 그 코드페이지로
    인코딩해 한글이 깨진 바이트로 나가고, 부모의 UTF-8 디코딩이
    `UnicodeDecodeError` 로 죽는다. `PYTHONIOENCODING`/`PYTHONUTF8` 로
    자식의 인코딩을 명시하면 그 어긋남이 없어진다 (`console.py::_p` 가
    이미 겪은 것과 같은 cp949 문제의 자식 프로세스 버전).
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _base_cmd(claude: str | list[str] | None) -> list[str]:
    if claude is None:
        return ["claude"]
    if isinstance(claude, str):
        return [claude]
    return list(claude)


def resolve_claude(claude: str | list[str] | None = None) -> list[str] | None:
    """실제로 띄울 명령을 확정한다. 찾지 못하면 `None`.

    **`/api/health` 와 `_attempt_turn` 이 반드시 같은 이 함수를 써야 한다.**
    예전엔 헬스가 `shutil.which("claude")` 로 보고, 실행은
    `Popen(["claude"], shell=False)` 로 했다. Windows 에서 그 둘은 갈린다 —
    `which` 는 `PATHEXT` 를 존중해 `.cmd`/`.ps1` 셰임까지 찾지만,
    `CreateProcess` 는 확장자 없는 이름에 `.exe` 만 붙인다. npm 식 `.cmd`
    설치본에서는 배지가 **연결됨** 인데 모든 턴이 `FileNotFoundError` 로
    죽었다 (실측: `.cmd` 셰임을 PATH 에 두면 `which` 는 찾고
    `Popen(["이름"])` 은 `[WinError 2]` 로 실패한다).

    해결은 헬스를 실행에 맞춰 좁히는 쪽이 아니라 **실행을 헬스에 맞춰
    넓히는 쪽**이다. `which` 가 찾아 준 전체 경로를 그대로 `Popen` 에
    넘기면 `.cmd` 셰임도 실제로 실행된다 (실측: 같은 셰임을 전체 경로로
    주면 정상 실행). 그래서 `.cmd` 설치본이 "연결됨 인데 안 됨" 에서 그냥
    "된다" 로 바뀌고, 두 자리가 같은 함수를 부르므로 애초에 갈릴 수 없다.
    """
    cmd = _base_cmd(claude)
    found = shutil.which(cmd[0])
    if found is None:
        return None
    return [found, *cmd[1:]]


def _skill_prompt() -> str:
    path = project_root() / ".claude" / "skills" / "interview" / "SKILL.md"
    return path.read_text(encoding="utf-8")


def _send_message(proc: subprocess.Popen, message: str) -> None:
    payload = json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": message}]},
    }, ensure_ascii=False)
    try:
        proc.stdin.write(payload + "\n")
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass  # 자식이 stdin 을 안 읽고 먼저 끝났을 수 있다 — 출력 처리는 계속한다


_CLOSE_WAIT_TIMEOUT = 5.0  # 초 — 이 이상은 자식 종료를 기다리지 않는다.


def _close(proc: subprocess.Popen) -> None:
    """자식 프로세스를 정리한다.

    소비자가 스트림을 다 안 받고 끊어도(브라우저가 SSE 연결을 끊어 Werkzeug
    가 이 제너레이터를 닫는 경우) 자식이 계속 살아있게 두지 않는다.
    `proc.wait()` 를 시간제한 없이 부르면(실측: 재검토 — `gen.close()` 가
    12초가 지나도 안 끝났고 자식은 그때까지 살아 있었다) 이 스레드가
    영원히 막히고 `claude` 프로세스가 좀비로 남는다. 일정 시간 기다려도 안
    끝나면 강제로 죽인다.

    **파이프는 여기서 닫지 않는다.** 지금은 stdout·stderr 를 각각 별도
    스레드가 읽으므로, 그 스레드가 아직 읽고 있는 파일 객체를 이 함수가
    닫으면 그 스레드 안에서 터진다. 닫기는 호출자(`_attempt_turn` 의
    `finally`)가 두 스레드를 `join` 한 뒤에 한다. 자식을 회수(또는 강제
    종료)하면 두 파이프 모두 EOF 가 되어 스레드들이 스스로 끝나므로, 이
    함수가 파이프를 닫아 줘야 그 스레드들이 풀리는 것도 아니다.
    """
    try:
        proc.wait(timeout=_CLOSE_WAIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _pump_stdout(stream, sink: queue.Queue) -> None:
    """`proc.stdout` 을 끝까지 읽어 큐로 넘긴다. 별도 스레드에서 돈다.

    마지막에 반드시 `_STDOUT_EOF` 를 하나 넣는다 — 그 표식이 없으면 자식이
    끝난 뒤에도 소비 루프가 "그냥 조용한 것" 과 구별하지 못해 하트비트만
    영원히 내보낸다.
    """
    try:
        if stream is not None:
            for raw_line in stream:
                sink.put(raw_line)
    except (OSError, ValueError):
        pass
    finally:
        sink.put(_STDOUT_EOF)


def _drain_stderr(proc: subprocess.Popen, sink: list[str]) -> None:
    """`proc.stderr` 를 끝까지 읽어 `sink` 에 쌓는다. 별도 스레드에서 돈다.

    stdout 을 읽는 메인 루프와 동시에 돌아야 한다 — stdout 을 다 읽은 뒤에
    읽기 시작하면, 그 사이 자식이 stderr 파이프를 채워 막혔을 때 이미
    늦는다.
    """
    try:
        if proc.stderr:
            for line in proc.stderr:
                sink.append(line)
    except (OSError, ValueError):
        pass


_UNSETTLED_MSG = (
    "claude 프로세스가 응답을 끝까지 보내지 않고 종료되었습니다."
)


def _unsettled_error_msg(stderr_detail: str) -> str:
    msg = _UNSETTLED_MSG
    if stderr_detail:
        tail = stderr_detail[-500:]     # 너무 길면 마지막 부분만
        msg += f" (원인: {tail})"
    return msg + " 잠시 후 다시 시도하세요."


# --- stream-json → ChatEvent -------------------------------------------------


def _events_from(obj: dict) -> Iterator[ChatEvent]:
    obj_type = obj.get("type")
    if obj_type == "assistant":
        yield from _assistant_events(obj)
    elif obj_type == "result":
        yield from _result_events(obj)
    elif obj_type == "system":
        # 실측: 진짜 스트림은 `assistant` 가 아니라 `system` 프레임
        # (`hook_started`·`init` 등) 으로 시작한다. 예전엔 "모르는 type" 이라
        # 그냥 버렸는데, 그 프레임이야말로 **자식이 살아 있다는 가장 이른
        # 증거**다 — 버리면 그 구간이 통째로 침묵이 된다. 내용을 해석할
        # 필요는 없다. 도착했다는 사실만으로 충분하다.
        yield _progress(_PHASE_PREPARING)


def _assistant_events(obj: dict) -> Iterator[ChatEvent]:
    for item in obj.get("message", {}).get("content", []):
        item_type = item.get("type")
        if item_type == "text":
            text = item.get("text", "")
            if text:
                yield ChatEvent("delta", {"text": text})
        elif item_type == "tool_use":
            name = item.get("name", "")
            # 이름을 그대로 싣는다. 고정 문구("도구 실행 중")로 뭉개면, 오래
            # 걸리는 턴에서 사용자가 **무엇 때문에** 기다리는지 알 수 없다 —
            # 진행 표시가 있으나 마나가 되는 지점이 정확히 거기다.
            yield _progress(f"{name} 실행 중" if name else "도구 실행 중")
            yield ChatEvent("tool", {
                "name": name,
                "summary": _tool_summary(item.get("input", {})),
            })


def _tool_summary(tool_input: dict) -> str:
    if "command" in tool_input:
        return str(tool_input["command"])
    return json.dumps(tool_input, ensure_ascii=False)


def _result_events(obj: dict) -> Iterator[ChatEvent]:
    if not obj.get("is_error"):
        yield ChatEvent("done", {})
        return
    # 성공한 것처럼 보이는 유일한 실패 모양이 401 이다 — `subtype` 만 보면
    # (`"success"`) 이 결과가 성공한 턴으로 오분류된다. `api_error_status`
    # 를 반드시 함께 봐야 하고, 401 이 아닌 다른 실패를 401 로 오분류해서도
    # 안 된다 (재인증 안내는 원인이 다를 때 사용자를 엉뚱한 길로 보낸다).
    if obj.get("api_error_status") == 401:
        yield ChatEvent("error", {"kind": "auth", "message": _AUTH_ERROR_MSG})
        return
    # 재검토: "세션 없음" 분류는 이 JSON 프레임을 파싱하는 바로 이 자리에서
    # 끝낸다 — `data["kind"]` 라는 코드로 내보내고, 소비자(`stream_turn`)는
    # 그 코드만 본다. 예전엔 렌더링된 한국어 메시지 문자열 안에서 영어
    # 원문을 다시 부분 문자열로 찾았는데, 그러면 렌더링 문구가 바뀌거나
    # stderr 꼬리를 자르기만 해도 판정이 조용히 깨진다(실측: 재검토가
    # 메시지 꼬리를 짧게 줄이는 것만으로 그 경로의 복구를 깼다). `resume`
    # 턴인지는 이 함수가 모른다 — 그 제약은 호출자가 건다(아래 `_looks_
    # like_unknown_session` 의 docstring 참고).
    if _looks_like_unknown_session(obj):
        yield ChatEvent("error", {
            "kind": "unknown_session",
            "message": _generic_error_msg(obj),
        })
        return
    yield ChatEvent("error", {"kind": "error", "message": _generic_error_msg(obj)})


def _looks_like_unknown_session(obj: dict) -> bool:
    """이 `result` 오류 프레임이 "그런 세션 없음" 인지 판정한다.

    이 판정 자체는 `resume` 여부를 모른다 — "재개 턴에서만 복구를 건다"
    는 제약은 `stream_turn` 이 별도로 지킨다(`ref.resume` 을 그 조건에
    직접 넣는다). 여기서까지 `resume` 으로 걸러 버리면, "그 가드를 빼도
    되는지" 를 이 함수만으로는 검사할 수 없어진다 — 가드가 두 곳에 나뉘어
    있어야 각각 독립적으로 뮤테이션 검증이 가능하다.

    1차: `errors` 배열 안에서 실측 문구를 직접 찾는다.

    2차(문구가 바뀌어도 대비): claude 가 모델을 한 번도 부르지 않고 인자
    단계에서 곧바로 거부한 것처럼 보이는 구조적 특징 — 턴 진행 0, 비용 0,
    401 이 아님 — 이면 "혹시" 세션 문제로 보고 새 세션으로 한 번 더
    시도한다. 문구가 아예 안 맞아도 이 폴백 덕분에 최악의 경우가 "비용
    0인 턴을 하나 헛되이 더 시도한다" 로 끝난다 — 문구 하나 어긋났다고
    컨텐츠가 영영 막히는 것보다 훨씬 낫다.
    """
    errors = obj.get("errors")
    if isinstance(errors, list) and any(_UNKNOWN_SESSION_MARKER in str(e) for e in errors):
        return True
    return (
        obj.get("num_turns") == 0
        and obj.get("total_cost_usd") == 0
        and obj.get("api_error_status") is None
    )


def _generic_error_msg(obj: dict) -> str:
    detail = obj.get("result") or _first_error_text(obj) or "원인을 알 수 없습니다"
    return f"claude 실행 중 오류가 발생했습니다: {detail}. 잠시 후 다시 시도하세요."


def _first_error_text(obj: dict) -> str:
    """`result` 키가 없는 오류 프레임에서 원인을 뽑는다.

    실측(재검토): 알 수 없는 `--resume` 대상 같은 일부 오류는 `result` 없이
    `errors` 배열만 온다(`"errors": ["No conversation found with session
    ID: ..."]`). `obj.get("result")` 만 보면 이 원인이 "원인을 알 수
    없습니다" 로 뭉개져 사용자가 진짜 원인을 못 본다 — 복구(재시도)가 안
    먹혔을 때 특히 중요하다, 그게 사용자가 보는 유일한 설명이므로.
    """
    errors = obj.get("errors")
    if isinstance(errors, list) and errors:
        return str(errors[0])
    return ""

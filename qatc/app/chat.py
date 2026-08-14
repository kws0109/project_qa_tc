"""`claude` 자식 프로세스 — 채팅 턴을 스트리밍한다.

로컬 `claude` 프로세스를 헤드리스로 띄워 대화를 이어간다. API 를 직접 부르지
않는 이유는 비용이다 — Anthropic API 로 직접 부르면 컨텐츠 하나에 수십 달러가
드는데, 사용자의 `claude` 구독으로 돌리면 이 앱이 공짜에 가깝다.

`qatc/app/views.py` 는 참조하지 않는다 — 이 모듈은 지식 DB 를 전혀 건드리지
않고, 오직 `claude` 자식 프로세스(그리고 그 프로세스가 스스로 부르는 `qatc`
CLI)와만 대화한다. 두 계층은 서로 몰라야 한다.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

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


class ClaudeMissing(Exception):
    """`claude` 실행 파일을 찾지 못했다. 메시지는 완성된 한국어 문장이다."""


@dataclass(frozen=True)
class ChatEvent:
    """`stream_turn` 이 흘려보내는 사건 하나.

    `kind` 는 `delta`(텍스트 조각) / `tool`(도구 호출) / `done`(턴 종료) /
    `error`(실패) 중 하나다.
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


# --- 턴 스트리밍 --------------------------------------------------------------


def stream_turn(
    cfg: AppConfig,
    message: str,
    content: str | None,
    *,
    claude: str | list[str] | None = None,
) -> Iterator[ChatEvent]:
    """`claude` 자식 프로세스로 한 턴을 보내고, 출력을 `ChatEvent` 로 바꿔 흘려보낸다.

    첫 턴은 `--session-id` 로 세션을 만들고, 그 다음 턴부터는 `--resume` 으로
    이어간다(둘 다 같은 id) — `session_ref_for` 참고.

    **복구.** `sessions.json` 은 "생성을 시도했다" 만 기억하지, 그 id 를 진짜
    `claude` 가 여전히 알고 있는지는 보장하지 않는다 — 사용자가 `~/.claude`
    상태를 지웠거나 세션이 오래돼 사라졌을 수 있다. 그런 경우 `--resume` 은
    (다른 자식 프로세스 인자 문제들과 같은 모양으로) delta/tool 하나 없이
    곧바로 죽는다. 그 자리에서 새 세션 id 로 딱 한 번 다시 시도한다 — 그냥
    포기하면 그 컨텐츠는 다시는 대화할 수 없는 상태로 영영 막히기 때문이다.
    이미 델타/도구 호출이 나온 뒤라면(즉 재개 자체는 성공했고 그 이후에
    죽은 것이라면) 다시 시도하지 않는다 — 이미 보여준 내용과 겹치는 새
    응답을 만드는 게 더 헷갈린다.
    """
    ref = session_ref_for(cfg, content)
    if not ref.resume:
        _mark_session_created(cfg, content, ref.id)

    emitted_real = False
    unsettled_error: ChatEvent | None = None
    for ev, unsettled in _attempt_turn(cfg, message, ref.id, ref.resume, claude):
        if ev.kind in ("delta", "tool"):
            emitted_real = True
        if unsettled and ref.resume and not emitted_real:
            # `unsettled` 는 그 시도의 마지막 이벤트에만 붙는다(`_attempt_turn`
            # 의 `finally` 가 만든 것) — 사용자에게 곧바로 내지 않고 들고
            # 있다가 아래에서 복구할지 그대로 낼지 정한다. 여기서 `return` 으로
            # 이 반복을 바로 끊으면 `_attempt_turn` 이 아직 `finally` 안에
            # 있는 채로 `GeneratorExit` 를 맞아 강제 종료된다 — 자기 `settled`
            # 판단을 끝내지 못한 채 죽으므로 `RuntimeError: generator ignored
            # GeneratorExit` 로 번진다(실측: 이 변경 직후 테스트 스위트에서
            # 재현됨). `continue` 로 반복을 이어가 `_attempt_turn` 이 스스로
            # 끝맺게(다음 `next()` 에서 `StopIteration`) 둬야 한다.
            unsettled_error = ev
            continue
        yield ev

    if unsettled_error is None:
        return

    reason = unsettled_error.data.get("message", "")
    _p(
        "이어가려던 세션을 찾을 수 없어 새 세션으로 다시 시작합니다"
        + (f" (원인: {reason})" if reason else ""),
        err=True,
    )
    new_id = str(uuid.uuid4())
    _mark_session_created(cfg, content, new_id)
    for retry_ev, _retry_unsettled in _attempt_turn(cfg, message, new_id, False, claude):
        yield retry_ev


def _attempt_turn(
    cfg: AppConfig,
    message: str,
    session_id: str,
    resume: bool,
    claude: str | list[str] | None,
) -> Iterator[tuple[ChatEvent, bool]]:
    """`claude` 자식 프로세스 한 번 실행. `(ChatEvent, unsettled)` 를 흘려보낸다.

    `unsettled` 는 done/error 프레임 하나 없이 스트림이 끝나 `stream_turn`
    이 스스로 만들어 낸 마지막 오류 이벤트에서만 참이다 — 그 값 자체는
    `ChatEvent.data` 에 넣지 않는다(프런트로 나가는 페이로드 모양을 이
    복구 판단 때문에 바꾸고 싶지 않다). `stream_turn` 은 이 표시로 "세션을
    못 찾아 곧바로 죽었을 가능성" 과 "정상적인 API/인증 오류"(둘 다 성공한
    JSON `result` 프레임으로 오므로 `unsettled=False`)를 구분해 후자는
    재시도하지 않는다.

    `cwd=project_root()` 로 고정한다 — `.claude/settings.json` 의 Bash
    allowlist 는 그 디렉터리 기준으로 매칭되고, 헤드리스 실행에는 권한 승인
    창에 답할 UI 가 없으므로 allowlist 가 빗나가면 턴이 조용히 멈춘다.
    """
    session_flag = ["--resume", session_id] if resume else ["--session-id", session_id]
    cmd = _base_cmd(claude) + [
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

    settled = False
    try:
        for raw_line in proc.stdout:
            line = raw_line.strip()
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
                yield ev, False
                if ev.kind in ("done", "error"):
                    settled = True
                    return
    finally:
        _close(proc)
        stderr_thread.join(timeout=5)
        if not settled:
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
            }), True


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


def _close(proc: subprocess.Popen) -> None:
    try:
        if proc.stdout:
            proc.stdout.close()
    except OSError:
        pass
    proc.wait()


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


def _assistant_events(obj: dict) -> Iterator[ChatEvent]:
    for item in obj.get("message", {}).get("content", []):
        item_type = item.get("type")
        if item_type == "text":
            text = item.get("text", "")
            if text:
                yield ChatEvent("delta", {"text": text})
        elif item_type == "tool_use":
            yield ChatEvent("tool", {
                "name": item.get("name", ""),
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
    else:
        yield ChatEvent("error", {"kind": "error", "message": _generic_error_msg(obj)})


def _generic_error_msg(obj: dict) -> str:
    detail = obj.get("result") or "원인을 알 수 없습니다"
    return f"claude 실행 중 오류가 발생했습니다: {detail}. 잠시 후 다시 시도하세요."

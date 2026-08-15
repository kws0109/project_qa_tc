# 앱 실사용 개선 4건 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 첫 실사용에서 드러난 네 가지를 고친다 — 진행 중인지 멈췄는지 모름 · 스크린샷을 못 넣음 · 슬롯을 늘릴 줄 모름 · 분모가 한도처럼 보임.

**Architecture:** 백엔드는 여전히 지식 DB 에 쓰지 않는다. 스크린샷은 백엔드가 임시 파일로 쓰고 `claude` 는 **읽기만** 한다 (쓰기 권한 벽을 피하는 핵심). 진행 표시는 새 SSE 종류 `progress` + 프런트 자체 타이머.

**Tech Stack:** Python 3.11+ · Flask · stdlib · Pillow (이미 설치됨) · pytest

## 근거 — 첫 실사용 실측

`로그인` 컨텐츠 인터뷰: 슬롯 10개(filled 8 · na 1 · unknown 1) → **TC 23건**. 잘 돌았다.
드러난 것:

- **`screen` 슬롯 하나에 화면 인벤토리 전체가 들어갔다** — *"1) 로그인 창: 입력 필드 2개…, 누를 수 있는 요소 4개(…"*. 사용자가 스크린샷 3장을 따로 저장해 두고 보면서 손으로 옮겼다.
- **`로그인` 의 유형이 `[]`** 다. 여섯 유형(가챠·편성·성장·던전·상점·임무) 중 어디에도 안 맞아 기본 10개만 받았다. 유형이 안 맞는 컨텐츠가 흔하다.
- **`qatc slot add` 가 `SKILL.md` 에 0회 등장한다.** 명령은 있고 동작하는데 스킬이 모른다. 라이브 테스트에서 모델이 한 번 쓴 것은 지시가 아니라 자체 판단이었다.
- 사용자 표현: *"진행하고 있는지 멈춘건지 구별이 불가능"*.

## Global Constraints

- Windows 전용. 경로는 `pathlib.Path`.
- 콘솔 출력은 `qatc/console.py` 의 `_p()` / `_p(msg, err=True)`. 맨 `print()` 금지.
- 테스트: `.venv\Scripts\python.exe -m pytest` — `-q` 를 더 붙이면 `-qq` 가 되어 개수 줄이 사라진다.
- **백엔드는 지식 DB 에 쓰지 않는다.** 행동 스냅숏 가드가 지킨다. 스크린샷 임시 파일은 **지식 루트 밖**에 둔다.
- `qatc/app/` 안의 어떤 파일도 지식 DB 쓰기 메서드 이름을 담지 않는다 (주석·도크스트링 포함).
- 진짜 Anthropic API 를 호출하지 않는다 (테스트). 가짜 `claude` 는 **argv 를 기록해 대조**한다 — 출력만 흉내내는 가짜는 계약을 검증하지 못한다는 것이 이 프로젝트에서 세 번 증명됐다.
- 사용자·화면에 보이는 문자열은 한국어. 오류는 다음 조치를 함께 알린다.
- 줄바꿈이 파일별로 다르다 — `.py`/`.js`/`.css` 는 LF, `SKILL.md`/`.gitignore` 는 CRLF. 복원 후 바이트와 줄바꿈을 각각 확인할 것. **미추적 파일은 `git diff` 가 아무것도 안 보여준다.**
- 커밋 메시지는 한국어. 마지막 줄에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- 시작 테스트 수: **537 passed**.
- **새 테스트는 전부 뮤테이션으로 검증한다.** 구현을 깨뜨려 그 테스트가 실패하는지 확인하고 **에디터로** 복원한다. `git checkout`/`stash`/`reset` 금지.

---

### Task 1: 진행 표시 — `progress` 이벤트와 경과 시간

**Files:**
- Modify: `qatc/app/chat.py`, `qatc/app/server.py`, `qatc/app/static/app.js`, `qatc/app/static/app.css`
- Modify: `tests/test_app_chat.py`, `tests/test_app_server.py`

**Interfaces:**
- Produces: `ChatEvent("progress", {"phase": "<한국어 한 줄>"})`

**왜 두 겹인가.** 프런트 자체 타이머(경과 초)는 **거짓말을 할 수 없다** — 백엔드가 아무 말이 없어도 화면은 움직인다. `progress` 이벤트는 *무엇을 하는 중인지* 를 알려준다. 둘 중 하나만으론 부족하다: 타이머만 있으면 "뭘 하는지" 모르고, 이벤트만 있으면 침묵 구간에서 화면이 멈춘다.

**침묵 구간이 진짜 문제다.** 실제 스트림은 `{"type":"system","subtype":"hook_started",...}` 프레임으로 시작하는데 지금은 **알 수 없는 type 이라 버린다.** 그게 바로 "자식이 살아 있다" 는 증거다. 그것을 `progress` 로 바꿔 내보낸다.

**단계 문구 (이 문자열을 그대로 쓴다):**

| 계기 | `phase` |
|---|---|
| 자식 프로세스 기동 직후 | `claude 를 깨우는 중` |
| `type == "system"` 프레임 | `준비 중` |
| `tool_use` | `<도구이름> 실행 중` (예: `Bash 실행 중`) |
| 무음 5초 경과 | `기다리는 중` (하트비트, 반복 가능) |

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_app_chat.py` 에 추가:

```python
def test_child_start_emits_a_progress_event_before_anything_else(cfg, tmp_path):
    """첫 프레임이 오기 전에도 화면이 움직여야 한다.

    실제 claude 는 기동에 수 초가 걸리고 그 사이 stdout 이 조용하다.
    사용자가 "진행 중인지 멈춘 건지 모르겠다" 고 한 구간이 정확히 여기다.
    """
    evs = list(stream_turn(cfg, "x", None, claude=_fake_claude(tmp_path, OK_LINES)))
    assert evs[0].kind == "progress"
    assert "깨우는" in evs[0].data["phase"]


def test_system_frames_become_progress_not_silence(cfg, tmp_path):
    """지금은 알 수 없는 type 이라 버리는 프레임이 살아있다는 증거다."""
    lines = [json.dumps({"type": "system", "subtype": "hook_started"},
                        ensure_ascii=False)] + OK_LINES
    evs = list(stream_turn(cfg, "x", None, claude=_fake_claude(tmp_path, lines)))
    phases = [e.data["phase"] for e in evs if e.kind == "progress"]
    assert "준비 중" in phases


def test_tool_use_names_the_tool_in_progress(cfg, tmp_path):
    evs = list(stream_turn(cfg, "x", None, claude=_fake_claude(tmp_path, OK_LINES)))
    phases = [e.data["phase"] for e in evs if e.kind == "progress"]
    assert any("Bash" in p and "실행" in p for p in phases)


def test_progress_never_arrives_after_done(cfg, tmp_path):
    """done 뒤에 진행 표시가 오면 화면이 끝난 턴을 계속 도는 것처럼 보인다."""
    evs = list(stream_turn(cfg, "x", None, claude=_fake_claude(tmp_path, OK_LINES)))
    kinds = [e.kind for e in evs]
    assert kinds.index("done") == len(kinds) - 1


def test_a_silent_child_still_produces_progress(cfg, tmp_path, monkeypatch):
    """5초 무음에도 하트비트가 나가야 한다 — 이것이 '멈춘 게 아니다' 의 유일한 증거다."""
    monkeypatch.setattr("qatc.app.chat._HEARTBEAT_SECONDS", 0.2)
    lines = ["__SLEEP__0.6"] + OK_LINES      # 가짜가 이 지시를 보고 잠든다
    evs = list(stream_turn(cfg, "x", None, claude=_fake_claude(tmp_path, lines)))
    phases = [e.data["phase"] for e in evs if e.kind == "progress"]
    assert "기다리는 중" in phases
```

`_fake_claude` 가 `__SLEEP__<초>` 줄을 만나면 그만큼 자도록 확장한다 (기존 시그니처·기록 동작은 유지).

`tests/test_app_server.py` 에 추가:

```python
def test_progress_reaches_the_browser_as_its_own_frame(app, monkeypatch):
    from qatc.app import chat as chat_mod

    def fake_stream(cfg, message, content, **kw):
        yield chat_mod.ChatEvent("progress", {"phase": "준비 중"})
        yield chat_mod.ChatEvent("done", {"changed": False})

    monkeypatch.setattr("qatc.app.server.stream_turn", fake_stream)
    text = app.test_client().post("/api/chat", json={"message": "x"}).get_data(as_text=True)
    assert "event: progress" in text
    assert "준비 중" in text
```

- [ ] **Step 2: 실패 확인 → 구현 → 통과 확인**

하트비트는 stdout 읽기를 블로킹하지 않는 방식으로 구현한다 (읽기 스레드 + 큐, 또는 타임아웃 있는 큐 대기). **`proc.stdout` 을 논블로킹으로 바꾸는 방식은 쓰지 말 것** — Windows 에서 신뢰할 수 없다.

프런트: 전송 시각을 기록해 **경과 초를 1초마다 갱신**하고, 마지막 `progress.phase` 를 함께 보여준다. `delta` 가 오기 시작하면 표시를 답변으로 대체하고, `done`/`error` 에서 지운다. CSS 애니메이션(점 세 개 또는 맥동)을 붙이되 `prefers-reduced-motion` 을 존중한다.

Run: `.venv\Scripts\python.exe -m pytest`
Expected: 537 + 6 = 543 passed

- [ ] **Step 3: 뮤테이션 검증**

| # | 뮤테이션 | 죽어야 하는 테스트 |
|---|---|---|
| M1 | 기동 시 `progress` 를 안 냄 | `test_child_start_emits_a_progress_event_before_anything_else` |
| M2 | `system` 프레임을 다시 버림 | `test_system_frames_become_progress_not_silence` |
| M3 | 하트비트 제거 | `test_a_silent_child_still_produces_progress` |
| M4 | `done` 뒤에도 `progress` 를 냄 | `test_progress_never_arrives_after_done` |
| M5 | 도구 이름 대신 고정 문구 | `test_tool_use_names_the_tool_in_progress` |
| M6 | SSE 에서 `progress` 를 안 내보냄 | `test_progress_reaches_the_browser_as_its_own_frame` |

- [ ] **Step 4: 커밋**

```bash
git commit -m "진행 중인지 멈췄는지 보이게 한다 — progress 이벤트와 경과 시간"
```

---

### Task 2: 스크린샷 첨부

**Files:**
- Modify: `qatc/app/chat.py`, `qatc/app/server.py`, `qatc/app/static/index.html`, `app.js`, `app.css`
- Modify: `tests/test_app_server.py`, `tests/test_app_chat.py`

**Interfaces:**
- `POST /api/chat` 이 `images: [{"data": "<base64>", "media_type": "image/png"}]` 를 선택적으로 받는다
- `stream_turn(cfg, message, content, *, images=None, claude=None)`

**핵심 — 왜 이 방식인가.** `claude` 는 **파일 경로로 이미지를 읽는다** (실측 확인: 합성 이미지의 도형 개수·색을 정확히 서술). 그래서 이미지를 대화에 밀어 넣는 특별한 프로토콜이 필요 없다.

그리고 이 방향이 **쓰기 권한 벽을 피한다.** `.qatc-tmp/` 가 거부됐던 것은 `claude` 가 **쓰려고** 했기 때문이다. 여기서는 **백엔드가 쓰고 `claude` 는 읽기만** 한다.

**검증 (전부 필수):**
- 매직 바이트로 실제 이미지인지 확인한다 (PNG/JPEG/WebP 만 허용). `media_type` 을 믿지 않는다.
- 한 장 8MB, 한 턴 4장 상한. 초과하면 한국어 400.
- **파일 이름을 클라이언트가 정하지 못한다.** 서버가 만든다.
- 저장 위치는 `knowledge_root` **밖** — 예: `%TEMP%` 하위 전용 폴더. 지식 루트에 두면 무쓰기 스냅숏 가드가 실패한다(그게 가드의 요점이다).
- 턴이 끝나면 지운다. 실패해도 지운다.

**메시지 조립:** 사용자 문장 뒤에 경로를 덧붙인다. 예:
```
<사용자 문장>

[첨부 이미지 — Read 로 확인할 것]
C:\...\qatc-shot-<uuid>.png
```

**개인정보 주의 (문서에 남길 것).** 화면에 있는 것이 그대로 전달된다. 첫 실사용의 스크린샷에는 실제 이메일 주소가 찍혀 있었다. 손으로 옮길 때는 무엇을 넘길지 골랐지만 첨부는 고르지 않는다. `README.md` 의 앱 절에 한 줄 넣는다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_attached_image_is_written_outside_the_knowledge_root(app, tmp_path, monkeypatch):
    """지식 루트에 쓰면 무쓰기 가드가 실패한다 — 그게 가드의 요점이다."""


def test_a_non_image_payload_is_rejected_in_korean(app):
    """media_type 을 믿지 않는다 — 매직 바이트로 판정한다."""


def test_too_many_images_are_rejected(app):
def test_an_oversized_image_is_rejected(app):
def test_the_client_cannot_choose_the_filename(app, monkeypatch):
def test_the_temp_image_is_deleted_after_the_turn(app, monkeypatch):
def test_the_image_path_reaches_the_child_argv_or_message(cfg, tmp_path):
    """가짜 claude 가 받은 메시지에 절대경로가 들어 있는지 확인한다."""
```

각 테스트의 본문은 기존 `tests/test_app_server.py` 의 관용구(스크래치 `cfg_env`, `os.startfile` 스텁 방식)를 따른다.

- [ ] **Step 2~4: 구현 · 통과 확인 · 뮤테이션**

Expected: 543 + 7 = 550 passed

| # | 뮤테이션 | 죽어야 하는 테스트 |
|---|---|---|
| M7 | 매직 바이트 검사 제거 | `test_a_non_image_payload_is_rejected_in_korean` |
| M8 | 저장 위치를 `knowledge_root` 로 | `test_attached_image_is_written_outside_the_knowledge_root` + 무쓰기 가드 |
| M9 | 장수 상한 제거 | `test_too_many_images_are_rejected` |
| M10 | 크기 상한 제거 | `test_an_oversized_image_is_rejected` |
| M11 | 클라이언트 파일명 사용 | `test_the_client_cannot_choose_the_filename` |
| M12 | 정리 생략 | `test_the_temp_image_is_deleted_after_the_turn` |
| M13 | 경로를 메시지에 안 붙임 | `test_the_image_path_reaches_the_child_argv_or_message` |

프런트: 붙여넣기(`paste`)와 끌어다 놓기(`drop`) 둘 다 받고, 채팅 로그에 썸네일을 남긴다 — 나중에 "이 슬롯 근거가 뭐였지" 를 되짚을 수 있어야 한다.

- [ ] **Step 5: 커밋**

---

### Task 3: `SKILL.md` 가 슬롯을 늘릴 줄 알게 한다

**Files:**
- Modify: `.claude/skills/interview/SKILL.md`, `README.md`
- Modify: `tests/test_interview_skill.py`

**실측 근거.** `qatc slot add` 가 `SKILL.md` 에 **0회** 등장한다. 스킬이 아는 명령은 `slot init`·`slot set`·`slot status`·`tc plan`·`tc add`·`tc list`·`export` 뿐이다. 명령 자체는 존재하고 동작한다.

기본 10개 + 유형별 4개이고 개수 제한 코드는 없다. 문제는 한도가 아니라 **스킬이 늘릴 줄 모른다**는 것이다.

**넣을 내용:**
- 기본 세트로 안 담기는 사실이 나오면 `qatc slot add <컨텐츠> <키> --hint "..." --family "<계열>"` 로 슬롯을 추가한다.
- `--family` 는 `FAMILY_META` 에 있는 계열만 받는다 (오타는 유효 목록과 함께 거부된다). 스킬에 **실제 계열 이름 목록**을 싣되, 그 목록이 코드와 일치하는지 테스트가 대조한다.
- 언제 추가하는가: 한 슬롯에 서로 다른 사실이 여러 개 쌓일 때, 또는 유형이 안 맞아 기본 세트만 받았을 때. **첫 실사용에서 `screen` 슬롯 하나에 화면 인벤토리 전체가 들어간 것이 정확히 그 신호다.**
- 언제 추가하지 않는가: 기존 슬롯을 더 자세히 쓰면 되는 경우. 슬롯이 늘면 분모가 늘어 진척이 흐려진다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_skill_knows_how_to_add_a_slot():
    text = SKILL.read_text(encoding="utf-8")
    assert "slot add" in text
    assert "--family" in text


def test_skill_family_list_matches_the_code():
    """스킬이 싣는 계열 이름이 FAMILY_META 와 어긋나면 tc add 가 거부한다."""
    from qatc.knowledge.gate import FAMILY_META
    text = SKILL.read_text(encoding="utf-8")
    for family in FAMILY_META:
        assert family in text, f"스킬이 모르는 계열: {family}"


def test_skill_says_when_not_to_add_a_slot():
    """무한정 늘리면 진척 표시가 무의미해진다."""
```

- [ ] **Step 2~4: 구현 · 통과 확인 · 뮤테이션**

Expected: 550 + 3 = 553 passed

`.claude/settings.json` 의 허용목록에 `slot` 계열이 이미 있으므로 새 항목은 필요 없다 — **확인하고 넘어갈 것.**

---

### Task 4: 분모가 한도처럼 보이지 않게 한다

**Files:**
- Modify: `qatc/app/views.py`, `qatc/app/static/app.js`, `app.css`
- Modify: `tests/test_app_views.py`, `tests/test_app_server.py`

`8/10 채움` 이 고정 상한처럼 읽힌다. 실제로는 유형과 `slot add` 로 늘어난다.

**최소한의 변경:** 트리 항목에 슬롯이 추가로 생겼는지 보이게 한다 — 기본 세트보다 많으면 그 사실을 표시한다(예: `8/14 채움 (+4 추가됨)`). `/api/tree` 의 컨텐츠 항목에 `base_total` 을 함께 실어 프런트가 계산 없이 판단하게 한다.

**과하게 하지 말 것.** 진척 자체는 여전히 `filled/total` 이다. 이건 "이 숫자는 늘 수 있다" 를 알리는 것뿐이다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_tree_reports_base_total_alongside_total(cfg):
    """유형·추가 슬롯으로 늘어난 만큼을 화면이 구분할 수 있어야 한다."""
    # 기본 10 + 유형 편성 4 = total 14, base_total 10
    # slot add 로 하나 더 = total 15, base_total 10
```

- [ ] **Step 2~4: 구현 · 통과 확인 · 뮤테이션**

Expected: 553 + 2 = 555 passed

---

## 완료 기준

- 전체 스위트 **555 passed** (숫자가 달라지면 사유를 보고한다)
- 진행 표시: 조용한 자식에도 화면이 움직이고, `done` 뒤에는 멈춘다
- 스크린샷: 지식 루트 밖에 저장되고, 턴 후 지워지고, 무쓰기 가드가 여전히 통과
- `SKILL.md` 가 싣는 계열 목록이 `FAMILY_META` 와 일치
- **라이브 확인**: 앱을 띄워 스크린샷 한 장을 붙여 한 턴을 돌리고, 진행 표시가 실제로 움직이는지 본다
- `<repo>\knowledge` 의 기존 `로그인` 데이터가 **손상되지 않았는지 확인** — 첫 실사용 결과물이다
- `git status --porcelain` 비어 있음
- 뮤테이션 22종 전부 검출

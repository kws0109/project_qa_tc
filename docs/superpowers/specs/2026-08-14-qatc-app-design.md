# QATC 스탠드얼론 앱 설계

**작성일:** 2026-08-14
**선행 설계:** [2026-08-14-interview-driven-tc-design.md](2026-08-14-interview-driven-tc-design.md)
**구현 기록:** [../2026-08-14-구현-기록.md](../2026-08-14-구현-기록.md)

## 목표

인터뷰·검토·산출을 한 창에서 한다. 지금은 Claude Code 세션에서 인터뷰하고, 무엇이
만들어졌는지 보려면 `qatc tc list` 를 치고, 결과물을 보려면 xlsx 를 따로 연다.
세 가지가 분리돼 있어서 **방금 만들어진 TC 가 쓸만한지 판단하는 순환이 느리다.**

3분할 로컬 웹앱으로 묶는다 — 왼쪽 트리, 가운데 채팅, 오른쪽 TC 검토.

## 제약

- Windows 전용. 사용자 1명(도구 소유자). 배포 계획 없음.
- 대화는 **Claude Code 구독**으로 처리한다. Anthropic API 를 호출하지 않는다.
  (선행 설계가 비용을 이유로 API 를 배제했고 그 판단은 유효하다.)
- 사용자에게 보이는 문자열은 한국어.
- 경로는 `pathlib.Path`. 콘솔 출력은 `qatc/console.py` 의 `_p()`.

---

## 1. 핵심 성질 — 읽기/쓰기 비대칭

```
┌──────────────────────────────────────────────────────────┐
│ 브라우저 (단일 페이지)                                     │
│  왼쪽: 트리      가운데: 채팅      오른쪽: TC 검토          │
└──────────────────────────────────────────────────────────┘
        │ GET /api/*          │ POST /api/chat (SSE)
        ▼                     ▼
┌──────────────────────────────────────────────────────────┐
│ 로컬 백엔드 (Python, Flask)                                │
│  · 읽기: KnowledgeStore ─────────────► SQLite  (읽기만)    │
│  · 쓰기: 없음  ★                                          │
│  · 채팅: claude 자식 프로세스 관리                          │
│  · 셸:  os.startfile(xlsx)                                │
└──────────────────────────────────────────────────────────┘
                              │
                              ▼
                    claude -p (구독 인증)
                              │
                              ▼
                    qatc CLI ──► SQLite  (쓰기, 게이트 통과)
```

**백엔드에 지식 DB 쓰기 경로가 하나도 없다.** 슬롯과 TC 의 모든 변경은 `claude` 가
`qatc` CLI 를 부르는 경로로만 일어난다.

(백엔드가 파일을 전혀 안 쓴다는 뜻은 아니다 — `/api/export` 는 xlsx 를 쓰고
세션 id 파일도 쓴다. 둘 다 SQLite 를 건드리지 않으므로 불변식 밖이다.)

이유 — 선행 설계의 단 하나의 load-bearing 불변식이 *근거 없는 TC는 만들어지지 않는다*
이고, 그것을 강제하는 것은 `qatc/knowledge/gate.py` 와 `qatc tc add` 두 관문이다.
백엔드가 `KnowledgeStore.add_testcase` 를 직접 부를 수 있게 되는 순간 그 관문 밖에
두 번째 쓰기 경로가 생긴다. 선행 브랜치에서 이 불변식은 **세 번** 다시 뚫렸고
(빈 문자열 → 제로폭 문자 → 한글 필러), 매번 "닫았다"고 판단한 다음 라운드에서
열렸다. 표면적을 늘리지 않는 것이 UI 편의보다 값어치가 크다.

이 성질은 화면 갱신 로직까지 단순하게 만든다 (§5).

**이 결정의 대가:** 오탈자 하나를 고치려 해도 채팅에 말해야 한다. 사용자가 그 대가를
알고 선택했다 (직접 편집 대신 채팅 지시). 나중에 뒤집고 싶어지면 그때는 "두 번째 쓰기
경로를 어떻게 게이트에 통과시킬 것인가"를 먼저 설계해야 한다.

---

## 2. 컴포넌트

| 파일 | 책임 | 아는 것 | 모르는 것 |
|---|---|---|---|
| `qatc/app/server.py` | 라우팅 · SSE · 정적 파일 | Flask, views, chat | SQLite 스키마 |
| `qatc/app/views.py` | SQLite → JSON | `KnowledgeStore`, `gate` | HTTP, `claude` |
| `qatc/app/chat.py` | `claude` 자식 프로세스 · 세션 · 스트림 파싱 | subprocess | SQLite, HTTP |
| `qatc/app/static/index.html` | 3분할 레이아웃 | — | — |
| `qatc/app/static/app.js` | fetch · SSE 수신 · 렌더 | API 모양 | — |
| `qatc/app/static/app.css` | 스타일 | — | — |
| `qatc/cli.py` | `qatc app` 명령 | server | — |

`views.py` 는 `claude` 를 모르고, `chat.py` 는 SQLite 를 모른다. 둘 다 독립적으로
테스트된다.

### 웹 프레임워크로 Flask 를 쓰는 이유

표준 라이브러리 `http.server` 로도 가능하지만 라우팅·SSE·동시 요청 처리를 직접 짜야
한다. "안정성은 새로 만들지 않은 코드에서 나온다"는 기준으로 Flask 를 택했다.
런타임 의존성이 2개(`openpyxl`, `pyyaml`)에서 3개가 된다.

**이 문단을 지우지 말 것** — 선행 브랜치에서 `pyyaml` 이 정확히 "왜 있는지 문서에
없는 의존성" 상태였고, 최종 리뷰가 "표시 전용 기능 하나가 런타임 의존성을 붙들고
있다"고 지적했다. 근거를 남겨야 나중에 뺄지 말지를 판단할 수 있다.

---

## 3. HTTP API

모든 응답은 `application/json`, UTF-8. 오류는 `{"error": "<한국어 문구>"}` 와 함께
4xx/5xx.

### `GET /`
`index.html`.

### `GET /api/tree`
왼쪽 패널 전체.

```json
{
  "db_mtime": {"starrail": 1755100000.0},
  "games": [
    {
      "game": "starrail",
      "contents": [
        {
          "name": "파티편성",
          "types": ["편성"],
          "filled": 4, "total": 14,
          "families": [
            {"family": "정상 경로", "planned": true,  "tc_count": 2, "withdrawn": false},
            {"family": "재화 부족", "planned": false, "tc_count": 0, "withdrawn": false,
             "blocked_by": "cost", "reason": "슬롯이 비어 있음"}
          ]
        }
      ]
    }
  ]
}
```

`withdrawn` 은 근거가 철회된 계열 (선행 설계의 `근거 철회됨`). `filled` 는 **FILLED
슬롯만** 센다 — UNKNOWN·NA 는 근거가 아니므로 진척에도 포함하지 않는다.

### `GET /api/content?game=<g>&name=<n>`
가운데·오른쪽이 함께 쓰는 상세.

```json
{
  "name": "파티편성", "game": "starrail", "types": ["편성"],
  "slots": [
    {"key": "core_action", "hint": "이 컨텐츠의 주 동작은 무엇인가",
     "family": "정상 경로", "status": "filled", "value": "파티에 캐릭터를 배치하고 저장한다"}
  ],
  "testcases": [
    {"id": "tc_...", "family": "정상 경로", "title": "파티 적용 정상",
     "kind": "정상", "priority": "High", "origin": "인터뷰",
     "precondition": "...", "steps": ["..."], "expected": ["..."],
     "rationale": "core_action 진술", "slot_keys": ["core_action"],
     "edited": false, "withdrawn": false}
  ]
}
```

`edited` 는 저장된 본문 해시가 `generated_hash` 와 다른 경우 — 사람이 손댄 TC 다.
오른쪽 패널이 이것을 표시하면 `generated_hash` 가 처음으로 프로덕션 소비자를 갖는다
(지금까지 읽는 코드가 없어 리뷰에서 "테스트 전용 아니냐"는 지적을 받았다).

### `POST /api/chat`
요청 `{"message": "...", "content": "파티편성" | null}`.
응답은 `text/event-stream`. 이벤트:

| event | data | 의미 |
|---|---|---|
| `delta` | `{"text": "..."}` | 어시스턴트 텍스트 조각 |
| `tool` | `{"name": "Bash", "summary": "qatc tc add ..."}` | 도구 호출 (진행 표시용) |
| `done` | `{"changed": true}` | 턴 종료. `changed` 면 프런트가 재조회 |
| `error` | `{"kind": "auth"\|"missing"\|"other", "message": "..."}` | §6 |

### `POST /api/export`
요청 `{"game": "...", "content": "..."}`. `qatc/export/tc_excel.py` 의
`export_tc_excel(content, testcases, skipped, out_path, withdrawn)` 을 직접 호출하고
`os.startfile()` 로 Excel 을 띄운다. 성공 `{"path": "..."}`, 실패는 §6.

CLI 를 자식 프로세스로 부르지 않고 함수를 직접 부르는 이유 — export 는 SQLite 를 읽기만
하므로 §1 의 비대칭을 깨지 않고, 함수 호출이 테스트하기 쉽다. `claude` 를 자식
프로세스로 두는 것은 인증과 대화 루프 때문이지 격리 때문이 아니다.

### `GET /api/health`
`{"claude": "ok"|"missing"|"unauthenticated", "knowledge_root": "..."}`.
앱 시작 시 1회, 그리고 `error(kind=auth)` 이후 재확인용.

---

## 4. 채팅 실행 계약

```
claude -p
  --session-id <uuid>                  세션 유지 (컨텐츠당 하나, 앱 재시작 시 복원)
  --output-format stream-json
  --input-format stream-json
  --append-system-prompt <SKILL.md 전문>
  --model opus
  cwd = 프로젝트 루트
```

**`cwd` 를 프로젝트 루트로 두는 것이 필수 조건이다.** `.claude/settings.json` 의
Bash allowlist 가 그 디렉터리에서만 적용되고, 헤드리스에는 권한 승인 창에 답할 UI 가
없다. allowlist 에 빠진 호출이 하나라도 있으면 그 턴이 조용히 멈춘다.
기존 테스트 `test_every_qatc_command_in_skill_is_registered` 가 SKILL.md 의 모든
호출을 allowlist 와 전수 대조하므로, 이 조건은 이미 봉인돼 있다.

`SKILL.md` 를 시스템 프롬프트로 주입하는 이유는 인터뷰 규칙의 출처를 하나로 두기
위해서다. 그 파일의 사실 주장(명령 이름·JSON 키·오류 문구)을 검증하는 기존 테스트
15개가 앱에도 그대로 유효하다.

세션 id 는 컨텐츠별로 `knowledge_root/sessions.json` 에 저장한다. 앱을 껐다 켜도
대화가 이어진다. `POST /api/chat` 의 `content` 가 `null` 인 경우(아직 컨텐츠를 고르지
않았거나 "새 컨텐츠를 시작하고 싶다" 같은 대화)는 `__default__` 키의 세션을 쓴다 —
그 턴에서 `slot init` 이 일어나면 다음 턴부터 해당 컨텐츠 세션으로 옮겨간다.

---

## 5. 화면 갱신

채팅 턴이 끝나면(`done`) 프런트가 `/api/tree` 와 `/api/content` 를 다시 가져온다.
응답의 `db_mtime` 이 이전과 같으면 렌더를 건너뛴다.

폴링도 파일 감시도 쓰지 않는다 — **쓰기가 채팅으로만 일어나므로 턴 종료가 "바뀌었을
수 있는 유일한 시점"이다.** §1 의 비대칭이 갱신 로직을 없애 준다.

---

## 6. 오류 처리

전부 화면에 한국어로 뜬다. 어느 것도 파이썬 예외 이름을 노출하지 않는다.

| 상황 | 감지 | 표시 |
|---|---|---|
| 토큰 만료 | stream-json 의 `is_error` + `api_error_status == 401` | "재인증이 필요합니다. 터미널에서 `claude` 를 실행해 로그인한 뒤 다시 시도하세요." |
| `claude` 실행 파일 없음 | 시작 시 `shutil.which` | "claude 를 찾을 수 없습니다. 채팅 없이 읽기 전용으로 계속합니다." — 트리·검토는 계속 동작 |
| xlsx 잠김 | `PermissionError` | "Excel 에서 이 파일을 닫고 다시 시도하세요: `<경로>`" |
| 포트 사용 중 | bind 실패 | 다음 포트를 자동으로 시도하고 실제 주소를 콘솔에 알림 |
| 지식 DB 없음 | 빈 목록 | "채팅에서 첫 컨텐츠를 시작하세요." |
| 자식 프로세스 비정상 종료 | 종료 코드 ≠ 0, `done` 없음 | "대화가 중단됐습니다. 다시 시도하세요." + 마지막 stderr |

**토큰 만료를 1순위로 둔다.** 그것이 유일하게 *성공한 것처럼 보이는* 실패이기
때문이다. 선행 브랜치의 `_p()` 크래시(작업은 성공했는데 성공 메시지가 프로세스를
죽임)와 같은 부류이고, 조용히 지나가면 사용자는 앱이 고장난 줄 안다.

---

## 7. 테스트 전략

| 대상 | 방법 | 핵심 |
|---|---|---|
| `views.py` | 스크래치 DB → JSON 단위 테스트 | `filled` 가 FILLED 만 센다 · `edited`/`withdrawn` 판정 |
| `chat.py` | `claude` 를 가짜 실행 파일로 치환 | 스트림 파싱 · **401 감지** · 세션 id 유지 · 비정상 종료 |
| `server.py` | Flask test client | 라우트별 응답 · SSE 프레임 · 오류 JSON |
| 통합 | 가짜 `claude` 로 한 턴 왕복 | `done` → 재조회 → 트리 갱신 |

진짜 API 호출은 어느 테스트도 하지 않는다. 가짜 `claude` 는 정해진 stream-json 을
표준출력으로 뱉는 작은 스크립트다.

**새 테스트는 전부 뮤테이션으로 검증한다.** 구현을 깨뜨려 그 테스트가 실제로 실패하는지
확인하고 복원한다. 선행 브랜치에서 "코드는 옳은데 테스트가 이름값을 못 하는" 결함이
**아홉 번** 나왔고 전부 뮤테이션으로만 잡혔다. 그중 하나는 태스크 리뷰 11번과 최종
리뷰 1차를 전부 통과했다.

---

## 8. 만들지 않는 것

인증 · 다중 사용자 · TC 직접 편집 · 실시간 협업 · 앱 안 엑셀 렌더링 · 테마 ·
슬롯 직접 편집 · 검색 · 필터.

엑셀은 렌더하지 않고 버튼으로 **진짜 Excel** 을 띄운다. 브라우저는 화면만 그리고
파일을 여는 것은 같은 PC 의 백엔드다 — 로컬 웹앱은 웹사이트가 아니므로 샌드박스가
이 경로에 관여하지 않는다.

---

## 9. 선행 작업

앱보다 먼저 붙인다. 둘 다 앱 워크플로가 매일 밟는 자리다.

### 9.1 `--game` 검증 + 기본 게임 (A+D)

- **A** — `--game` 을 `profiles/` 의 프로파일 이름과 대조한다. 지금은
  `slot init 테스트 --game starrial`(오타)이 rc=0 으로 유령 DB 를 만들고, 그 인터뷰는
  `qatc knowledge` 어디에도 안 보인다.
- **D** — 기본 게임을 config 에 저장한다. `qatc config --game starrail` 이후
  `slot init` 에서 `--game` 이 불필요해진다.

D 만 하면 오히려 위험하다 — 기본값에 오타가 들어가면 이후 모든 `slot init` 이 조용히
엉뚱한 DB 로 간다. A 가 D 의 전제다.

A 를 하면 `profiles/` 와 `pyyaml` 이 처음으로 동작을 강제하게 되어, 최종 리뷰가
지적한 "표시 전용 기능 하나가 런타임 의존성을 붙들고 있다"가 해소된다.

### 9.2 xlsx 잠김 처리

`qatc/export/tc_excel.py:158` 의 `wb.save(path)` 에 잠김 처리가 없다. 실측:

```
오류: PermissionError: [Errno 13] Permission denied: '...starrail_잠김_TC.xlsx'
rc=1
```

파이썬 예외 이름만 나오고 다음 조치가 없어 `cli_knowledge.py:9` 의 계약("오류는 다음
조치를 항상 함께 알린다")을 어긴다. 앱은 "TC 확인 → 엑셀 열기 → 돌아와 수정 지시 →
재생성"을 중심 워크플로로 삼으므로, Excel 을 닫는 것을 잊는 일이 상시로 발생한다.

---

## 10. 열려 있는 항목

| 항목 | 상태 |
|---|---|
| `claude` 토큰 만료 | 재인증 전까지 헤드리스 성공 호출 미확인. 메커니즘(OAuth=구독)은 확인됨. 구현 전 `claude -p "Reply with exactly: OK" --output-format json` 로 확인할 것 |
| `tc add --origin user` 비멱등 | 선행 브랜치에서 파킹. 앱이 USER TC 를 만들지 않으므로 지금은 무관 |
| `slot_keys` · `contents.game` 쓰기 전용 | `slot_keys` 는 `/api/content` 가 처음으로 읽는다 — 파킹 해소 |
| 브랜치 전략 | `feat/interview-tc` 가 아직 master 에 병합되지 않았다. 이 작업을 어디에 올릴지 사용자 결정 필요 |

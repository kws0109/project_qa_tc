# QATC — 게임 QA 테스트케이스 생성 도구

서브컬쳐 게임(원신, 붕괴:스타레일, 명조, 블루아카이브)의 QA 테스트케이스를
**근거가 붙은 Excel**로 만듭니다.

Windows 전용 · Python 3.11+ · 스탠드얼론 (서버 불필요)

---

## ⚠️ 현재 상태 — 방향 전환 중

이 저장소는 **두 방향의 코드가 함께 들어 있습니다.**

| | 이전 방향 (동작함) | 새 방향 (설계 완료, 미구현) |
|---|---|---|
| 입력 | 플레이 녹화 | 사용자와의 대화형 인터뷰 |
| 지식 출처 | 화면 전이 관측 | 사용자 진술 |
| TC 생성 | Anthropic API 직접 호출 | Claude Code 세션 |
| 코드 | `capture/` `record/` `analyze/` `icons/` `review/` `llm/` | `knowledge/` (예정) |

**새 방향의 설계는 [docs/superpowers/specs/2026-08-14-interview-driven-tc-design.md](docs/superpowers/specs/2026-08-14-interview-driven-tc-design.md)
에 있습니다.** 구현 계획 수립 단계이며, 아직 실행 가능한 명령은 없습니다.

이전 방향의 코드는 **삭제하지 않고 남겨 두었습니다.** 구현 계획에서 정리 시점을
잡습니다 — `export/excel.py` 가 `models.FlowGraph` · `storage.SessionStore` 에 묶여
있어 단순 삭제가 아니라 리팩터이기 때문입니다.

---

## 왜 방향을 바꿨는가

녹화 기반 파이프라인은 붕괴:스타레일에서 실측까지 마쳤습니다. 캡처도 OCR도
클러스터링도 동작했습니다. 문제는 **버튼의 기능은 실제로 눌러본 것에서만 알 수
있다**는 구조적 한계였습니다.

2026-08-13 실측 세션(87초, 메뉴 위주 플레이)에서:

```
검출된 UI 요소   1,358개 (노이즈 포함)
기능이 밝혀진 것      8개  (0.6%)
```

나머지는 "안 눌러봤으므로 미상"입니다. 전부 눌러 보는 것은 애초에 피하려던
수작업 그 자체입니다.

그래서 **사용자가 컨텐츠와 기능을 설명하면 도구가 대화로 정보를 끌어내
TC를 쓰는** 방향으로 전환했습니다.

---

## 실측으로 확인된 것 (녹화 방향)

이후 방향이 바뀌었지만, 아래는 실제 측정 결과라 기록해 둡니다.

| 항목 | 결과 |
|---|---|
| 캡처 백엔드 | WGC / DXGI / GDI 3종 모두 스타레일에서 PASS |
| 커널 안티치트 | WGC 캡처는 `mhyprot2.sys` 환경에서 정상 동작 |
| 입력 훅 | **관리자 권한이면 동작**, 일반 권한이면 Windows UIPI로 전면 차단 |
| OCR (RapidOCR 한국어) | 실제 게임에서 `아케론`·`어벤츄린`·`장낙천` 정확 인식 |
| Python 3.14 | 전 의존성 동작 (`paddlepaddle`만 불가 → RapidOCR로 대체) |

### 화면 식별 — 신호별 판별력

2026-08-13 세션의 화면 8개(28쌍)를 실측한 결과입니다. **개별 신호는 전부
무너지고 결합 점수만 분리해냅니다.**

| 신호 | 같음 min | 다름 max | 분리 간격 |
|---|---|---|---|
| cell | 0.464 | 0.565 | −0.101 |
| layout | 0.000 | 0.839 | −0.839 |
| struct | 0.069 | 0.054 | +0.015 |
| text | 0.333 | 0.267 | +0.067 |
| **결합** | **0.571** | **0.355** | **+0.216** |

같아야 할 4쌍이 전부 애매 구간(0.55~0.80)에 들어가 LLM 판별 대상으로 정확히
분류되었습니다. 즉 **판별기는 정상 동작했고**, API 키가 없어 LLM 판별이 돌지
않은 것이 화면 과분리의 원인이었습니다.

`combined_similarity` 의 텍스트 우선 가중치는 필드 이동 세션(HUD 고정)에서
뽑은 값입니다. 메뉴 화면에서는 **텍스트가 곧 데이터**라 이 가중치가 맞지
않습니다 — 파티 편성 화면 3개가 공유하는 토큰은 `파티/빠른편성/|파티/uid`
4개뿐이고 나머지는 전부 캐릭터 이름입니다.

---

## 미해결 버그 (녹화 파이프라인)

방향 전환으로 우선순위에서 내려갔지만 기록해 둡니다.

1. **`latest` 가 엉뚱한 세션을 고름** — `list_sessions()` 가 폴더 이름 역순
   정렬이라 `fixture_synthetic` 이 항상 1등 (`storage.py:437`).
   정렬 키를 `session.started_at` 으로 바꿔야 합니다.
2. **유령 전이 34%** — `_maybe_idle_snapshot()` 에 입력 직후 억제 구간이 없어
   전이 애니메이션을 "자동 진행"으로 오인 (`recorder.py:237`). 실측 전이 32건 중
   11건이 이것이었습니다. 쿨다운 중 `_last_idle_ref` 를 갱신하지 않아 두 번째
   오발이 사실상 보장됩니다.
3. **`ctrl_l` 이 `ignore_keys` 를 통과** — `is_ignored_key()` 가 완전일치라
   `ignore_keys: [ctrl]` 에 안 걸립니다 (`profiles.py:62`). 같은 파일의
   `canonical_modifier()` 를 쓰면 됩니다.
4. **클릭 좌표가 전부 "불확실"로 표시** — `click_coords_reliable()` 이 수식키
   상태만 봐서 메뉴 클릭까지 걸립니다 (`profiles.py:71`). 실측 11건 중 실제
   포인터 잠금은 1건뿐이었습니다.
5. **`Ctrl+C`(녹화 종료)가 게임 입력으로 기록됨** — 게임이 `C` 를 받아 캐릭터
   화면을 열어 가짜 상태·전이가 생성됩니다.

**아래는 새 방향에서도 고쳐야 합니다** — `export/excel.py` 에 openpyxl 셀 값
sanitize 가 없어 제어문자가 들어오면 `IllegalCharacterError` 로 export 전체가
실패합니다. 새 도구도 같은 익스포트를 씁니다.

---

## 안전 원칙 — 관찰만 하고 개입하지 않습니다

대상 게임 중 원신/스타레일(`mhyprot2.sys`), 명조(ACE)는 커널 레벨 안티치트가
돕니다. 이 프로그램은 다음을 **절대 하지 않습니다**:

- 입력 주입 (`SendInput`) · 프로세스 메모리 접근 · DLL 인젝션 · DirectX 오버레이 후킹

사용하는 것은 OBS·Discord와 동일한 OS 표준 경로뿐입니다 —
`SetWindowsHookEx` 읽기 전용 저수준 훅 + Windows Graphics Capture.

---

## 설치

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
pip install -e ".[capture,gui]"     # 캡처 가속 + 리뷰 GUI
```

Python 3.14까지 검증했습니다. 선택 의존성이 없어도 동작합니다.

> `paddlepaddle` 은 Python 3.14 휠이 없어 **PaddleOCR 대신 RapidOCR** 을 씁니다.
> 같은 PP-OCR 모델을 ONNX로 쓰므로 정확도는 같고 용량은 1/3입니다.
> 한국어 모델은 첫 실행 시 자동으로 내려받습니다(약 23MB).

---

## 지금 동작하는 명령 (녹화 방향)

> ⚠️ **게임이 관리자 권한으로 실행 중이면 녹화도 관리자 권한이어야 합니다.**
> Windows UIPI가 저수준 입력 훅을 차단해 클릭·키 입력이 하나도 기록되지 않습니다
> (화면 캡처는 정상 동작하므로 겉보기엔 녹화되는 것처럼 보입니다).

```bash
qatc record --profile starrail   # 플레이 녹화 (F9 북마크, F10 일시정지, Ctrl+C 종료)
qatc analyze <세션ID>             # 화면 상태와 전이 추출
qatc name    <세션ID>             # LLM으로 화면 이름 붙이기 (API 키 필요)
qatc review  <세션ID>             # 리뷰 GUI
qatc tc      <세션ID>             # 테스트케이스 생성 (API 키 필요)
qatc export  <세션ID>             # xlsx + 다이어그램
qatc list                        # 세션 목록
```

**`latest` 는 쓰지 마세요** — 위 버그 1번 때문에 엉뚱한 세션을 고릅니다.
세션 ID를 명시하세요.

### 진단 도구

```bash
python scripts/spike.py --profile starrail        # 캡처·OCR·훅·변동성 4항목 검사
python scripts/diag_input.py --profile starrail   # 입력이 어디서 사라지는지 추적
```

`diag_input.py` 는 훅이 못 받은 것(권한 문제)과 레코더가 버린 것(코드 버그)을
구분해 줍니다.

---

## 개발

```bash
.venv\Scripts\python.exe -m pytest        # 테스트 (녹화 파이프라인 기준)
```

`.claude/settings.json` 에 `qatc` 하위명령용 Bash allowlist가 들어 있습니다.
새 방향에서 Claude Code가 인터뷰를 진행할 때 매 슬롯 기록마다 권한 승인 창이
뜨지 않도록 하는 전제 조건입니다.

`sessions/` 는 `.gitignore` 에 있습니다 — 녹화 데이터는 커밋되지 않습니다.

# QATC — 게임 QA 테스트케이스 생성 도구

서브컬쳐 게임(원신, 붕괴:스타레일, 명조, 블루아카이브)의 QA 테스트케이스를
**근거가 붙은 Excel**로 만듭니다.

Windows 전용 · Python 3.11+ · 스탠드얼론 (서버 불필요)

---

## 어떻게 동작하는가 — 인터뷰 기반

플레이 화면을 녹화·분석해 TC를 추출하는 대신, **Claude Code 세션에서 사용자와
대화하며 컨텐츠 지식을 슬롯에 채우고, 채워진 슬롯에서만 테스트케이스를
생성**합니다.

```
사용자가 컨텐츠를 설명 → 지식 슬롯 채움 → 계열 게이트 통과 → TC 생성 → xlsx 출력
```

핵심 불변식은 하나입니다 — **근거 없는 TC는 만들어지지 않습니다.** 어떤
테스트케이스 계열을 만들 수 있는지는 Claude가 아니라 `qatc tc plan` 이 슬롯
충족 여부를 보고 판정합니다 (`qatc/knowledge/gate.py`). 만들 수 없는 계열은
`qatc tc add` 가 거부합니다.

인터뷰 진행 방법은 [`.claude/skills/interview/SKILL.md`](.claude/skills/interview/SKILL.md)
에 있습니다. 설계 배경은
[docs/superpowers/specs/2026-08-14-interview-driven-tc-design.md](docs/superpowers/specs/2026-08-14-interview-driven-tc-design.md)
를 보세요.

이전에는 플레이를 녹화해 화면 전이를 관측하는 방식으로 시작했지만, **버튼의
기능은 실제로 눌러본 것에서만 알 수 있다**는 구조적 한계에 부딪혔습니다.
실측 세션에서 검출된 UI 요소 1,358개 중 기능이 밝혀진 것은 8개(0.6%)뿐이었고,
나머지를 전부 눌러 보는 것은 애초에 피하려던 수작업 그 자체였습니다. 그래서
**사용자가 설명하면 도구가 대화로 정보를 끌어내는** 지금 방향으로 바꿨습니다.
그 파이프라인의 코드는 삭제했습니다 — 필요하면 git 이력에서 볼 수 있습니다.

---

## 설치

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

런타임 의존성은 `openpyxl`(xlsx 출력)과 `pyyaml`(게임 프로파일 로딩)뿐입니다.

---

## 지금 동작하는 명령

```bash
qatc slot init <컨텐츠> --game starrail --types 편성   # 지식 슬롯 세트 생성
qatc slot status <컨텐츠> --json                        # 남은 항목 확인 (질문 전 매번 호출)
qatc slot set <컨텐츠> <키> --status filled --value "..."  # 슬롯 값 기록
qatc slot add <컨텐츠> <키> --hint "..." --family "..."    # 유형에 없던 슬롯 추가
qatc tc plan <컨텐츠>                                    # 만들 수 있는 TC 계열과 제외된 계열
qatc tc add <컨텐츠> --family "정상 경로" --origin interview --json -  # TC 저장
qatc tc list <컨텐츠>                                    # TC 목록 + 미충족 슬롯 리포트
qatc knowledge --game starrail                          # 게임별 컨텐츠 커버리지
qatc export <컨텐츠>                                     # xlsx 출력
qatc config                                              # 설정·프로파일 확인
```

`--game` 은 컨텐츠 이름이 한 게임의 지식 저장소에서만 발견되면 생략할 수
있습니다. 여러 게임에 같은 이름의 컨텐츠가 있으면 명시해야 합니다.

---

## 개발

```bash
.venv\Scripts\python.exe -m pytest
```

`.claude/settings.json` 에 `qatc` 하위명령용 Bash allowlist가 들어 있습니다.
Claude Code가 인터뷰를 진행할 때 매 슬롯 기록마다 권한 승인 창이 뜨지 않도록
하는 전제 조건입니다.

`sessions/` 는 `.gitignore` 에 있습니다 — 이전 녹화 파이프라인이 쓰던 폴더로,
지금은 아무 코드도 읽거나 쓰지 않습니다.

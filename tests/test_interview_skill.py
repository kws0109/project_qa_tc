import argparse
import json
import re
from pathlib import Path

import pytest

from qatc.cli import build_parser, main
from qatc.knowledge.models import SlotStatus

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude" / "skills" / "interview" / "SKILL.md"
README = ROOT / "README.md"


def test_skill_file_exists():
    assert SKILL.exists(), f"{SKILL} 가 없습니다"


def test_skill_has_frontmatter_name_and_description():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    head = text.split("---", 2)[1]
    assert re.search(r"^name:\s*interview\s*$", head, re.M)
    assert re.search(r"^description:\s*\S", head, re.M)


def test_skill_mandates_slot_status_before_asking():
    text = SKILL.read_text(encoding="utf-8")
    assert "qatc slot status" in text
    assert "질문" in text


def _subparser_choices(parser) -> dict:
    """argparse 파서에서 하위명령 이름 → 하위파서 매핑을 꺼낸다."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def test_every_qatc_command_in_skill_is_registered():
    """스킬이 없는 명령을 부르면 인터뷰가 첫 턴에 죽는다.

    스킬은 지시문이라 타입 검사도 import 오류도 이걸 잡아주지 않는다.
    """
    text = SKILL.read_text(encoding="utf-8")
    top = _subparser_choices(build_parser())
    assert top, "하위명령이 하나도 등록되지 않았습니다"

    # 스킬은 `.venv/Scripts/qatc.exe slot status` 와 `qatc slot status` 두 형태를
    # 모두 쓴다. `.exe` 를 선택적으로 허용해야 실제 호출을 검사할 수 있다.
    pattern = re.compile(r"qatc(?:\.exe)?\s+([a-z]+)(?:\s+([a-z]+))?")
    found = list(pattern.finditer(text))
    assert found, "스킬에 qatc 명령 호출이 하나도 없습니다"

    for m in found:
        cmd, sub = m.group(1), m.group(2)
        assert cmd in top, f"등록되지 않은 명령: qatc {cmd}"
        nested = _subparser_choices(top[cmd])
        if nested and sub:
            assert sub in nested, f"등록되지 않은 하위명령: qatc {cmd} {sub}"


def _executed_invocations(text: str) -> list[str]:
    """SKILL.md 에서 실제로 실행되는 명령줄만 골라낸다.

    구분 규칙: 실행 가능한 명령줄은 항상 allowlist 접두사와 같은 형태인
    `.venv/Scripts/qatc.exe ...` 로 시작한다 (예: 3단계의 코드펜스 안 호출들).
    반면 프로즈에서 명령을 언급할 때는 실행 파일 경로 없이 바로 `qatc ...`
    로 쓴다 (예: "절대 규칙"의 `qatc slot status` 문구, 서두의 `qatc tc plan`).
    후자는 셸에서 실행되지 않으므로 allowlist 매칭 대상이 아니다. 이 두
    형태의 어휘적 차이(경로 접두사 유무)로 실행줄만 골라낸다.
    """
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith(".venv/Scripts/qatc.exe")
    ]


def test_skill_uses_allowlisted_executable_form():
    """`.claude/settings.json` 의 권한 규칙은 명령 접두사로 매칭된다.

    스킬이 쓰는 호출 형태와 allowlist 접두사가 어긋나면 매 슬롯 기록마다
    승인 창이 떠서 인터뷰가 성립하지 않는다. 일부 명령(예: slot, tc)만
    하드코딩해서 확인하면 새로 추가된 호출(예: export)이 allowlist에서
    빠져도 잡아내지 못한다 — 스킬에 등장하는 실행 가능한 호출을 전부 뽑아
    하나도 빠짐없이 allowlist 접두사와 대조한다.
    """
    text = SKILL.read_text(encoding="utf-8")
    settings = Path(__file__).resolve().parents[1] / ".claude" / "settings.json"
    allow = json.loads(settings.read_text(encoding="utf-8"))["permissions"]["allow"]
    prefixes = [a[len("Bash("):-len(" *)")] for a in allow if a.startswith("Bash(")]

    invocations = _executed_invocations(text)
    assert invocations, "스킬에 실행 가능한 qatc 호출이 하나도 없습니다"

    for inv in invocations:
        assert any(inv.startswith(p) for p in prefixes), (
            f"allowlist에 없는 호출: {inv!r} — "
            f".claude/settings.json 의 permissions.allow 에 접두사를 추가하세요"
        )


# --- 문서가 주장하는 것과 코드가 하는 것 (Minor 15 · 16 · 18 · 20) --------


def test_skill_step1_branches_on_the_real_cli_messages(cfg_env, capsys):
    """1단계 분기는 CLI가 **실제로 내는 문구**를 근거로 갈라져야 한다.

    예전 SKILL.md 는 "컨텐츠가 없다는 오류가 나오면 → slot init" 한 줄뿐이었는데,
    신규 컨텐츠에서 가장 흔한 실제 출력은
    `'X' 컨텐츠를 가진 게임 DB가 없습니다. --game 으로 지정하세요.` 다 —
    **메시지 자신이 정반대(=--game 을 붙여 다시 부르라)를 지시**하므로 모델이
    턴을 하나 버린다. 세 문구를 실제로 만들어 스킬이 전부 다루는지 확인한다.
    문구를 고치면 이 테스트가 깨져 스킬도 함께 고치게 된다.
    """
    text = SKILL.read_text(encoding="utf-8")
    seen = []

    # (1) 지식 DB가 하나도 없을 때
    assert main(["slot", "status", "신규컨텐츠"]) == 1
    seen.append(capsys.readouterr().out.strip())

    # (2) DB는 있는데 어디에도 그 컨텐츠가 없을 때 — 신규 컨텐츠의 최빈 경로
    main(["slot", "init", "다른컨텐츠", "--game", "starrail"])
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다
    assert main(["slot", "status", "신규컨텐츠"]) == 1
    seen.append(capsys.readouterr().out.strip())

    # (3) --game 을 줬는데 그 컨텐츠가 없을 때
    assert main(["slot", "status", "신규컨텐츠", "--game", "starrail"]) == 1
    seen.append(capsys.readouterr().out.strip())

    assert len(set(seen)) == 3, seen      # 정말 서로 다른 세 문구인지
    for msg in seen:
        quoted = msg.replace("'신규컨텐츠'", "'<컨텐츠>'")
        assert quoted in text, f"SKILL.md 가 다루지 않는 1단계 문구: {quoted!r}"


def test_skill_step1_covers_the_ambiguous_content_message(cfg_env, capsys):
    """1단계 분기 (b) — 같은 이름의 컨텐츠가 두 게임 DB에 있을 때.

    위 테스트가 봉인한 세 문구와 달리 이 네 번째 문구는 어느 테스트도 잡고 있지
    않았다. 최종 게이트에서 문구를 `여러 게임에 존재합니다` 로 바꿔 봤더니
    **스위트가 그대로 초록이었다** (F4). 이 분기가 무너지면 모델은 게임이 둘인
    상황에서 무엇을 해야 하는지 모른 채 멈춘다.

    **바이트 단위로 통째로 비교할 수는 없다.** 실제 출력의 괄호 안에는 그 컨텐츠를
    가진 게임 목록(`(genshin, starrail)`)이 들어가는데 SKILL.md 는 그 자리를
    `(...)` 로 줄여 적었다. 그래서 고정된 부분만 못박는다:

    1. 괄호 **앞**의 문장 — 컨텐츠 이름만 자리표시자로 바꿔 SKILL.md 에서 찾는다.
    2. 괄호 **안**이 실제 게임 목록인지 — `(...)` 로 줄인 것이 무엇인지 확인한다.
    3. 괄호 **뒤**가 여전히 `--game` 을 지시하는지 — 이 문구의 값어치는 다음
       조치를 말해 주는 데 있다. 그게 사라지면 분기 설명도 근거를 잃는다.
    4. SKILL.md 의 그 분기 자체가 `--game` 을 붙여 다시 부르라고 말하는지.
    """
    for game in ("starrail", "genshin"):
        assert main(["slot", "init", "파티편성", "--game", game]) == 0
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다

    assert main(["slot", "status", "파티편성"]) == 1
    msg = capsys.readouterr().out.strip()

    head, opened, rest = msg.partition("(")
    games, closed, tail = rest.partition(")")
    assert opened and closed, f"괄호로 게임 목록을 싣지 않습니다: {msg!r}"
    assert sorted(g.strip() for g in games.split(",")) == ["genshin", "starrail"], msg

    step1 = _step1(SKILL.read_text(encoding="utf-8"))
    quoted = head.replace("'파티편성'", "'<컨텐츠>'") + "("
    assert quoted in step1, f"SKILL.md 1단계가 다루지 않는 문구: {quoted!r}"

    # 문구 자신이 다음 조치를 말해야 한다
    assert "--game" in tail, f"괄호 뒤가 --game 을 지시하지 않습니다: {msg!r}"

    # 그리고 그 분기가 --game 을 붙여 다시 부르라고 지시해야 한다
    lines = step1.splitlines()
    start = next(i for i, ln in enumerate(lines) if quoted in ln)
    branch = []
    for ln in lines[start:]:
        if branch and ln.startswith("- "):
            break
        branch.append(ln)
    assert "--game" in " ".join(branch), (
        f"1단계 (b) 분기가 --game 을 다시 부르라고 말하지 않습니다: {branch!r}"
    )


def test_skill_says_the_game_name_comes_from_the_user(cfg_env):
    """`<게임>` 을 어떻게 정하는지 1단계가 말해야 한다.

    1단계는 `--game` 없이 `slot status` 를 부른 뒤 곧바로
    `slot init ... --game <게임>` 을 요구한다. 출처가 없으면 모델이 추측한다.

    **이 도크스트링의 원래 근거는 이제 거짓이다** — "`--game` 은 `profiles/` 와
    대조되지 않으므로 오타가 조용히 새 DB를 만든다" 는 이 브랜치가 고친 두 결함을
    그대로 주장하고 있었다. 지금은 생성 명령이 대조하고, 읽기 명령은 없는 DB
    파일을 만들지 않는다. 그래도 이 테스트는 남는다: 오타가 거부된다는 것이
    **모델이 게임 이름을 지어내도 된다는 뜻은 아니기 때문**이다. 추측이 우연히
    등록된 다른 게임 이름과 맞으면 CLI 는 아무 이의도 제기하지 않고, 인터뷰는
    엉뚱한 DB 에 쌓인다. 출처는 사용자여야 한다.
    """
    text = SKILL.read_text(encoding="utf-8")
    step1 = text[text.index("## 1단계"):text.index("## 2단계")]
    assert "사용자에게 묻는다" in step1


def test_every_slot_status_choice_is_documented():
    """`--status` 의 선택지는 코드가 정한다 — 문서가 하나라도 빠뜨리면 안 된다.

    `--status empty` 는 실질적인 **되돌리기**인데 README·SKILL.md 어디에도
    없었다. `SlotStatus` 를 진실 원천으로 삼아 전수 대조한다.
    """
    skill = SKILL.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    for status in SlotStatus:
        assert f"--status {status.value}" in skill, f"SKILL.md: {status.value}"
        assert f"--status {status.value}" in readme, f"README: {status.value}"


def test_readme_tells_the_user_that_knowledge_output_is_gitignored():
    """`knowledge/` 는 사용자가 매일 만드는 산출물이 쌓이는 곳이다.

    README 는 `sessions/`(이제 아무도 안 쓰는 폴더)는 설명하면서 정작 이쪽은
    말하지 않았다.
    """
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "/knowledge/" in gitignore          # 전제가 사실인지 먼저 확인

    readme = README.read_text(encoding="utf-8")
    start = readme.index("## 산출물이 어디에 쌓이는가")
    section = readme[start:readme.index("---", start)]
    assert "knowledge/" in section
    assert ".gitignore" in section


# --- 설계 문서 §4 가 실제 계열 이름을 싣는다 (D1) -------------------------

DESIGN = ROOT / "docs" / "superpowers" / "specs" / "2026-08-14-interview-driven-tc-design.md"


def _table_rows(section: str) -> list[list[str]]:
    """마크다운 표에서 구분선과 헤더를 뺀 데이터 행."""
    rows = []
    for line in section.splitlines():
        if not line.startswith("|") or set(line) <= set("|- "):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append(cells)
    return rows[1:]          # 헤더 제외


def test_design_doc_family_table_matches_family_meta():
    """§4 의 `TC 계열 → TCKind` 표가 `FAMILY_META` 와 정확히 같아야 한다.

    §4 는 **`--family` 에 넣을 문자열을 찾으러 보는 표**다. 여기에
    `미해금 상태 접근` · `미저장 이탈 / 취소` 처럼 `tc add` 가 거부하는 이름이
    적혀 있으면 (실측으로 확인된 상태였다) 그대로 옮겨 적은 호출이 rc=1 로
    죽는다. 계열이 늘거나 이름이 바뀌면 이 테스트가 깨져 문서도 같이 고치게 된다.
    """
    from qatc.knowledge.gate import FAMILY_META

    text = DESIGN.read_text(encoding="utf-8")
    section = text.split("### TC 계열 → `TCKind` 대응", 1)[1].split("\n### ", 1)[0]

    doc = {}
    for family, kind, priority in _table_rows(section):
        doc[family] = (kind.strip("`"), priority)

    assert set(doc) == set(FAMILY_META), (
        f"문서에만: {sorted(set(doc) - set(FAMILY_META))} / "
        f"코드에만: {sorted(set(FAMILY_META) - set(doc))}"
    )
    for family, (kind, priority) in doc.items():
        real_kind, real_priority = FAMILY_META[family]
        assert real_kind.name == kind, family
        assert real_priority.value == priority, family


def test_design_doc_counts_the_family_table_correctly():
    """표 아래 문장이 세는 행 수가 실제 행 수·`FAMILY_META` 와 맞아야 한다.

    `중단` 행이 표에 추가되자 *"위 표 9행 + `중단` = 키 10개"* 가 그 행을 두 번
    세게 됐다 (표는 이미 10행이다). 집합 비교만으로는 이 문장이 틀린 것을
    잡지 못한다 — 산문에 박힌 숫자는 아무도 안 보기 때문이다. §4 는 계열이
    몇 개인지 확인하러 오는 자리라 이 숫자가 사실이어야 한다.
    """
    import re

    from qatc.knowledge.gate import FAMILY_META

    text = DESIGN.read_text(encoding="utf-8")
    section = text.split("### TC 계열 → `TCKind` 대응", 1)[1].split("\n### ", 1)[0]
    rows = _table_rows(section)

    claim = re.search(r"위 표 (\d+)행", section)
    assert claim, "표 행 수를 말하는 문장이 사라졌습니다"
    assert int(claim.group(1)) == len(rows) == len(FAMILY_META), (
        f"문장은 {claim.group(1)}행이라 하는데 실제 표는 {len(rows)}행 · "
        f"FAMILY_META 는 {len(FAMILY_META)}개입니다"
    )


def test_design_doc_slot_table_matches_the_base_slot_set():
    """§4 의 `슬롯 키 → 만드는 TC 계열` 표도 코드가 진실 원천이다.

    **계열 칸은 사람이 복사하는 그대로 비교한다.** 예전에는 `family.strip("*")`
    로 볼드를 벗겨내고 비교해서, `core_action` 행이 `**정상 경로**` 로 적혀
    있는데도 이 테스트가 초록이었다. §4 는 바로 위 노트에서 *"이 열의 이름이 곧
    `--family` 문자열이다"* 라고 말하는 자리라, 그대로 옮겨 적으면
    `tc add --family "**정상 경로**"` 가 `오류: 등록되지 않은 계열` rc=1 로 죽는다
    (실측). 강조를 벗기고 비교하는 테스트는 그 오해를 정확히 감춘다.

    슬롯 키의 백틱은 벗긴다 — 코드 스팬은 식별자를 감싸는 이 문서의 관용구고
    읽는 사람이 복사하는 것은 백틱 **안쪽**이다. 볼드는 이름 자체에 씌운
    강조라 다르다.
    """
    from qatc.knowledge.slots import BASE_SLOTS

    text = DESIGN.read_text(encoding="utf-8")
    section = text.split("### 기본 슬롯", 1)[1].split("\n### ", 1)[0]

    doc = {}
    for key, _asked, family in _table_rows(section):
        doc[key.strip("`")] = family

    code = {s.key: (s.tc_family or "(TC 없음 — 문맥)") for s in BASE_SLOTS}
    assert doc == code


# --- SKILL.md 1단계의 순서 (D5) ------------------------------------------


def _step1(text: str) -> str:
    return text[text.index("## 1단계"):text.index("## 2단계")]


def _flat(text: str) -> str:
    """줄바꿈을 접어 한 줄로 만든다.

    마크다운 산문은 줄 폭에 맞춰 감싸므로, 문장을 그대로 대조하려면 어디서
    감쌌는지까지 테스트가 알아야 한다. 그러면 줄 나누기만 바꿔도 테스트가
    깨지고, 사람은 단언을 약한 부분 문자열로 낮춰 버린다 — 이 파일이 앞선
    라운드에서 실제로 겪은 실패 방식이다.
    """
    return " ".join(text.split())


def _new_content_branch(step1: str) -> str:
    """`slot status` 가 "신규 컨텐츠" 라고 답했을 때 무엇을 하라고 적힌 분기.

    1단계는 `slot status` 출력별로 갈라지는 목록이고, **`slot init` 을 언제
    부를지는 이 분기 안에서만 정해진다.** 1단계 전체를 한 덩어리로 검사하면
    아래 "왜 개괄이 먼저인가" 근거 블록의 문구가 섞여 들어와, 정작 모델이
    따르는 지시문이 뒤집혀도 검사가 통과한다.
    """
    rest = step1[step1.index("신규 컨텐츠다"):]
    return rest[:rest.index("\n- **")]          # 다음 최상위 분기 직전까지


def test_skill_step1_tells_the_model_to_hold_slot_init_until_the_overview_answer():
    """신규 컨텐츠 분기는 `slot init` 을 **개괄 답변 뒤로 미루라고** 말해야 한다.

    라운드 1c 이전 판은 같은 자리에서 "게임 이름을 확보한 뒤 아래 `slot init`
    을 실행하고 개괄 질문으로 시작한다" · "곧장 `slot init` 으로 간다" 라고
    했다 — 설명을 듣기 전에 유형을 추측하라는 뜻이고, `slot init` 은 가산
    전용이라 (`qatc slot` 에 제거 명령이 없다) 그 추측은 되돌릴 수 없다.

    **문자열 오프셋 비교로는 이 결함을 잡을 수 없다.** 1c 가 고친 것은 산문이고
    `slot init` 코드 펜스는 원래부터 개괄 질문 뒤에 있었다 — 08aa57c 실측으로
    개괄 질문 1182 · "답변에서 유형을 판정한다" 1250 · 펜스 1357 이라 부등식
    셋이 모두 성립했다. 그래서 여기서는 지시문 자체를 **양방향으로** 고정한다:
    미루라는 문장이 있어야 하고, 지금 실행하라는 문장이 남아 있으면 안 된다.
    한쪽만 검사하면 두 지시가 동시에 적힌 모순된 개정을 통과시킨다.
    """
    branch = _new_content_branch(_step1(SKILL.read_text(encoding="utf-8")))

    assert "**`slot init` 은 아직 실행하지 말고**" in branch, (
        "신규 컨텐츠 분기에 `slot init` 을 개괄 뒤로 미루라는 지시가 없습니다"
    )
    # 인용된 CLI 오류 문구("...'qatc slot init ...'을 실행하세요")는 백틱이 없어
    # 여기 걸리지 않는다 — 모델에게 주는 지시문만 본다.
    for imperative in ("`slot init` 을 실행하고", "곧장 `slot init` 으로"):
        assert imperative not in branch, (
            f"개괄을 듣기 전에 실행하라는 지시가 살아 있습니다: {imperative!r}"
        )


def test_skill_step1_puts_the_slot_init_command_under_the_after_the_answer_heading():
    """`slot init` 명령줄은 "답을 들은 뒤" 라고 말하는 절에 있어야 한다.

    산문과 별개로, 명령을 옮겨 적는 쪽이 실제로 보는 것은 **코드 펜스가 어느
    절에 있는가**다. 소제목이 사라지거나 펜스가 개괄 질문 절로 올라가면 문서
    구조만 봐도 순서가 뒤집힌 것이므로 여기서 깨진다.
    """
    step1 = _step1(SKILL.read_text(encoding="utf-8"))

    fence = step1.index(".venv/Scripts/qatc.exe slot init")
    heads = re.findall(r"^### (.+)$", step1[:fence], re.M)
    assert heads, "`slot init` 명령줄 위에 소제목이 하나도 없습니다"
    assert "답을 들은 뒤" in heads[-1], (
        f"`slot init` 이 '{heads[-1]}' 절에 있습니다 — 답변을 들은 뒤라는 표시가 없습니다"
    )


def test_skill_step1_explains_that_slot_init_cannot_be_undone():
    """"왜 먼저인가"가 없으면 다음 개정에서 순서가 다시 뒤집힌다."""
    step1 = _step1(SKILL.read_text(encoding="utf-8"))
    assert "가산 전용" in step1
    assert "지우는 CLI 명령은 없다" in step1


def test_slot_has_no_command_that_removes_a_slot():
    """위 문서 주장의 전제 — `qatc slot` 에 제거 명령이 실제로 없어야 한다.

    나중에 `slot remove` 가 생기면 이 테스트가 깨지고, 그때 SKILL.md 의
    "지울 수 없다" 도 함께 고치게 된다.
    """
    top = _subparser_choices(build_parser())
    assert set(_subparser_choices(top["slot"])) == {"status", "init", "set", "add"}


def test_skill_tells_the_model_about_the_default_game(cfg_env, capsys):
    """기본 게임이 없을 때 CLI가 실제로 내는 오류를 스킬이 그대로 인용해야 하고,
    "먼저 `--game` 없이 시도하고, 실패할 때만 묻는다" 는 지시가 있어야 하며,
    "미리 물어본다" 는 옛 지시가 남아 있으면 안 된다.

    **부분 문자열만 보는 첫 판은 두 가지를 놓쳤다** (리뷰에서 재현됨).
    (1) 인용문을 실제로 실행하지 않고 옮겨 적었다면 CLI 문구가 바뀌어도 스킬은
    낡은 문구를 계속 인용한다 — 그럴듯하지만 존재하지 않는 문구로 바꿔도
    `"qatc config --game"` · `"기본 게임"` 부분 문자열은 여전히 있어 통과했다.
    (2) 이 태스크의 핵심 지시("먼저 생략하고 시도")를 옛 지시("미리 묻는다")로
    되돌려도, 손대지 않은 서두 문단에 `qatc config --game` 과 `기본 게임` 이라는
    낱말이 남아 있어 부분 문자열 검사가 그대로 통과했다 — 이 태스크의 존재
    이유가 없어졌는데도 초록이었다.

    그래서 셋을 각각 못박는다: 오류 문구는 **실행해서** 얻은 문자열과 바이트
    단위로 대조하고, 새 지시문은 있어야 하고, 옛 지시문은 없어야 한다
    (같은 두 방향 고정을 `test_skill_step1_tells_the_model_to_hold_slot_init_until_the_overview_answer`
    에서도 쓴다 — 한쪽만 보면 두 지시가 동시에 적힌 모순된 개정을 통과시킨다).
    """
    assert main(["slot", "init", "신규컨텐츠"]) == 1
    err = capsys.readouterr().out.strip()

    text = SKILL.read_text(encoding="utf-8")
    assert err in text, f"SKILL.md 가 실제 오류 문구를 그대로 인용하지 않습니다: {err!r}"

    step1 = _step1(text)
    assert "**`--game` 은 먼저 생략하고 시도한다.**" in step1, (
        "1단계가 --game 을 먼저 생략하고 시도하라고 지시하지 않습니다"
    )
    assert "**`<게임>` 은 사용자에게 묻는다.**" not in step1, (
        "게임을 미리 물어보라는 옛 지시가 1단계에 남아 있습니다"
    )


def test_readme_documents_the_default_game():
    """`--game` 생략의 두 경로(읽기 = 추론, 생성 = 기본값)를 README가 구분해 설명해야 한다.

    **부분 문자열만 보는 첫 판은 놓쳤다** (리뷰에서 재현됨) — 명령 목록의
    `qatc config --game starrail  # 기본 게임 설정 ...` 한 줄에 `"config --game"`
    과 `"기본 게임"` 이 이미 다 있어서, 그 아래 설명 문단을 통째로 지워도
    초록이었다. 설명 문단에만 있고 명령 목록 줄에는 없는 문장으로 문단
    자체를 못박는다.
    """
    from pathlib import Path

    readme = Path(__file__).resolve().parent.parent / "README.md"
    text = readme.read_text(encoding="utf-8")

    assert "qatc config --game starrail" in text        # 명령 목록의 예시 줄

    # 설명 문단 — 읽기 명령(추론)과 생성 명령(기본값)을 구분해 말해야 한다.
    # 두 문장 모두 문단에만 있고 명령 목록 줄의 주석에는 없다.
    assert "컨텐츠 이름이 한 게임에서만 발견되면 알아서 찾습니다." in text, (
        "README 가 읽기 명령의 추론 동작을 설명하지 않습니다"
    )
    assert "**생성 명령**(`slot init`, `knowledge`)은 `qatc config --game <게임>` 으로 정해 둔" in text, (
        "README 가 생성 명령이 기본 게임을 쓴다고 설명하지 않습니다"
    )


def test_slot_init_is_additive_and_keeps_wrongly_guessed_type_slots(cfg_env, capsys):
    """문서가 근거로 드는 동작을 실제로 실행해 확인한다.

    잘못 추측한 유형으로 만든 뒤 `--types` 없이 다시 실행해도 그 슬롯은 남고,
    **거기 적힌 답도 남는다.**

    개수만 세면 안 되는 이유 — 슬롯은 그대로 두고 값만 비우는 회귀는 `total`
    비교를 그대로 통과한다 (실측: `init_content` 가 재실행 때 유형 접두 슬롯의
    status·value 를 비우게 만들어도 전체 스위트가 초록이었다). 사용자 입장에서
    답이 지워진 슬롯은 사라진 슬롯과 같고, SKILL.md 가 "기존 값과 상태는
    보존된다" 라고 약속하는 것이 바로 이 부분이다.
    """
    assert main(["slot", "init", "유형추측", "--game", "starrail", "--types", "던전"]) == 0
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다
    main(["slot", "status", "유형추측", "--json"])
    before = json.loads(capsys.readouterr().out)
    assert before["total"] == 14
    assert any(s["key"].startswith("던전.") for s in before["open"])

    # 잘못 추측한 유형의 슬롯에도 사용자는 답을 적는다 — 그 답이 곧 근거다
    assert main(["slot", "set", "유형추측", "던전.제한시간",
                 "--status", "filled", "--value", "3분"]) == 0
    capsys.readouterr()

    assert main(["slot", "init", "유형추측", "--game", "starrail"]) == 0
    capsys.readouterr()
    main(["slot", "status", "유형추측", "--json"])
    after = json.loads(capsys.readouterr().out)
    assert after["total"] == 14, "가산 전용이 아니게 되었다면 SKILL.md 도 고쳐야 한다"
    assert any(s["key"].startswith("던전.") for s in after["open"])

    closed = {s["key"]: s for s in after["closed"]}
    assert "던전.제한시간" in closed, "재실행이 유형 슬롯의 답을 지웠다"
    assert closed["던전.제한시간"]["value"] == "3분"
    assert closed["던전.제한시간"]["status"] == "filled"


# --- 1단계 서두의 세 주장 — 실측과 대조한다 (최종 검토 M6 · M7 · 3절 3번) ----
#
# 이 셋은 전부 **변이 생존**이었다. 문장을 정반대로 뒤집어도 스위트가 초록이라,
# 모델이 따르는 지시가 뒤집혀도 아무도 모른다. 그래서 각 테스트는
#   (1) 그 주장을 참으로 만드는 명령을 **실제로 실행**하고,
#   (2) SKILL.md 의 그 문장을 **통째로** 대조한다.
# 부분 문자열이나 "이 낱말이 없어야 한다" 식 차단 목록은 쓰지 않는다 —
# 앞선 라운드에서 어떤 의역도 통과시킨 방식이다. 문장을 고치려는 사람은 이
# 테스트가 실행하는 명령을 다시 읽고, 고친 문장이 여전히 참인지 확인해야 한다.


def test_skill_does_not_ask_for_the_game_up_front_and_that_actually_works(
        cfg_env, capsys):
    """"미리 묻지 않는다" 가 실제로 가능한지 확인한다 (M7).

    이 문장을 "먼저 물어본다" 로 뒤집어도 스위트가 초록이었다 — 같은 절에
    정면으로 모순되는 두 지시가 공존하는데도. 여기서는 `--game` 없이
    **생성과 읽기가 둘 다 되는지**를 실행해서 확인한다.
    """
    assert main(["config", "--game", "starrail"]) == 0          # 기본 게임 한 번
    capsys.readouterr()

    # 생성 — 기본 게임이 있으면 --game 없이 된다
    assert main(["slot", "init", "파티편성", "--types", "편성"]) == 0
    capsys.readouterr()

    # 읽기 — 컨텐츠 이름으로 게임을 역추적한다
    assert main(["slot", "status", "파티편성", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["game"] == "starrail"

    step1 = _step1(SKILL.read_text(encoding="utf-8"))
    assert "**`<게임>` 은 미리 묻지 않는다.**" in step1


def test_a_default_game_does_not_resolve_an_ambiguous_content(cfg_env, capsys):
    """기본 게임이 있어도 같은 이름이 두 게임에 있으면 CLI 는 **묻는다** (3절 3번).

    SKILL.md 는 "사용자에게 묻는 시점은 … CLI가 게임을 알 수 없다고 답할
    때뿐이다" 라고 절대 표현으로 적고 있었는데, 두 번째 질문 지점이 같은 절
    스무 줄 아래에 있었다. 안전한 독해가 틀린 독해였다 — 34행을 믿는 모델은
    묻지 않고 게임을 **추측**하게 된다.

    읽기 명령은 기본 게임을 아예 쓰지 않는다(`resolve_store` 는 `cfg.default_game`
    을 보지 않는다). 그 사실을 실행해서 고정하고, 1단계가 두 지점을 모두
    말하는지 확인한다.
    """
    assert main(["config", "--game", "starrail"]) == 0
    for game in ("starrail", "genshin"):
        assert main(["slot", "init", "파티편성", "--game", game]) == 0
    capsys.readouterr()

    assert main(["slot", "status", "파티편성"]) == 1
    msg = capsys.readouterr().out
    assert "여러 게임에 있습니다" in msg      # 기본 게임이 해소하지 못했다
    assert "starrail" in msg and "genshin" in msg

    step1 = _step1(SKILL.read_text(encoding="utf-8"))
    assert "**묻는 지점은 두 곳이고, 둘 다 CLI가 먼저 말해 준다.**" in step1
    assert "**기본 게임은 이 경우를 해소하지 못한다**" in step1
    # 하나뿐이라고 단언하던 옛 문장이 남아 있으면 두 지시가 모순된다
    assert "때뿐이다" not in step1


def test_skill_typo_claim_matches_what_both_command_families_do(cfg_env, capsys):
    """"오타는 거부된다 — 새 DB 가 생기지 않는다" 를 두 명령군에서 실행해 확인한다 (M6).

    이 문장을 "거부되지 않는다 — 새 DB 가 조용히 생긴다" 로 뒤집어도 초록이었고,
    **뒤집힌 쪽이 읽기 경로의 진실이었다** (`slot status … --game starrial` 이
    rc=1 로 끝나면서 `starrial.db` 를 남겼다). 지금은 양쪽 다 거부하지만
    **막는 방식이 다르므로** 문장도 그렇게 적어야 한다 — 생성 명령은 `profiles/`
    와 대조하고, 읽기 명령은 없는 DB 파일을 만들지 않는다.
    """
    assert main(["slot", "init", "파티편성", "--game", "starrail"]) == 0
    capsys.readouterr()

    # (1) 생성 명령 — profiles/ 와 대조해 거부한다
    assert main(["slot", "init", "신규", "--game", "starrial"]) == 1
    assert "등록된 게임이 아닙니다" in capsys.readouterr().out

    # (2) 읽기 명령 — 대조하지 않지만 없는 DB 를 만들지 않는다
    assert main(["slot", "status", "파티편성", "--game", "starrial"]) == 1
    read_msg = capsys.readouterr().out
    assert "지식 DB가 없습니다" in read_msg
    assert "등록된 게임이 아닙니다" not in read_msg     # 대조는 하지 않는다

    assert not (cfg_env / "starrial.db").exists(), "유령 DB 가 생겼다"

    # 마크다운은 줄바꿈으로 감싸므로 문장 비교는 공백을 접어서 한다 —
    # 안 그러면 줄 나누기만 바꿔도 테스트가 깨져 사람이 단언을 약하게 만든다.
    step1 = _flat(_step1(SKILL.read_text(encoding="utf-8")))
    assert "**`--game` 오타는 CLI가 거부한다 — 잘못된 이름으로 새 DB가 생기지 않는다.**" in step1
    assert ("생성 명령(`slot init` · `knowledge` · `config --game`)은 `profiles/` 와 "
            "대조하고, 읽기 명령은 대조하지 않는 대신 그 이름의 DB 파일이 없으면 "
            "만들지 않고 멈춘다") in step1

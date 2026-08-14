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


def test_skill_says_the_game_name_comes_from_the_user(cfg_env):
    """`<게임>` 을 어떻게 정하는지 1단계가 말해야 한다.

    1단계는 `--game` 없이 `slot status` 를 부른 뒤 곧바로
    `slot init ... --game <게임>` 을 요구한다. 출처가 없으면 모델이 추측하는데,
    `--game` 은 `profiles/` 와 대조되지 않으므로 오타가 조용히 새 DB를 만든다.
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


def test_skill_step1_asks_the_overview_before_running_slot_init():
    """유형을 **듣기 전에** `slot init` 을 실행하라고 지시하면 안 된다.

    예전 1단계는 "`slot init` 을 실행하고 개괄 질문으로 시작한다" 라고 해 놓고
    아래에서 "답변에서 유형을 판정한다" 라고 했다 — 유형을 들어보기 전에
    추측하라는 뜻이 된다. `slot init` 은 가산 전용이라 잘못 넣은 유형의 슬롯을
    CLI 로 지울 수 없으므로, 그 추측은 되돌릴 수 없는 실수다.
    """
    step1 = _step1(SKILL.read_text(encoding="utf-8"))

    question = step1.index("먼저 이 컨텐츠가 어떤 것인지 설명해주세요")
    init = step1.index(".venv/Scripts/qatc.exe slot init")
    assert question < init, "개괄 질문이 slot init 뒤에 있다"

    # 유형 판정도 답변 뒤여야 한다
    decide = step1.index("답변에서 유형을 판정한다")
    assert question < decide < init


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


def test_slot_init_is_additive_and_keeps_wrongly_guessed_type_slots(cfg_env, capsys):
    """문서가 근거로 드는 동작을 실제로 실행해 확인한다.

    잘못 추측한 유형으로 만든 뒤 `--types` 없이 다시 실행해도 그 슬롯은 남는다.
    """
    assert main(["slot", "init", "유형추측", "--game", "starrail", "--types", "던전"]) == 0
    capsys.readouterr()          # 앞선 명령의 확인 문구를 버린다
    main(["slot", "status", "유형추측", "--json"])
    before = json.loads(capsys.readouterr().out)
    assert before["total"] == 14
    assert any(s["key"].startswith("던전.") for s in before["open"])

    assert main(["slot", "init", "유형추측", "--game", "starrail"]) == 0
    capsys.readouterr()
    main(["slot", "status", "유형추측", "--json"])
    after = json.loads(capsys.readouterr().out)
    assert after["total"] == 14, "가산 전용이 아니게 되었다면 SKILL.md 도 고쳐야 한다"
    assert any(s["key"].startswith("던전.") for s in after["open"])

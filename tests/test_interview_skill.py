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

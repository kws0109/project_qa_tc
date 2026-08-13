import argparse
import json
import re
from pathlib import Path

import pytest

from qatc.cli import build_parser

SKILL = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "interview" / "SKILL.md"


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


def test_skill_uses_allowlisted_executable_form():
    """`.claude/settings.json` 의 권한 규칙은 명령 접두사로 매칭된다.

    스킬이 쓰는 호출 형태와 allowlist 접두사가 어긋나면 매 슬롯 기록마다
    승인 창이 떠서 인터뷰가 성립하지 않는다.
    """
    settings = Path(__file__).resolve().parents[1] / ".claude" / "settings.json"
    allow = json.loads(settings.read_text(encoding="utf-8"))["permissions"]["allow"]
    prefixes = [a[len("Bash("):-len(" *)")] for a in allow if a.startswith("Bash(")]
    assert any("qatc.exe slot" in p for p in prefixes)
    assert any("qatc.exe tc" in p for p in prefixes)

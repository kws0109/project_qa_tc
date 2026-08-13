import pytest
from openpyxl import load_workbook

from qatc.export.tc_excel import clean_cell, export_tc_excel
from qatc.knowledge.gate import FamilySkip
from qatc.knowledge.models import SlotStatus
from qatc.models import Priority, TCKind, TCOrigin, TestCase


def _tc(title="제목", origin=TCOrigin.INTERVIEW) -> TestCase:
    return TestCase(
        id="tc_1", category_major="파티 편성", category_minor="정상 경로",
        title=title, precondition="파티 편성 화면",
        steps=["파티 적용을 누른다"], expected=["파티가 적용된다"],
        priority=Priority.HIGH, kind=TCKind.HAPPY_PATH, origin=origin,
        rationale="core_action 슬롯에서 도출",
    )


def test_clean_cell_strips_control_characters():
    assert clean_cell("A\x03B") == "AB"
    assert clean_cell("정상\x00문자") == "정상문자"


def test_clean_cell_keeps_newline_and_tab():
    assert clean_cell("가\n나\t다") == "가\n나\t다"


def test_clean_cell_passes_through_normal_text():
    assert clean_cell("파티 적용") == "파티 적용"


def test_export_creates_three_sheets(tmp_path):
    p = export_tc_excel("파티편성", [_tc()], [], tmp_path / "out.xlsx")
    wb = load_workbook(p)
    assert wb.sheetnames == ["테스트케이스", "미확인 항목", "요약"]


def test_export_writes_testcase_row(tmp_path):
    p = export_tc_excel("파티편성", [_tc(title="정상 동작")], [], tmp_path / "out.xlsx")
    ws = load_workbook(p)["테스트케이스"]
    values = [c.value for c in ws[2]]
    assert "정상 동작" in values
    assert "인터뷰" in values


def test_export_survives_control_characters(tmp_path):
    # 이 케이스가 예전 export 를 통째로 죽였다 (IllegalCharacterError)
    bad = _tc(title="제어문자\x03포함")
    p = export_tc_excel("파티편성", [bad], [], tmp_path / "out.xlsx")
    ws = load_workbook(p)["테스트케이스"]
    assert any(c.value == "제어문자포함" for c in ws[2])


def test_export_lists_skipped_families(tmp_path):
    skip = FamilySkip("재화 부족", "cost", "무엇을 소모하는가", SlotStatus.EMPTY)
    p = export_tc_excel("파티편성", [_tc()], [skip], tmp_path / "out.xlsx")
    ws = load_workbook(p)["미확인 항목"]
    row = [c.value for c in ws[2]]
    assert "cost" in row
    assert "재화 부족" in row


def test_export_summary_counts_by_origin(tmp_path):
    cases = [_tc(origin=TCOrigin.INTERVIEW), _tc(origin=TCOrigin.INFERRED)]
    p = export_tc_excel("파티편성", cases, [], tmp_path / "out.xlsx")
    ws = load_workbook(p)["요약"]
    text = "\n".join(str(c.value) for row in ws.iter_rows() for c in row if c.value)
    assert "인터뷰" in text
    assert "추론됨" in text


def test_export_with_no_testcases_still_writes(tmp_path):
    p = export_tc_excel("파티편성", [], [], tmp_path / "out.xlsx")
    assert p.exists()

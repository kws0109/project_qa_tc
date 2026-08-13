import pytest
from openpyxl import load_workbook

from qatc.export.tc_excel import clean_cell, export_tc_excel
from qatc.knowledge.gate import FamilySkip
from qatc.knowledge.models import SlotStatus
from qatc.models import TCOrigin


def test_clean_cell_strips_control_characters():
    assert clean_cell("A\x03B") == "AB"
    assert clean_cell("정상\x00문자") == "정상문자"


def test_clean_cell_keeps_newline_and_tab():
    assert clean_cell("가\n나\t다") == "가\n나\t다"


def test_clean_cell_passes_through_normal_text():
    assert clean_cell("파티 적용") == "파티 적용"


def test_export_creates_three_sheets(make_tc, tmp_path):
    p = export_tc_excel("파티편성", [make_tc()], [], tmp_path / "out.xlsx")
    wb = load_workbook(p)
    assert wb.sheetnames == ["테스트케이스", "미확인 항목", "요약"]


def test_export_writes_testcase_row(make_tc, tmp_path):
    p = export_tc_excel("파티편성", [make_tc(title="정상 동작")], [], tmp_path / "out.xlsx")
    ws = load_workbook(p)["테스트케이스"]
    values = [c.value for c in ws[2]]
    assert "정상 동작" in values
    assert "인터뷰" in values


def test_export_survives_control_characters(make_tc, tmp_path):
    # 이 케이스가 예전 export 를 통째로 죽였다 (IllegalCharacterError)
    bad = make_tc(title="제어문자\x03포함")
    p = export_tc_excel("파티편성", [bad], [], tmp_path / "out.xlsx")
    ws = load_workbook(p)["테스트케이스"]
    assert any(c.value == "제어문자포함" for c in ws[2])


def test_export_lists_skipped_families(make_tc, tmp_path):
    skip = FamilySkip("재화 부족", "cost", "무엇을 소모하는가", SlotStatus.EMPTY)
    p = export_tc_excel("파티편성", [make_tc()], [skip], tmp_path / "out.xlsx")
    ws = load_workbook(p)["미확인 항목"]
    row = [c.value for c in ws[2]]
    assert "cost" in row
    assert "재화 부족" in row


def test_export_survives_control_characters_in_skipped_sheet(make_tc, tmp_path):
    # clean_cell 이 _sheet_skipped 에서 빠지면 이 케이스가 IllegalCharacterError 로 죽는다 —
    # prompt_hint 는 인터뷰 중 사람이 받아적거나 OCR 로 채워질 수 있는 자유 텍스트다.
    skip = FamilySkip("재화 부족", "cost", "무엇을\x03소모하는가", SlotStatus.EMPTY)
    p = export_tc_excel("파티편성", [make_tc()], [skip], tmp_path / "out.xlsx")
    ws = load_workbook(p)["미확인 항목"]
    row = [c.value for c in ws[2]]
    assert "무엇을소모하는가" in row
    assert not any(v is not None and "\x03" in str(v) for v in row)


def _summary_value(ws, label: str) -> str:
    for row in ws.iter_rows(min_row=2):
        if row[0].value == label:
            return row[1].value
    raise AssertionError(f"'{label}' 행을 요약 시트에서 찾을 수 없습니다")


def test_export_summary_counts_by_origin(make_tc, tmp_path):
    cases = [make_tc(origin=TCOrigin.INTERVIEW), make_tc(origin=TCOrigin.INFERRED)]
    p = export_tc_excel("파티편성", cases, [], tmp_path / "out.xlsx")
    ws = load_workbook(p)["요약"]
    # "읽는 방법" 설명 칸에 '인터뷰'·'추론됨' 문구가 항상 들어있어 시트 전체 텍스트를
    # 뒤지면 데이터가 비어 있어도 통과한다. 라벨로 실제 행을 찾아 집계값을 확인한다.
    assert _summary_value(ws, "출처별") == "인터뷰 1, 추론됨 1"
    assert _summary_value(ws, "유형별") == "정상 2"


def test_export_survives_control_characters_in_summary_sheet(make_tc, tmp_path):
    # clean_cell 이 _sheet_summary 에서 빠지면 이 케이스가 IllegalCharacterError 로 죽는다.
    p = export_tc_excel("파티\x03편성", [make_tc()], [], tmp_path / "out.xlsx")
    ws = load_workbook(p)["요약"]
    values = [c.value for row in ws.iter_rows() for c in row]
    assert "파티편성" in values
    assert not any(v is not None and "\x03" in str(v) for v in values)


def test_export_with_no_testcases_still_writes(tmp_path):
    p = export_tc_excel("파티편성", [], [], tmp_path / "out.xlsx")
    assert p.exists()


# --- 근거 철회 (I1) ------------------------------------------------------


def test_testcase_sheet_marks_withdrawn_family(make_tc, tmp_path):
    p = export_tc_excel("파티편성", [make_tc()], [], tmp_path / "out.xlsx",
                        withdrawn={"정상 경로"})
    ws = load_workbook(p)["테스트케이스"]
    header = [c.value for c in ws[1]]
    assert "근거 상태" in header
    assert ws[2][header.index("근거 상태")].value == "근거 철회됨"


def test_testcase_sheet_marks_live_family_as_valid(make_tc, tmp_path):
    """철회가 없으면 같은 칸이 "유효" 여야 한다 — 빈 칸은 "누락"으로 읽힌다."""
    p = export_tc_excel("파티편성", [make_tc()], [], tmp_path / "out.xlsx")
    ws = load_workbook(p)["테스트케이스"]
    header = [c.value for c in ws[1]]
    assert ws[2][header.index("근거 상태")].value == "유효"


def test_skipped_sheet_does_not_claim_a_family_has_no_tc_when_it_does(make_tc, tmp_path):
    """"만들지 못한 계열" 은 TC 가 실제로 있는 계열에 대해 거짓말이다.

    실측 BEFORE — `테스트케이스` 시트 2행에 `정상 경로` TC 가 있는데
    `미확인 항목` 시트가 같은 계열을 "만들지 못한 계열" 로 올렸다.
    """
    skip = FamilySkip("정상 경로", "core_action", "주 동작은 무엇인가", SlotStatus.NA)
    p = export_tc_excel("파티편성", [make_tc()], [skip], tmp_path / "out.xlsx",
                        withdrawn={"정상 경로"})
    ws = load_workbook(p)["미확인 항목"]
    assert "만들지 못한 계열" not in [c.value for c in ws[1]]
    row = [c.value for c in ws[2]]
    assert any(v and "근거 철회됨" in str(v) for v in row)
    assert not any(v and "TC 없음" in str(v) for v in row)


def test_skipped_sheet_still_says_no_tc_when_there_really_is_none(tmp_path):
    skip = FamilySkip("재화 부족", "cost", "무엇을 소모하는가", SlotStatus.EMPTY)
    p = export_tc_excel("파티편성", [], [skip], tmp_path / "out.xlsx")
    ws = load_workbook(p)["미확인 항목"]
    row = [c.value for c in ws[2]]
    assert any(v and "TC 없음" in str(v) for v in row)


def test_summary_counts_withdrawn_testcases(make_tc, tmp_path):
    cases = [make_tc(title="철회된 것"), make_tc(title="살아 있는 것")]
    cases[1].category_minor = "경계값"
    p = export_tc_excel("파티편성", cases, [], tmp_path / "out.xlsx",
                        withdrawn={"정상 경로"})
    ws = load_workbook(p)["요약"]
    assert _summary_value(ws, "근거 철회된 TC") == "1"

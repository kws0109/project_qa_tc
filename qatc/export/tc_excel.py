"""컨텐츠 지식에서 만든 TC를 xlsx로 내보낸다.

녹화 파이프라인의 출력기(그래프·세션 저장소가 필요했다)와 달리, 이 모듈은
지식 저장소(:class:`~qatc.knowledge.store.KnowledgeStore`)만 있으면 된다.
녹화 세션이 없는 인터뷰 기반 파이프라인의 출력 경로다.

**모든 셀 값은 :func:`clean_cell` 을 통과시킨다.** openpyxl은 제어문자가 든
문자열을 거부하는데, 예전 구현에 이 방어가 없어 OCR·키 이름에 섞여 들어온
`\\x03` 하나가 export 전체를 죽였다.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..knowledge.gate import FamilySkip
from ..models import TCOrigin, TestCase

#: openpyxl이 거부하는 제어문자. 탭·개행·복귀는 남긴다 (셀 안에서 유효하다).
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

_HEADER_FILL = PatternFill("solid", fgColor="1F3864")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_WRAP = Alignment(vertical="top", wrap_text=True)

_ORIGIN_FILL = {
    TCOrigin.INTERVIEW: PatternFill("solid", fgColor="E2EFDA"),
    TCOrigin.INFERRED: PatternFill("solid", fgColor="FCE4D6"),
    TCOrigin.USER: PatternFill("solid", fgColor="DDEBF7"),
    TCOrigin.RECORDED: PatternFill("solid", fgColor="E2EFDA"),
}


def clean_cell(value: str) -> str:
    """셀에 넣어도 안전한 문자열로 만든다."""
    return _ILLEGAL.sub("", value)


def _header(ws, titles: Sequence[str], widths: Sequence[int]) -> None:
    for i, (t, w) in enumerate(zip(titles, widths), start=1):
        c = ws.cell(row=1, column=i, value=t)
        c.fill = _HEADER_FILL
        c.font = _HEADER_FONT
        c.alignment = _WRAP
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"


def _sheet_testcases(wb: Workbook, cases: Sequence[TestCase]) -> None:
    ws = wb.active
    ws.title = "테스트케이스"
    _header(
        ws,
        ["TC ID", "대분류", "중분류", "제목", "사전조건", "절차", "기대결과",
         "우선순위", "유형", "출처", "근거"],
        [14, 14, 14, 40, 28, 40, 40, 10, 10, 10, 36],
    )
    for r, tc in enumerate(cases, start=2):
        values = [
            tc.id, tc.category_major, tc.category_minor, tc.title, tc.precondition,
            "\n".join(f"{i}. {s}" for i, s in enumerate(tc.steps, 1)),
            "\n".join(f"- {s}" for s in tc.expected),
            tc.priority.value, tc.kind.value, tc.origin.value, tc.rationale,
        ]
        for col, v in enumerate(values, start=1):
            cell = ws.cell(row=r, column=col, value=clean_cell(str(v)))
            cell.alignment = _WRAP
        ws.cell(row=r, column=10).fill = _ORIGIN_FILL.get(tc.origin, PatternFill())


def _sheet_skipped(wb: Workbook, skipped: Sequence[FamilySkip]) -> None:
    ws = wb.create_sheet("미확인 항목")
    _header(ws, ["슬롯", "묻는 것", "만들지 못한 계열", "상태"], [18, 40, 18, 14])
    for r, s in enumerate(skipped, start=2):
        for col, v in enumerate(
            [s.slot_key, s.prompt_hint, s.family, s.reason], start=1
        ):
            ws.cell(row=r, column=col, value=clean_cell(str(v))).alignment = _WRAP


def _sheet_summary(
    wb: Workbook, content: str, cases: Sequence[TestCase], skipped: Sequence[FamilySkip]
) -> None:
    ws = wb.create_sheet("요약")
    _header(ws, ["항목", "값"], [28, 60])

    by_origin = Counter(tc.origin.value for tc in cases)
    by_kind = Counter(tc.kind.value for tc in cases)
    rows = [
        ("컨텐츠", content),
        ("TC 총계", str(len(cases))),
        ("유형별", ", ".join(f"{k} {v}" for k, v in sorted(by_kind.items())) or "-"),
        ("출처별", ", ".join(f"{k} {v}" for k, v in sorted(by_origin.items())) or "-"),
        ("미확인 항목", str(len(skipped))),
        ("읽는 방법",
         "출처 '인터뷰'는 담당자가 진술한 내용, '추론됨'은 진술에서 도출한 것입니다. "
         "'추론됨'은 실제로 그렇게 동작하는지 검증되지 않았습니다. "
         "'미확인 항목' 시트는 아직 설명되지 않아 TC를 만들지 못한 부분입니다."),
    ]
    for r, (k, v) in enumerate(rows, start=2):
        ws.cell(row=r, column=1, value=clean_cell(k)).alignment = _WRAP
        ws.cell(row=r, column=2, value=clean_cell(v)).alignment = _WRAP


def export_tc_excel(
    content: str,
    testcases: Sequence[TestCase],
    skipped: Sequence[FamilySkip],
    out_path: Path | str,
) -> Path:
    """TC를 xlsx로 내보낸다. 반환값은 생성된 파일 경로."""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    _sheet_testcases(wb, testcases)
    _sheet_skipped(wb, skipped)
    _sheet_summary(wb, content, testcases, skipped)
    wb.save(path)
    return path

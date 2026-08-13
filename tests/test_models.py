"""데이터 모델 불변식 테스트.

녹화 파이프라인 전용 타입(NormRect, ScreenState, FlowGraph 등)은 파이프라인과
함께 삭제됐다. 여기 남는 것은 인터뷰 기반 파이프라인이 실제로 쓰는 TestCase
직렬화 왕복뿐이다.
"""

from __future__ import annotations

from qatc.models import Priority, TCKind, TCOrigin, TestCase


def test_testcase_roundtrip():
    tc = TestCase(
        id="TC-001",
        title="제목",
        steps=["1단계"],
        expected=["결과"],
        priority=Priority.HIGH,
        kind=TCKind.BOUNDARY,
        origin=TCOrigin.INFERRED,
        edge_ids=["e1"],
    )
    back = TestCase.from_row(tc.to_row())
    assert back.priority is Priority.HIGH
    assert back.origin is TCOrigin.INFERRED
    assert back.steps == ["1단계"]

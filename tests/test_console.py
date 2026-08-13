"""콘솔 출력 헬퍼(`_p`) 테스트.

회귀 배경: 예전 구현은 `UnicodeEncodeError` 폴백에서 UTF-8 로 인코딩 후 다시
UTF-8 로 디코딩했다. 이 왕복은 원래 문자열과 완전히 같은 str 을 돌려주므로,
폴백의 `print` 가 방금 잡은 것과 똑같은 `UnicodeEncodeError` 를 다시 던진다 —
이번엔 처리되지 않은 채로. 실제 한국어 Windows 콘솔의 기본 코드페이지(cp949)는
"✓" 같은 문자를 인코딩하지 못하므로, `cmd_slot_set` 등에서 작업은 성공하고 나서
성공 메시지 출력이 프로세스를 죽이는 사고가 났다.

pytest 는 표준출력을 UTF-8 로 캡처하므로 이 버그는 테스트에서 재현되지 않는다.
그래서 아래 테스트는 `sys.stdout` 을 cp949 인코딩의 실제 스트림으로 바꿔치기해
재현한다.
"""

from __future__ import annotations

import io
import sys

import pytest

from qatc.console import _p


def _cp949_stream() -> tuple[io.BytesIO, io.TextIOWrapper]:
    """cp949(errors="strict")로 인코딩하는 실제 콘솔과 동등한 stdout."""
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="cp949", errors="strict", newline="")
    return buf, stream


def test_unencodable_char_does_not_raise(monkeypatch):
    """**회귀 방지** — cp949 로 인코딩 불가능한 문자가 있어도 예외 없이 출력돼야 한다.

    주의: `sys.stdout` 교체는 테스트 본문 안에서 해야 한다. pytest 는
    setup/call 단계 사이에 자체 캡처용 stdout 을 다시 설치하므로, 픽스처의
    setup 코드(yield 이전)에서 교체하면 테스트 본문이 실행되는 call 단계에서
    조용히 원상 복구되어 버린다.
    """
    buf, stream = _cp949_stream()
    monkeypatch.setattr(sys, "stdout", stream)

    _p("✓ 완료")
    stream.flush()
    written = buf.getvalue().decode("cp949")
    assert "완료" in written
    assert "✓" not in written  # 인코딩 불가 문자는 물음표 등으로 저하된다


def test_old_roundtrip_fallback_still_raises_on_this_machine():
    """구현이 왜 고쳐졌는지 증명 — UTF-8 왕복은 원래 문자열과 동일하다."""
    msg = "✓ 완료"
    roundtripped = msg.encode("utf-8", "replace").decode("utf-8", "replace")
    assert roundtripped == msg
    with pytest.raises(UnicodeEncodeError):
        msg.encode("cp949")


def test_ascii_prints_unchanged(capsys):
    _p("hello world")
    assert capsys.readouterr().out == "hello world\n"


def test_hangul_prints_unchanged_through_utf8_stream(capsys):
    _p("완료되었습니다")
    assert capsys.readouterr().out == "완료되었습니다\n"


def test_default_empty_message(capsys):
    _p()
    assert capsys.readouterr().out == "\n"


class _RaiseOnceStream:
    """`encoding` 이 None 인 스트림 흉내.

    `io.TextIOWrapper` 는 내장 타입이라 `encoding` 프로퍼티를 몽키패치할 수
    없으므로(속성 불변 오류), 순수 파이썬 객체로 최소한의 파일 인터페이스만
    구현한다. 목적은 실제 코드페이지 동작 재현이 아니라 `sys.stdout.encoding
    or "utf-8"` 분기가 `encoding=None` 일 때 `TypeError` 없이 "utf-8" 로
    안전하게 대체되는지, 즉 폴백 이후 두 번째 print 가 예외 없이 끝나는지
    확인하는 것이다 (문자 재현 충실도는 위 cp949 테스트가 이미 검증한다).
    """

    def __init__(self) -> None:
        self.encoding = None
        self.written: list[str] = []
        self._raised = False

    def write(self, s: str) -> int:
        if not self._raised:
            self._raised = True
            raise UnicodeEncodeError("cp949", s, 0, 1, "simulated")
        self.written.append(s)
        return len(s)

    def flush(self) -> None:
        pass


def test_stdout_encoding_none_does_not_raise(monkeypatch):
    """일부 리다이렉션 환경에서는 `sys.stdout.encoding` 이 None 일 수 있다."""
    stream = _RaiseOnceStream()
    monkeypatch.setattr(sys, "stdout", stream)

    _p("✓ 완료")  # None 을 인코딩 이름으로 넘겨 TypeError 가 나면 안 된다

    assert stream.encoding is None
    assert "".join(stream.written)  # 폴백에서 실제로 뭔가 기록됐다

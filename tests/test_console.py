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

from qatc.console import _p, display_width, pad


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


# --- 동아시아 문자 폭 (Minor 11) -----------------------------------------


def test_display_width_counts_wide_characters_as_two():
    """한글·전각은 콘솔에서 두 칸을 먹는다. `len()` 은 그걸 모른다."""
    assert display_width("abc") == 3
    assert display_width("컨텐츠") == 6
    assert display_width("파티 편성") == 9      # 한글 4자 × 2 + 공백 1
    assert display_width("") == 0


def test_pad_fills_to_the_requested_display_width():
    """`f"{s:<14}"` 는 한글을 폭 1로 세어 실제 콘솔에서 열이 어긋난다."""
    for text in ("abc", "컨텐츠", "파티 편성", "가나다라마바사"):
        assert display_width(pad(text, 14)) == 14, text


def test_pad_right_aligns_by_display_width():
    padded = pad("채움", 7, align="right")
    assert display_width(padded) == 7
    assert padded.endswith("채움")
    assert padded.startswith(" ")


def test_pad_never_truncates():
    """폭을 넘겨도 자르지 않는다 — 컨텐츠 이름이 잘려 나가면 안 된다."""
    long = "아주아주긴컨텐츠이름입니다"
    assert pad(long, 4) == long


def test_skipped_profile_notice_survives_the_console_codepage(tmp_path, monkeypatch):
    """`load_profiles` 의 "건너뜁니다" 안내가 콘솔을 죽이면 안 된다 (Minor 1).

    이 안내만 맨 `print()` 로 남아 있었다 (`profiles.py:49`). pytest 는 표준출력을
    UTF-8 로 캡처하므로 그 위반이 테스트에서 **보이지 않았다** — `_p` 를 다시
    `print` 로 되돌려도 전체 스위트가 통과했다. 실제 한국어 Windows 콘솔은
    cp949 라, 프로파일 파일 이름에 코드페이지 밖 문자가 하나만 있어도
    `qatc config` 가 UnicodeEncodeError 로 죽는다. 여기서는 그 콘솔을 재현한다.
    """
    from qatc.profiles import load_profiles

    (tmp_path / "깨진✓.yaml").write_text("key: [닫히지 않음", encoding="utf-8")

    buf, stream = _cp949_stream()
    monkeypatch.setattr(sys, "stdout", stream)

    assert load_profiles(tmp_path) == {}     # 맨 print 였다면 여기서 예외로 죽는다
    stream.flush()
    assert "건너뜁니다" in buf.getvalue().decode("cp949")

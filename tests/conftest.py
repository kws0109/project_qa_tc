"""테스트 공용 픽스처.

`cfg_env` 는 `test_cli_slot.py` / `test_cli_tc.py` / `test_cli_knowledge_cmd.py`
세 곳에 완전히 복붙돼 있었고, 셋 다 실제 `AppConfig.load()` 를 먼저 불러
개발자 기계의 ``%APPDATA%\\qatc\\config.json`` 을 읽었다 (원장 이월 #14 ·
Minor 7). 표준입력 스텁과 `TestCase` 빌더도 각각 두 벌씩 있었다 (Minor 8).
여기 한 벌만 둔다.
"""

from __future__ import annotations

import pytest

from qatc.config import AppConfig
from qatc.models import Priority, TCKind, TCOrigin, TestCase


@pytest.fixture()
def cfg_env(tmp_path, monkeypatch):
    """`AppConfig.load()` 가 임시 디렉터리만 보게 만든다. 반환값은 knowledge 루트.

    예전 세 벌은 진짜 `AppConfig.load()` 를 먼저 호출한 뒤 `knowledge_root` 만
    덮어썼다. 실제로 새는 값은 `profiles_dir` 하나뿐이고 어떤 CLI 테스트도 그걸
    쓰지 않지만, **테스트 결과가 개발자 기계의 설정 파일에 달려 있다**는 것
    자체가 재현성 구멍이다. 여기서는 두 경로를 직접 주어 디스크를 읽지 않는다.
    """
    kroot = tmp_path / "knowledge"

    def patched() -> AppConfig:
        return AppConfig(knowledge_root=str(kroot), profiles_dir=str(tmp_path / "profiles"))

    monkeypatch.setattr(AppConfig, "load", staticmethod(patched))
    return kroot


class _StdInText:
    """`tc add --json -` 이 읽는 표준입력 스텁."""

    def __init__(self, text: str):
        self._text = text

    def read(self) -> str:
        return self._text


@pytest.fixture()
def stdin_text(monkeypatch):
    """`tc add --json -` 에 줄 표준입력을 갈아끼운다."""

    def _feed(text: str) -> None:
        monkeypatch.setattr("sys.stdin", _StdInText(text))

    return _feed


@pytest.fixture()
def make_tc():
    """`TestCase` 빌더. 지정한 필드만 덮어쓴다.

    `test_knowledge_tc_store.py` 와 `test_tc_excel.py` 가 거의 같은 헬퍼를
    각자 정의하고 있었다 (Minor 8). 기본값이 갈라지면 두 파일이 서로 다른
    "정상적인 TC" 를 상정하게 된다.
    """

    def _make(title: str = "제목", origin: TCOrigin = TCOrigin.INTERVIEW,
              id: str = "", **kw) -> TestCase:
        return TestCase(
            id=id,
            category_major="파티 편성",
            category_minor="정상 경로",
            title=title,
            precondition="파티 편성 화면",
            steps=["파티 적용을 누른다"],
            expected=["파티가 적용된다"],
            priority=Priority.HIGH,
            kind=TCKind.HAPPY_PATH,
            origin=origin,
            rationale="core_action 슬롯에서 도출",
            **kw,
        )

    return _make

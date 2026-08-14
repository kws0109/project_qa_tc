"""게임 프로파일 — 게임 식별 정보.

지식 저장소는 게임 키(예: "starrail")로 나뉘고, 그 정체성이 여기 있다.
``qatc config`` 가 사용 가능한 프로파일 목록을 보여줄 때 이 모듈을 쓴다
(``key`` 와 ``name`` 만 읽는다).

창 탐색·캡처 ROI·무시 영역·입력 해석 규칙 등은 녹화 파이프라인 전용
필드였으나 파이프라인 자체가 삭제되어 더 이상 코드에서 읽지 않는다.
``profiles/*.yaml`` 파일에는 과거 값이 그대로 남아 있을 수 있지만, 아래
로더는 ``name`` 외의 키를 읽지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .console import _warn


@dataclass
class GameProfile:
    key: str    # 파일명 기반 식별자 (예: "genshin")
    name: str   # 표시 이름 (예: "원신")

    @classmethod
    def from_dict(cls, key: str, d: dict[str, Any]) -> GameProfile:
        return cls(key=key, name=d.get("name", key))

    @classmethod
    def load(cls, path: Path | str) -> GameProfile:
        """YAML 한 장을 프로파일로 읽는다.

        :raises ValueError: 최상위가 매핑이 아닐 때. `load_profiles` 가 이미
            잡는 타입이므로 그 파일만 건너뛰어진다.

        문법 오류(`yaml.YAMLError`)만 막으면 부족하다. **문법은 맞지만 최상위가
        매핑이 아닌** YAML — 리스트를 붙여넣은 파일, 콜론 없는 메모 한 줄
        (스칼라로 파싱된다) — 은 `from_dict` 의 `.get` 에서 `AttributeError` 를
        내며, 그것은 `load_profiles` 의 그물에 걸리지 않고 CLI 포괄 핸들러까지
        올라가 `오류: AttributeError: 'list' object has no attribute 'get'` 이
        된다. 파일 **하나**가 모든 게임의 명령을 죽인다.

        판정 방식과 문구는 `AppConfig.load` 의 같은 실패 유형(설정 JSON 의
        최상위가 객체가 아님)에서 그대로 가져왔다 — 두 로더가 사람이 손으로
        만드는 같은 형태의 깨짐을 다르게 대접할 이유가 없다.

        **`None` 만 `{}` 로 받는다.** 예전에는 `safe_load(...) or {}` 를 위
        판정보다 **먼저** 했는데, 그러면 `[]` · `0` · `''` · `false` 처럼
        **거짓값인** 최상위가 전부 `{}` 로 정규화돼 판정에 닿지도 못했다 —
        건너뛰어지는 대신 **파일명을 키로 한 프로파일로 조용히 등록**됐다
        (실측: `[]` 만 든 `empty-list.yaml` → `profiles=['empty-list',
        'starrail']`). 이름으로 건너뛴다는 이 도크스트링의 약속이 참값
        형태에서만 지켜지고 있었던 셈이다.

        반대로 `None` 을 `{}` 로 받는 것은 **의도된 오래된 동작**이라 남긴다:
        빈 파일 · `null` · 주석뿐인 파일은 유효한 프로파일이고, `name` 이
        없으면 파일명이 표시 이름을 대신한다 (`from_dict`). 그것까지 건너뛰면
        등록돼 있던 게임이 목록에서 사라져 그 게임의 생성 명령이 통째로
        거부된다. 그래서 `or {}` 를 판정 뒤로 옮기는 것으로는 부족하다 —
        `None` 만 따로 걸러야 한다.
        """
        p = Path(path)
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if data is None:                    # 빈 파일 · `null` · 주석뿐인 파일
            data = {}
        if not isinstance(data, dict):
            raise ValueError(f"최상위가 객체가 아닙니다 ({type(data).__name__})")
        return cls.from_dict(p.stem, data)


def load_profiles(profiles_dir: Path | str) -> dict[str, GameProfile]:
    """프로파일 폴더의 모든 YAML을 읽는다. 깨진 파일은 건너뛰되 이유를 남긴다."""
    d = Path(profiles_dir)
    out: dict[str, GameProfile] = {}
    if not d.exists():
        return out
    for f in sorted(d.glob("*.yaml")):
        try:
            prof = GameProfile.load(f)
            out[prof.key] = prof
        except (yaml.YAMLError, KeyError, ValueError, OSError) as exc:
            _warn(f"[프로파일] {f.name} 을(를) 건너뜁니다: {exc}")
    return out

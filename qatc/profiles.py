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
        p = Path(path)
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
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

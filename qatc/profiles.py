"""게임 프로파일 — 게임마다 다른 창 탐색 규칙, 캡처 영역, 무시 영역, LLM 컨텍스트.

프로파일이 필요한 이유는 셋이다.

1. **창 탐색**: 게임마다 창 제목과 프로세스명이 다르다. 원신은 로케일에 따라
   제목이 "原神"/"Genshin Impact"로 갈린다.
2. **캡처 ROI**: 블루아카이브는 에뮬레이터 창이라 상단 툴바와 우측 사이드바를
   잘라내야 게임 화면만 남는다.
3. **무시 영역**: 시계, 프레임레이트, 핑 표시처럼 항상 변하는 HUD를 해시에서
   빼지 않으면 같은 화면이 매번 다른 화면으로 잡힌다.

``static_ignore``는 사람이 아는 것(시계 위치)을 미리 넣는 용도이고, 실제로 변하는
영역 대부분은 :mod:`qatc.analyze.motion`이 자동으로 학습한다. 둘은 합쳐서 쓴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import NormRect


@dataclass
class IgnoreRegion:
    rect: NormRect
    why: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IgnoreRegion:
        return cls(
            rect=NormRect(float(d["x"]), float(d["y"]), float(d["w"]), float(d["h"])),
            why=d.get("why", ""),
        )


@dataclass
class InputRules:
    """게임별 입력 해석 규칙.

    같은 키 입력이라도 게임마다 의미가 다르다. 스타레일 필드에서 Alt는 마우스
    포인터를 활성화하는 **수단**이지 게임 동작이 아니고, WASD는 이동이라 QA
    테스트케이스의 대상이 아니다. 이걸 구분하지 않으면 TC에 "[W] 키 입력"이
    수십 줄 쌓인다.
    """

    #: 홀드해야 마우스 포인터가 활성화되는 키 (예: 스타레일 필드의 ``alt``).
    #: **이 키를 안 누른 상태의 클릭 좌표는 신뢰할 수 없다** — 포인터가 화면
    #: 중앙에 잠겨 있어 항상 (0.5, 0.5)로 기록되기 때문이다.
    pointer_modifier: str = ""
    #: 스텝으로 만들지 않을 키 (이동·카메라 등). 기록 자체를 하지 않는다.
    ignore_keys: frozenset[str] = frozenset()
    #: 포인터 수식키를 누르지 않은 클릭을 버릴지. 좌표가 무의미하기 때문이다.
    #: 다만 메뉴 화면에서는 수식키 없이도 포인터가 활성화되므로, 기본값은
    #: 버리지 않고 "좌표 불확실" 표시만 남기는 쪽이다.
    drop_unmodified_clicks: bool = False

    def is_ignored_key(self, key: str) -> bool:
        return (key or "").lower() in self.ignore_keys

    def is_pointer_modifier(self, key: str) -> bool:
        if not self.pointer_modifier:
            return False
        k = (key or "").lower()
        return k == self.pointer_modifier or k.startswith(self.pointer_modifier + "_")

    def click_coords_reliable(self, modifiers: frozenset[str]) -> bool:
        """이 클릭의 좌표를 믿을 수 있는가.

        수식키가 설정되지 않은 게임(대부분의 메뉴 기반 UI)에서는 항상 참이다.
        """
        if not self.pointer_modifier:
            return True
        return self.pointer_modifier in modifiers

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> InputRules:
        return cls(
            pointer_modifier=str(d.get("pointer_modifier", "")).lower(),
            ignore_keys=frozenset(str(k).lower() for k in (d.get("ignore_keys") or [])),
            drop_unmodified_clicks=bool(d.get("drop_unmodified_clicks", False)),
        )


@dataclass
class GameProfile:
    key: str                              # 파일명 기반 식별자 (예: "genshin")
    name: str                             # 표시 이름 (예: "원신")
    title_regex: str = ""
    process_name: str = ""
    capture_roi: NormRect | None = None   # None이면 클라이언트 영역 전체
    ui_language: str = "ko"
    static_ignore: list[IgnoreRegion] = field(default_factory=list)
    #: LLM 프롬프트에 주입할 게임 배경 지식. 화면 명명 품질을 크게 좌우한다.
    llm_context: str = ""
    #: 이 게임에서 의미 있는 키 (TC 절차 문구에 그대로 쓰인다).
    key_hints: dict[str, str] = field(default_factory=dict)
    #: 에뮬레이터 등 창 안에 다른 UI가 있는 경우 True — 캡처 ROI를 반드시 확인시킨다.
    is_emulator: bool = False
    #: 입력 해석 규칙.
    input_rules: InputRules = field(default_factory=InputRules)

    # -- 창 매칭 -----------------------------------------------------

    def matches_window(self, title: str, process: str = "") -> bool:
        """창 제목/프로세스명이 이 프로파일에 해당하는지.

        프로세스명이 주어지면 그것을 우선한다 — 제목은 로케일과 패치로 바뀌지만
        실행 파일명은 안정적이다.
        """
        if self.process_name and process:
            if process.lower() == self.process_name.lower():
                return True
        if self.title_regex and title:
            if re.search(self.title_regex, title):
                return True
        return False

    def describe_key(self, key: str) -> str:
        """키 이름을 TC 문구용으로 바꾼다. 예: 'esc' → 'ESC(뒤로가기)'."""
        hint = self.key_hints.get(key.lower())
        return f"{key.upper()}({hint})" if hint else key.upper()

    @property
    def ignore_rects(self) -> list[NormRect]:
        return [r.rect for r in self.static_ignore]

    # -- 로딩 --------------------------------------------------------

    @classmethod
    def from_dict(cls, key: str, d: dict[str, Any]) -> GameProfile:
        window = d.get("window") or {}
        roi_raw = d.get("capture_roi")
        roi: NormRect | None = None
        if isinstance(roi_raw, (list, tuple)) and len(roi_raw) == 4:
            roi = NormRect(*(float(v) for v in roi_raw))
        elif isinstance(roi_raw, dict):
            roi = NormRect(
                float(roi_raw["x"]), float(roi_raw["y"]), float(roi_raw["w"]), float(roi_raw["h"])
            )
        # "full" 또는 누락이면 None (전체 영역)

        return cls(
            key=key,
            name=d.get("name", key),
            title_regex=window.get("title_regex", ""),
            process_name=window.get("process", ""),
            capture_roi=roi,
            ui_language=d.get("ui_language", "ko"),
            static_ignore=[IgnoreRegion.from_dict(r) for r in (d.get("static_ignore") or [])],
            llm_context=(d.get("llm_context") or "").strip(),
            key_hints={str(k).lower(): str(v) for k, v in (d.get("key_hints") or {}).items()},
            is_emulator=bool(d.get("is_emulator", False)),
            input_rules=InputRules.from_dict(d.get("input") or {}),
        )

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
            print(f"[프로파일] {f.name} 을(를) 건너뜁니다: {exc}")
    return out


def get_profile(profiles_dir: Path | str, key: str) -> GameProfile:
    profiles = load_profiles(profiles_dir)
    if key in profiles:
        return profiles[key]
    available = ", ".join(sorted(profiles)) or "(없음)"
    raise KeyError(f"프로파일 '{key}'를 찾을 수 없습니다. 사용 가능: {available}")


def generic_profile(name: str = "미지정 게임") -> GameProfile:
    """프로파일 없이 아무 창이나 잡을 때 쓰는 폴백.

    창 매칭 규칙이 비어 있어 :meth:`matches_window`가 항상 False를 돌려준다.
    호출부는 이 경우 사용자에게 창을 직접 고르게 해야 한다.
    """
    return GameProfile(key="generic", name=name, llm_context="일반 PC 게임.")

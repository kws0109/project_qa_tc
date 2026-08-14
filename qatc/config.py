"""앱 설정 및 경로."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "qatc"


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def user_config_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class AppConfig:
    knowledge_root: str = ""
    profiles_dir: str = ""
    default_game: str = ""      # 빈 문자열이면 "설정 안 됨"

    def __post_init__(self) -> None:
        if not self.knowledge_root:
            self.knowledge_root = str(project_root() / "knowledge")
        if not self.profiles_dir:
            self.profiles_dir = str(project_root() / "profiles")

    @property
    def knowledge_path(self) -> Path:
        p = Path(self.knowledge_root)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def profiles_path(self) -> Path:
        return Path(self.profiles_dir)

    # -- 영속화 ------------------------------------------------------

    @staticmethod
    def config_file() -> Path:
        return user_config_dir() / "config.json"

    @classmethod
    def load(cls) -> AppConfig:
        """설정 파일을 읽는다. 파일이 없으면 기본값 (첫 실행).

        :raises SystemExit: 파일이 있는데 읽을 수 없으면. `main()` 이 문자열
            코드를 `오류: …` + rc=1 로 바꾼다 (`resolve_store` 와 같은 관용구).

        예전에는 JSON 오류를 삼키고 **말없이 기본값으로 돌아갔다.** 그러면
        `knowledge_root` 가 사용자 폴더에서 저장소 기본값으로 바뀌고, 다음
        명령은 다른 폴더를 보며 "컨텐츠가 없습니다" 라고 답한다 — 사용자는
        쌓아둔 지식이 사라진 줄 안다. 게다가 `cmd_config` 가 첫 문장에서
        `save()` 를 부르고 있어서, 그 기본값이 사용자의 파일을 **덮어썼다**
        (재검증자가 실제로 당했다). 깨진 설정은 조용히 넘길 값이 아니라
        사용자가 고쳐야 할 것이므로, 어느 파일인지와 다음 조치를 말하고 멈춘다.
        """
        f = cls.config_file()
        if not f.exists():
            return cls()
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise SystemExit(
                f"설정 파일을 읽을 수 없습니다 — {f} ({exc}). "
                f"파일을 고치거나, 기본값으로 다시 시작하려면 지우면 됩니다."
            ) from exc
        if not isinstance(raw, dict):
            raise SystemExit(
                f"설정 파일을 읽을 수 없습니다 — {f} "
                f"(최상위가 객체가 아닙니다: {type(raw).__name__}). "
                f"파일을 고치거나, 기본값으로 다시 시작하려면 지우면 됩니다."
            )
        return cls(
            knowledge_root=raw.get("knowledge_root", ""),
            profiles_dir=raw.get("profiles_dir", ""),
            default_game=raw.get("default_game", ""),
        )

    def save(self) -> Path:
        f = self.config_file()
        d = {
            "knowledge_root": self.knowledge_root,
            "profiles_dir": self.profiles_dir,
            "default_game": self.default_game,
        }
        try:
            f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        except PermissionError as exc:
            raise SystemExit(
                f"설정 파일에 쓸 수 없습니다 — {f}. "
                f"다른 프로그램이 열고 있으면 닫고, 파일 권한을 확인한 뒤 다시 시도하세요."
            ) from exc
        return f

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
        f = cls.config_file()
        if not f.exists():
            return cls()
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # 설정이 깨졌다고 앱이 못 뜨면 안 된다. 기본값으로 계속한다.
            return cls()
        return cls(
            knowledge_root=raw.get("knowledge_root", ""),
            profiles_dir=raw.get("profiles_dir", ""),
        )

    def save(self) -> Path:
        f = self.config_file()
        d = {"knowledge_root": self.knowledge_root, "profiles_dir": self.profiles_dir}
        f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        return f

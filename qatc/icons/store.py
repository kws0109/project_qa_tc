"""아이콘 사전 저장소 — 게임 단위로 영구 보관.

**세션 폴더가 아니라 사용자 설정 폴더에 둡니다.** 세션은 분석이 끝나면 지우기도
하는 임시 데이터지만, 아이콘 사전은 지우면 안 되는 자산입니다. "데이터가 쌓일수록
정교해진다"는 성질은 사전이 세션보다 오래 사는 데서 나옵니다.

::

    %APPDATA%/qatc/icons/<프로파일키>/
        dictionary.json          아이콘 정의 + 학습 샘플
        templates/<icon_id>.png  대표 이미지 (GUI 표시용)

게임마다 분리하는 이유: 원신의 '기원' 아이콘과 스타레일의 '워프' 아이콘은 모양도
의미도 다릅니다. 한 사전에 섞으면 오매칭이 늘 뿐입니다.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

from ..config import user_config_dir
from . import descriptor as desc
from .models import IconEntry, IconSample, new_icon_id

SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def icons_root() -> Path:
    return user_config_dir() / "icons"


class IconStore:
    """한 게임의 아이콘 사전."""

    def __init__(self, profile_key: str, root: Path | None = None):
        self.profile_key = profile_key or "generic"
        self.root = (root or icons_root()) / self.profile_key
        self.templates_dir = self.root / "templates"
        self.entries: dict[str, IconEntry] = {}
        self._dirty = False

    # -- 수명주기 ----------------------------------------------------

    @classmethod
    def load(cls, profile_key: str, root: Path | None = None) -> IconStore:
        store = cls(profile_key, root)
        path = store.root / "dictionary.json"
        if not path.exists():
            return store
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # 사전이 깨졌다고 앱이 못 뜨면 안 된다. 백업해두고 빈 사전으로 계속한다.
            try:
                path.rename(path.with_suffix(".json.corrupt"))
            except OSError:
                pass
            return store
        for raw in data.get("icons", []):
            try:
                entry = IconEntry.from_dict(raw)
                store.entries[entry.id] = entry
            except (KeyError, TypeError, ValueError):
                continue
        return store

    def save(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "dictionary.json"
        payload = {
            "schema_version": SCHEMA_VERSION,
            "profile": self.profile_key,
            "descriptor_dim": desc.DIM,
            "updated_at": _now(),
            "icons": [e.to_dict() for e in self.entries.values()],
        }
        # 임시 파일에 쓰고 교체 — 저장 중 크래시로 사전을 통째로 잃지 않게.
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        self._dirty = False
        return path

    @property
    def dirty(self) -> bool:
        return self._dirty

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[IconEntry]:
        return iter(sorted(self.entries.values(), key=lambda e: (e.name or "~", e.id)))

    # -- 조회 --------------------------------------------------------

    def get(self, icon_id: str) -> IconEntry | None:
        return self.entries.get(icon_id)

    def by_name(self, name: str) -> IconEntry | None:
        target = name.strip().lower()
        for entry in self.entries.values():
            if entry.name.strip().lower() == target:
                return entry
        return None

    def complete_entries(self) -> list[IconEntry]:
        """TC 생성에 쓸 만큼 채워진 아이콘만."""
        return [e for e in self.entries.values() if e.is_complete]

    def stats(self) -> dict[str, int]:
        return {
            "icons": len(self.entries),
            "complete": len(self.complete_entries()),
            "samples": sum(e.sample_count for e in self.entries.values()),
        }

    # -- 등록 / 수정 -------------------------------------------------

    def register(
        self,
        name: str,
        patch: np.ndarray,
        *,
        action=None,
        screen_name: str = "",
        rect=None,
        notes: str = "",
    ) -> IconEntry:
        """새 아이콘을 등록한다. 첫 샘플과 대표 이미지가 함께 저장된다."""
        from .models import IconAction

        entry = IconEntry(
            id=new_icon_id(),
            name=name.strip(),
            action=action or IconAction(),
            notes=notes,
            created_at=_now(),
            updated_at=_now(),
        )
        self.entries[entry.id] = entry
        self._save_template(entry, patch)
        self.add_sample(entry.id, patch, screen_name=screen_name, rect=rect)
        return entry

    def add_sample(
        self, icon_id: str, patch: np.ndarray, *, screen_name: str = "", rect=None
    ) -> bool:
        """확정된 관측을 학습 샘플로 추가한다. **이게 정확도가 오르는 지점이다.**

        거의 동일한 샘플은 넣지 않는다 — 같은 프레임을 여러 번 확정해도 학습에
        보탬이 없고, kNN 투표만 한쪽으로 치우치게 만든다.
        """
        entry = self.entries.get(icon_id)
        if entry is None:
            return False
        try:
            vector = desc.describe(patch)
            digest = desc.patch_hash(patch)
        except ValueError:
            return False

        for existing in entry.samples:
            if desc.similarity(np.asarray(existing.descriptor, np.float32), vector) > 0.995:
                return False

        entry.samples.append(
            IconSample(
                descriptor=[round(float(v), 5) for v in vector],
                dhash=digest,
                screen_name=screen_name,
                rect=list(rect.as_tuple()) if rect is not None else None,
            )
        )
        entry.updated_at = _now()
        self._dirty = True
        return True

    def move_sample(self, from_id: str, to_id: str, sample_index: int) -> bool:
        """오분류 교정 — 샘플을 다른 아이콘으로 옮긴다.

        "이건 A가 아니라 B야"라고 고치면 A의 결정 경계가 좁아지고 B가 넓어진다.
        틀린 라벨을 지우기만 하는 것보다 옮기는 편이 학습에 훨씬 효과적이다.
        """
        src, dst = self.entries.get(from_id), self.entries.get(to_id)
        if src is None or dst is None or not (0 <= sample_index < len(src.samples)):
            return False
        dst.samples.append(src.samples.pop(sample_index))
        src.updated_at = dst.updated_at = _now()
        self._dirty = True
        return True

    def update(self, icon_id: str, **fields) -> bool:
        entry = self.entries.get(icon_id)
        if entry is None:
            return False
        for key, value in fields.items():
            if hasattr(entry, key):
                setattr(entry, key, value)
        entry.updated_at = _now()
        self._dirty = True
        return True

    def delete(self, icon_id: str) -> bool:
        entry = self.entries.pop(icon_id, None)
        if entry is None:
            return False
        if entry.template_rel:
            try:
                (self.root / entry.template_rel).unlink(missing_ok=True)
            except OSError:
                pass
        self._dirty = True
        return True

    def merge(self, keep_id: str, absorb_id: str) -> bool:
        """같은 아이콘을 두 번 등록했을 때 합친다. 샘플이 합쳐져 학습이 강해진다."""
        keep, absorb = self.entries.get(keep_id), self.entries.get(absorb_id)
        if keep is None or absorb is None or keep_id == absorb_id:
            return False
        keep.samples.extend(absorb.samples)
        if not keep.notes:
            keep.notes = absorb.notes
        if not keep.action.is_defined and absorb.action.is_defined:
            keep.action = absorb.action
        keep.updated_at = _now()
        self.delete(absorb_id)
        return True

    # -- 템플릿 이미지 -----------------------------------------------

    def _save_template(self, entry: IconEntry, patch: np.ndarray) -> None:
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        rel = f"templates/{entry.id}.png"
        try:
            cv2.imwrite(str(self.root / rel), desc.normalize_patch(patch))
            entry.template_rel = rel
        except Exception:
            entry.template_rel = ""

    def template_image(self, entry: IconEntry) -> np.ndarray | None:
        if not entry.template_rel:
            return None
        path = self.root / entry.template_rel
        if not path.exists():
            return None
        return cv2.imread(str(path), cv2.IMREAD_COLOR)

    def template_path(self, entry: IconEntry) -> Path | None:
        if not entry.template_rel:
            return None
        path = self.root / entry.template_rel
        return path if path.exists() else None

    # -- 백업 --------------------------------------------------------

    def export_to(self, dest: Path) -> Path:
        """사전을 다른 폴더로 복사한다. 팀원과 공유하거나 백업할 때 쓴다."""
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        self.save()
        shutil.copy2(self.root / "dictionary.json", dest / "dictionary.json")
        if self.templates_dir.exists():
            shutil.copytree(self.templates_dir, dest / "templates", dirs_exist_ok=True)
        return dest


def list_dictionaries(root: Path | None = None) -> list[tuple[str, dict[str, int]]]:
    """등록된 게임별 사전 목록과 통계. ``qatc icons`` 명령이 쓴다."""
    base = root or icons_root()
    out: list[tuple[str, dict[str, int]]] = []
    if not base.exists():
        return out
    for child in sorted(base.iterdir()):
        if (child / "dictionary.json").exists():
            store = IconStore.load(child.name, base)
            out.append((child.name, store.stats()))
    return out

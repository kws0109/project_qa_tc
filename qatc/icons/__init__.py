"""아이콘 사전 — 텍스트 없는 UI 아이콘의 정체와 동작을 축적한다.

**이 계층이 메우는 구멍**: OCR과 LLM은 텍스트가 있는 UI만 알아봅니다. 아이콘만
있는 버튼(기원, 가방, 우편…)은 무엇인지도, 누르면 무슨 일이 나는지도 모릅니다.
그래서 TC 절차가 ``화면 (0.42, 0.31) 위치 클릭``으로 남습니다.

담당자가 한 번 지정하면 게임 단위로 영구 저장되고, 이후 모든 세션에 자동 적용됩니다.
확정할 때마다 학습 샘플이 늘어 매칭이 정확해집니다.

::

    등록  아이콘 패치 + 이름 + 구조화된 동작  →  IconStore (사용자 설정 폴더)
    매칭  새 세션의 요소  →  IconMatcher  →  라벨·동작 자동 적용
    학습  사용자 확정/교정  →  샘플 누적  →  kNN 결정 경계 개선

의존 방향은 ``icons → models`` 뿐입니다. 분석·LLM 계층은 아이콘 사전이 없어도
동작하고, 있으면 결과가 좋아집니다.
"""

from .descriptor import DIM, crop_patch, describe, patch_hash, similarity
from .matcher import IconMatcher, unmatched_icon_elements
from .models import ActionKind, IconAction, IconEntry, IconMatch, IconSample
from .store import IconStore, icons_root, list_dictionaries
from .suggest import IconSuggestion, pending_icons, suggest_for_element, suggest_from_transition

__all__ = [
    "ActionKind",
    "DIM",
    "IconAction",
    "IconEntry",
    "IconMatch",
    "IconMatcher",
    "IconSample",
    "IconStore",
    "IconSuggestion",
    "crop_patch",
    "describe",
    "icons_root",
    "list_dictionaries",
    "patch_hash",
    "pending_icons",
    "similarity",
    "suggest_for_element",
    "suggest_from_transition",
    "unmatched_icon_elements",
]

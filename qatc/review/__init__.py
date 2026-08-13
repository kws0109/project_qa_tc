"""리뷰 워크스페이스 (PySide6).

**PySide6는 선택 의존성이다.** 이 패키지를 import하지 않으면 나머지 전부가
GUI 없이 동작한다. CLI가 ``qatc review``에서만 여기를 부르고, ImportError를
잡아 설치 안내로 바꾼다.
"""

from .app import run_review_app

__all__ = ["run_review_app"]

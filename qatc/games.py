"""`--game` 이 등록된 게임인지 대조한다.

`cli_knowledge.py`(slot init·knowledge)와 `cli.py`(config --game)가 모두 이
규칙을 쓴다. 둘 중 하나에 두면 다른 쪽이 그 모듈을 import 하게 되어 순환이
생기므로 별도 모듈로 둔다 (`console.py` 를 분리했던 것과 같은 이유).

**검증 시점.** 생성 명령(`slot init` · `knowledge` · `config --game`)에서만
대조한다. 컨텐츠 이름으로 DB를 역추적하는 `resolve_store` 경로는 대조하지
않는다 — 이미 존재하는 DB의 읽기를 막으면, 게임이 `profiles/` 를 떠났을 때
거기 쌓인 인터뷰에 접근할 방법이 없어진다.

**읽기 경로의 오타는 다른 방식으로 막힌다.** `resolve_store` 가 없는 DB 파일을
만들지 않는다 (sqlite 는 여는 순간 파일을 만들므로, 예전에는 오타가 rc=1 로
끝나면서도 유령 DB를 남겼다). 프로파일을 보지 않고도 오타를 잡으므로 위
설계 결정과 충돌하지 않는다.
"""

from __future__ import annotations

from .config import AppConfig
from .console import _warn
from .profiles import load_profiles


def known_games(cfg: AppConfig) -> list[str]:
    """등록된 게임 키 목록 (정렬됨)."""
    return sorted(load_profiles(cfg.profiles_path))


def validate_game(cfg: AppConfig, game: str) -> None:
    """`game` 이 등록된 게임이 아니면 멈춘다.

    :raises SystemExit: 등록되지 않은 이름일 때. `main()` 이 문자열 코드를
        `오류: …` + rc=1 로 바꾼다 (`resolve_store` 와 같은 관용구).

    프로파일이 **하나도 없으면 통과시킨다.** 무조건 거부하면 `profiles/` 가
    없거나 비었을 때 모든 게임 이름이 막혀 도구 전체가 벽돌이 된다. 대신
    건너뛴 사실을 화면에 남긴다 — 조용히 넘기면 검증이 도는 줄 알게 된다.
    """
    names = known_games(cfg)
    if not names:
        _warn(f"[경고] {cfg.profiles_path} 에 프로파일이 없어 --game 검증을 건너뜁니다.")
        return
    if game not in names:
        raise SystemExit(
            f"'{game}'은(는) 등록된 게임이 아닙니다. "
            f"사용 가능: {', '.join(names)}. "
            f"프로파일은 {cfg.profiles_path} 에 있습니다."
        )

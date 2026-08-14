"""QATC 명령줄 진입점.

인터뷰 기반 흐름(slot / tc / knowledge / export)은 `cli_knowledge.py`를 보라.

::

    qatc slot init 파티편성 --game starrail --types 편성
    qatc slot status 파티편성 --json     남은 항목 확인
    qatc slot set 파티편성 <키> --status filled --value "..."
    qatc tc plan 파티편성                 만들 수 있는 계열
    qatc tc list 파티편성                 TC + 미충족 항목
    qatc export 파티편성                  xlsx 출력
    qatc config                          설정 확인
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from .config import AppConfig
from .console import _p
from .knowledge.store import DB_LOCKED_HINT, is_locked_error


def cmd_config(args: argparse.Namespace, cfg: AppConfig) -> int:
    """설정과 프로파일을 **보여준다.** 첫 실행의 파일 생성 말고는 쓰지 않는다.

    예전에는 첫 문장이 `cfg.save()` 였다. README 는 이 파일을 손으로 고치라고
    안내하면서 위치를 이 명령으로 찾으라고 하는데, 찾는 행위가 파일을 덮어썼다.
    `cfg.knowledge_path` 프로퍼티도 `mkdir` 을 하므로 출력용으로는 쓰지 않는다 —
    확인만 했는데 오타 난 경로에 빈 폴더가 생기면 그것도 조용한 쓰기다.
    """
    game = getattr(args, "game", None)
    if game:
        from .games import validate_game

        validate_game(cfg, game)        # 잘못된 이름이면 여기서 SystemExit
        cfg.default_game = game
        saved = cfg.save()
        _p(f"✓ 기본 게임 = {game}  ({saved})")
        return 0

    path = AppConfig.config_file()
    if path.exists():
        _p(f"설정 파일 : {path}")
    else:
        # 첫 실행 — 손으로 고칠 파일이 있어야 README 의 안내가 성립한다.
        # 만들되 만들었다고 말한다. 말하지 않으면 화면의 경로가 파일 내용인지
        # 기본값인지 구분되지 않는다.
        cfg.save()
        _p(f"설정 파일 : {path}  (없어서 기본값으로 새로 만들었습니다)")

    kroot = Path(cfg.knowledge_root)
    _p(f"지식 폴더 : {kroot}" + ("" if kroot.exists() else "  (아직 없습니다 — slot init 때 만들어집니다)"))
    _p(f"프로파일  : {cfg.profiles_path}")

    from .profiles import load_profiles

    profiles = load_profiles(cfg.profiles_path)
    _p(f"\n사용 가능한 프로파일 ({len(profiles)}개):")
    for key, prof in sorted(profiles.items()):
        _p(f"  {key:<16} {prof.name}")
    return 0


# ---------------------------------------------------------------- 파서


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qatc",
        description="게임 QA 테스트케이스 자동 생성 도구",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
일반적인 흐름 (Claude Code 세션에서 인터뷰 진행):
  qatc slot init 파티편성 --game starrail --types 편성
  qatc slot status 파티편성 --json     남은 항목 확인
  qatc slot set 파티편성 <키> --status filled --value "..."
  qatc tc plan 파티편성                 만들 수 있는 계열
  qatc tc list 파티편성                 TC + 미충족 항목
  qatc export 파티편성                  xlsx 출력
""",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    from .cli_knowledge import register as _register_knowledge

    _register_knowledge(sub)

    cf = sub.add_parser("config", help="설정 확인")
    cf.add_argument("--game", "-g", help="기본 게임을 설정한다 (이후 --game 생략 가능)")
    cf.set_defaults(func=cmd_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        # `AppConfig.load()` 도 try 안이다 — 깨진 설정 파일은 `SystemExit` 문자열로
        # 올라오고, 아래 핸들러가 그것을 `오류: …` + rc=1 로 바꾼다. 예전처럼
        # 밖에서 부르면 그 예외가 스택트레이스로 새어나간다.
        cfg = AppConfig.load()
        return args.func(args, cfg)
    except KeyboardInterrupt:
        _p("\n중단되었습니다.")
        return 130
    except SystemExit as exc:
        if isinstance(exc.code, str):
            _p(f"오류: {exc.code}")
            return 1
        raise
    except sqlite3.OperationalError as exc:
        # 스펙 §7: 동시 실행으로 DB가 잠기면 그 사실을 말해준다.
        # 잠금이 아닌 DB 오류는 원인을 그대로 보여야 진단이 된다.
        _p(f"오류: {DB_LOCKED_HINT}" if is_locked_error(exc)
           else f"\n오류: {type(exc).__name__}: {exc}")
        if "--debug" in sys.argv:
            raise
        return 1
    except Exception as exc:
        _p(f"\n오류: {type(exc).__name__}: {exc}")
        if "--debug" in sys.argv:
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())

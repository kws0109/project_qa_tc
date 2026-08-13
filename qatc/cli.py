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

from .config import AppConfig
from .console import _p
from .knowledge.store import DB_LOCKED_HINT, is_locked_error


def cmd_config(args: argparse.Namespace, cfg: AppConfig) -> int:
    path = cfg.save()
    _p(f"설정 파일 : {path}")
    _p(f"지식 폴더 : {cfg.knowledge_path}")
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
    cf.set_defaults(func=cmd_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    cfg = AppConfig.load()
    try:
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

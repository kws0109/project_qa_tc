"""지식·인터뷰 관련 CLI 하위명령.

`cli.py` 에 넣지 않고 분리한 이유는 그 파일이 이미 녹화 파이프라인 명령으로
680줄이기 때문이다. 지식 계열은 함께 바뀌므로 함께 둔다.

이 모듈의 명령은 **Claude Code 세션이 인터뷰 중 호출한다.** 출력이 사람과 모델
양쪽에게 읽히므로 `--json` 을 제공하고, 오류는 다음 조치를 항상 함께 알린다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import AppConfig
from .console import _p
from .knowledge.gate import FAMILY_META, plan_families, validate_family
from .knowledge.models import SlotStatus
from .knowledge.slots import KNOWN_TYPES
from .knowledge.store import KnowledgeStore
from .models import Priority, TCOrigin, TestCase


def resolve_store(cfg: AppConfig, game: str | None, content: str | None) -> KnowledgeStore:
    """어느 게임 DB를 열지 정한다.

    `--game` 이 있으면 그대로. 없으면 컨텐츠를 가진 DB를 찾는다 — 인터뷰 중
    매번 `--game` 을 치게 하면 호출이 길어지고 오타가 난다.
    """
    root = cfg.knowledge_path
    if game:
        return KnowledgeStore(root / f"{game}.db").open()

    dbs = sorted(root.glob("*.db"))
    if not dbs:
        raise SystemExit("지식 DB가 없습니다. 먼저 'qatc slot init <컨텐츠> --game <게임>'을 실행하세요.")

    hits: list[Path] = []
    for p in dbs:
        with KnowledgeStore(p) as s:
            if content is None or s.get_content(content) is not None:
                hits.append(p)
    if not hits:
        raise SystemExit(f"'{content}' 컨텐츠를 가진 게임 DB가 없습니다. --game 으로 지정하세요.")
    if len(hits) > 1:
        names = ", ".join(p.stem for p in hits)
        raise SystemExit(f"'{content}'가 여러 게임에 있습니다 ({names}). --game 으로 지정하세요.")
    return KnowledgeStore(hits[0]).open()


def _status_payload(store: KnowledgeStore, content: str) -> dict:
    slots = store.slots(content)
    planned, skipped = plan_families(slots)
    return {
        "content": content,
        "game": store.game,
        "total": len(slots),
        "filled": sum(1 for s in slots if s.status is SlotStatus.FILLED),
        "open": [
            {"key": s.key, "hint": s.prompt_hint, "family": s.tc_family}
            for s in slots if s.is_open
        ],
        "closed": [
            {"key": s.key, "status": s.status.value, "value": s.value}
            for s in slots if s.is_closed
        ],
        "planned_families": [p.family for p in planned],
        "skipped_families": [
            {"family": s.family, "slot": s.slot_key, "status": s.status.value}
            for s in skipped
        ],
    }


def cmd_slot_status(args: argparse.Namespace, cfg: AppConfig) -> int:
    store = resolve_store(cfg, args.game, args.content)
    try:
        if store.get_content(args.content) is None:
            _p(f"컨텐츠 '{args.content}'가 없습니다. 'qatc slot init'을 먼저 실행하세요.")
            return 1
        payload = _status_payload(store, args.content)
    finally:
        store.close()

    if args.json:
        _p(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    _p(f"[{payload['content']}] {payload['game']} · {payload['filled']}/{payload['total']} 채움")
    if payload["open"]:
        _p("\n남은 항목:")
        for s in payload["open"]:
            _p(f"  {s['key']:<16} {s['hint']}")
    else:
        _p("\n모든 항목이 채워졌습니다. 'qatc tc plan'으로 생성 대상을 확인하세요.")
    return 0


def cmd_slot_init(args: argparse.Namespace, cfg: AppConfig) -> int:
    types = [t.strip() for t in (args.types or "").split(",") if t.strip()]
    store = KnowledgeStore(cfg.knowledge_path / f"{args.game}.db").open()
    try:
        content = store.init_content(args.content, game=args.game, types=types)
        n = len(store.slots(args.content))
    except ValueError as exc:
        _p(f"오류: {exc}")
        return 1
    finally:
        store.close()
    _p(f"[{content.name}] 슬롯 {n}개 준비됨 (유형: {', '.join(content.types) or '없음'})")
    return 0


def cmd_slot_set(args: argparse.Namespace, cfg: AppConfig) -> int:
    store = resolve_store(cfg, args.game, args.content)
    try:
        slot = store.set_slot(
            args.content, args.key, SlotStatus(args.status), args.value or ""
        )
    except KeyError as exc:
        _p(f"오류: {exc.args[0]}")
        return 1
    finally:
        store.close()
    _p(f"✓ {slot.key} = {slot.status.value}")
    return 0


def cmd_slot_add(args: argparse.Namespace, cfg: AppConfig) -> int:
    store = resolve_store(cfg, args.game, args.content)
    try:
        store.add_slot(args.content, args.key, args.hint, args.family)
    except KeyError as exc:
        _p(f"오류: {exc.args[0]}")
        return 1
    finally:
        store.close()
    _p(f"✓ 슬롯 추가됨: {args.key} → {args.family}")
    return 0


_ORIGIN_BY_FLAG = {
    "interview": TCOrigin.INTERVIEW,
    "inferred": TCOrigin.INFERRED,
    "user": TCOrigin.USER,
}


def cmd_tc_plan(args: argparse.Namespace, cfg: AppConfig) -> int:
    store = resolve_store(cfg, args.game, args.content)
    try:
        if store.get_content(args.content) is None:
            _p(f"컨텐츠 '{args.content}'가 없습니다.")
            return 1
        planned, skipped = plan_families(store.slots(args.content))
    finally:
        store.close()

    if args.json:
        _p(json.dumps({
            "content": args.content,
            "planned": [
                {"family": p.family, "slot": p.slot_key,
                 "kind": p.kind.value, "priority": p.priority.value}
                for p in planned
            ],
            "skipped": [
                {"family": s.family, "slot": s.slot_key,
                 "hint": s.prompt_hint, "status": s.status.value}
                for s in skipped
            ],
        }, ensure_ascii=False, indent=2))
        return 0

    _p(f"[{args.content}] 생성 대상 계열 {len(planned)}개")
    for p in planned:
        _p(f"  {p.family:<16} {p.slot_key:<16} {p.kind.value} / {p.priority.value}")
    if skipped:
        _p("\n제외됨:")
        reason = {"empty": "슬롯 비어 있음", "unknown": "사용자가 모름", "na": "해당 없음"}
        for s in skipped:
            _p(f"  {s.family:<16} {s.slot_key:<16} {reason[s.status.value]}")
    return 0


def _read_json_arg(source: str) -> dict:
    raw = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    return json.loads(raw)


def cmd_tc_add(args: argparse.Namespace, cfg: AppConfig) -> int:
    try:
        payload = _read_json_arg(args.json)
    except (OSError, json.JSONDecodeError) as exc:
        _p(f"오류: JSON을 읽을 수 없습니다 — {exc}")
        return 1

    items = payload.get("testcases")
    if not isinstance(items, list) or not items:
        _p("오류: 최상위에 비어 있지 않은 'testcases' 배열이 필요합니다.")
        return 1

    store = resolve_store(cfg, args.game, args.content)
    try:
        slots = store.slots(args.content)
        if not slots:
            _p(f"컨텐츠 '{args.content}'가 없습니다.")
            return 1
        try:
            plan = validate_family(args.family, slots)
        except ValueError as exc:
            _p(f"오류: {exc}")
            return 1

        cases: list[TestCase] = []
        for i, item in enumerate(items):
            missing = [k for k in ("title", "steps", "expected") if not item.get(k)]
            if missing:
                _p(f"오류: testcases[{i}] 에 필수 필드가 없습니다 — {', '.join(missing)}")
                return 1
            cases.append(TestCase(
                id="",
                category_major=args.content,
                category_minor=args.family,
                title=str(item["title"]),
                precondition=str(item.get("precondition", "")),
                steps=[str(x) for x in item["steps"]],
                expected=[str(x) for x in item["expected"]],
                priority=Priority(item["priority"]) if item.get("priority") else plan.priority,
                kind=plan.kind,
                origin=_ORIGIN_BY_FLAG[args.origin],
                rationale=str(item.get("rationale", "")),
            ))

        added, kept = store.replace_generated(
            args.content, args.family, cases, [plan.slot_key]
        )
    finally:
        store.close()

    _p(f"✓ [{args.family}] TC {added}건 저장" + (f" · 사람 손댄 {kept}건 보존" if kept else ""))
    return 0


def cmd_tc_list(args: argparse.Namespace, cfg: AppConfig) -> int:
    store = resolve_store(cfg, args.game, args.content)
    try:
        if store.get_content(args.content) is None:
            _p(f"컨텐츠 '{args.content}'가 없습니다.")
            return 1
        cases = store.testcases(args.content)
        _, skipped = plan_families(store.slots(args.content))
    finally:
        store.close()

    by_kind: dict[str, int] = {}
    for tc in cases:
        by_kind[tc.kind.value] = by_kind.get(tc.kind.value, 0) + 1
    summary = " · ".join(f"{k} {v}" for k, v in sorted(by_kind.items()))
    _p(f"TC {len(cases)}건" + (f" ({summary})" if summary else ""))

    for tc in cases:
        _p(f"  [{tc.category_minor}] {tc.title}  ({tc.origin.value})")

    if skipped:
        _p("\n⚠ 다음 항목이 미확인이라 해당 TC가 없습니다")
        reason = {"empty": "비어있음", "unknown": "사용자가 모름", "na": "해당 없음"}
        for s in skipped:
            _p(f"   {s.slot_key:<16} ({s.prompt_hint}) → {s.family} TC 없음  "
               f"[{reason[s.status.value]}]")
        _p("\n   이어서 채우려면 Claude Code에서 인터뷰를 재개하세요.")
    return 0


def register(sub) -> None:
    """`qatc` 하위파서에 지식 명령을 등록한다."""
    slot = sub.add_parser("slot", help="컨텐츠 지식 슬롯 조회·기록")
    slot_sub = slot.add_subparsers(dest="slot_command", required=True)

    st = slot_sub.add_parser("status", help="슬롯 상태 (질문 전 매번 호출)")
    st.add_argument("content")
    st.add_argument("--game", "-g")
    st.add_argument("--json", action="store_true", help="기계가 읽을 JSON으로 출력")
    st.set_defaults(func=cmd_slot_status)

    it = slot_sub.add_parser("init", help="컨텐츠 슬롯 세트 생성 (재실행 시 값 보존)")
    it.add_argument("content")
    it.add_argument("--game", "-g", required=True)
    it.add_argument("--types", "-t", default="",
                    help=f"쉼표 구분. 사용 가능: {', '.join(KNOWN_TYPES)}")
    it.set_defaults(func=cmd_slot_init)

    se = slot_sub.add_parser("set", help="슬롯 값 기록")
    se.add_argument("content")
    se.add_argument("key")
    se.add_argument("--status", required=True, choices=[s.value for s in SlotStatus])
    se.add_argument("--value", default="")
    se.add_argument("--game", "-g")
    se.set_defaults(func=cmd_slot_set)

    ad = slot_sub.add_parser("add", help="유형에 없던 슬롯 추가")
    ad.add_argument("content")
    ad.add_argument("key")
    ad.add_argument("--hint", required=True)
    ad.add_argument("--family", required=True)
    ad.add_argument("--game", "-g")
    ad.set_defaults(func=cmd_slot_add)

    tc = sub.add_parser("tc", help="지식에서 테스트케이스 생성·조회")
    tc_sub = tc.add_subparsers(dest="tc_command", required=True)

    pl = tc_sub.add_parser("plan", help="만들 수 있는 계열과 제외된 계열")
    pl.add_argument("content")
    pl.add_argument("--game", "-g")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=cmd_tc_plan)

    ad2 = tc_sub.add_parser("add", help="TC 저장 (계열이 생성 대상인지 검증)")
    ad2.add_argument("content")
    # choices 를 쓰지 않는 이유: argparse 가 먼저 거부하면 "왜 안 되는지"가
    # 사라진다. validate_family 가 "cost 슬롯이 비어 있음 → tc plan 을 보라"까지
    # 알려주는데, argparse 는 유효값 나열만 하고 종료 코드 2로 죽는다.
    ad2.add_argument("--family", required=True,
                     help=f"TC 계열. 대상 여부는 tc plan 이 정한다 "
                          f"(정의된 계열: {', '.join(sorted(FAMILY_META))})")
    ad2.add_argument("--origin", required=True, choices=sorted(_ORIGIN_BY_FLAG))
    ad2.add_argument("--json", required=True, help="JSON 파일 경로 또는 '-' (표준입력)")
    ad2.add_argument("--game", "-g")
    ad2.set_defaults(func=cmd_tc_add)

    ls = tc_sub.add_parser("list", help="TC 목록 + 미충족 슬롯 리포트")
    ls.add_argument("content")
    ls.add_argument("--game", "-g")
    ls.set_defaults(func=cmd_tc_list)

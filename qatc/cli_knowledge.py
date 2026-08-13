"""지식·인터뷰 관련 CLI 하위명령.

`cli.py` 에 넣지 않고 분리한 이유는 파일 크기가 아니라 응집도다 — `slot`/`tc`/
`knowledge`/`export` 는 지식 게이트(`knowledge/gate.py`) 하나를 같이 바라보며
함께 바뀌는 한 덩어리라 따로 둔다. `cli.py` 는 파서 조립과 `config` 명령만
남아 있다.

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
from .knowledge.models import SLOT_STATUS_LABEL, SlotStatus
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


def _no_content(content: str) -> str:
    """"그 컨텐츠가 없다"를 알리는 문구. 다섯 명령이 같이 쓴다.

    이 모듈의 계약은 맨 위 도크스트링에 있다 — *"오류는 다음 조치를 항상 함께
    알린다."* 그런데 같은 조건을 `slot status` 만 지키고 `tc plan`/`tc add`/
    `tc list`/`export` 는 "없습니다."에서 끝냈다. 이 출력의 1차 독자는 인터뷰를
    진행하는 **모델**이라, 다음 조치가 없으면 무엇을 할지 추측하게 되고 그게
    CLI 를 결정론적 진실 원천으로 쓰는 이유를 갉아먹는다. 문구를 한 곳에 두어
    다음에 명령이 늘어도 갈라지지 않게 한다.
    """
    return f"컨텐츠 '{content}'가 없습니다. 'qatc slot init'을 먼저 실행하세요."


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
            _p(_no_content(args.content))
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
    status = SlotStatus(args.status)
    value = args.value or ""

    # 빈 근거를 FILLED 로 받으면 게이트가 그것을 근거로 인정한다. DB를 열기 전에
    # 막고, 다음 조치(모른다 / 해당 없음)를 함께 알린다 — 이 명령의 호출자는
    # 인터뷰를 진행하는 모델이라 "안 됩니다"만으로는 무엇을 할지 모른다.
    if status is SlotStatus.FILLED and not value.strip():
        _p("오류: --status filled 에는 --value 가 필요합니다. "
           "내용을 모르면 --status unknown, 해당 없으면 --status na 를 쓰세요.")
        return 1

    store = resolve_store(cfg, args.game, args.content)
    try:
        slot = store.set_slot(args.content, args.key, status, value)
    except KeyError as exc:
        _p(f"오류: {exc.args[0]}")
        return 1
    except ValueError as exc:
        _p(f"오류: {exc}")
        return 1
    finally:
        store.close()
    _p(f"✓ {slot.key} = {slot.status.value}")
    return 0


def cmd_slot_add(args: argparse.Namespace, cfg: AppConfig) -> int:
    # `--family` 를 여기서 검증하는 이유: 통과시키면 되돌릴 수 없이 조용히 틀린다.
    # 오타(`중단됨`)는 `tc plan` 에서 `FAMILY_META` 폴백을 타 의도한 INTERRUPT 대신
    # HAPPY_PATH / Medium 이 배정되고, 빈 문자열은 어떤 계열도 만들지 못하면서
    # 커버리지 분모(`total`)만 늘리는 "죽은 슬롯"이 된다. 둘 다 rc=0 이라
    # 아무도 눈치채지 못한다.
    # argparse `choices` 를 쓰지 않는 이유는 `tc add` 와 같다 (register() 주석 참조).
    if args.family not in FAMILY_META:
        _p(f"오류: 알 수 없는 계열 '{args.family}'. "
           f"정의된 계열 중 하나를 쓰세요: {', '.join(sorted(FAMILY_META))}")
        return 1

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
            _p(_no_content(args.content))
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
        for s in skipped:
            _p(f"  {s.family:<16} {s.slot_key:<16} {SLOT_STATUS_LABEL[s.status]}")
    return 0


def _read_json_arg(source: str) -> dict:
    raw = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    return json.loads(raw)


#: `tc add` 항목에서 반드시 있어야 하는 필드.
_REQUIRED_FIELDS = ("title", "steps", "expected")
#: 문자열 배열이어야 하는 필드.
_LIST_FIELDS = ("steps", "expected")
#: `in` 검사에 집합 대신 튜플을 쓰는 이유: priority 자리에 배열·객체가 와도
#: 해시 불가 예외 없이 그냥 "유효하지 않음"으로 떨어져야 한다.
_PRIORITY_VALUES = tuple(p.value for p in Priority)


def _validate_item(item: object, i: int) -> str | None:
    """`tc add` JSON 항목 하나를 검사한다. 문제가 없으면 `None`.

    이 명령의 호출자는 **인터뷰를 진행하는 모델**이라, 검증 실패는 사람이 읽는
    예외가 아니라 모델이 고칠 수 있는 지시여야 한다. 그래서 모든 메시지가
    (a) 몇 번째 항목의 (b) 어느 필드가 틀렸는지와 (c) 다음 조치를 함께 준다.

    특히 `"steps": "한 줄"` 은 배열 자리에 문자열을 주는 가장 흔한 형태 오류인데,
    truthiness 만 보면 통과한 뒤 문자열을 순회해 `['한', ' ', '줄']` 이 조용히
    저장된다. 실패도 경고도 없이 쓰레기가 최종 xlsx 절차 칸까지 가므로 여기서 막는다.
    """
    if not isinstance(item, dict):
        return (f"testcases[{i}] 가 객체가 아닙니다 ({type(item).__name__}). "
                f'각 항목을 {{"title": "...", "steps": ["..."], "expected": ["..."]}} '
                f"형태의 객체로 주세요.")

    missing = [k for k in _REQUIRED_FIELDS if not item.get(k)]
    if missing:
        # 빠진 것만 나열한다 — 필수 필드 세 개를 항상 찍으면 모델이 어느 것을
        # 더해야 하는지 다시 추론해야 한다.
        return (f"testcases[{i}] 에 필수 필드가 없습니다 — {', '.join(missing)}. "
                f"빠진 필드를 채워 다시 주세요.")

    for field in _LIST_FIELDS:
        value = item[field]
        if not isinstance(value, list):
            return (f"testcases[{i}].{field} 는 문자열 배열이어야 합니다 "
                    f"({type(value).__name__} 이 왔습니다). "
                    f'한 줄이어도 ["..."] 처럼 배열로 감싸세요.')
        for j, element in enumerate(value):
            if not isinstance(element, str):
                return (f"testcases[{i}].{field}[{j}] 가 문자열이 아닙니다 "
                        f"({type(element).__name__}). 배열의 원소는 모두 문자열이어야 합니다.")

    priority = item.get("priority")
    if priority and priority not in _PRIORITY_VALUES:
        return (f"testcases[{i}].priority 값 '{priority}' 을(를) 알 수 없습니다. "
                f"사용 가능: {', '.join(_PRIORITY_VALUES)}. "
                f"생략하면 계열 기본 우선순위가 쓰입니다.")

    return None


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
            _p(_no_content(args.content))
            return 1
        try:
            plan = validate_family(args.family, slots)
        except ValueError as exc:
            _p(f"오류: {exc}")
            return 1

        cases: list[TestCase] = []
        for i, item in enumerate(items):
            error = _validate_item(item, i)
            if error:
                _p(f"오류: {error}")
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
            _p(_no_content(args.content))
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
        for s in skipped:
            _p(f"   {s.slot_key:<16} ({s.prompt_hint}) → {s.family} TC 없음  "
               f"[{SLOT_STATUS_LABEL[s.status]}]")
        _p("\n   이어서 채우려면 Claude Code에서 인터뷰를 재개하세요.")
    return 0


def cmd_knowledge(args: argparse.Namespace, cfg: AppConfig) -> int:
    path = cfg.knowledge_path / f"{args.game}.db"
    if not path.exists():
        # 조건이 다르다 (컨텐츠가 아니라 게임 DB) 라 문구를 공유하지 않지만,
        # 계약("다음 조치를 함께 알린다")은 같이 지킨다.
        _p(f"'{args.game}' 지식 DB가 없습니다 ({path}). "
           f"'qatc slot init <컨텐츠> --game {args.game}'을 먼저 실행하세요.")
        return 1

    rows = []
    with KnowledgeStore(path) as store:
        for c in store.list_contents():
            slots = store.slots(c.name)
            planned, skipped = plan_families(slots)
            rows.append({
                "content": c.name,
                "types": c.types,
                "total": len(slots),
                "filled": sum(1 for s in slots if s.status is SlotStatus.FILLED),
                "planned_families": len(planned),
                "skipped_families": len(skipped),
                "testcases": len(store.testcases(c.name)),
            })

    if args.json:
        _p(json.dumps({"game": args.game, "contents": rows}, ensure_ascii=False, indent=2))
        return 0

    _p(f"[{args.game}] 컨텐츠 {len(rows)}개\n")
    _p(f"  {'컨텐츠':<14} {'채움':>7}  {'계열':>7}  {'TC':>4}")
    _p(f"  {'-' * 40}")
    for r in rows:
        _p(f"  {r['content']:<14} {r['filled']:>3}/{r['total']:<3}  "
           f"{r['planned_families']:>3}/{r['planned_families'] + r['skipped_families']:<3}  "
           f"{r['testcases']:>4}")
    return 0


def cmd_export(args: argparse.Namespace, cfg: AppConfig) -> int:
    from .export.tc_excel import export_tc_excel

    store = resolve_store(cfg, args.game, args.content)
    try:
        if store.get_content(args.content) is None:
            _p(_no_content(args.content))
            return 1
        cases = store.testcases(args.content)
        _, skipped = plan_families(store.slots(args.content))
        game = store.game
    finally:
        store.close()

    out = Path(args.out) if args.out else cfg.knowledge_path / f"{game}_{args.content}_TC.xlsx"
    path = export_tc_excel(args.content, cases, skipped, out)
    _p(f"✓ {path}  (TC {len(cases)}건 · 미확인 {len(skipped)}건)")
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

    kn = sub.add_parser("knowledge", help="게임별 지식 커버리지")
    kn.add_argument("--game", "-g", required=True)
    kn.add_argument("--json", action="store_true")
    kn.set_defaults(func=cmd_knowledge)

    ex = sub.add_parser("export", help="xlsx 출력")
    ex.add_argument("content")
    ex.add_argument("--game", "-g")
    ex.add_argument("--out", "-o")
    ex.set_defaults(func=cmd_export)

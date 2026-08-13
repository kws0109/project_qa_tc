"""QATC 명령줄 진입점.

새 인터뷰 기반 흐름(slot / tc / knowledge / export)은 `cli_knowledge.py`를
보라. 아래는 레거시 녹화 파이프라인으로, `--legacy` 뒤에서만 노출된다.

::

    qatc --legacy record  --profile genshin      게임 플레이 녹화
    qatc --legacy analyze <session>              화면 상태·전이 그래프 추출
    qatc --legacy name    <session>              LLM으로 화면 명명
    qatc --legacy review  <session>              리뷰 GUI 실행
    qatc --legacy tc      <session>              테스트케이스 생성
    qatc --legacy export  <session>              xlsx + 다이어그램 출력
    qatc --legacy run     --profile genshin      녹화→분석→명명→TC→출력 한 번에
    qatc --legacy list                           세션 목록
    qatc config                                  설정 / API 키

**LLM이 필요한 명령(name, tc)과 아닌 명령을 분리**했다. API 키 없이도
record → analyze → export까지 돌아가고, 화면 이름만 자동 이름으로 남는다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

from .config import AppConfig, get_api_key, set_api_key
from .console import _p


def _progress() -> Callable[[str, float], None]:
    last = {"msg": ""}

    def report(msg: str, pct: float) -> None:
        if msg != last["msg"]:
            _p(f"  [{pct:4.0%}] {msg}")
            last["msg"] = msg

    return report


# ---------------------------------------------------------------- 세션 탐색


def _resolve_session(cfg: AppConfig, token: str | None):
    """세션 ID·경로·'latest'를 세션 폴더로 해석한다."""
    from .storage import SessionStore, list_sessions

    sessions = list_sessions(cfg.sessions_path)
    if not sessions:
        raise SystemExit("세션이 없습니다. 먼저 'qatc record'로 녹화하세요.")

    if token in (None, "latest", "last"):
        return SessionStore.open_existing(sessions[0][0])

    direct = Path(token)
    if (direct / "session.db").exists():
        return SessionStore.open_existing(direct)

    for path, meta in sessions:
        if meta.id == token or path.name == token:
            return SessionStore.open_existing(path)

    matches = [(p, m) for p, m in sessions if token in m.id]
    if len(matches) == 1:
        return SessionStore.open_existing(matches[0][0])
    if len(matches) > 1:
        raise SystemExit(
            f"'{token}'과 일치하는 세션이 여러 개입니다:\n"
            + "\n".join(f"  {m.id}" for _, m in matches[:10])
        )
    raise SystemExit(
        f"세션 '{token}'을(를) 찾을 수 없습니다. 'qatc list'로 목록을 확인하세요."
    )


def _image_loader(store):
    """프레임 ID → BGR 이미지. LLM 계층에 주입한다."""
    import cv2

    def load(frame_id: str):
        frame = store.frame(frame_id)
        if frame is None:
            return None
        return cv2.imread(str(store.frame_path(frame)), cv2.IMREAD_COLOR)

    return load


def _profile_for(cfg: AppConfig, store):
    from .profiles import generic_profile, load_profiles

    meta = store.get_session()
    profiles = load_profiles(cfg.profiles_path)
    return profiles.get(meta.profile_name) or generic_profile(meta.game_name)


# ---------------------------------------------------------------- record


def cmd_record(args: argparse.Namespace, cfg: AppConfig) -> int:
    from .capture import enumerate_windows, find_game_window, get_window_info
    from .profiles import generic_profile, get_profile
    from .record import Recorder, create_session

    if args.profile:
        profile = get_profile(cfg.profiles_path, args.profile)
    else:
        profile = generic_profile()

    window = None
    if args.hwnd:
        window = get_window_info(int(args.hwnd))
    elif args.profile:
        window = find_game_window(profile)

    if window is None:
        _p("게임 창을 자동으로 찾지 못했습니다. 창을 선택하세요:\n")
        windows = [w for w in enumerate_windows() if w.is_capturable]
        for i, w in enumerate(windows):
            _p(f"  [{i:2d}] {w.width}x{w.height:<6} {w.process_name:<24} {w.title[:44]}")
        try:
            choice = int(input("\n번호 입력: ").strip())
            window = windows[choice]
        except (ValueError, IndexError, KeyboardInterrupt):
            raise SystemExit("취소되었습니다.")

    _p(f"\n대상: {window.title[:56]} ({window.width}x{window.height})")
    _p(f"프로파일: {profile.name}")
    if profile.is_emulator:
        _p("  ⚠ 에뮬레이터 프로파일입니다. 캡처 영역(capture_roi)이 게임 화면과")
        _p("    맞는지 첫 프레임을 확인하세요.")

    # 권한 불일치는 시작 전에 잡는다 — 5분 녹화 후 입력 0건을 발견하는 건 최악이다
    from .capture.window import input_hook_blocked_by

    blocked = input_hook_blocked_by(window)
    if blocked:
        _p(f"\n  ⛔ {blocked}")
        if not args.force:
            _p("\n  그래도 진행하려면 --force 를 붙이세요 (화면만 기록됩니다).")
            raise SystemExit(1)
        _p("\n  --force 지정됨: 입력 없이 화면만 기록합니다.")

    _p("\n조작:  F9 = 북마크   F10 = 일시정지/재개   Ctrl+C = 종료\n")

    store = create_session(cfg.sessions_path, profile, window, note=args.note or "")
    recorder = Recorder(store, profile, window, cfg.capture, on_status=_p,
                        preferred_backend=args.backend)

    stop_file = Path(args.stop_file) if args.stop_file else None
    if stop_file is not None:
        stop_file.unlink(missing_ok=True)   # 이전 실행의 잔재를 지우고 시작
        _p(f"종료 신호 파일: {stop_file}  (이 파일을 만들면 정상 종료합니다)")

    recorder.start()
    try:
        _wait_for_stop(recorder, stop_file, args.max_minutes)
    except KeyboardInterrupt:
        _p("\n종료 중...")
    finally:
        recorder.stop()
        if stop_file is not None:
            stop_file.unlink(missing_ok=True)
        _p(f"\n세션 저장: {store.root}")
        _p(f"다음 단계: qatc analyze {store.get_session().id}")
        store.close()
    return 0


def _wait_for_stop(recorder, stop_file: Path | None, max_minutes: float) -> None:
    """Ctrl+C · 종료 신호 파일 · 최대 시간 중 먼저 오는 것까지 기다린다.

    신호 파일 경로는 백그라운드 실행용이다 — 터미널에 Ctrl+C를 보낼 수 없는
    상황(자동화, 원격 조작)에서도 **정상 종료**시킬 수 있어야 한다. 강제 종료하면
    세션 메타데이터가 마무리되지 않고 마지막 프레임이 유실된다.

    최대 시간은 안전장치다. 신호 파일을 못 만드는 사고가 나도 입력 훅이 무한정
    떠 있으면 안 된다.
    """
    import time

    deadline = time.monotonic() + max_minutes * 60 if max_minutes > 0 else None
    while True:
        if stop_file is not None and stop_file.exists():
            _p("\n종료 신호를 받았습니다.")
            return
        if deadline is not None and time.monotonic() > deadline:
            _p(f"\n최대 녹화 시간({max_minutes:.0f}분)에 도달했습니다.")
            return
        time.sleep(0.3)


# ---------------------------------------------------------------- analyze


def cmd_analyze(args: argparse.Namespace, cfg: AppConfig) -> int:
    from .analyze import analyze_session

    store = _resolve_session(cfg, args.session)
    profile = _profile_for(cfg, store)
    _p(f"분석: {store.get_session().id} ({profile.name})\n")

    disambiguate = None
    if not args.no_llm and get_api_key():
        disambiguate = _build_disambiguator(cfg, store, profile)

    try:
        built, progress = analyze_session(
            store, profile, cfg.analyze,
            disambiguate=disambiguate,
            on_progress=_progress(),
            reuse_volatility=not args.fresh_volatility,
        )
    finally:
        store.close()

    _p("\n" + progress.summary())
    _p(f"\n{built.summary()}")
    _p(f"\n다음 단계: qatc name {store.get_session().id}"
       if get_api_key() else "\n다음 단계: qatc export (LLM 없이 다이어그램만)")
    return 0


def _build_disambiguator(cfg: AppConfig, store, profile):
    """애매한 화면 쌍 판별용 LLM 콜백.

    분석기는 프레임을 **인덱스**로 말하고 LLM은 **이미지**가 필요하므로
    변환표를 만들어 넘긴다. 인덱스 순서는 ``store.frames()``와 같아야 한다.
    """
    from .llm import LlmClient, make_disambiguator

    client = LlmClient(cfg.llm)
    frame_ids = [f.id for f in store.frames()]
    return make_disambiguator(
        client, frame_ids, _image_loader(store), profile,
        model=cfg.llm.model_bulk, on_progress=_progress(),
    )


# ---------------------------------------------------------------- name


def cmd_name(args: argparse.Namespace, cfg: AppConfig) -> int:
    from .llm import CostTracker, LlmClient, name_states

    store = _resolve_session(cfg, args.session)
    if not store.has_graph():
        store.close()
        raise SystemExit("분석 결과가 없습니다. 먼저 'qatc analyze'를 실행하세요.")

    profile = _profile_for(cfg, store)
    graph = store.load_graph()
    tracker = CostTracker(budget_usd=cfg.llm.budget_usd)
    client = LlmClient(cfg.llm, tracker)

    if not client.available:
        store.close()
        raise SystemExit("API 키가 없습니다. 'qatc config --set-api-key'로 등록하세요.")

    _p(f"화면 명명: {store.get_session().id} · 화면 {len(graph.states)}개\n")
    try:
        report = name_states(
            client, graph, profile, _image_loader(store),
            model=cfg.llm.model_bulk, on_progress=_progress(),
        )
        store.save_graph(graph)
    finally:
        store.close()

    _p("\n" + report.summary())
    for note in report.notes[:5]:
        _p(f"  ※ {note}")
    _p("\n" + tracker.summary())
    for sid, state in sorted(graph.states.items()):
        mark = "✓" if state.confidence >= 0.7 else "?"
        _p(f"  {mark} {sid}: {state.name}  [{state.category}]")
    return 0


# ---------------------------------------------------------------- tc


def cmd_tc(args: argparse.Namespace, cfg: AppConfig) -> int:
    from .llm import CostTracker, LlmClient, generate_testcases
    from .llm.tcgen import estimate_cost_usd

    store = _resolve_session(cfg, args.session)
    if not store.has_graph():
        store.close()
        raise SystemExit("분석 결과가 없습니다. 먼저 'qatc analyze'를 실행하세요.")

    profile = _profile_for(cfg, store)
    graph = store.load_graph()
    tracker = CostTracker(budget_usd=cfg.llm.budget_usd)
    client = LlmClient(cfg.llm, tracker)

    if not client.available:
        store.close()
        raise SystemExit("API 키가 없습니다. 'qatc config --set-api-key'로 등록하세요.")

    estimate = estimate_cost_usd(graph, cfg.llm.model_deep, cfg.llm.max_derived_screens)
    _p(f"TC 생성: {store.get_session().id}")
    _p(f"  화면 {len(graph.visible_states())}개 · 전이 {len(graph.transitions)}개")
    _p(f"  예상 비용 약 ${estimate:.2f} (예산 ${cfg.llm.budget_usd:.2f})\n")

    if cfg.llm.show_cost_estimate and not args.yes:
        try:
            if input("진행할까요? [Y/n] ").strip().lower() in ("n", "no"):
                store.close()
                return 0
        except (EOFError, KeyboardInterrupt):
            store.close()
            return 1

    from .icons import IconStore

    icon_store = IconStore.load(profile.key)
    if len(icon_store):
        stats = icon_store.stats()
        _p(f"  아이콘 사전: {stats['complete']}개 활용 (샘플 {stats['samples']}개)\n")

    try:
        testcases, report = generate_testcases(
            client, graph, profile, _image_loader(store),
            model=cfg.llm.model_deep,
            include_derived=not args.happy_only,
            include_reverse=not args.happy_only,
            max_derived_screens=cfg.llm.max_derived_screens,
            icon_store=icon_store,
            on_progress=_progress(),
        )
        store.save_testcases(testcases)
    finally:
        store.close()

    _p("\n" + report.summary())
    for note in report.notes[:5]:
        _p(f"  ※ {note}")
    _p("\n" + tracker.summary())
    _p(f"\n다음 단계: qatc export {store.get_session().id}")
    return 0


# ---------------------------------------------------------------- export


def cmd_export(args: argparse.Namespace, cfg: AppConfig) -> int:
    from .export import export_excel, export_mermaid

    store = _resolve_session(cfg, args.session)
    if not store.has_graph():
        store.close()
        raise SystemExit("분석 결과가 없습니다. 먼저 'qatc analyze'를 실행하세요.")

    graph = store.load_graph()
    testcases = store.testcases()
    meta = store.get_session()

    try:
        xlsx = export_excel(store, graph, testcases, args.output,
                            embed_thumbnails=not args.no_thumbnails)
        diagram = export_mermaid(
            graph, testcases, store.export_dir / f"{meta.id}_flow.md"
        )
    finally:
        store.close()

    _p(f"Excel      : {xlsx}")
    _p(f"다이어그램 : {diagram}")
    if not testcases:
        _p("\n※ 테스트케이스가 없습니다 — 'qatc --legacy tc'로 생성하면 시트가 채워집니다.")
        _p("  지금은 화면 전이 다이어그램과 커버리지 시트만 들어 있습니다.")
    return 0


# ---------------------------------------------------------------- review


def cmd_review(args: argparse.Namespace, cfg: AppConfig) -> int:
    store = _resolve_session(cfg, args.session)
    if not store.has_graph():
        store.close()
        raise SystemExit("분석 결과가 없습니다. 먼저 'qatc analyze'를 실행하세요.")
    try:
        from .review import run_review_app
    except ImportError as exc:
        store.close()
        raise SystemExit(
            f"리뷰 GUI를 열 수 없습니다: {exc}\n"
            f"PySide6를 설치하세요:  pip install PySide6"
        )
    return run_review_app(store, cfg)


# ---------------------------------------------------------------- run (전체)


def cmd_run(args: argparse.Namespace, cfg: AppConfig) -> int:
    """녹화부터 출력까지 한 번에. 각 단계는 개별 명령과 동일하다."""
    rc = cmd_record(args, cfg)
    if rc != 0:
        return rc
    args.session = "latest"
    args.no_llm = False
    args.fresh_volatility = False
    _p("\n" + "=" * 56 + "\n")
    cmd_analyze(args, cfg)
    if get_api_key():
        _p("\n" + "=" * 56 + "\n")
        cmd_name(args, cfg)
        _p("\n" + "=" * 56 + "\n")
        cmd_tc(args, cfg)
    _p("\n" + "=" * 56 + "\n")
    return cmd_export(args, cfg)


# ---------------------------------------------------------------- list / config


def cmd_list(args: argparse.Namespace, cfg: AppConfig) -> int:
    from .storage import SessionStore, list_sessions

    sessions = list_sessions(cfg.sessions_path)
    if not sessions:
        _p("세션이 없습니다. 'qatc record'로 녹화를 시작하세요.")
        return 0

    _p(f"{'세션 ID':<28} {'게임':<18} {'프레임':>7} {'화면':>5} {'TC':>5}  상태")
    _p("-" * 84)
    for path, meta in sessions:
        with SessionStore.open_existing(path) as store:
            frames = len(store.frames())
            states = len(store.load_graph().states) if store.has_graph() else 0
            tcs = len(store.testcases())
        stage = "녹화됨"
        if states:
            stage = "분석됨"
        if tcs:
            stage = "TC 생성됨"
        _p(f"{meta.id:<28} {meta.game_name[:16]:<18} {frames:>7} {states:>5} {tcs:>5}  {stage}")
    return 0


def cmd_icons(args: argparse.Namespace, cfg: AppConfig) -> int:
    """아이콘 사전 조회·관리. 등록은 리뷰 GUI에서 한다."""
    from .icons import IconStore, list_dictionaries

    if not args.game:
        dicts = list_dictionaries()
        if not dicts:
            _p("등록된 아이콘 사전이 없습니다.")
            _p("\n리뷰 GUI에서 아이콘을 더블클릭하거나, '아이콘 영역 지정'을 켜고")
            _p("드래그해 등록하세요:  qatc review latest")
            return 0
        _p(f"{'게임':<20} {'아이콘':>7} {'완성':>6} {'샘플':>7}")
        _p("-" * 44)
        for key, stats in dicts:
            _p(f"{key:<20} {stats['icons']:>7} {stats['complete']:>6} {stats['samples']:>7}")
        _p("\n'완성'은 이름과 동작이 모두 채워져 TC 생성에 쓰이는 아이콘 수입니다.")
        return 0

    store = IconStore.load(args.game)
    if not len(store):
        raise SystemExit(f"'{args.game}' 사전이 비어 있습니다.")

    if args.export:
        dest = store.export_to(Path(args.export))
        _p(f"사전을 내보냈습니다: {dest}")
        return 0

    stats = store.stats()
    _p(f"{args.game} 아이콘 사전 — {stats['icons']}개 (완성 {stats['complete']}, 샘플 {stats['samples']})")
    _p(f"위치: {store.root}\n")
    for entry in store:
        flag = "✓" if entry.is_complete else "!"
        _p(f"  {flag} {entry.name:<16} {entry.action.describe()}")
        if entry.action.expected:
            _p(f"      기대: {entry.action.expected}")
        detail = f"      샘플 {entry.sample_count}개"
        if entry.seen_screens:
            detail += f" · 관측: {', '.join(entry.seen_screens[:3])}"
        _p(detail)
    incomplete = [e for e in store if not e.is_complete]
    if incomplete:
        _p(f"\n! 표시 {len(incomplete)}개는 동작이 지정되지 않아 TC 생성에 쓰이지 않습니다.")
        _p("  qatc review 에서 해당 아이콘을 더블클릭해 채우세요.")
    return 0


def cmd_config(args: argparse.Namespace, cfg: AppConfig) -> int:
    if args.set_api_key:
        import getpass

        key = getpass.getpass("Anthropic API 키 (입력은 표시되지 않습니다): ").strip()
        if not key:
            raise SystemExit("취소되었습니다.")
        where = set_api_key(key)
        _p(f"API 키를 {where}에 저장했습니다.")
        return 0

    if args.set_budget is not None:
        cfg.llm.budget_usd = float(args.set_budget)
        _p(f"세션 예산을 ${cfg.llm.budget_usd:.2f}로 설정했습니다.")
        cfg.save()
        return 0

    path = cfg.save()
    _p(f"설정 파일 : {path}")
    _p(f"세션 폴더 : {cfg.sessions_path}")
    _p(f"프로파일  : {cfg.profiles_path}")
    _p(f"API 키    : {'등록됨' if get_api_key() else '없음  (qatc config --set-api-key)'}")
    _p(f"모델      : 대량={cfg.llm.model_bulk} / 정밀={cfg.llm.model_deep}")
    _p(f"세션 예산 : ${cfg.llm.budget_usd:.2f}")
    _p(f"파생 TC 화면 상한 : {cfg.llm.max_derived_screens}개  (비용의 주 변수)")

    from .profiles import load_profiles

    profiles = load_profiles(cfg.profiles_path)
    _p(f"\n사용 가능한 프로파일 ({len(profiles)}개):")
    for key, prof in sorted(profiles.items()):
        _p(f"  {key:<16} {prof.name}")
    return 0


# ---------------------------------------------------------------- 파서


def build_parser(legacy: bool = False) -> argparse.ArgumentParser:
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

녹화 기반 명령은 'qatc --legacy <명령>' 으로만 노출됩니다.
""",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    if legacy:
        def session_arg(sp: argparse.ArgumentParser) -> None:
            sp.add_argument("session", nargs="?", default="latest",
                            help="세션 ID 또는 경로 (기본: 가장 최근 세션)")

        rec = sub.add_parser("record", help="게임 플레이 녹화")
        rec.add_argument("--profile", "-p", help="게임 프로파일 (genshin, starrail, wuwa, bluearchive)")
        rec.add_argument("--hwnd", help="대상 창 핸들을 직접 지정")
        rec.add_argument("--backend", choices=["wgc", "dxgi", "gdi"], help="캡처 백엔드 강제")
        rec.add_argument("--note", help="세션 메모")
        rec.add_argument(
            "--stop-file", metavar="경로",
            help="이 경로에 파일이 생기면 정상 종료합니다 (백그라운드 실행용)",
        )
        rec.add_argument(
            "--max-minutes", type=float, default=60.0,
            help="최대 녹화 시간(분). 0이면 무제한. 기본 60분",
        )
        rec.add_argument(
            "--force", action="store_true",
            help="권한 경고를 무시하고 진행 (입력 없이 화면만 기록됩니다)",
        )
        rec.set_defaults(func=cmd_record)

        ana = sub.add_parser("analyze", help="화면 상태·전이 그래프 추출")
        session_arg(ana)
        ana.add_argument("--no-llm", action="store_true", help="애매한 화면 판별에 LLM을 쓰지 않음")
        ana.add_argument("--fresh-volatility", action="store_true",
                         help="이전 세션의 변동성 학습 결과를 재사용하지 않음")
        ana.set_defaults(func=cmd_analyze)

        nm = sub.add_parser("name", help="LLM으로 화면 이름 붙이기")
        session_arg(nm)
        nm.set_defaults(func=cmd_name)

        tc = sub.add_parser("tc", help="테스트케이스 생성")
        session_arg(tc)
        tc.add_argument("--happy-only", action="store_true",
                        help="정상 경로만 생성 (추론 TC 제외 — 비용 절감)")
        tc.add_argument("--yes", "-y", action="store_true", help="비용 확인을 묻지 않음")
        tc.set_defaults(func=cmd_tc)

        rv = sub.add_parser("review", help="리뷰 GUI 실행")
        session_arg(rv)
        rv.set_defaults(func=cmd_review)

        ex = sub.add_parser("export", help="xlsx + 다이어그램 출력")
        session_arg(ex)
        ex.add_argument("--output", "-o", help="xlsx 저장 경로")
        ex.add_argument("--no-thumbnails", action="store_true", help="썸네일 삽입 생략 (빠름)")
        ex.set_defaults(func=cmd_export)

        run = sub.add_parser("run", help="녹화→분석→명명→TC→출력 한 번에")
        run.add_argument("--profile", "-p", help="게임 프로파일")
        run.add_argument("--hwnd")
        run.add_argument("--backend", choices=["wgc", "dxgi", "gdi"])
        run.add_argument("--note")
        run.add_argument("--stop-file", metavar="경로")
        run.add_argument("--max-minutes", type=float, default=60.0)
        run.add_argument("--force", action="store_true")
        run.add_argument("--happy-only", action="store_true")
        run.add_argument("--yes", "-y", action="store_true")
        run.add_argument("--output", "-o")
        run.add_argument("--no-thumbnails", action="store_true")
        run.set_defaults(func=cmd_run)

        ls = sub.add_parser("list", help="세션 목록")
        ls.set_defaults(func=cmd_list)

        ic = sub.add_parser("icons", help="아이콘 사전 조회 (등록은 qatc review에서)")
        ic.add_argument("game", nargs="?", help="게임 프로파일 키. 생략하면 사전 목록")
        ic.add_argument("--export", metavar="경로", help="사전을 폴더로 내보내기 (백업·공유용)")
        ic.set_defaults(func=cmd_icons)
    else:
        from .cli_knowledge import register as _register_knowledge
        _register_knowledge(sub)

    cf = sub.add_parser("config", help="설정 확인 / API 키 등록")
    cf.add_argument("--set-api-key", action="store_true", help="Anthropic API 키 등록")
    cf.add_argument("--set-budget", type=float, help="세션당 LLM 예산(USD) 설정")
    cf.set_defaults(func=cmd_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    legacy = "--legacy" in raw
    if legacy:
        raw.remove("--legacy")
    args = build_parser(legacy=legacy).parse_args(raw)
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
    except Exception as exc:
        _p(f"\n오류: {type(exc).__name__}: {exc}")
        if "--debug" in sys.argv:
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())

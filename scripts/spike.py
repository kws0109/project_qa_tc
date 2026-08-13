"""M0 리스크 스파이크 — 설계 가정을 실제 환경에서 검증한다.

계획 단계에서 "실패하면 설계가 바뀐다"고 표시한 네 가지를 코드를 더 쌓기 전에
확인한다. 게임을 실행해 두고 돌리면 그 게임에서의 실제 결과가 나온다.

::

    python scripts/spike.py                    창 목록에서 선택
    python scripts/spike.py --profile genshin  프로파일로 자동 탐색
    python scripts/spike.py --skip-hook        입력 훅 검사 생략

검사 항목:

1. **캡처 백엔드** — WGC / DXGI / GDI 중 무엇이 실제 프레임을 주는가.
   전부 실패하면 캡처 전략을 다시 세워야 한다.
2. **OCR** — 게임 화면의 한글을 얼마나 읽는가. 못 읽으면 텍스트 신호를 버리고
   셀·구조 시그니처만으로 가야 한다.
3. **입력 훅** — 안티치트가 도는 게임에서 저수준 훅이 이벤트를 받는가.
   못 받으면 수동 단축키 방식으로 바꿔야 한다.
4. **변동성 학습** — 같은 화면의 연속 프레임에서 애니메이션 영역이 실제로
   분리되는가. 안 되면 상태 식별의 주 신호를 다시 골라야 한다.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

RESULT_DIR = Path(__file__).resolve().parent.parent / "sessions" / "_spike"


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    impact: str = ""
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        out = [f"[{mark}] {self.name}", f"       {self.detail}"]
        out.extend(f"       · {n}" for n in self.notes)
        if not self.passed and self.impact:
            out.append(f"       → {self.impact}")
        return "\n".join(out)


def _p(msg: str = "") -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("utf-8", "replace").decode("utf-8", "replace"), flush=True)


# ---------------------------------------------------------------- 1. 캡처


def check_capture(window) -> tuple[Check, list[np.ndarray]]:
    from qatc.capture.base import CaptureTarget, available_backends, probe

    target = CaptureTarget(window=window)
    results: list[str] = []
    working: list[str] = []
    frames: list[np.ndarray] = []

    for cls in available_backends():
        t0 = time.time()
        ok = probe(cls, target)
        results.append(f"{cls.name}: {'PASS' if ok else 'fail'} ({time.time() - t0:.1f}s)")
        if ok:
            working.append(cls.name)

    if working:
        from qatc.capture.base import select_backend

        backend, name = select_backend(target)
        backend.start()
        try:
            # 변동성 학습용으로 연속 프레임을 모은다 (0.5초 간격 14장 = 7초)
            _p("       프레임 수집 중 (7초)... 게임 화면을 그대로 두세요.")
            for _ in range(14):
                frame = backend.grab()
                if frame is not None:
                    frames.append(frame.copy())
                time.sleep(0.5)
        finally:
            backend.stop()

    check = Check(
        name="1. 캡처 백엔드",
        passed=bool(working),
        detail=f"동작: {', '.join(working) or '없음'}   |   {' / '.join(results)}",
        impact="게임을 창모드 또는 테두리없는창으로 실행해 보세요. "
        "그래도 실패하면 캡처 전략을 재설계해야 합니다.",
        notes=[f"수집한 프레임 {len(frames)}장"] if frames else [],
    )
    return check, frames


# ---------------------------------------------------------------- 2. OCR


def check_ocr(frames: list[np.ndarray], lang: str = "ko") -> Check:
    if not frames:
        return Check("2. OCR (한글 인식)", False, "캡처 프레임이 없어 검사할 수 없습니다")

    from qatc.analyze.ocr import OcrEngine, text_signature

    engine = OcrEngine(lang=lang)
    t0 = time.time()
    lines = engine.read(frames[len(frames) // 2])
    elapsed = time.time() - t0

    if not engine.available:
        return Check(
            "2. OCR (한글 인식)",
            False,
            f"OCR 엔진을 초기화하지 못했습니다: {engine.load_error}",
            impact="첫 실행 시 모델을 내려받습니다. 인터넷 연결을 확인하거나, "
            "텍스트 신호 없이 진행하세요(정확도만 낮아집니다).",
        )

    tokens = text_signature(lines)
    korean = [t for t in tokens if any("가" <= c <= "힣" for c in t)]
    sample = ", ".join(tokens[:12]) or "(없음)"

    # 기준: 한글 토큰 3개 이상이면 보조 신호로 쓸 만하다.
    passed = len(korean) >= 3
    return Check(
        name="2. OCR (한글 인식)",
        passed=passed,
        detail=f"토큰 {len(tokens)}개 (한글 {len(korean)}개), 추론 {elapsed:.1f}s",
        impact="텍스트 신호를 못 쓰지만 셀·구조 시그니처만으로도 동작합니다. "
        "게임 UI가 한글인지, 화면에 텍스트가 있는지 확인하세요.",
        notes=[f"인식 예: {sample}"],
    )


# ---------------------------------------------------------------- 3. 입력 훅


def check_input_hook(window, seconds: float = 8.0) -> Check:
    from qatc.capture import get_window_info
    from qatc.record.hooks import InputObserver

    _p(f"       게임 창을 클릭하고 {seconds:.0f}초 안에 마우스/키보드를 몇 번 조작하세요.")
    observer = InputObserver()
    observer.start()
    events: list[str] = []
    in_window = 0
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            try:
                raw = observer.queue.get(timeout=0.2)
            except Exception:
                continue
            events.append(raw.kind)
            info = get_window_info(window.hwnd)
            if info and info.is_foreground:
                if raw.kind in ("down", "up", "scroll"):
                    if info.to_normalized(raw.x, raw.y) is not None:
                        in_window += 1
                else:
                    in_window += 1
    finally:
        observer.stop()

    kinds = ", ".join(sorted(set(events))) or "없음"
    passed = len(events) > 0
    return Check(
        name="3. 입력 훅 (읽기 전용 관찰)",
        passed=passed,
        detail=f"이벤트 {len(events)}건 수신 (게임 창 내 {in_window}건) · 종류: {kinds}",
        impact="저수준 훅이 이벤트를 받지 못합니다. 관리자 권한으로 실행되는 게임이라면 "
        "이 스크립트도 관리자 권한으로 실행해 보세요.",
        notes=(
            ["게임 창 안에서 발생한 입력이 0건입니다 — 게임 창을 포그라운드로 두고 다시 시도하세요"]
            if passed and in_window == 0
            else []
        ),
    )


# ---------------------------------------------------------------- 4. 변동성


def check_volatility(frames: list[np.ndarray]) -> Check:
    if len(frames) < 8:
        return Check(
            "4. 변동성 학습 (애니메이션 분리)",
            False,
            f"프레임이 {len(frames)}장뿐이라 학습할 수 없습니다 (8장 이상 필요)",
        )

    from qatc.analyze.hashing import ScreenSignature
    from qatc.analyze.motion import learn_from_frames

    timestamps = [i * 0.5 for i in range(len(frames))]
    vol = learn_from_frames(frames, timestamps)

    a, b = ScreenSignature.of(frames[0]), ScreenSignature.of(frames[-1])
    naive = a.similarity(b, None)
    learned = a.similarity(b, vol)

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import cv2

        cv2.imwrite(str(RESULT_DIR / "volatility.png"), vol.to_debug_image())
        cv2.imwrite(str(RESULT_DIR / "frame_first.jpg"), frames[0])
        cv2.imwrite(str(RESULT_DIR / "frame_last.jpg"), frames[-1])
    except Exception:
        pass

    # 같은 화면의 연속 프레임이므로 유사도가 0.9 이상이어야 정상이다.
    passed = learned >= 0.90
    return Check(
        name="4. 변동성 학습 (애니메이션 분리)",
        passed=passed,
        detail=(
            f"안정 셀 {vol.stable_ratio:.0%} · 같은 화면 유사도 "
            f"{naive:.3f} → {learned:.3f} (학습 후)"
        ),
        impact="같은 화면인데 유사도가 낮습니다. 화면이 실제로 바뀌었거나 "
        "애니메이션이 화면 전체를 덮고 있을 수 있습니다. "
        "정적인 메뉴 화면에서 다시 시도해 보세요.",
        notes=[f"변동성 히트맵: {RESULT_DIR / 'volatility.png'}"],
    )


# ---------------------------------------------------------------- 메인


def pick_window(profile_key: str | None):
    from qatc.capture import enumerate_windows, find_game_window
    from qatc.config import AppConfig
    from qatc.profiles import get_profile

    if profile_key:
        cfg = AppConfig.load()
        profile = get_profile(cfg.profiles_path, profile_key)
        window = find_game_window(profile)
        if window is not None:
            _p(f"프로파일 '{profile.name}'로 창을 찾았습니다: {window.title[:50]}")
            return window, profile
        _p(f"프로파일 '{profile.name}'에 해당하는 창을 찾지 못했습니다. 직접 선택하세요.\n")

    windows = [w for w in enumerate_windows() if w.is_capturable]
    for i, w in enumerate(windows):
        _p(f"  [{i:2d}] {w.width}x{w.height:<6} {w.process_name:<24} {w.title[:44]}")
    try:
        choice = int(input("\n대상 창 번호: ").strip())
        return windows[choice], None
    except (ValueError, IndexError, KeyboardInterrupt):
        raise SystemExit("취소되었습니다.")


def main() -> int:
    parser = argparse.ArgumentParser(description="QATC M0 리스크 스파이크")
    parser.add_argument("--profile", "-p", help="게임 프로파일로 창 자동 탐색")
    parser.add_argument("--skip-hook", action="store_true", help="입력 훅 검사 생략")
    parser.add_argument("--hook-seconds", type=float, default=8.0)
    args = parser.parse_args()

    _p("=" * 66)
    _p("QATC M0 리스크 스파이크")
    _p("=" * 66)
    _p("설계 가정 네 가지를 실제 환경에서 확인합니다.")
    _p("게임을 실행해 두고 정적인 메뉴 화면(홈/캐릭터 등)에 두면 가장 정확합니다.\n")

    window, profile = pick_window(args.profile)
    lang = profile.ui_language if profile else "ko"
    _p(f"\n대상: {window.title[:56]} ({window.width}x{window.height})")
    _p(f"DPI 인식: 활성\n")

    checks: list[Check] = []

    _p("1/4 캡처 백엔드 검사...")
    capture_check, frames = check_capture(window)
    checks.append(capture_check)

    _p("2/4 OCR 검사...")
    checks.append(check_ocr(frames, lang))

    if args.skip_hook:
        _p("3/4 입력 훅 검사 — 생략됨")
    else:
        _p("3/4 입력 훅 검사...")
        checks.append(check_input_hook(window, args.hook_seconds))

    _p("4/4 변동성 학습 검사...")
    checks.append(check_volatility(frames))

    _p("\n" + "=" * 66)
    _p("결과")
    _p("=" * 66)
    for check in checks:
        _p(check.render())
        _p("")

    passed = sum(1 for c in checks if c.passed)
    _p("=" * 66)
    _p(f"통과 {passed}/{len(checks)}")
    if passed == len(checks):
        _p("\n모든 가정이 확인됐습니다. 바로 녹화를 시작할 수 있습니다:")
        _p(f"  qatc record{f' --profile {args.profile}' if args.profile else ''}")
    else:
        _p("\n실패 항목의 '→' 안내를 먼저 해결하세요.")
        _p("일부 실패는 치명적이지 않습니다 — OCR 실패는 정확도만 낮추고,")
        _p("캡처 실패만이 진행 자체를 막습니다.")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())

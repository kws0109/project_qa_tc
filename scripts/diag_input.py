"""입력 훅 진단 — 이벤트가 **어디서** 사라지는지 짚어낸다.

레코더가 입력을 기록하지 못할 때 원인은 두 곳 중 하나다. 둘은 대응이 완전히 다르므로
반드시 구분해야 한다.

===================== ============================================ =====================
실패 지점              증상                                          대응
===================== ============================================ =====================
A. 훅이 못 받음         저수준 훅에 이벤트 자체가 도착하지 않음          권한·안티치트 문제
B. 레코더가 버림        훅은 받았는데 포그라운드/좌표 검사에서 탈락      코드 버그
===================== ============================================ =====================

이 스크립트는 훅에 도착한 **모든** 이벤트를 그대로 찍고, 레코더가 적용하는 검사를
하나씩 통과시켜 어디서 걸러지는지 보여준다.

::

    python scripts/diag_input.py --profile starrail --seconds 20
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _p(msg: str = "") -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("utf-8", "replace").decode("utf-8", "replace"), flush=True)


def _is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _integrity_level(pid: int) -> str:
    """프로세스 무결성 수준.

    **왜 중요한가**: UIPI는 무결성이 낮은 프로세스가 높은 프로세스로 향하는 입력을
    받지 못하게 막는다. 게임이 High이고 레코더가 Medium이면 저수준 훅에 이벤트가
    도착하지 않을 수 있다 — 그 경우 해법은 코드 수정이 아니라 관리자 권한 실행이다.
    """
    # **원시 포인터 연산을 쓰지 않는다.** ctypes로 SID를 직접 훑던 첫 구현은
    # 반환 타입을 지정하지 않아 64비트 포인터가 32비트로 잘렸고, 그 주소를
    # 역참조하다 세그폴트로 진단 전체를 죽였다. 부수 정보 조회가 본 작업을
    # 중단시키는 것은 어떤 경우에도 잘못된 설계다.
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TOKEN_QUERY = 0x0008
    TOKEN_INTEGRITY_LEVEL = 25

    try:
        import win32api
        import win32security

        handle = win32api.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        try:
            token = win32security.OpenProcessToken(handle, TOKEN_QUERY)
            sid = win32security.GetTokenInformation(token, TOKEN_INTEGRITY_LEVEL)[0]
            # PySID 는 문자열로 바꾸면 "S-1-16-<RID>" 형태다. 마지막 조각이 무결성 값.
            rid = int(win32security.ConvertSidToStringSid(sid).rsplit("-", 1)[-1])
        finally:
            win32api.CloseHandle(handle)
        return {
            0: "Untrusted", 4096: "Low", 8192: "Medium",
            8448: "Medium+", 12288: "High", 16384: "System",
        }.get(rid, f"RID {rid}")
    except Exception as exc:
        return f"조회 불가 ({type(exc).__name__})"


def main() -> int:
    parser = argparse.ArgumentParser(description="입력 훅 진단")
    parser.add_argument("--profile", "-p", help="게임 프로파일")
    parser.add_argument("--hwnd", type=int, help="대상 창 핸들 직접 지정")
    parser.add_argument("--seconds", type=float, default=20.0)
    args = parser.parse_args()

    import win32gui
    import win32process

    from qatc.capture import find_game_window, get_window_info
    from qatc.config import AppConfig
    from qatc.profiles import get_profile
    from qatc.record.hooks import InputObserver

    # -- 대상 창 --------------------------------------------------
    window = None
    if args.hwnd:
        window = get_window_info(args.hwnd)
    elif args.profile:
        window = find_game_window(get_profile(AppConfig.load().profiles_path, args.profile))
    if window is None:
        _p("대상 창을 찾지 못했습니다. --profile 또는 --hwnd 를 지정하세요.")
        return 1

    _, target_pid = win32process.GetWindowThreadProcessId(window.hwnd)

    _p("=" * 70)
    _p("입력 훅 진단")
    _p("=" * 70)
    _p(f"대상 창    : {window.title[:50]}  (hwnd={window.hwnd})")
    _p(f"클라이언트 : {window.client_rect}")
    _p(f"게임 프로세스 : {window.process_name} (pid={target_pid}) · 무결성={_integrity_level(target_pid)}")
    import os

    _p(f"진단 프로세스 : pid={os.getpid()} · 무결성={_integrity_level(os.getpid())} · 관리자={_is_elevated()}")
    _p("")
    _p(f"{args.seconds:.0f}초 동안 관찰합니다.")
    _p("  1) 먼저 **바탕화면이나 다른 창**을 클릭해 보세요")
    _p("  2) 그다음 **게임 창**을 클릭하고 메뉴를 눌러 보세요")
    _p("")
    _p("-" * 70)

    observer = InputObserver(clock=time.monotonic)
    observer.start()

    stats = {
        "received": 0, "fg_ok": 0, "fg_fail": 0,
        "coord_ok": 0, "coord_fail": 0, "key": 0,
    }
    fg_seen: dict[int, str] = {}
    deadline = time.time() + args.seconds
    shown = 0

    try:
        while time.time() < deadline:
            try:
                raw = observer.queue.get(timeout=0.2)
            except Exception:
                continue
            stats["received"] += 1

            fg_hwnd = win32gui.GetForegroundWindow()
            if fg_hwnd not in fg_seen:
                try:
                    fg_seen[fg_hwnd] = win32gui.GetWindowText(fg_hwnd)[:38] or "(제목 없음)"
                except Exception:
                    fg_seen[fg_hwnd] = "(조회 실패)"

            info = get_window_info(window.hwnd)
            is_fg = bool(info and info.is_foreground)
            stats["fg_ok" if is_fg else "fg_fail"] += 1

            verdict = ""
            if raw.kind == "key":
                stats["key"] += 1
                verdict = "기록됨" if is_fg else "버려짐(포그라운드 아님)"
            elif raw.kind in ("down", "up", "scroll"):
                norm = info.to_normalized(raw.x, raw.y) if info else None
                if norm is None:
                    stats["coord_fail"] += 1
                    verdict = "버려짐(클라이언트 영역 밖)"
                else:
                    stats["coord_ok"] += 1
                    verdict = (
                        f"기록됨 ({norm[0]:.2f},{norm[1]:.2f})"
                        if is_fg else f"버려짐(포그라운드 아님) 좌표는 유효 ({norm[0]:.2f},{norm[1]:.2f})"
                    )

            if shown < 40:
                shown += 1
                detail = raw.key if raw.kind == "key" else f"({raw.x},{raw.y})"
                _p(
                    f"  {raw.kind:6} {detail:14} fg={fg_hwnd:<9} "
                    f"{'==대상' if fg_hwnd == window.hwnd else '≠대상':6} → {verdict}"
                )
    finally:
        observer.stop()

    # -- 판정 ------------------------------------------------------
    _p("-" * 70)
    _p("")
    _p("=" * 70)
    _p("판정")
    _p("=" * 70)
    _p(f"훅이 받은 이벤트   : {stats['received']}건 (훅 유실 {observer.dropped}건)")
    _p(f"  게임이 포그라운드 : {stats['fg_ok']}건")
    _p(f"  아닌 상태        : {stats['fg_fail']}건")
    _p(f"  좌표 유효/무효   : {stats['coord_ok']} / {stats['coord_fail']}")
    _p("")
    _p("관찰된 포그라운드 창:")
    for hwnd, title in fg_seen.items():
        mark = "  ← 대상 창" if hwnd == window.hwnd else ""
        _p(f"  hwnd={hwnd:<10} {title}{mark}")
    _p("")

    if stats["received"] == 0:
        _p("[A] 훅이 이벤트를 전혀 받지 못했습니다.")
        _p("    → 저수준 훅이 차단되고 있습니다. 다음을 순서대로 시도하세요:")
        _p("      1. 이 스크립트를 **일반 터미널에서 직접** 실행 (백그라운드 실행 문제 배제)")
        _p("      2. 터미널을 **관리자 권한**으로 실행 (게임 무결성이 더 높으면 UIPI가 막습니다)")
        _p("      3. 그래도 안 되면 안티치트가 훅을 차단하는 것입니다 —")
        _p("         F9 수동 북마크 방식으로 전환해야 합니다.")
    elif stats["fg_ok"] == 0 and stats["received"] > 0:
        _p("[B] 훅은 정상인데 **포그라운드 판정에서 전부 버려졌습니다.**")
        _p("    → 레코더가 추적하는 hwnd가 실제 포그라운드 창과 다릅니다.")
        _p("      위 '관찰된 포그라운드 창' 목록에서 게임에 해당하는 hwnd를 확인하고")
        _p(f"      --hwnd 로 다시 시도하세요:  qatc record --hwnd <번호>")
    elif stats["coord_ok"] == 0 and stats["coord_fail"] > 0:
        _p("[B] 훅과 포그라운드는 정상인데 **좌표가 전부 클라이언트 영역 밖**입니다.")
        _p("    → 창 좌표 계산이 틀렸습니다. DPI 배율 설정을 확인하세요.")
    else:
        _p("[정상] 훅·포그라운드·좌표가 모두 동작합니다.")
        _p(f"       기록 가능한 입력 {stats['fg_ok']}건이 확인되었습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

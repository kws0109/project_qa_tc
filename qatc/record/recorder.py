"""레코더 — 캡처·입력·저장을 묶는 오케스트레이터.

스레드 셋이 협력한다::

    [캡처 스레드]  2fps로 grab() → 링버퍼에 push, idle 변화 감지
    [훅 스레드]    pynput 콜백 → RawInput 큐 (즉시 반환)
    [워커 스레드]  큐 소비 → 좌표 변환 → 버스트 캡처 예약 → 디스크/DB 기록

**버스트 캡처가 이 설계의 핵심**이다. 입력이 발생하면 다음 4장을 남긴다::

    T-100ms  PRE_ACTION    행동 직전 화면 (링버퍼에서 회수)
    T+250ms  POST_FAST     전이 시작
    T+700ms  POST_MID      전이 중
    T+1500ms POST_SETTLED  전이 완료 — 상태 식별의 주 근거

서브컬쳐 게임의 화면 전환 페이드가 0.5~1초라, 클릭 직후 한 장만 찍으면 반투명
중간 프레임이 잡혀 상태 식별이 통째로 무너진다. 마지막 SETTLED 한 장을 위해
나머지를 찍는 셈이지만, 전환이 빠른 화면에서는 중간 프레임이 정답일 때도 있어
모두 남기고 분석 단계에서 고른다.
"""

from __future__ import annotations

import heapq
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from ..capture import CaptureTarget, WindowInfo, get_window_info, select_backend
from ..config import CaptureConfig
from ..models import CaptureReason, Frame, InputEvent, InputKind, SessionMeta, new_id
from ..profiles import GameProfile
from ..storage import SessionStore, utcnow
from .hooks import ClickResolver, InputObserver, RawInput
from .ringbuffer import FrameRingBuffer


@dataclass(order=True)
class _PendingShot:
    """예약된 버스트 캡처 한 건. heapq로 시각순 정렬된다."""

    due: float
    seq: int
    reason: CaptureReason = CaptureReason.POST_FAST
    event_id: str | None = None


@dataclass
class RecorderStats:
    frames_saved: int = 0
    events_saved: int = 0
    bookmarks: int = 0
    dropped_inputs: int = 0
    filtered_inputs: int = 0
    suppressed_repeats: int = 0
    capture_failures: int = 0
    paused_seconds: float = 0.0

    def summary(self) -> str:
        return (
            f"프레임 {self.frames_saved}장 · 입력 {self.events_saved}건 · "
            f"북마크 {self.bookmarks}개 · 캡처실패 {self.capture_failures}회"
            + (f" · 입력유실 {self.dropped_inputs}건" if self.dropped_inputs else "")
            + (f" · 규칙 제외 {self.filtered_inputs}건" if self.filtered_inputs else "")
            + (f" · 자동반복 억제 {self.suppressed_repeats}건" if self.suppressed_repeats else "")
        )


class Recorder:
    """게임 플레이 세션 하나를 기록한다.

    :param on_status: GUI/CLI에 상태 문자열을 흘려보낼 콜백 (선택).
    """

    def __init__(
        self,
        store: SessionStore,
        profile: GameProfile,
        window: WindowInfo,
        config: CaptureConfig | None = None,
        on_status: Callable[[str], None] | None = None,
        preferred_backend: str | None = None,
    ):
        self.store = store
        self.profile = profile
        self.window = window
        self.cfg = config or CaptureConfig()
        self.on_status = on_status or (lambda _: None)
        self.preferred_backend = preferred_backend

        self.stats = RecorderStats()
        self.session_id = store.get_session().id
        self.backend_name = ""

        self._ring = FrameRingBuffer(self.cfg.ring_seconds, self.cfg.idle_fps)
        self._observer: InputObserver | None = None
        self._resolver = ClickResolver()
        self._backend = None

        self._stop = threading.Event()
        self._paused = threading.Event()
        self._pause_started = 0.0
        self._t0 = 0.0

        self._shots: list[_PendingShot] = []
        self._shots_lock = threading.Lock()
        self._shot_seq = 0

        self._threads: list[threading.Thread] = []
        self._last_idle_snapshot = -999.0
        self._last_idle_ref: np.ndarray | None = None
        self._pending_bookmark: queue.Queue[str] = queue.Queue()

    # -- 시간 --------------------------------------------------------

    def _now(self) -> float:
        """세션 시작 기준 경과 초. 모든 타임스탬프의 기준."""
        return time.monotonic() - self._t0

    # -- 수명주기 ----------------------------------------------------

    def start(self) -> None:
        target = CaptureTarget(
            window=self.window,
            roi=self.profile.capture_roi.as_tuple() if self.profile.capture_roi else None,
        )
        self._backend, self.backend_name = select_backend(target, self.preferred_backend)
        self._backend.start()

        self._t0 = time.monotonic()
        self._observer = InputObserver(clock=self._now, on_hotkey=self._on_hotkey)
        self._observer.start()

        self._threads = [
            threading.Thread(target=self._capture_loop, name="qatc-capture", daemon=True),
            threading.Thread(target=self._worker_loop, name="qatc-worker", daemon=True),
        ]
        for t in self._threads:
            t.start()

        self.on_status(
            f"녹화 시작 — 백엔드={self.backend_name}, "
            f"대상={self.window.title[:40]} ({self.window.width}x{self.window.height})"
        )

    def stop(self) -> None:
        self._stop.set()
        if self._observer is not None:
            self._observer.stop()
            self.stats.dropped_inputs = self._observer.dropped
            self.stats.suppressed_repeats = self._observer.suppressed_repeats
        for t in self._threads:
            t.join(timeout=3.0)
        if self._backend is not None:
            self._backend.stop()
        self.store.finish_session(backend=self.backend_name)
        self.on_status(f"녹화 종료 — {self.stats.summary()}")

    def __enter__(self) -> Recorder:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    @property
    def is_paused(self) -> bool:
        return self._paused.is_set()

    def wait(self, poll: float = 0.25) -> None:
        """중지될 때까지 블로킹. CLI에서 Ctrl+C를 기다릴 때 쓴다."""
        while not self._stop.is_set():
            time.sleep(poll)

    # -- 핫키 --------------------------------------------------------

    def _on_hotkey(self, name: str) -> None:
        """훅 스레드에서 호출된다. 절대 무거운 일을 하지 말 것."""
        if name == "f10":
            if self._paused.is_set():
                self._paused.clear()
                self.stats.paused_seconds += time.monotonic() - self._pause_started
                self.on_status("▶ 녹화 재개")
            else:
                self._pause_started = time.monotonic()
                self._paused.set()
                self.on_status("⏸ 녹화 일시정지 (F10으로 재개)")
        elif name == "f9":
            self._pending_bookmark.put("")
            self.on_status("🔖 북마크 표시")

    def bookmark(self, note: str = "") -> None:
        """외부(GUI/CLI)에서 메모를 붙인 북마크를 남긴다."""
        self._pending_bookmark.put(note)

    # -- 캡처 스레드 -------------------------------------------------

    def _capture_loop(self) -> None:
        interval = 1.0 / max(0.5, self.cfg.idle_fps)
        next_tick = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            if now < next_tick:
                time.sleep(min(0.02, next_tick - now))
                continue
            next_tick = now + interval

            # 예약된 버스트 캡처가 있으면 먼저 처리한다 (타이밍이 생명)
            self._flush_due_shots()

            if self._paused.is_set():
                continue

            frame = self._safe_grab()
            if frame is None:
                continue
            ts = self._now()
            self._ring.push(ts, frame)
            self._maybe_idle_snapshot(ts, frame)

    def _safe_grab(self) -> np.ndarray | None:
        try:
            frame = self._backend.grab() if self._backend else None
        except Exception:
            self.stats.capture_failures += 1
            return None
        if frame is None:
            self.stats.capture_failures += 1
        return frame

    def _maybe_idle_snapshot(self, ts: float, frame: np.ndarray) -> None:
        """입력 없이 화면이 크게 바뀌면 스냅샷을 남긴다 (컷씬·로딩·자동진행).

        입력 이벤트만 따라가면 "클릭 후 5초 뒤 컷씬이 끝나고 새 화면"같은 흐름을
        통째로 놓친다. 서브컬쳐 게임은 이런 자동 진행이 많다.
        """
        if ts - self._last_idle_snapshot < self.cfg.idle_snapshot_min_gap:
            return
        small = cv2.resize(frame, (96, 54), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        ref = self._last_idle_ref
        self._last_idle_ref = gray
        if ref is None:
            return
        diff = float(np.mean(cv2.absdiff(gray, ref))) / 255.0
        if diff >= self.cfg.idle_change_threshold:
            self._last_idle_snapshot = ts
            event = InputEvent(
                id=new_id("ev"),
                session_id=self.session_id,
                ts=ts,
                kind=InputKind.AUTO_SNAPSHOT,
            )
            self.store.add_event(event)
            self.stats.events_saved += 1
            self._save_frame(frame, ts, CaptureReason.IDLE_CHANGE, event.id)

    # -- 버스트 예약 -------------------------------------------------

    def _schedule_burst(self, event_id: str) -> None:
        base = time.monotonic()
        with self._shots_lock:
            for delay, reason in zip(
                self.cfg.post_action_delays,
                (CaptureReason.POST_FAST, CaptureReason.POST_MID, CaptureReason.POST_SETTLED),
            ):
                self._shot_seq += 1
                heapq.heappush(
                    self._shots, _PendingShot(base + delay, self._shot_seq, reason, event_id)
                )

    def _flush_due_shots(self) -> None:
        now = time.monotonic()
        due: list[_PendingShot] = []
        with self._shots_lock:
            while self._shots and self._shots[0].due <= now:
                due.append(heapq.heappop(self._shots))
        for shot in due:
            frame = self._safe_grab()
            if frame is not None:
                self._save_frame(frame, self._now(), shot.reason, shot.event_id)

    # -- 워커 스레드 -------------------------------------------------

    def _worker_loop(self) -> None:
        assert self._observer is not None
        while not self._stop.is_set():
            try:
                raw = self._observer.queue.get(timeout=0.15)
            except queue.Empty:
                self._drain_bookmarks()
                continue
            if self._paused.is_set():
                continue
            try:
                self._handle_raw(raw)
            except Exception as exc:  # 입력 하나가 세션 전체를 죽이면 안 된다
                self.on_status(f"입력 처리 오류(무시): {exc}")
            self._drain_bookmarks()

    def _drain_bookmarks(self) -> None:
        while True:
            try:
                note = self._pending_bookmark.get_nowait()
            except queue.Empty:
                return
            ts = self._now()
            event = InputEvent(
                id=new_id("ev"),
                session_id=self.session_id,
                ts=ts,
                kind=InputKind.BOOKMARK,
                note=note or None,
            )
            self.store.add_event(event)
            self.stats.events_saved += 1
            self.stats.bookmarks += 1
            latest = self._ring.latest()
            if latest is not None:
                self._save_frame(latest.image, ts, CaptureReason.MANUAL, event.id)

    def _current_window(self) -> WindowInfo | None:
        """창이 움직였을 수 있으므로 매번 최신 정보를 읽는다."""
        info = get_window_info(self.window.hwnd)
        if info is not None:
            self.window = info
        return info

    def _handle_raw(self, raw: RawInput) -> None:
        info = self._current_window()
        # 게임 창이 포그라운드가 아니면 기록하지 않는다 — 딴짓이 TC에 섞이지 않게.
        if info is None or not info.is_foreground:
            return

        rules = self.profile.input_rules

        if raw.kind == "key_up":
            return  # 수식키 해제는 상태 추적용일 뿐 스텝이 아니다

        if raw.kind == "key":
            # 이동·카메라 키와 포인터 수식키는 게임 동작이 아니다. 기록하지 않으면
            # 버스트 캡처도 예약되지 않으므로 디스크와 분석 시간이 함께 절약된다.
            if rules.is_ignored_key(raw.key) or rules.is_pointer_modifier(raw.key):
                self.stats.filtered_inputs += 1
                return
            self._record_event(
                InputEvent(
                    id=new_id("ev"),
                    session_id=self.session_id,
                    ts=raw.ts,
                    kind=InputKind.KEY,
                    key=raw.key,
                )
            )
            return

        norm = info.to_normalized(raw.x, raw.y)
        if norm is None:
            return  # 창 밖 클릭
        nx, ny = self._apply_roi(norm)

        if raw.kind == "scroll":
            self._record_event(
                InputEvent(
                    id=new_id("ev"),
                    session_id=self.session_id,
                    ts=raw.ts,
                    kind=InputKind.SCROLL,
                    nx=nx,
                    ny=ny,
                    scroll_dy=raw.scroll_dy,
                )
            )
            return

        if raw.kind == "down":
            self._resolver.on_down(raw.ts, nx, ny)
            return

        if raw.kind == "up":
            resolved = self._resolver.on_up(raw.ts, nx, ny)
            if resolved is None:
                return
            kind_str, dnx, dny, unx, uny = resolved
            kind = {
                "click": InputKind.CLICK,
                "double_click": InputKind.DOUBLE_CLICK,
                "drag": InputKind.DRAG,
            }[kind_str]
            if raw.button == "right" and kind is InputKind.CLICK:
                kind = InputKind.RIGHT_CLICK

            # 포인터 수식키를 안 누른 클릭은 좌표가 화면 중앙에 고정된다 —
            # 스타레일 필드에서 Alt 없이 클릭하면 항상 (0.50, 0.50)이 찍힌다.
            reliable = rules.click_coords_reliable(raw.modifiers)
            if not reliable and rules.drop_unmodified_clicks:
                self.stats.filtered_inputs += 1
                return

            self._record_event(
                InputEvent(
                    id=new_id("ev"),
                    session_id=self.session_id,
                    ts=raw.ts,
                    kind=kind,
                    nx=dnx,
                    ny=dny,
                    nx2=unx if kind is InputKind.DRAG else None,
                    ny2=uny if kind is InputKind.DRAG else None,
                    coords_reliable=reliable,
                )
            )

    def _apply_roi(self, norm: tuple[float, float]) -> tuple[float, float]:
        """클라이언트 기준 좌표를 ROI(잘라낸 게임 영역) 기준으로 다시 정규화한다.

        에뮬레이터에서 툴바를 잘라냈다면 캡처 이미지의 (0,0)은 클라이언트의 (0,0)이
        아니다. 클릭 좌표도 같은 기준으로 옮겨야 UI 요소와 맞물린다.
        """
        roi = self.profile.capture_roi
        if roi is None:
            return norm
        nx, ny = norm
        if roi.w <= 0 or roi.h <= 0:
            return norm
        return ((nx - roi.x) / roi.w, (ny - roi.y) / roi.h)

    def _record_event(self, event: InputEvent) -> None:
        self.store.add_event(event)
        self.stats.events_saved += 1

        # 행동 직전 화면을 링버퍼에서 회수
        pre = self._ring.before(event.ts - self.cfg.pre_action_lookback)
        if pre is not None:
            self._save_frame(pre.image, pre.ts, CaptureReason.PRE_ACTION, event.id)
        self._schedule_burst(event.id)

    # -- 저장 --------------------------------------------------------

    def _save_frame(
        self, image: np.ndarray, ts: float, reason: CaptureReason, event_id: str | None
    ) -> None:
        frame_id = new_id("fr")
        rel = f"frames/{frame_id}.jpg"
        path: Path = self.store.root / rel
        try:
            ok = cv2.imwrite(
                str(path), image, [int(cv2.IMWRITE_JPEG_QUALITY), self.cfg.jpeg_quality]
            )
        except Exception:
            ok = False
        if not ok:
            self.stats.capture_failures += 1
            return

        h, w = image.shape[:2]
        self.store.add_frame(
            Frame(
                id=frame_id,
                session_id=self.session_id,
                ts=ts,
                path=rel,
                reason=reason,
                client_w=w,
                client_h=h,
                event_id=event_id,
            )
        )
        self.stats.frames_saved += 1


def create_session(
    sessions_root: Path | str, profile: GameProfile, window: WindowInfo, note: str = ""
) -> SessionStore:
    """새 세션 폴더와 DB를 만든다. 세션 ID는 시간 기반이라 정렬하면 최신순이 된다."""
    session_id = f"{time.strftime('%Y%m%d-%H%M%S')}_{profile.key}"
    meta = SessionMeta(
        id=session_id,
        profile_name=profile.key,
        game_name=profile.name,
        started_at=utcnow(),
        client_w=window.width,
        client_h=window.height,
        note=note,
    )
    return SessionStore.create(sessions_root, meta)

"""입력 관찰 (읽기 전용).

**안전 원칙**: 이 모듈은 ``pynput``의 저수준 리스너(``SetWindowsHookEx`` /
``WH_KEYBOARD_LL`` / ``WH_MOUSE_LL``)로 **관찰만** 한다. 입력을 주입하거나
(``SendInput``), 소비하거나(suppress), 게임 프로세스에 접근하지 않는다.
OBS·Discord 오버레이가 쓰는 것과 같은 OS 표준 경로다.

**콜백에서 무거운 일을 하면 안 된다.** 저수준 훅 콜백은 시스템 입력 큐를 블로킹한다.
여기서 캡처나 디스크 I/O를 하면 마우스가 끊기고, 심하면 Windows가 훅을 강제 해제한다.
그래서 콜백은 :class:`RawInput`을 큐에 넣고 즉시 반환하고, 무거운 처리는
:class:`~qatc.record.recorder.Recorder`의 워커 스레드가 맡는다.

**드래그 판정**: 마우스 다운/업을 짝지어 이동 거리가 임계값을 넘으면 DRAG,
아니면 CLICK으로 본다. 게임에서 드래그(카메라 회전, 아이템 이동)와 클릭은
전혀 다른 행동이라 구분이 필요하다.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

from pynput import keyboard, mouse

#: 마우스 다운→업 사이 이 거리(정규화)를 넘으면 클릭이 아니라 드래그로 본다.
DRAG_THRESHOLD = 0.012
#: 이 시간(초) 안에 같은 위치를 두 번 누르면 더블클릭.
DOUBLE_CLICK_WINDOW = 0.35


#: 수식키로 취급할 키 이름. 게임 동작이 아니라 다른 입력을 바꾸는 역할이다.
MODIFIER_KEYS = frozenset(
    {"alt", "alt_l", "alt_r", "alt_gr", "ctrl", "ctrl_l", "ctrl_r",
     "shift", "shift_l", "shift_r", "cmd", "cmd_l", "cmd_r"}
)


def canonical_modifier(key: str) -> str:
    """``alt_l`` → ``alt`` 처럼 좌우 구분을 없앤다. 프로파일 설정과 맞추기 위함."""
    k = key.lower()
    for base in ("alt_gr", "alt", "ctrl", "shift", "cmd"):
        if k.startswith(base):
            return "alt" if base == "alt_gr" else base
    return k


@dataclass
class RawInput:
    """훅이 관찰한 원시 입력. 아직 게임 좌표로 변환되지 않았다."""

    ts: float
    kind: str  # "down" | "up" | "scroll" | "key" | "key_up"
    x: int = 0
    y: int = 0
    button: str = ""
    key: str = ""
    scroll_dy: int = 0
    #: 이 입력이 발생한 순간 눌려 있던 수식키 (정규화된 이름).
    #: 스타레일처럼 Alt를 홀드해야 포인터가 활성화되는 게임에서, 클릭 좌표가
    #: 유효한지 판정하는 근거가 된다.
    modifiers: frozenset[str] = frozenset()


def _key_name(key: object) -> str:
    """pynput 키 객체를 사람이 읽는 이름으로. 'Key.esc' → 'esc', "'w'" → 'w'."""
    try:
        if isinstance(key, keyboard.Key):
            return key.name
        char = getattr(key, "char", None)
        if char:
            return char
        vk = getattr(key, "vk", None)
        return f"vk{vk}" if vk is not None else str(key)
    except Exception:
        return str(key)


class InputObserver:
    """마우스·키보드 리스너를 띄우고 원시 입력을 큐에 흘려보낸다.

    :param on_hotkey: 훅 스레드에서 즉시 처리해야 하는 핫키(F9/F10) 콜백.
        큐를 거치지 않는 이유는 녹화 일시정지가 즉시 반영돼야 하기 때문이다.
        콜백은 반드시 빨라야 한다.
    """

    def __init__(
        self,
        clock: Callable[[], float] | None = None,
        on_hotkey: Callable[[str], None] | None = None,
        hotkeys: frozenset[str] = frozenset({"f9", "f10"}),
    ):
        self.queue: queue.Queue[RawInput] = queue.Queue(maxsize=4096)
        self._clock = clock or time.monotonic
        self._on_hotkey = on_hotkey
        self._hotkeys = hotkeys
        self._mouse: mouse.Listener | None = None
        self._keyboard: keyboard.Listener | None = None
        self._dropped = 0
        self._lock = threading.Lock()
        #: 현재 눌려 있는 키. **자동 반복 억제의 핵심.**
        self._held: set[str] = set()
        self._suppressed_repeats = 0

    # -- 수명주기 ----------------------------------------------------

    def start(self) -> None:
        self._mouse = mouse.Listener(
            on_click=self._on_click, on_scroll=self._on_scroll, suppress=False
        )
        self._keyboard = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release, suppress=False
        )
        self._mouse.start()
        self._keyboard.start()

    def stop(self) -> None:
        for listener in (self._mouse, self._keyboard):
            if listener is not None:
                try:
                    listener.stop()
                except Exception:
                    pass
        self._mouse = self._keyboard = None

    def __enter__(self) -> InputObserver:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    @property
    def dropped(self) -> int:
        """큐가 가득 차서 버린 입력 수. 0이 아니면 워커가 못 따라가고 있다는 뜻."""
        with self._lock:
            return self._dropped

    @property
    def suppressed_repeats(self) -> int:
        """자동 반복으로 판정해 걸러낸 keydown 수. 홀드가 많을수록 크다."""
        with self._lock:
            return self._suppressed_repeats

    @property
    def held_keys(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._held)

    # -- 훅 콜백 (반드시 빠르게 반환할 것) ---------------------------

    def _emit(self, item: RawInput) -> None:
        try:
            self.queue.put_nowait(item)
        except queue.Full:
            with self._lock:
                self._dropped += 1

    def _active_modifiers(self) -> frozenset[str]:
        with self._lock:
            return frozenset(
                canonical_modifier(k) for k in self._held if k in MODIFIER_KEYS
            )

    def _on_click(self, x: int, y: int, button: object, pressed: bool) -> None:
        self._emit(
            RawInput(
                ts=self._clock(),
                kind="down" if pressed else "up",
                x=int(x),
                y=int(y),
                button=getattr(button, "name", str(button)),
                modifiers=self._active_modifiers(),
            )
        )

    def _on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        self._emit(
            RawInput(
                ts=self._clock(), kind="scroll", x=int(x), y=int(y),
                scroll_dy=int(dy), modifiers=self._active_modifiers(),
            )
        )

    def _on_press(self, key: object) -> None:
        """키를 눌렀을 때. **자동 반복은 여기서 걸러낸다.**

        Windows는 키를 누르고 있으면 약 33ms마다 keydown을 계속 보낸다.
        그대로 기록하면 "W를 한 번 누르고 3초 홀드"가 "W를 90번 눌렀다"가 된다.
        실측 세션에서 40초 녹화에 프레임 627장·266MB가 나온 원인이 이것이었다 —
        입력 하나마다 4장씩 버스트 캡처를 하기 때문에 피해가 곱해진다.

        눌려 있는 키를 집합으로 추적해 **처음 눌린 순간에만** 내보낸다.
        """
        name = _key_name(key)

        if name in self._hotkeys:
            if self._on_hotkey is not None:
                try:
                    self._on_hotkey(name)
                except Exception:
                    pass
            return  # 핫키는 게임 입력이 아니므로 기록하지 않는다

        with self._lock:
            if name in self._held:
                self._suppressed_repeats += 1
                return  # 자동 반복 — 이미 눌려 있다
            self._held.add(name)

        self._emit(
            RawInput(
                ts=self._clock(), kind="key", key=name,
                modifiers=self._active_modifiers(),
            )
        )

    def _on_release(self, key: object) -> None:
        """키를 뗐을 때. 홀드 상태를 풀어 다음 누름이 다시 기록되게 한다.

        ``key_up`` 이벤트도 내보내는 이유: 수식키를 언제 뗐는지 알아야
        "Alt를 홀드한 채 클릭했다"와 "Alt를 떼고 클릭했다"를 구분할 수 있다.
        """
        name = _key_name(key)
        with self._lock:
            self._held.discard(name)
        if name in MODIFIER_KEYS:
            self._emit(
                RawInput(
                    ts=self._clock(), kind="key_up", key=name,
                    modifiers=self._active_modifiers(),
                )
            )


class ClickResolver:
    """마우스 다운/업 쌍을 클릭·더블클릭·드래그로 해석한다.

    훅은 down과 up을 따로 준다. 게임 QA 관점에서 의미 있는 단위는 "클릭 한 번"이나
    "드래그 한 번"이므로 여기서 합쳐준다.
    """

    def __init__(self, drag_threshold: float = DRAG_THRESHOLD):
        self.drag_threshold = drag_threshold
        self._pending: tuple[float, float, float] | None = None  # (ts, nx, ny)
        self._last_click: tuple[float, float, float] | None = None

    def on_down(self, ts: float, nx: float, ny: float) -> None:
        self._pending = (ts, nx, ny)

    def on_up(self, ts: float, nx: float, ny: float) -> tuple[str, float, float, float, float] | None:
        """업 이벤트를 해석한다.

        :returns: ``(kind, nx, ny, nx2, ny2)`` — kind는 "click"/"double_click"/"drag".
            대응하는 down이 없으면 None (녹화 시작 직전에 누른 버튼 등).
        """
        if self._pending is None:
            return None
        down_ts, dnx, dny = self._pending
        self._pending = None
        dist = ((nx - dnx) ** 2 + (ny - dny) ** 2) ** 0.5

        if dist > self.drag_threshold:
            return ("drag", dnx, dny, nx, ny)

        # 같은 자리 연속 클릭 → 더블클릭
        if self._last_click is not None:
            last_ts, lnx, lny = self._last_click
            near = ((nx - lnx) ** 2 + (ny - lny) ** 2) ** 0.5 < self.drag_threshold
            if near and (ts - last_ts) < DOUBLE_CLICK_WINDOW:
                self._last_click = None
                return ("double_click", dnx, dny, nx, ny)

        self._last_click = (ts, nx, ny)
        return ("click", dnx, dny, nx, ny)

    def reset(self) -> None:
        self._pending = None
        self._last_click = None

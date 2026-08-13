"""테스트용 합성 게임 세션 생성기.

실제 게임 없이 파이프라인 전체를 검증하기 위한 픽스처를 만든다. 서브컬쳐 게임의
까다로운 특성을 의도적으로 재현한다.

* 캐릭터 대기 애니메이션 (좌측 영역이 매 프레임 변함)
* 배경 파티클 (전 영역에 랜덤 노이즈)
* 화면 전환 페이드 (클릭 후 0.5~1초에 걸쳐 알파 블렌딩)
* 재화 수량이 매 프레임 바뀜 (숫자 토큰이 시그니처를 오염시키는지 확인)
* 한국어 UI 텍스트

의도한 정답 경로: 홈 → 캐릭터 → 장비 → 강화 → (뒤로) 장비 → (뒤로) 홈
즉 **고유 화면 4개**, 전이 6개.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qatc.models import CaptureReason, Frame, InputEvent, InputKind, SessionMeta, new_id
from qatc.storage import SessionStore, utcnow

W, H = 1280, 720
FONT_PATH = r"C:\Windows\Fonts\malgun.ttf"

#: 화면 정의: (이름, 배경색, 패널색, 탭 라벨들, 항목 라벨들)
SCREENS = {
    "home": ("홈", (26, 28, 34), (48, 46, 58),
             ["임무", "캐릭터", "가방", "상점", "기원"],
             ["일일 임무", "주간 보상", "이벤트 안내"]),
    "character": ("캐릭터", (30, 26, 40), (58, 50, 70),
                  ["속성", "장비", "재능", "운명"],
                  ["공격력", "생명력", "방어력", "원소 마스터리"]),
    "equipment": ("장비", (24, 34, 40), (44, 62, 72),
                  ["무기", "성유물", "세트 효과"],
                  ["강화하기", "장착 해제", "잠금", "분해"]),
    "enhance": ("강화", (40, 30, 24), (74, 56, 42),
                ["재료 선택", "미리보기"],
                ["강화 시작", "재료 부족", "확인", "취소"]),
}


def _font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except OSError:
        return ImageFont.load_default()


def render(screen: str, t: float, rng: np.random.Generator, currency: int) -> np.ndarray:
    """화면 하나를 시각 t에 렌더링한다."""
    name, bg, panel, tabs, items = SCREENS[screen]
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)

    # 상단 탭바 (고정 UI — 화면 정체성)
    d.rectangle([0, 0, W, 86], fill=panel)
    f_tab = _font(30)
    for i, tab in enumerate(tabs):
        x = 70 + i * 210
        d.rectangle([x - 18, 20, x + 160, 68], fill=tuple(min(255, c + 34) for c in panel))
        d.text((x, 28), tab, font=f_tab, fill=(240, 238, 232))

    # 우측 콘텐츠 패널 (고정 UI)
    d.rectangle([int(W * 0.46), 120, W - 40, H - 60], fill=tuple(max(0, c - 6) for c in panel))
    f_item = _font(26)
    for i, item in enumerate(items):
        y = 170 + i * 92
        d.rectangle([int(W * 0.49), y, W - 80, y + 66], fill=tuple(min(255, c + 26) for c in panel))
        d.text((int(W * 0.51), y + 18), item, font=f_item, fill=(246, 244, 238))

    # 재화 표시 — 매 프레임 숫자가 바뀐다 (시그니처 오염 테스트)
    d.text((W - 300, 28), f"재화 {currency:,}", font=_font(28), fill=(255, 226, 120))

    arr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

    # 캐릭터 대기 애니메이션 (좌측) — 매 프레임 크게 변함
    cx = int(W * 0.22 + 70 * np.sin(t * 1.9))
    cy = int(H * 0.58 + 44 * np.cos(t * 2.4))
    cv2.circle(arr, (cx, cy), 150, (int(110 + 70 * np.sin(t)), 96, int(150 + 60 * np.cos(t * 1.4))), -1)
    cv2.circle(arr, (cx, cy - 170), 62, (210, 196, 180), -1)

    # 배경 파티클
    for _ in range(90):
        px, py = int(rng.integers(20, int(W * 0.44))), int(rng.integers(100, H - 40))
        cv2.circle(arr, (px, py), int(rng.integers(2, 6)), (255, 252, 235), -1)
    return arr


def blend(a: np.ndarray, b: np.ndarray, alpha: float) -> np.ndarray:
    """전환 페이드. 클릭 직후 프레임이 반투명 중간 상태가 되는 걸 재현한다."""
    return cv2.addWeighted(a, 1.0 - alpha, b, alpha, 0)


#: (from, to, 행동, 클릭 좌표) — 의도한 정답 경로
PATH = [
    ("home", "character", "캐릭터 탭 클릭", (0.22, 0.06)),
    ("character", "equipment", "장비 탭 클릭", (0.22, 0.06)),
    ("equipment", "enhance", "강화하기 클릭", (0.60, 0.27)),
    ("enhance", "equipment", "ESC 뒤로가기", None),
    ("equipment", "character", "ESC 뒤로가기", None),
    ("character", "home", "ESC 뒤로가기", None),
]


def build(sessions_root: str = "sessions", session_id: str = "fixture_synthetic") -> Path:
    rng = np.random.default_rng(7)
    root = Path(sessions_root) / session_id
    if root.exists():
        import shutil

        shutil.rmtree(root)

    meta = SessionMeta(
        id=session_id,
        profile_name="generic",
        game_name="합성 테스트 게임",
        started_at=utcnow(),
        capture_backend="fixture",
        client_w=W,
        client_h=H,
        note="파이프라인 검증용 합성 세션",
    )
    store = SessionStore.create(sessions_root, meta)

    t = 0.0
    currency = 12_500
    frames: list[Frame] = []

    def save(img: np.ndarray, ts: float, reason: CaptureReason, event_id: str | None) -> None:
        fid = new_id("fr")
        rel = f"frames/{fid}.jpg"
        cv2.imwrite(str(store.root / rel), img, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        frames.append(
            Frame(id=fid, session_id=session_id, ts=ts, path=rel, reason=reason,
                  client_w=W, client_h=H, event_id=event_id)
        )

    # 시작 화면에 잠시 머무름 (변동성 학습 재료)
    for k in range(6):
        t += 0.5
        currency += int(rng.integers(-40, 40))
        save(render("home", t, rng, currency), t, CaptureReason.IDLE_CHANGE, None)

    for src, dst, desc, click in PATH:
        t += 1.0
        currency += int(rng.integers(-40, 40))
        kind = InputKind.CLICK if click else InputKind.KEY
        ev = InputEvent(
            id=new_id("ev"), session_id=session_id, ts=t, kind=kind,
            nx=click[0] if click else None, ny=click[1] if click else None,
            key=None if click else "esc",
        )
        store.add_event(ev)

        before = render(src, t, rng, currency)
        save(before, t - 0.1, CaptureReason.PRE_ACTION, ev.id)

        after_base = render(dst, t + 1.5, rng, currency)
        # 페이드 진행: +250ms=35%, +700ms=80%, +1500ms=100%
        save(blend(before, render(dst, t + 0.25, rng, currency), 0.35), t + 0.25, CaptureReason.POST_FAST, ev.id)
        save(blend(before, render(dst, t + 0.70, rng, currency), 0.80), t + 0.70, CaptureReason.POST_MID, ev.id)
        save(after_base, t + 1.50, CaptureReason.POST_SETTLED, ev.id)

        # 도착 화면에 잠시 머무름 (애니메이션 프레임들)
        for k in range(3):
            t += 0.5
            currency += int(rng.integers(-40, 40))
            save(render(dst, t + 2.0, rng, currency), t + 2.0, CaptureReason.IDLE_CHANGE, None)

    store.add_frames(frames)
    store.finish_session(backend="fixture")
    store.close()
    return root


if __name__ == "__main__":
    out = build()
    print(f"픽스처 생성: {out}")
    print(f"정답: 고유 화면 {len(SCREENS)}개, 전이 {len(PATH)}개")

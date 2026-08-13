"""앱 설정 및 경로.

API 키는 **설정 파일에 평문으로 저장하지 않는다.** Windows 자격 증명 관리자(keyring)에
넣고, 그게 불가능한 환경에서만 환경변수를 본다. 설정 파일은 사용자가 실수로
스크린샷·저장소에 노출하기 쉬운 물건이라 비밀을 두면 안 된다.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "qatc"
KEYRING_SERVICE = "qatc-anthropic"
KEYRING_USER = "api_key"
ENV_API_KEY = "ANTHROPIC_API_KEY"

#: 대량·단순 작업 (화면 명명, 동일성 판별). 호출 수가 많아 저렴한 쪽을 쓴다.
MODEL_BULK = "claude-sonnet-5"
#: 소량·고난도 작업 (TC 생성, 리뷰 채팅). 품질이 결과물을 좌우한다.
MODEL_DEEP = "claude-opus-5"

#: Anthropic 권장 이미지 상한. 이보다 크게 보내도 정확도 이득 없이 토큰만 늘어난다.
MAX_IMAGE_EDGE = 1568


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def user_config_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class CaptureConfig:
    """캡처 타이밍. 여기 숫자가 상태 식별 품질을 직접 좌우한다."""

    #: 상시 링버퍼 캡처 레이트. 디스크에 쓰지 않고 메모리에만 둔다.
    idle_fps: float = 2.0
    #: 링버퍼 보관 시간(초).
    ring_seconds: float = 6.0
    #: 입력 발생 시 "행동 직전" 프레임을 링버퍼에서 얼마나 되돌아가 꺼낼지(초).
    pre_action_lookback: float = 0.10
    #: 입력 후 캡처 시점(초). 서브컬쳐 게임의 페이드 전환이 0.5~1초라 마지막 값이 핵심이다.
    post_action_delays: tuple[float, ...] = (0.25, 0.70, 1.50)
    #: 입력 없이 화면이 이만큼 달라지면 자동 스냅샷 (0~1, 정규화 차분 비율).
    idle_change_threshold: float = 0.12
    #: 자동 스냅샷 최소 간격(초). 컷씬에서 폭주하지 않게 막는다.
    idle_snapshot_min_gap: float = 1.5
    #: JPEG 품질. UI 텍스트가 뭉개지면 OCR이 무너지므로 낮추지 말 것.
    jpeg_quality: int = 92


@dataclass
class AnalyzeConfig:
    #: 모션 마스크 학습에 쓸 연속 프레임 최소 개수.
    motion_min_frames: int = 8
    #: 프레임 차분이 이 값을 넘으면 "움직이는 영역"으로 본다 (0~255).
    motion_diff_threshold: int = 18
    #: 마스킹 pHash 해밍거리가 이 이하이면 1차 중복으로 즉시 제거.
    dedupe_hamming: int = 4
    #: 클러스터 결합 점수가 이 구간이면 LLM에게 물어본다.
    ambiguous_low: float = 0.55
    ambiguous_high: float = 0.80
    #: 이 값 이상이면 무조건 같은 화면.
    same_threshold: float = 0.80
    #: LLM 판별 호출 상한. 비용 폭주 방지.
    max_llm_disambiguations: int = 60
    #: UI 요소로 인정할 최소/최대 면적 비율.
    element_min_area: float = 0.0004
    element_max_area: float = 0.60
    #: OCR을 돌릴 프레임 상한 (상태 대표 프레임만 돌리면 충분하다).
    max_ocr_frames: int = 300


@dataclass
class LlmConfig:
    model_bulk: str = MODEL_BULK
    model_deep: str = MODEL_DEEP
    max_retries: int = 3
    timeout_s: float = 120.0
    #: 세션당 비용 상한(USD). 초과하면 호출을 막고 사용자에게 알린다.
    budget_usd: float = 5.0
    enable_prompt_cache: bool = True
    #: 경계·예외 TC를 만들 대상 화면 수 상한.
    #: **이 값이 세션 비용을 가장 크게 좌우한다** — 고해상도 스크린샷을 고성능 모델에
    #: 보내는 유일한 단계이기 때문이다. 실측에서 전체 비용의 70~80%를 차지했다.
    max_derived_screens: int = 24
    #: 실행 전에 예상 비용을 계산해 보여줄지. 끄면 확인 없이 바로 호출한다.
    show_cost_estimate: bool = True


@dataclass
class AppConfig:
    sessions_root: str = ""
    profiles_dir: str = ""
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    analyze: AnalyzeConfig = field(default_factory=AnalyzeConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)

    def __post_init__(self) -> None:
        if not self.sessions_root:
            self.sessions_root = str(project_root() / "sessions")
        if not self.profiles_dir:
            self.profiles_dir = str(project_root() / "profiles")

    @property
    def sessions_path(self) -> Path:
        p = Path(self.sessions_root)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def profiles_path(self) -> Path:
        return Path(self.profiles_dir)

    # -- 영속화 ------------------------------------------------------

    @staticmethod
    def config_file() -> Path:
        return user_config_dir() / "config.json"

    @classmethod
    def load(cls) -> AppConfig:
        f = cls.config_file()
        if not f.exists():
            return cls()
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # 설정이 깨졌다고 앱이 못 뜨면 안 된다. 기본값으로 계속한다.
            return cls()
        cfg = cls(
            sessions_root=raw.get("sessions_root", ""),
            profiles_dir=raw.get("profiles_dir", ""),
        )
        for key, klass in (("capture", CaptureConfig), ("analyze", AnalyzeConfig), ("llm", LlmConfig)):
            sub = raw.get(key)
            if isinstance(sub, dict):
                known = {k: v for k, v in sub.items() if k in klass.__dataclass_fields__}
                if key == "capture" and "post_action_delays" in known:
                    known["post_action_delays"] = tuple(known["post_action_delays"])
                setattr(cfg, key, klass(**known))
        return cfg

    def save(self) -> Path:
        f = self.config_file()
        d = asdict(self)
        d["capture"]["post_action_delays"] = list(self.capture.post_action_delays)
        f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        return f


# ---------------------------------------------------------------- API 키


def get_api_key() -> str | None:
    """keyring → 환경변수 순으로 API 키를 찾는다."""
    try:
        import keyring

        key = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        if key:
            return key
    except Exception:
        pass  # keyring 백엔드가 없는 환경 (CI, 일부 서버) — 환경변수로 폴백
    return os.environ.get(ENV_API_KEY) or None


def set_api_key(key: str) -> str:
    """API 키를 Windows 자격 증명 관리자에 저장한다.

    반환값은 저장 위치 설명 — 사용자에게 어디에 들어갔는지 알려주기 위함이다.
    """
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
        return "Windows 자격 증명 관리자"
    except Exception as exc:
        raise RuntimeError(
            f"keyring에 저장할 수 없습니다({exc}). "
            f"대신 환경변수 {ENV_API_KEY}를 설정하세요."
        ) from exc


def clear_api_key() -> None:
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception:
        pass

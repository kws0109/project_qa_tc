"""토큰·비용 추적.

리뷰 GUI에 실시간으로 표시되고, 세션 예산을 넘으면 호출을 막는다.
"비용이 얼마나 나갈지 모르겠다"가 이런 도구를 못 쓰게 만드는 가장 흔한 이유라
숫자를 항상 보이게 두는 것이 중요하다.

**가격은 공개 목록가 기준이며 변동될 수 있다.** 정확한 청구액은 Anthropic 콘솔을
봐야 한다 — 여기 숫자는 "지금 이 작업이 대략 얼마짜리인가"를 판단하기 위한 것이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

#: 100만 토큰당 USD (입력, 출력).
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

#: Sonnet 5 도입가. 이 날짜까지는 더 싸다 — 지나면 정가로 자동 전환된다.
_SONNET5_INTRO_UNTIL = date(2026, 8, 31)
_SONNET5_INTRO = (2.00, 10.00)

#: 캐시 쓰기는 입력가의 1.25배(5분 TTL), 읽기는 0.1배.
CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10


def model_rates(model: str, today: date | None = None) -> tuple[float, float]:
    """모델의 (입력, 출력) 100만 토큰당 단가."""
    if model == "claude-sonnet-5":
        now = today or date.today()
        if now <= _SONNET5_INTRO_UNTIL:
            return _SONNET5_INTRO
    return _PRICING.get(model, (5.00, 25.00))  # 미등록 모델은 Opus 기준으로 보수적 추정


def estimate_image_tokens(width: int, height: int) -> int:
    """이미지 토큰 수 추정. Anthropic 공식은 대략 (가로 × 세로) / 750.

    스크린샷 한 장이 얼마나 비싼지 감을 잡는 용도다. 1568px 긴 변 기준
    16:9 이미지가 약 1,800토큰, 2576px면 약 4,800토큰이 된다.
    """
    return int(width * height / 750)


@dataclass
class Usage:
    """한 번의 호출에서 소비된 토큰."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read + other.cache_read,
            self.cache_write + other.cache_write,
        )

    def cost_usd(self, model: str, today: date | None = None) -> float:
        """이 사용량의 예상 비용(USD)."""
        in_rate, out_rate = model_rates(model, today)
        m = 1_000_000
        return (
            self.input_tokens * in_rate
            + self.output_tokens * out_rate
            + self.cache_read * in_rate * CACHE_READ_MULT
            + self.cache_write * in_rate * CACHE_WRITE_MULT
        ) / m

    @classmethod
    def from_response(cls, usage: object) -> Usage:
        """Anthropic 응답의 usage 객체에서 뽑아낸다."""
        return cls(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )


@dataclass
class CostTracker:
    """세션 전체의 비용 원장. 예산 초과 시 호출을 막는다."""

    budget_usd: float = 5.0
    by_model: dict[str, Usage] = field(default_factory=dict)
    by_purpose: dict[str, float] = field(default_factory=dict)
    calls: int = 0

    def record(self, model: str, purpose: str, usage: Usage) -> float:
        """사용량을 기록하고 이번 호출의 비용을 돌려준다."""
        self.by_model[model] = self.by_model.get(model, Usage()) + usage
        cost = usage.cost_usd(model)
        self.by_purpose[purpose] = self.by_purpose.get(purpose, 0.0) + cost
        self.calls += 1
        return cost

    @property
    def total_usd(self) -> float:
        return sum(u.cost_usd(m) for m, u in self.by_model.items())

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.budget_usd - self.total_usd)

    @property
    def over_budget(self) -> bool:
        return self.total_usd >= self.budget_usd

    @property
    def cache_savings_usd(self) -> float:
        """캐시 덕분에 아낀 금액. 캐시가 실제로 동작하는지 보여주는 지표다.

        0에 가까우면 프롬프트 접두사가 매번 바뀌고 있다는 뜻 — 캐시가 무효화되고 있다.
        """
        saved = 0.0
        for model, u in self.by_model.items():
            in_rate, _ = model_rates(model)
            saved += u.cache_read * in_rate * (1.0 - CACHE_READ_MULT) / 1_000_000
        return saved

    def summary(self) -> str:
        if not self.calls:
            return "LLM 호출 없음"
        lines = [
            f"LLM 호출 {self.calls}회 · 예상 비용 ${self.total_usd:.4f} "
            f"(예산 ${self.budget_usd:.2f} 중 ${self.remaining_usd:.4f} 남음)"
        ]
        for model, u in sorted(self.by_model.items()):
            lines.append(
                f"  {model}: 입력 {u.input_tokens:,} / 출력 {u.output_tokens:,}"
                f" / 캐시읽기 {u.cache_read:,} → ${u.cost_usd(model):.4f}"
            )
        if self.cache_savings_usd > 0.0001:
            lines.append(f"  캐시 절감 ${self.cache_savings_usd:.4f}")
        for purpose, cost in sorted(self.by_purpose.items(), key=lambda kv: -kv[1]):
            lines.append(f"  [{purpose}] ${cost:.4f}")
        return "\n".join(lines)


class BudgetExceeded(RuntimeError):
    """세션 예산을 초과해 호출을 거부했을 때."""

    def __init__(self, tracker: CostTracker):
        super().__init__(
            f"LLM 예산 ${tracker.budget_usd:.2f}를 초과했습니다 "
            f"(현재 ${tracker.total_usd:.4f}). 설정에서 예산을 올리거나 "
            f"분석 범위를 줄이세요."
        )
        self.tracker = tracker

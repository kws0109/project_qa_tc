"""LLM 계층 — 화면 명명, 동일성 판별, TC 생성, 리뷰 채팅.

**LLM 없이도 파이프라인은 동작한다.** 이 계층은 분석 결과의 품질을 올릴 뿐
필수 경로가 아니다. API 키가 없으면 화면은 "미확인 화면 1a2b" 같은 자동 이름을
갖고, TC는 생성되지 않으며, 나머지(녹화·분석·플로우 그래프·다이어그램)는
그대로 나온다.

이 방향의 의존성(analyze → llm 없음, llm → analyze 있음)이 그 성질을 보장한다.
"""

from .chat import ChatTurn, ReviewChat, ToolEffect
from .client import (
    EDGE_BULK,
    EDGE_DETAIL,
    LlmClient,
    LlmError,
    LlmRefused,
    LlmResult,
    encode_image,
    text_block,
)
from .cost import BudgetExceeded, CostTracker, Usage, estimate_image_tokens, model_rates
from .disambiguate import make_disambiguator
from .naming import NamingReport, name_states
from .tcgen import TcGenReport, generate_testcases

__all__ = [
    "BudgetExceeded",
    "ChatTurn",
    "CostTracker",
    "EDGE_BULK",
    "EDGE_DETAIL",
    "LlmClient",
    "LlmError",
    "LlmRefused",
    "LlmResult",
    "NamingReport",
    "ReviewChat",
    "TcGenReport",
    "ToolEffect",
    "Usage",
    "encode_image",
    "estimate_image_tokens",
    "generate_testcases",
    "make_disambiguator",
    "model_rates",
    "name_states",
    "text_block",
]

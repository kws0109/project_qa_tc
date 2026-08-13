"""Anthropic API 래퍼.

세 가지를 담당한다.

1. **이미지 인코딩** — 게임 스크린샷을 리사이즈·JPEG 압축해 base64로. 해상도가 토큰
   비용을 직접 결정하므로 용도별로 다른 크기를 쓴다.
2. **프롬프트 캐싱** — 게임 프로파일 컨텍스트와 작성 규칙은 매 호출 동일하다.
   접두사로 고정하고 캐시 표시를 달면 반복 호출이 90% 싸진다.
3. **구조화 출력** — ``output_config.format``으로 JSON 스키마를 강제한다.
   파싱 실패 재시도 루프가 아예 필요 없어진다.

**캐싱은 접두사 일치다.** 프롬프트 앞쪽이 1바이트라도 바뀌면 그 뒤 전부가 무효화된다.
그래서 게임 컨텍스트(고정) → 작업 지시(고정) → 화면 데이터(가변) 순서를 반드시 지킨다.
이 순서를 어기면 캐시 적중률이 0이 되고, 그 사실이 조용히 지나간다 —
:attr:`CostTracker.cache_savings_usd`가 0에 머무르면 그걸 의심할 것.
"""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import cv2
import numpy as np

from ..config import LlmConfig, get_api_key
from .cost import BudgetExceeded, CostTracker, Usage

#: 대량 작업(화면 명명, 동일성 판별)용 해상도. 비용을 억제한다.
EDGE_BULK = 1568
#: 정확도가 결정적인 작업(TC 생성, 리뷰 채팅)용. Opus 5 / Sonnet 5 고해상도 티어 상한.
#: 토큰이 약 3배가 되므로 소량 호출에만 쓴다.
EDGE_DETAIL = 2576


class LlmError(RuntimeError):
    """LLM 호출이 최종적으로 실패했을 때. 원인 메시지는 사용자에게 그대로 보여준다."""


class LlmRefused(LlmError):
    """안전 분류기가 요청을 거부했을 때 (``stop_reason == "refusal"``).

    게임 QA에서는 거의 발생하지 않지만, 발생하면 조용히 빈 결과로 넘어가는 대신
    사용자에게 알려야 한다 — 안 그러면 '왜 이 화면만 이름이 없지?'가 된다.
    """


def encode_image(bgr: np.ndarray, max_edge: int = EDGE_BULK, quality: int = 85) -> dict[str, Any]:
    """BGR 이미지를 Anthropic 이미지 블록으로.

    :param max_edge: 긴 변 최대 픽셀. 토큰 비용이 면적에 비례하므로 이 값이 곧 비용이다.
    """
    h, w = bgr.shape[:2]
    scale = min(1.0, max_edge / max(h, w))
    if scale < 1.0:
        bgr = cv2.resize(bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise LlmError("이미지를 JPEG로 인코딩할 수 없습니다")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.standard_b64encode(buf.tobytes()).decode("ascii"),
        },
    }


def text_block(text: str, cache: bool = False) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "text", "text": text}
    if cache:
        block["cache_control"] = {"type": "ephemeral"}
    return block


@dataclass
class LlmResult:
    """구조화 호출의 결과."""

    data: dict[str, Any]
    usage: Usage
    model: str
    cost_usd: float


class LlmClient:
    """Anthropic 클라이언트 래퍼. 세션 하나에 하나씩 만든다."""

    def __init__(
        self,
        config: LlmConfig | None = None,
        tracker: CostTracker | None = None,
        api_key: str | None = None,
    ):
        self.cfg = config or LlmConfig()
        self.tracker = tracker or CostTracker(budget_usd=self.cfg.budget_usd)
        self._api_key = api_key or get_api_key()
        self._client: Any = None

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _ensure(self) -> Any:
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise LlmError(
                "Anthropic API 키가 설정되지 않았습니다.\n"
                "  qatc config --set-api-key  로 등록하거나\n"
                "  환경변수 ANTHROPIC_API_KEY 를 설정하세요."
            )
        import anthropic

        self._client = anthropic.Anthropic(
            api_key=self._api_key,
            max_retries=self.cfg.max_retries,
            timeout=self.cfg.timeout_s,
        )
        return self._client

    # -- 핵심 호출 ---------------------------------------------------

    def structured(
        self,
        *,
        purpose: str,
        model: str,
        system: Sequence[dict[str, Any]],
        content: Sequence[dict[str, Any]],
        schema: dict[str, Any],
        max_tokens: int = 8000,
        effort: str | None = None,
    ) -> LlmResult:
        """JSON 스키마를 강제해 구조화된 응답을 받는다.

        ``output_config.format``을 쓰므로 파싱 실패가 원천적으로 없다 —
        재시도 루프와 정규식 추출이 필요 없다.

        :param system: 시스템 블록 목록. **고정 내용을 앞에 두고 마지막 블록에
            캐시 표시를 달 것** (:func:`text_block` 의 ``cache=True``).
        :param content: 사용자 메시지의 콘텐츠 블록(텍스트 + 이미지).
        """
        if self.tracker.over_budget:
            raise BudgetExceeded(self.tracker)

        client = self._ensure()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": list(system),
            "messages": [{"role": "user", "content": list(content)}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        if effort:
            kwargs["output_config"]["effort"] = effort

        response = self._call(client, kwargs, purpose)
        raw = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            # output_config.format이 있으면 사실상 일어나지 않지만, 일어나면
            # 원문을 보여줘야 원인을 알 수 있다.
            raise LlmError(f"응답을 JSON으로 읽을 수 없습니다: {exc}\n원문: {raw[:400]}") from exc

        usage = Usage.from_response(response.usage)
        cost = self.tracker.record(model, purpose, usage)
        return LlmResult(data=data, usage=usage, model=model, cost_usd=cost)

    def converse(
        self,
        *,
        purpose: str,
        model: str,
        system: Sequence[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: Sequence[dict[str, Any]] | None = None,
        max_tokens: int = 8000,
        effort: str | None = None,
    ) -> Any:
        """도구 호출이 가능한 대화. 리뷰 채팅이 쓴다.

        구조화 출력과 달리 여기서는 LLM이 **앱 상태를 실제로 바꾸는 도구**를 부른다
        (``rename_state``, ``merge_states`` 등). 원본 응답 객체를 그대로 돌려주므로
        호출부가 ``stop_reason``과 ``tool_use`` 블록을 직접 다룬다.
        """
        if self.tracker.over_budget:
            raise BudgetExceeded(self.tracker)

        client = self._ensure()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": list(system),
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = list(tools)
        if effort:
            kwargs["output_config"] = {"effort": effort}

        response = self._call(client, kwargs, purpose)
        self.tracker.record(model, purpose, Usage.from_response(response.usage))
        return response

    def _call(self, client: Any, kwargs: dict[str, Any], purpose: str) -> Any:
        """실제 API 호출 + 오류 매핑.

        SDK가 429/5xx를 자동 재시도하므로 여기서는 재시도하지 않고, 사용자가
        무엇을 해야 하는지 알 수 있는 메시지로 바꾸는 데 집중한다.
        """
        import anthropic

        try:
            response = client.messages.create(**kwargs)
        except anthropic.NotFoundError as exc:
            raise LlmError(
                f"모델 '{kwargs.get('model')}'을(를) 찾을 수 없습니다. "
                f"설정의 모델 ID를 확인하세요. ({exc})"
            ) from exc
        except anthropic.AuthenticationError as exc:
            raise LlmError(f"API 키가 유효하지 않습니다. 다시 등록하세요. ({exc})") from exc
        except anthropic.PermissionDeniedError as exc:
            raise LlmError(f"이 API 키로는 해당 모델을 쓸 수 없습니다. ({exc})") from exc
        except anthropic.RateLimitError as exc:
            raise LlmError(
                f"요청 한도를 초과했습니다. 잠시 후 다시 시도하거나 "
                f"분석 설정에서 LLM 질의 상한을 낮추세요. ({exc})"
            ) from exc
        except anthropic.APIStatusError as exc:
            raise LlmError(f"API 오류 {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LlmError(f"네트워크 연결 실패. 인터넷 연결을 확인하세요. ({exc})") from exc

        if getattr(response, "stop_reason", None) == "refusal":
            detail = getattr(response, "stop_details", None)
            category = getattr(detail, "category", None) if detail else None
            raise LlmRefused(
                f"안전 분류기가 요청을 거부했습니다"
                + (f" (분류: {category})" if category else "")
                + f". 작업: {purpose}"
            )
        if getattr(response, "stop_reason", None) == "max_tokens":
            # 잘린 응답은 구조화 출력에서 JSON 파싱 실패로 이어진다. 먼저 알려준다.
            raise LlmError(
                f"응답이 max_tokens({kwargs.get('max_tokens')})에서 잘렸습니다. "
                f"한 번에 처리하는 항목 수를 줄이세요. 작업: {purpose}"
            )
        return response


def batched(items: Sequence[Any], size: int) -> Iterable[list[Any]]:
    """항목을 배치로 나눈다. 화면 50개를 한 번에 보내면 응답이 잘리므로 쪼갠다."""
    for i in range(0, len(items), size):
        yield list(items[i : i + size])

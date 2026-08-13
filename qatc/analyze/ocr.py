"""OCR 래퍼 (RapidOCR / ONNX Runtime).

**PaddleOCR을 쓰지 않는 이유**: ``paddlepaddle``은 Python 3.14 휠이 없다. RapidOCR은
같은 PP-OCR 모델을 ONNX로 변환해 쓰므로 ``onnxruntime``만 있으면 되고, 배포 용량도
1/3이며 PyInstaller 패키징이 훨씬 쉽다.

**모델 조합**: 인식(Rec)만 한국어 모델로 바꾸고 검출(Det)은 기본값을 쓴다.
"어디에 글자가 있나"는 언어와 거의 무관하고 "그게 무슨 글자인가"만 언어 모델이
필요하기 때문이다.

**OCR은 보조 신호다.** 실측 정확도가 게임 스타일 텍스트에서 80% 수준이라
화면 정체성의 주 신호로는 못 쓴다. 텍스트 집합의 자카드 유사도를 낼 때
몇 글자 틀려도 결과가 뒤집히지 않는 용도로만 쓴다.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from ..models import ElementKind, NormRect, UIElement

#: 토큰에서 제거할 가변 문자 — 숫자와 그 구분자.
#: 재화 수량·레벨·타이머·시간은 같은 화면에서도 계속 바뀐다. 토큰 전체가 숫자인
#: 경우만 버리면 부족하다: OCR은 라벨과 숫자를 한 줄로 붙여 "재화12,684"처럼 읽는데,
#: 그건 한글이 섞여 있어 "숫자 토큰" 필터를 통과해 버린다. 숫자를 **제거**해야
#: "재화12,684"와 "재화11,980"이 같은 "재화"로 수렴한다.
_DIGITS = re.compile(r"[\d,./:%+\-]+")
#: OCR 노이즈로 흔한 한두 글자 기호 조각
_JUNK = re.compile(r"^[^\w가-힣]{1,2}$")


@dataclass
class OcrLine:
    text: str
    rect: NormRect
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "rect": list(self.rect.as_tuple()),
            "confidence": round(self.confidence, 4),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OcrLine:
        return cls(text=d["text"], rect=NormRect(*d["rect"]), confidence=float(d.get("confidence", 0)))

    def to_element(self) -> UIElement:
        return UIElement(
            rect=self.rect,
            kind=ElementKind.TEXT,
            text=self.text,
            confidence=self.confidence,
            source="ocr",
        )


class OcrEngine:
    """지연 로딩 OCR 엔진. 모델 로드가 3~4초 걸리므로 실제로 쓸 때까지 미룬다.

    엔진 인스턴스는 스레드 안전하지 않으므로 호출을 락으로 감싼다. 어차피
    ONNX Runtime이 내부적으로 멀티스레드를 쓰기 때문에 병렬 호출의 이득은 작다.
    """

    def __init__(self, lang: str = "ko", min_confidence: float = 0.55):
        self.lang = lang
        self.min_confidence = min_confidence
        self._engine: Any = None
        self._lock = threading.Lock()
        self._load_error: str | None = None

    @property
    def available(self) -> bool:
        """모델을 쓸 수 있는지. 첫 호출 시 모델을 내려받으므로 오프라인이면 False가 된다."""
        self._ensure()
        return self._engine is not None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def _ensure(self) -> None:
        if self._engine is not None or self._load_error is not None:
            return
        with self._lock:
            if self._engine is not None or self._load_error is not None:
                return
            try:
                from rapidocr import RapidOCR
                from rapidocr.utils.typings import LangRec, ModelType, OCRVersion

                lang_map = {
                    "ko": LangRec.KOREAN,
                    "ja": LangRec.JAPAN,
                    "en": LangRec.EN,
                    "zh": LangRec.CH,
                }
                self._engine = RapidOCR(
                    params={
                        "Rec.lang_type": lang_map.get(self.lang, LangRec.KOREAN),
                        "Rec.ocr_version": OCRVersion.PPOCRV5,
                        "Rec.model_type": ModelType.MOBILE,
                        "Global.log_level": "error",
                    }
                )
            except Exception as exc:
                # OCR 실패가 파이프라인 전체를 막으면 안 된다. 텍스트 신호 없이도
                # 셀 시그니처와 구조 시그니처로 상태 식별은 동작한다.
                self._load_error = f"{type(exc).__name__}: {exc}"
                self._engine = None

    def read(self, bgr: np.ndarray) -> list[OcrLine]:
        """이미지에서 텍스트 줄을 읽는다. 실패하면 빈 목록 (예외를 던지지 않는다)."""
        self._ensure()
        if self._engine is None or bgr is None or bgr.size == 0:
            return []
        h, w = bgr.shape[:2]
        try:
            with self._lock:
                res = self._engine(bgr)
        except Exception:
            return []

        boxes = getattr(res, "boxes", None)
        txts = getattr(res, "txts", None)
        scores = getattr(res, "scores", None)
        if txts is None:
            return []

        lines: list[OcrLine] = []
        for i, text in enumerate(txts):
            conf = float(scores[i]) if scores is not None and i < len(scores) else 0.0
            if conf < self.min_confidence:
                continue
            text = (text or "").strip()
            if not text:
                continue
            rect = self._box_to_rect(boxes[i] if boxes is not None and i < len(boxes) else None, w, h)
            lines.append(OcrLine(text=text, rect=rect, confidence=conf))
        return lines

    @staticmethod
    def _box_to_rect(box: Any, w: int, h: int) -> NormRect:
        """4점 폴리곤을 정규화 바운딩박스로. 박스가 없으면 화면 전체."""
        if box is None:
            return NormRect(0.0, 0.0, 1.0, 1.0)
        pts = np.asarray(box, dtype=np.float32).reshape(-1, 2)
        x0, y0 = pts[:, 0].min(), pts[:, 1].min()
        x1, y1 = pts[:, 0].max(), pts[:, 1].max()
        return NormRect(
            float(x0 / w), float(y0 / h), float((x1 - x0) / w), float((y1 - y0) / h)
        )


# ---------------------------------------------------------------- 시그니처


def normalize_token(text: str) -> str:
    """비교용 정규화 — 숫자 제거, 공백 제거, 소문자화.

    숫자를 지우는 이유는 :data:`_DIGITS` 주석 참고. "재화 12,684" → "재화".
    """
    return re.sub(r"\s+", "", _DIGITS.sub("", text)).lower()


def text_signature(lines: Sequence[OcrLine], min_len: int = 2) -> list[str]:
    """화면 비교에 쓸 텍스트 토큰 집합.

    **가변 숫자를 제거한다.** 재화 수량·레벨·타이머는 같은 화면에서도 매 순간
    달라지므로, 남겨두면 같은 화면이 계속 다른 화면으로 잡힌다. 탭 이름과 버튼
    라벨처럼 화면의 정체성을 담은 문자열만 남긴다.
    """
    tokens: set[str] = set()
    for line in lines:
        t = normalize_token(line.text)
        if len(t) < min_len or _JUNK.match(t):
            continue
        tokens.add(t)
    return sorted(tokens)


def jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    """두 토큰 집합의 자카드 유사도. 둘 다 비어 있으면 '판단 불가'를 뜻하는 0.0."""
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def has_text_evidence(a: Sequence[str], b: Sequence[str], min_tokens: int = 2) -> bool:
    """텍스트 신호를 신뢰할 만한지. 양쪽 모두 토큰이 충분해야 한다.

    한쪽이 비어 있으면 자카드가 0이 되는데, 그건 "다른 화면"이 아니라
    "OCR이 못 읽었다"는 뜻일 수 있다. 그 구분을 못 하면 과분리가 일어난다.
    """
    return len(set(a)) >= min_tokens and len(set(b)) >= min_tokens

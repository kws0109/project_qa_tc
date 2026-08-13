"""분석 파이프라인 — 프레임 더미에서 화면 전이 그래프를 뽑아낸다.

::

    frames/ → ① 변동성 학습 → ② 1차 dedupe → ③ UI검출+OCR
            → ④ 클러스터링(+LLM 판별) → ⑤ 플로우 그래프 → flow.json

각 단계는 독립적으로 테스트 가능하고, 파라미터를 바꿔 **재분석해도 녹화는 그대로**다.
"""

from .cluster import (
    ClusterResult,
    FrameFeatures,
    Signals,
    cluster_frames,
    cluster_report,
    combined_similarity,
    compute_signals,
)
from .flow import FlowBuildResult, build_flow, describe_action
from .hashing import ScreenSignature, dedupe, dhash, hamming
from .motion import VolatilityMap, cell_means, learn_from_frames, learn_volatility
from .ocr import OcrEngine, OcrLine, jaccard, text_signature
from .pipeline import AnalyzeProgress, analyze_session
from .signature import struct_similarity, to_struct_signature
from .ui_detect import detect_elements, draw_overlay, element_at

__all__ = [
    "AnalyzeProgress",
    "ClusterResult",
    "FlowBuildResult",
    "FrameFeatures",
    "OcrEngine",
    "OcrLine",
    "ScreenSignature",
    "Signals",
    "VolatilityMap",
    "analyze_session",
    "build_flow",
    "cell_means",
    "cluster_frames",
    "cluster_report",
    "combined_similarity",
    "compute_signals",
    "dedupe",
    "describe_action",
    "detect_elements",
    "dhash",
    "draw_overlay",
    "element_at",
    "hamming",
    "jaccard",
    "learn_from_frames",
    "learn_volatility",
    "struct_similarity",
    "text_signature",
    "to_struct_signature",
]

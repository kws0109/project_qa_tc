"""플로우 다이어그램 (Mermaid ``stateDiagram-v2``).

**커버된 전이는 실선, 커버되지 않은 전이는 점선으로 그린다.** 엑셀 커버리지 시트가
숫자로 말하는 것을 그림으로 한눈에 보여주는 것이 목적이다 — 점선이 몰려 있는
영역이 곧 테스트 공백이다.

Mermaid를 고른 이유: 텍스트라서 git diff가 되고, GitHub·Notion·VS Code가 그대로
렌더링하며, 별도 렌더러 설치가 필요 없다. PNG가 필요하면 mermaid-cli로 변환할 수
있지만 필수는 아니다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from ..models import FlowGraph, TestCase, coverage

#: 노드 라벨 최대 길이. 길면 다이어그램이 옆으로 터진다.
MAX_LABEL = 22
#: 이 개수를 넘는 전이는 그리지 않는다 (관측 횟수 순으로 자름).
MAX_EDGES = 120


def _node_id(state_id: str) -> str:
    """Mermaid 식별자로 안전하게. 영숫자와 밑줄만 허용된다."""
    return re.sub(r"[^0-9A-Za-z_]", "_", state_id)


def _label(text: str, limit: int = MAX_LABEL) -> str:
    """라벨 이스케이프. Mermaid에서 따옴표와 개행이 구문을 깨뜨린다."""
    clean = re.sub(r"\s+", " ", text).replace('"', "'").strip()
    if len(clean) > limit:
        clean = clean[: limit - 1] + "…"
    return clean


def render_mermaid(
    graph: FlowGraph,
    testcases: Sequence[TestCase] = (),
    *,
    max_edges: int = MAX_EDGES,
    include_hidden: bool = False,
) -> str:
    """플로우 그래프를 Mermaid 텍스트로."""
    covered, uncovered = coverage(graph, testcases)

    states = {
        s.id: s
        for s in (graph.states.values() if include_hidden else graph.visible_states())
    }
    edges = [
        t for t in graph.ordered_transitions()
        if t.from_state in states and t.to_state in states
    ]
    dropped = 0
    if len(edges) > max_edges:
        # 자주 관측된 전이를 남긴다 — 드물게 지나간 경로보다 주 동선이 중요하다
        edges = sorted(edges, key=lambda t: -t.observed_count)[:max_edges]
        dropped = len(graph.transitions) - len(edges)

    lines = ["stateDiagram-v2", "    direction LR"]

    for sid, state in sorted(states.items()):
        node = _node_id(sid)
        lines.append(f'    {node} : {_label(state.name)}')

    lines.append("")
    for t in edges:
        src, dst = _node_id(t.from_state), _node_id(t.to_state)
        action = _label(t.action_desc, 26) or "(행동 미상)"
        if t.observed_count > 1:
            action += f" ×{t.observed_count}"
        if t.id in covered:
            lines.append(f"    {src} --> {dst} : {action}")
        else:
            # Mermaid에는 간선별 점선 문법이 없으므로 라벨에 표식을 달고
            # linkStyle로 실제 점선을 입힌다 (아래).
            lines.append(f"    {src} --> {dst} : ⚠ {action}")

    # 미커버 간선을 점선 + 빨강으로
    uncovered_indices = [i for i, t in enumerate(edges) if t.id not in covered]
    if uncovered_indices:
        lines.append("")
        for i in uncovered_indices:
            lines.append(
                f"    linkStyle {i} stroke:#c62828,stroke-width:1.5px,stroke-dasharray:5 4"
            )

    lines.append("")
    lines.append(
        f"    %% 커버 {len(covered)}/{len(graph.transitions)} 전이"
        f" · ⚠ 점선 = 테스트되지 않은 경로 ({len(uncovered)}건)"
    )
    if dropped > 0:
        lines.append(f"    %% 관측 빈도가 낮은 전이 {dropped}건은 가독성을 위해 생략됨")
    return "\n".join(lines)


def export_mermaid(
    graph: FlowGraph,
    testcases: Sequence[TestCase],
    out_path: Path | str,
    *,
    as_markdown: bool = True,
) -> Path:
    """Mermaid 다이어그램을 파일로 저장한다.

    :param as_markdown: True면 ```mermaid 펜스로 감싼다 — GitHub/Notion에
        붙여넣으면 바로 렌더링된다.
    """
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = render_mermaid(graph, testcases)

    if as_markdown:
        covered, uncovered = coverage(graph, testcases)
        rate = len(covered) / max(1, len(graph.transitions))
        text = (
            "# 화면 전이 플로우\n\n"
            f"- 화면 {len(graph.visible_states())}개 · 전이 {len(graph.transitions)}개\n"
            f"- 전이 커버리지 **{rate:.0%}** ({len(covered)}/{len(graph.transitions)})\n"
            f"- 빨간 점선(⚠) = 어떤 테스트케이스도 커버하지 않은 경로 **{len(uncovered)}건**\n\n"
            "```mermaid\n" + body + "\n```\n"
        )
    else:
        text = body

    path.write_text(text, encoding="utf-8")
    return path

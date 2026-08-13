"""스텝 타임라인 — 플레이 순서대로 전이를 나열한다.

**시간순이 기본 정렬이다.** 화면 목록(그래프 노드)이 아니라 전이 목록(스텝)을
보여주는 이유는, QA 담당자가 기억하는 것이 "무엇을 눌렀더니 어디로 갔다"는
순서이기 때문이다. 화면 이름만 나열하면 자기가 뭘 했는지 되짚기 어렵다.

신뢰도가 낮은 화면(⚠)과 자기 전이(↻)를 표시해 손봐야 할 곳이 눈에 띄게 한다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..models import FlowGraph

_ROLE_KIND = Qt.ItemDataRole.UserRole
_ROLE_ID = Qt.ItemDataRole.UserRole + 1

VIEW_STEPS = "스텝 순서 (플레이 순)"
VIEW_STATES = "화면 목록"
VIEW_REVIEW = "검토 필요만"


class TimelinePanel(QWidget):
    """왼쪽 타임라인. 항목을 고르면 캔버스와 편집 패널이 따라간다."""

    #: (kind, id) — kind는 "transition" 또는 "state"
    selected = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.graph: FlowGraph | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        header = QHBoxLayout()
        self.view_mode = QComboBox()
        self.view_mode.addItems([VIEW_STEPS, VIEW_STATES, VIEW_REVIEW])
        self.view_mode.currentTextChanged.connect(lambda _t: self.refresh())
        header.addWidget(self.view_mode, 1)
        layout.addLayout(header)

        self.summary = QLabel("—")
        self.summary.setStyleSheet("color:#8a8f98; font-size:11px;")
        layout.addWidget(self.summary)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setAlternatingRowColors(True)
        self.list.setWordWrap(True)
        self.list.currentItemChanged.connect(self._on_current_changed)
        layout.addWidget(self.list, 1)

    # -- 데이터 ------------------------------------------------------

    def set_graph(self, graph: FlowGraph) -> None:
        self.graph = graph
        self.refresh()

    def refresh(self, keep_id: str | None = None) -> None:
        """목록을 다시 그린다. 갱신 후에도 선택을 유지한다."""
        if self.graph is None:
            return
        keep = keep_id or self.current_id()
        self.list.blockSignals(True)
        self.list.clear()

        mode = self.view_mode.currentText()
        if mode == VIEW_STEPS:
            self._fill_steps()
        elif mode == VIEW_STATES:
            self._fill_states(review_only=False)
        else:
            self._fill_states(review_only=True)

        self.list.blockSignals(False)
        self._update_summary()
        if keep:
            self.select_id(keep)
        elif self.list.count():
            self.list.setCurrentRow(0)

    def _fill_steps(self) -> None:
        assert self.graph is not None
        for n, t in enumerate(self.graph.ordered_transitions(), 1):
            src = self.graph.states.get(t.from_state)
            dst = self.graph.states.get(t.to_state)
            src_name = src.name if src else t.from_state
            dst_name = dst.name if dst else t.to_state

            marks = ""
            if t.is_self_loop:
                marks += " ↻"
            if (src and src.needs_review) or (dst and dst.needs_review):
                marks += " ⚠"

            item = QListWidgetItem(
                f"{n:>3}. {src_name}  →  {dst_name}{marks}\n"
                f"      {t.action_desc}"
            )
            item.setData(_ROLE_KIND, "transition")
            item.setData(_ROLE_ID, t.id)
            if t.is_self_loop:
                # 자기 전이는 대개 병합의 부산물이다. 흐리게 표시해 정리 대상임을 알린다.
                item.setForeground(QColor("#9aa0a6"))
            self.list.addItem(item)

    def _fill_states(self, review_only: bool) -> None:
        assert self.graph is not None
        states = sorted(self.graph.states.values(), key=lambda s: s.id)
        for state in states:
            if review_only and not state.needs_review and not state.user.hidden:
                continue
            out_n = len(self.graph.outgoing(state.id))
            in_n = len(self.graph.incoming(state.id))

            marks = ""
            if state.user.name:
                marks += " 🔒"
            elif state.needs_review:
                marks += " ⚠"
            if state.user.hidden:
                marks += " (제외됨)"

            item = QListWidgetItem(
                f"{state.name}{marks}\n"
                f"      {state.category} · 진입 {in_n} / 진출 {out_n} · 프레임 {len(state.auto.member_frame_ids)}"
            )
            item.setData(_ROLE_KIND, "state")
            item.setData(_ROLE_ID, state.id)
            if state.user.hidden:
                item.setForeground(QColor("#6c7079"))
            elif state.user.name:
                font = QFont()
                font.setBold(True)
                item.setFont(font)
            self.list.addItem(item)

    def _update_summary(self) -> None:
        if self.graph is None:
            return
        states = list(self.graph.states.values())
        need = sum(1 for s in states if s.needs_review and not s.user.hidden)
        locked = sum(1 for s in states if s.user.name)
        self.summary.setText(
            f"화면 {len(states)}개 · 전이 {len(self.graph.transitions)}개 · "
            f"확정 {locked} · 검토 필요 {need}"
        )

    # -- 선택 --------------------------------------------------------

    def _on_current_changed(self, current: QListWidgetItem | None, _prev) -> None:
        if current is None:
            return
        self.selected.emit(current.data(_ROLE_KIND), current.data(_ROLE_ID))

    def current_id(self) -> str | None:
        item = self.list.currentItem()
        return item.data(_ROLE_ID) if item else None

    def current_kind(self) -> str | None:
        item = self.list.currentItem()
        return item.data(_ROLE_KIND) if item else None

    def selected_state_ids(self) -> list[str]:
        """선택된 항목들의 화면 ID. 병합 기능이 쓴다.

        스텝 뷰에서는 전이의 도착 화면을 화면으로 친다 — 사용자가 스텝 두 개를
        고르고 '병합'을 누르면 그 스텝들이 도착한 화면을 합치려는 의도다.
        """
        out: list[str] = []
        for item in self.list.selectedItems():
            kind, ident = item.data(_ROLE_KIND), item.data(_ROLE_ID)
            if kind == "state":
                out.append(ident)
            elif kind == "transition" and self.graph is not None:
                t = next((x for x in self.graph.transitions if x.id == ident), None)
                if t is not None:
                    out.append(t.to_state)
        # 순서를 유지하며 중복 제거
        seen: set[str] = set()
        return [x for x in out if not (x in seen or seen.add(x))]

    def select_id(self, ident: str) -> None:
        for row in range(self.list.count()):
            if self.list.item(row).data(_ROLE_ID) == ident:
                self.list.setCurrentRow(row)
                return

"""화면 편집 패널 — 이름·분류·메모를 확정하고 요소에 라벨을 붙인다.

**여기서 입력한 값은 ``user`` 층에 저장된다.** LLM 추정값(``llm`` 층)과 분리되어
있어, 재분석하거나 LLM을 다시 돌려도 사라지지 않는다. 패널이 "LLM 추정"과
"확정됨"을 시각적으로 구분해 보여주는 것도 그래서다 — 무엇이 사람의 판단이고
무엇이 기계의 추측인지 항상 보여야 한다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..models import FlowGraph, ScreenState


class StatePanel(QWidget):
    """선택된 화면의 편집 폼."""

    changed = Signal(str)          # 변경된 화면 ID
    element_selected = Signal(int)  # 요소 목록에서 선택

    def __init__(self, parent=None):
        super().__init__(parent)
        self.graph: FlowGraph | None = None
        self._state: ScreenState | None = None
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        self.title = QLabel("화면을 선택하세요")
        self.title.setStyleSheet("font-size:14px; font-weight:600;")
        root.addWidget(self.title)

        self.origin = QLabel("")
        self.origin.setStyleSheet("color:#8a8f98; font-size:11px;")
        self.origin.setWordWrap(True)
        root.addWidget(self.origin)

        form_box = QGroupBox("화면 정보")
        form = QFormLayout(form_box)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("예: 캐릭터 목록")
        self.category_edit = QLineEdit()
        self.category_edit.setPlaceholderText("예: 캐릭터  (TC 대분류가 됩니다)")
        self.role_edit = QLineEdit()
        self.role_edit.setPlaceholderText("이 화면이 하는 일 (한 문장)")
        for w in (self.name_edit, self.category_edit, self.role_edit):
            w.editingFinished.connect(self._apply_fields)
        form.addRow("이름", self.name_edit)
        form.addRow("분류", self.category_edit)
        form.addRow("역할", self.role_edit)
        root.addWidget(form_box)

        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText(
            "테스트 시 주의점이나 배경 지식.\nTC 생성 시 LLM에게 함께 전달됩니다."
        )
        self.notes.setMaximumHeight(78)
        self.notes.textChanged.connect(self._mark_dirty)
        notes_box = QGroupBox("메모")
        QVBoxLayout(notes_box).addWidget(self.notes)
        root.addWidget(notes_box)

        flags = QHBoxLayout()
        self.locked = QCheckBox("확정 (재분석 시 보존)")
        self.locked.toggled.connect(self._apply_flags)
        self.hidden = QCheckBox("노이즈 — TC에서 제외")
        self.hidden.toggled.connect(self._apply_flags)
        flags.addWidget(self.locked)
        flags.addWidget(self.hidden)
        root.addLayout(flags)

        el_box = QGroupBox("검출된 UI 요소")
        el_layout = QVBoxLayout(el_box)
        hint = QLabel(
            "🏷 담당자 확정 · 🔷 아이콘 사전 인식 · ⬚ 미등록 아이콘\n"
            "캔버스에서 더블클릭하면 이름과 동작(누르면 무슨 일이 일어나는지)을 지정합니다."
        )
        hint.setStyleSheet("color:#8a8f98; font-size:11px;")
        hint.setWordWrap(True)
        el_layout.addWidget(hint)
        self.elements = QListWidget()
        self.elements.currentRowChanged.connect(self._on_element_row)
        self.elements.itemDoubleClicked.connect(self._rename_element)
        el_layout.addWidget(self.elements)
        root.addWidget(el_box, 1)

        btns = QHBoxLayout()
        self.label_btn = QPushButton("요소에 이름 붙이기")
        self.label_btn.clicked.connect(lambda: self._rename_element(self.elements.currentItem()))
        btns.addWidget(self.label_btn)
        root.addLayout(btns)

        self.setEnabled(False)

    # -- 로딩 --------------------------------------------------------

    def set_graph(self, graph: FlowGraph) -> None:
        self.graph = graph

    def show_state(self, state: ScreenState | None) -> None:
        self._state = state
        if state is None:
            self.setEnabled(False)
            self.title.setText("화면을 선택하세요")
            self.origin.setText("")
            self.elements.clear()
            return

        self._loading = True
        self.setEnabled(True)
        self.title.setText(f"{state.name}")

        if state.user.name:
            src = "담당자 확정"
        elif state.llm and state.llm.name:
            src = f"LLM 추정 (신뢰도 {state.llm.confidence:.0%}, {state.llm.model})"
        else:
            src = "미확인 — 이름을 지정해 주세요"
        frames = len(state.auto.member_frame_ids)
        self.origin.setText(f"{state.id} · {src} · 프레임 {frames}장")

        self.name_edit.setText(state.user.name or (state.llm.name if state.llm else ""))
        self.category_edit.setText(state.user.category or (state.llm.category if state.llm else ""))
        self.role_edit.setText(state.user.role or (state.llm.role if state.llm else ""))
        self.notes.setPlainText(state.user.notes)
        self.locked.setChecked(state.user.locked)
        self.hidden.setChecked(state.user.hidden)

        self.elements.clear()
        for i, el in enumerate(state.elements_sorted()):
            # 출처를 기호로 구분한다 — 무엇이 사람의 판단이고 무엇이 자동인지 항상 보여야 한다
            if el.source == "user":
                mark = "🏷 "
            elif el.source == "icon":
                mark = "🔷 "
            elif not el.text.strip() and 0.0004 <= el.rect.area <= 0.05:
                mark = "⬚ "   # 텍스트 없는 작은 요소 = 미등록 아이콘 후보
            else:
                mark = "   "
            text = el.label or el.text or f"({el.kind.value})"
            item = QListWidgetItem(
                f"{mark}{i:>2}. {text}   @ ({el.rect.cx:.2f}, {el.rect.cy:.2f})"
            )
            item.setData(Qt.ItemDataRole.UserRole, i)
            self.elements.addItem(item)

        self._loading = False

    # -- 편집 반영 ---------------------------------------------------

    def _apply_fields(self) -> None:
        """입력값을 ``user`` 층에 쓴다. 빈 칸은 None으로 — LLM 추정으로 되돌아간다."""
        if self._loading or self._state is None:
            return
        state = self._state
        state.user.name = self.name_edit.text().strip() or None
        state.user.category = self.category_edit.text().strip() or None
        state.user.role = self.role_edit.text().strip() or None
        self.title.setText(state.name)
        self.changed.emit(state.id)

    def _mark_dirty(self) -> None:
        if self._loading or self._state is None:
            return
        self._state.user.notes = self.notes.toPlainText().strip()

    def _apply_flags(self) -> None:
        if self._loading or self._state is None:
            return
        self._state.user.locked = self.locked.isChecked()
        self._state.user.hidden = self.hidden.isChecked()
        self.changed.emit(self._state.id)

    def commit_pending(self) -> None:
        """포커스가 남아 있는 입력을 강제로 반영한다. 저장 직전에 호출한다."""
        self._apply_fields()
        self._mark_dirty()

    # -- 요소 --------------------------------------------------------

    def _on_element_row(self, row: int) -> None:
        if row >= 0 and not self._loading:
            self.element_selected.emit(row)

    def select_element(self, index: int) -> None:
        if 0 <= index < self.elements.count():
            self._loading = True
            self.elements.setCurrentRow(index)
            self._loading = False

    def _rename_element(self, item: QListWidgetItem | None) -> None:
        from PySide6.QtWidgets import QInputDialog

        if item is None or self._state is None:
            return
        index = item.data(Qt.ItemDataRole.UserRole)
        elements = self._state.elements_sorted()
        if not (0 <= index < len(elements)):
            return
        element = elements[index]
        current = element.label or element.text
        text, ok = QInputDialog.getText(
            self, "요소 이름", "이 요소의 이름 (TC 문구에 쓰입니다):", text=current
        )
        if not ok:
            return
        element.label = text.strip()
        element.source = "user" if text.strip() else element.source
        self.show_state(self._state)
        self.select_element(index)
        self.changed.emit(self._state.id)

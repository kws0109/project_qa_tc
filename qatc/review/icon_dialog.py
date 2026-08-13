"""아이콘 등록 다이얼로그.

**초안이 채워진 채로 열립니다.** 플로우 그래프에서 "이 클릭 → 캐릭터 화면 이동"을
추론해 동작 유형·대상 화면·기대 결과를 미리 넣어두므로, 담당자는 대개 **이름 한 칸만**
채우면 됩니다.

이름을 자동으로 채우지 않는 이유: 화면 전이만으로는 그 그림이 "기원"인지 "워프"인지
알 수 없습니다. 틀린 이름을 미리 넣어두면 무심코 승인해 사전이 오염됩니다.
모르는 칸은 비워두는 편이 낫습니다.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..icons import ActionKind, IconAction, IconEntry, IconStore, IconSuggestion
from ..models import FlowGraph, NormRect
from .canvas_panel import numpy_to_pixmap


class IconDialog(QDialog):
    """아이콘 하나를 등록하거나 수정한다."""

    def __init__(
        self,
        patch: np.ndarray,
        store: IconStore,
        graph: FlowGraph,
        *,
        suggestion: IconSuggestion | None = None,
        entry: IconEntry | None = None,
        screen_name: str = "",
        rect: NormRect | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.patch = patch
        self.store = store
        self.graph = graph
        self.entry = entry
        self.screen_name = screen_name
        self.rect = rect
        self.result_entry: IconEntry | None = None

        self.setWindowTitle("아이콘 등록" if entry is None else f"아이콘 수정 — {entry.name}")
        self.setMinimumWidth(520)
        self._build()
        self._prefill(suggestion)

    # -- 구성 --------------------------------------------------------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # 아이콘 미리보기 + 제안 근거
        head = QHBoxLayout()
        preview = QLabel()
        preview.setPixmap(
            numpy_to_pixmap(self.patch).scaled(
                84, 84, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        preview.setStyleSheet("border:1px solid #c8cdd4; border-radius:6px; background:#20242a;")
        head.addWidget(preview)

        self.rationale = QLabel()
        self.rationale.setWordWrap(True)
        self.rationale.setStyleSheet("color:#5f6672; font-size:12px;")
        head.addWidget(self.rationale, 1)
        root.addLayout(head)

        form = QFormLayout()
        form.setSpacing(8)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("예: 기원  ·  가방  ·  우편   ← 이 칸만 채우면 됩니다")
        form.addRow("이름 *", self.name_edit)

        self.kind_combo = QComboBox()
        for kind in ActionKind:
            self.kind_combo.addItem(kind.value, kind)
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        form.addRow("동작 유형", self.kind_combo)

        self.target_combo = QComboBox()
        self.target_combo.setEditable(True)
        self.target_combo.addItem("")
        for state in sorted(self.graph.visible_states(), key=lambda s: s.name):
            self.target_combo.addItem(state.name)
        self.target_combo.currentTextChanged.connect(self._sync_expected)
        form.addRow("대상 화면", self.target_combo)

        self.expected_edit = QLineEdit()
        self.expected_edit.setPlaceholderText("누르면 무슨 일이 일어나는지 — TC 기대결과에 그대로 들어갑니다")
        form.addRow("기대 결과", self.expected_edit)

        self.consumes_edit = QLineEdit()
        self.consumes_edit.setPlaceholderText("예: 성간 항행권   (없으면 비워두세요)")
        form.addRow("소모 재화", self.consumes_edit)

        self.reversible = QCheckBox("되돌릴 수 있음")
        self.reversible.setChecked(True)
        self.reversible.setToolTip(
            "체크 해제하면 이 아이콘이 관련된 TC의 우선순위가 High로 올라갑니다."
        )
        form.addRow("", self.reversible)

        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("테스트 시 주의점 — TC 생성 시 LLM에게 함께 전달됩니다")
        self.notes.setMaximumHeight(64)
        form.addRow("메모", self.notes)
        root.addLayout(form)

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(
            "color:#8a5a1a; background:#fdf2e4; padding:8px 10px; border-radius:6px; font-size:12px;"
        )
        root.addWidget(self.hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("저장")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # -- 초기값 ------------------------------------------------------

    def _prefill(self, suggestion: IconSuggestion | None) -> None:
        if self.entry is not None:
            self._load_entry(self.entry)
            self.rationale.setText(
                f"샘플 {self.entry.sample_count}개 학습됨"
                + (f" · 관측 화면: {', '.join(self.entry.seen_screens[:3])}"
                   if self.entry.seen_screens else "")
            )
        elif suggestion is not None:
            self._load_action(suggestion.action)
            if suggestion.name_hint:
                self.name_edit.setText(suggestion.name_hint)
            self.rationale.setText(
                f"<b>자동 제안</b><br>{suggestion.rationale}"
                if suggestion.rationale else "자동 제안된 초안입니다."
            )
        else:
            self.rationale.setText("새 아이콘을 등록합니다.")

        self._on_kind_changed()
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _load_entry(self, entry: IconEntry) -> None:
        self.name_edit.setText(entry.name)
        self.notes.setPlainText(entry.notes)
        self._load_action(entry.action)

    def _load_action(self, action: IconAction) -> None:
        index = self.kind_combo.findText(action.kind.value)
        if index >= 0:
            self.kind_combo.setCurrentIndex(index)
        self.target_combo.setCurrentText(action.target_screen_name)
        self.expected_edit.setText(action.expected)
        self.consumes_edit.setText(action.consumes)
        self.reversible.setChecked(action.reversible)

    # -- 반응 --------------------------------------------------------

    def _current_kind(self) -> ActionKind:
        """콤보박스에서 동작 유형을 읽는다.

        ``ActionKind``는 ``str`` Enum이라 Qt의 QVariant를 왕복하면서 평범한
        문자열로 강등된다 — ``currentData()``를 그대로 쓰면 ``'이동'``이 돌아오고,
        거기에 ``.tc_hint()``를 부르면 AttributeError가 난다. 반드시 되돌린다.
        """
        raw = self.kind_combo.currentData()
        if isinstance(raw, ActionKind):
            return raw
        try:
            return ActionKind(raw)
        except (ValueError, TypeError):
            return ActionKind.UNKNOWN

    def _on_kind_changed(self) -> None:
        kind = self._current_kind()
        # 유형에 따라 관련 없는 칸을 비활성화한다 — 채울 필요 없는 칸이 비어 있으면
        # 사용자가 "빠뜨린 건가?" 하고 망설인다.
        needs_target = kind in (
            ActionKind.NAVIGATE, ActionKind.BACK, ActionKind.TAB, ActionKind.OPEN
        )
        self.target_combo.setEnabled(needs_target)

        hint = kind.tc_hint()
        self.hint.setText(f"이 유형은 이렇게 테스트됩니다 — {hint}" if hint else "")
        self.hint.setVisible(bool(hint))

        if not self.expected_edit.text().strip():
            self._sync_expected()
        # 확인·구매는 기본적으로 되돌리기 어렵다
        if self.entry is None:
            self.reversible.setChecked(kind.default_reversible)

    def _sync_expected(self) -> None:
        """대상 화면이 정해지면 기대 결과 문장을 제안한다 (이미 쓴 것은 건드리지 않음)."""
        if self.expected_edit.text().strip():
            return
        kind = self._current_kind()
        target = self.target_combo.currentText().strip()
        if not target or kind is ActionKind.UNKNOWN:
            return
        from ..icons.suggest import _expected_sentence

        state = next((s for s in self.graph.states.values() if s.name == target), None)
        if state is not None:
            self.expected_edit.setText(_expected_sentence(kind, state))

    # -- 저장 --------------------------------------------------------

    def _save(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "아이콘 등록", "이름을 입력해 주세요.")
            self.name_edit.setFocus()
            return

        existing = self.store.by_name(name)
        if existing is not None and (self.entry is None or existing.id != self.entry.id):
            answer = QMessageBox.question(
                self,
                "같은 이름의 아이콘",
                f"'{name}'이(가) 이미 등록되어 있습니다 (샘플 {existing.sample_count}개).\n\n"
                f"[Yes] 이 이미지를 기존 아이콘의 학습 샘플로 추가합니다 (권장)\n"
                f"[No] 같은 이름으로 별도 등록합니다",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.store.add_sample(
                    existing.id, self.patch, screen_name=self.screen_name, rect=self.rect
                )
                self.result_entry = existing
                self.accept()
                return

        action = IconAction(
            kind=self._current_kind(),
            target_screen_name=(
                self.target_combo.currentText().strip() if self.target_combo.isEnabled() else ""
            ),
            expected=self.expected_edit.text().strip(),
            consumes=self.consumes_edit.text().strip(),
            reversible=self.reversible.isChecked(),
        )
        notes = self.notes.toPlainText().strip()

        if self.entry is not None:
            self.store.update(self.entry.id, name=name, action=action, notes=notes)
            self.store.add_sample(
                self.entry.id, self.patch, screen_name=self.screen_name, rect=self.rect
            )
            self.result_entry = self.entry
        else:
            self.result_entry = self.store.register(
                name, self.patch, action=action,
                screen_name=self.screen_name, rect=self.rect, notes=notes,
            )
        self.store.save()
        self.accept()


def pick_icon(store: IconStore, parent: QWidget | None = None) -> IconEntry | None:
    """등록된 아이콘 중 하나를 고른다. 오분류 교정("이건 A가 아니라 B야")에 쓴다."""
    from PySide6.QtWidgets import QInputDialog

    entries = sorted(store.entries.values(), key=lambda e: e.name)
    if not entries:
        QMessageBox.information(parent, "아이콘 선택", "등록된 아이콘이 없습니다.")
        return None
    labels = [f"{e.name}  ({e.action.describe()}, 샘플 {e.sample_count})" for e in entries]
    choice, ok = QInputDialog.getItem(parent, "아이콘 선택", "올바른 아이콘:", labels, 0, False)
    if not ok:
        return None
    return entries[labels.index(choice)]

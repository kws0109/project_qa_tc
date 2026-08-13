"""리뷰 워크스페이스 메인 윈도우.

3분할 구성::

    ┌──────────────┬──────────────────────────┬──────────────┐
    │ 스텝 타임라인  │  스크린샷 + 검출 오버레이   │  LLM 채팅     │
    │              │                          │              │
    │ 화면 목록     │  ▭요소  ⊕클릭 지점        │  화면 편집    │
    └──────────────┴──────────────────────────┴──────────────┘

**저장은 명시적이다.** 자동 저장을 하지 않는 이유는, 리뷰 도중 실수로 병합한 것을
되돌리고 싶을 때 파일을 다시 읽는 것이 유일한 복구 수단이기 때문이다.
창을 닫을 때 변경사항이 있으면 반드시 묻는다.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig, get_api_key
from ..models import FlowGraph, ScreenState
from ..profiles import generic_profile, load_profiles
from ..storage import SessionStore
from .canvas_panel import CanvasPanel
from .chat_panel import ChatPanel
from .state_panel import StatePanel
from .timeline_panel import TimelinePanel


class ReviewWindow(QMainWindow):
    def __init__(self, store: SessionStore, cfg: AppConfig):
        super().__init__()
        self.store = store
        self.cfg = cfg
        self.graph: FlowGraph = store.load_graph()
        self.meta = store.get_session()
        self._dirty = False
        self._image_cache: dict[str, np.ndarray | None] = {}
        self._current_state: ScreenState | None = None
        self._current_image: np.ndarray | None = None

        profiles = load_profiles(cfg.profiles_path)
        self.profile = profiles.get(self.meta.profile_name) or generic_profile(self.meta.game_name)

        # 아이콘 사전은 게임 단위로 세션보다 오래 산다 — 여기서 로드해 창이 닫힐 때까지 쓴다
        from ..icons import IconMatcher, IconStore

        self.icon_store = IconStore.load(self.profile.key)
        self.icon_matcher = IconMatcher(self.icon_store)
        self._needs_icon: set[int] = set()

        self.setWindowTitle(f"QATC 리뷰 — {self.meta.id} ({self.meta.game_name})")
        self.resize(1680, 980)

        self._build_ui()
        self._build_toolbar()
        self._setup_chat()

        self.timeline.set_graph(self.graph)
        self.state_panel.set_graph(self.graph)
        self._update_status()

    # -- 구성 --------------------------------------------------------

    def _build_ui(self) -> None:
        self.timeline = TimelinePanel()
        self.timeline.selected.connect(self._on_timeline_selected)

        self.canvas = CanvasPanel()
        self.canvas.element_clicked.connect(self._on_canvas_element)
        self.canvas.element_double_clicked.connect(self._on_canvas_element_dbl)
        self.canvas.region_selected.connect(self._on_region_selected)

        self.state_panel = StatePanel()
        self.state_panel.changed.connect(self._on_state_changed)
        self.state_panel.element_selected.connect(self.canvas.select_element)

        self.chat = ChatPanel()
        self.chat.graph_changed.connect(self._on_graph_changed_by_llm)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.addWidget(self.state_panel)
        right_splitter.addWidget(self.chat)
        right_splitter.setSizes([520, 460])
        right_layout.addWidget(right_splitter)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.timeline)
        splitter.addWidget(self.canvas)
        splitter.addWidget(right)
        splitter.setSizes([360, 860, 460])
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

        self.setStatusBar(QStatusBar())

    def _build_toolbar(self) -> None:
        bar = QToolBar("작업")
        bar.setMovable(False)
        self.addToolBar(bar)

        save = QAction("저장", self)
        save.setShortcut(QKeySequence.StandardKey.Save)
        save.triggered.connect(self.save)
        bar.addAction(save)
        bar.addSeparator()

        merge = QAction("선택 화면 병합", self)
        merge.setShortcut("Ctrl+M")
        merge.setToolTip("타임라인에서 2개 이상 선택한 뒤 누르세요 (Ctrl+클릭)")
        merge.triggered.connect(self.merge_selected)
        bar.addAction(merge)

        drop = QAction("전이 삭제", self)
        drop.setToolTip("선택한 스텝의 전이를 제거합니다 (병합으로 생긴 자기 전이 정리용)")
        drop.triggered.connect(self.delete_selected_transition)
        bar.addAction(drop)
        bar.addSeparator()

        self.show_elements = QCheckBox("요소 표시")
        self.show_elements.setChecked(True)
        self.show_elements.toggled.connect(self.canvas.set_elements_visible)
        bar.addWidget(self.show_elements)
        bar.addSeparator()

        self.region_mode = QCheckBox("아이콘 영역 지정")
        self.region_mode.setToolTip(
            "켜고 캔버스에서 드래그하면 그 영역을 새 아이콘으로 등록합니다.\n"
            "검출되지 않은 아이콘을 직접 잡을 때 쓰세요. (ESC로 해제)"
        )
        self.region_mode.toggled.connect(self.canvas.set_region_mode)
        bar.addWidget(self.region_mode)

        rescan = QAction("아이콘 재인식", self)
        rescan.setToolTip("사전이 갱신된 뒤 모든 화면을 다시 대조합니다")
        rescan.triggered.connect(self.rescan_icons)
        bar.addAction(rescan)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)

        self.cost_label = QLabel("")
        self.cost_label.setStyleSheet("color:#8a8f98; padding-right:10px;")
        bar.addWidget(self.cost_label)

    def _setup_chat(self) -> None:
        if not get_api_key():
            self.chat.attach(
                None,
                "API 키가 없어 채팅을 쓸 수 없습니다. "
                "'qatc config --set-api-key'로 등록한 뒤 다시 여세요.",
            )
            return
        from ..llm import CostTracker, LlmClient
        from ..llm.chat import ReviewChat

        self.tracker = CostTracker(budget_usd=self.cfg.llm.budget_usd)
        client = LlmClient(self.cfg.llm, self.tracker)
        self.chat.attach(
            ReviewChat(
                client, self.graph, self.profile, self._load_image,
                model=self.cfg.llm.model_deep,
            )
        )
        self._update_cost()

    # -- 이미지 ------------------------------------------------------

    def _load_image(self, frame_id: str) -> np.ndarray | None:
        """프레임 이미지를 읽고 캐시한다. 같은 화면을 오갈 때 디스크를 다시 안 친다."""
        if frame_id in self._image_cache:
            return self._image_cache[frame_id]
        frame = self.store.frame(frame_id)
        img = None
        if frame is not None:
            img = cv2.imread(str(self.store.frame_path(frame)), cv2.IMREAD_COLOR)
        # 캐시가 무한정 커지지 않게 상한을 둔다 (1080p 기준 장당 ~6MB)
        if len(self._image_cache) > 60:
            self._image_cache.clear()
        self._image_cache[frame_id] = img
        return img

    # -- 선택 처리 ---------------------------------------------------

    def _on_timeline_selected(self, kind: str, ident: str) -> None:
        if kind == "state":
            state = self.graph.states.get(ident)
            self._show(state, click=None)
        else:
            transition = next((t for t in self.graph.transitions if t.id == ident), None)
            if transition is None:
                return
            # 스텝을 고르면 **행동 직전 화면**을 보여준다 — 무엇을 눌렀는지가 핵심이다
            state = self.graph.states.get(transition.from_state)
            click = None
            event = self.store.event(transition.event_id) if transition.event_id else None
            if event is not None and event.nx is not None:
                click = (event.nx, event.ny)
            self._show(state, click=click)
            self.statusBar().showMessage(
                f"{transition.action_desc}  →  "
                f"{self.graph.states[transition.to_state].name if transition.to_state in self.graph.states else transition.to_state}",
                6000,
            )

    def _show(self, state: ScreenState | None, click: tuple[float, float] | None) -> None:
        image = self._load_image(state.auto.exemplar_frame_id) if state else None
        self._current_state = state
        self._current_image = image
        self._needs_icon = self._apply_icons(state, image)
        self.canvas.show_state(image, state, click, needs_icon=self._needs_icon)
        self.state_panel.show_state(state)
        self.chat.set_focus_state(state.id if state else None)

    def _apply_icons(self, state: ScreenState | None, image) -> set[int]:
        """사전과 대조해 라벨을 채우고, 미등록 아이콘 후보를 표시한다."""
        from ..icons import unmatched_icon_elements

        if state is None or image is None or self.icon_matcher.is_empty:
            if state is None or image is None:
                return set()
            return {i for i, _ in unmatched_icon_elements(state, set())}

        matched = self.icon_matcher.annotate_state(state, image)
        matched_indices = {index for index, _ in matched}
        return {i for i, _ in unmatched_icon_elements(state, matched_indices)}

    def _on_canvas_element(self, index: int) -> None:
        self.state_panel.select_element(index)

    def _on_canvas_element_dbl(self, index: int) -> None:
        """요소를 더블클릭하면 아이콘 등록으로 간다.

        텍스트가 이미 읽힌 요소는 아이콘이 아니므로 기존 이름 붙이기로 보낸다.
        """
        state, image = self._current_state, self._current_image
        if state is None or image is None:
            return
        elements = state.elements_sorted()
        if not (0 <= index < len(elements)):
            return
        element = elements[index]

        if element.text.strip() and element.source != "icon":
            self.state_panel.select_element(index)
            self.state_panel._rename_element(self.state_panel.elements.currentItem())
            return

        self._register_icon(element.rect, existing_label=element.label)

    def _on_region_selected(self, x: float, y: float, w: float, h: float) -> None:
        """드래그로 지정한 영역을 새 아이콘으로 등록한다 — 검출 누락 대응 경로."""
        from ..models import NormRect

        self.region_mode.setChecked(False)
        self._register_icon(NormRect(x, y, w, h))

    def _register_icon(self, rect, existing_label: str = "") -> None:
        from ..icons import crop_patch, suggest_for_element
        from ..models import UIElement
        from .icon_dialog import IconDialog

        state, image = self._current_state, self._current_image
        if state is None or image is None:
            return
        patch = crop_patch(image, rect)
        if patch is None or patch.size == 0:
            QMessageBox.information(self, "아이콘 등록", "영역이 너무 작습니다.")
            return

        existing = self.icon_store.by_name(existing_label) if existing_label else None
        suggestion = None
        if existing is None:
            suggestion = suggest_for_element(self.graph, state, UIElement(rect=rect))

        dialog = IconDialog(
            patch, self.icon_store, self.graph,
            suggestion=suggestion, entry=existing,
            screen_name=state.name, rect=rect, parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_entry is None:
            return

        self.icon_matcher.refresh()
        entry = dialog.result_entry
        self._dirty = True
        self._show(state, None)
        self.statusBar().showMessage(
            f"아이콘 '{entry.name}' 저장 — 사전 {len(self.icon_store)}개 · "
            f"샘플 {self.icon_store.stats()['samples']}개",
            6000,
        )

    def rescan_icons(self) -> None:
        """모든 화면을 사전과 다시 대조한다. 아이콘을 여러 개 등록한 뒤 한 번 누른다."""
        self.icon_matcher.refresh()
        if self.icon_matcher.is_empty:
            QMessageBox.information(
                self, "아이콘 재인식",
                "등록된 아이콘이 없습니다.\n캔버스에서 아이콘을 더블클릭해 등록하세요.",
            )
            return

        total = 0
        for state in self.graph.states.values():
            image = self._load_image(state.auto.exemplar_frame_id)
            if image is None:
                continue
            total += len(self.icon_matcher.annotate_state(state, image))

        self._dirty = True
        self.timeline.refresh()
        if self._current_state is not None:
            self._show(self._current_state, None)
        self.statusBar().showMessage(f"전체 화면에서 아이콘 {total}개를 인식했습니다.", 6000)

    # -- 변경 --------------------------------------------------------

    def _on_state_changed(self, state_id: str) -> None:
        self._dirty = True
        self.timeline.refresh()
        self._update_status()

    def _on_graph_changed_by_llm(self) -> None:
        """LLM이 도구로 그래프를 바꿨다. 전체를 다시 그린다."""
        self._dirty = True
        self.timeline.refresh()
        current = self.timeline.current_id()
        if current:
            kind = self.timeline.current_kind() or "state"
            self._on_timeline_selected(kind, current)
        self._update_status()
        self._update_cost()

    def merge_selected(self) -> None:
        ids = self.timeline.selected_state_ids()
        if len(ids) < 2:
            QMessageBox.information(
                self, "화면 병합",
                "병합하려면 타임라인에서 2개 이상을 선택하세요 (Ctrl+클릭).",
            )
            return
        keep, absorbed = ids[0], ids[1:]
        names = ", ".join(self.graph.states[i].name for i in absorbed if i in self.graph.states)
        if QMessageBox.question(
            self, "화면 병합",
            f"'{names}' 을(를) '{self.graph.states[keep].name}'에 병합합니다.\n"
            f"되돌릴 수 없습니다 (저장 전이면 파일을 다시 열어 복구할 수 있습니다).\n\n계속할까요?",
        ) != QMessageBox.StandardButton.Yes:
            return
        for other in absorbed:
            if other in self.graph.states:
                self.graph.merge_states(keep, other)
        self._dirty = True
        self.timeline.refresh(keep_id=keep)
        self._show(self.graph.states.get(keep), None)
        self._update_status()

    def delete_selected_transition(self) -> None:
        if self.timeline.current_kind() != "transition":
            QMessageBox.information(
                self, "전이 삭제", "스텝 순서 보기에서 삭제할 스텝을 선택하세요."
            )
            return
        tid = self.timeline.current_id()
        if tid is None:
            return
        self.graph.delete_transition(tid)
        self._dirty = True
        self.timeline.refresh()
        self._update_status()

    # -- 저장 --------------------------------------------------------

    def save(self) -> None:
        self.state_panel.commit_pending()
        self.store.save_graph(self.graph)
        self._dirty = False
        self._update_status()
        self.statusBar().showMessage("저장했습니다.", 4000)

    def _update_status(self) -> None:
        states = self.graph.visible_states()
        confirmed = sum(1 for s in states if s.user.name)
        mark = " ●변경됨" if self._dirty else ""
        self.statusBar().showMessage(
            f"화면 {len(states)}개 · 확정 {confirmed}개 · 전이 {len(self.graph.transitions)}개{mark}"
        )

    def _update_cost(self) -> None:
        tracker = getattr(self, "tracker", None)
        if tracker is None:
            return
        self.cost_label.setText(
            f"LLM ${tracker.total_usd:.4f} / ${tracker.budget_usd:.2f}"
        )

    def closeEvent(self, event) -> None:  # noqa: N802
        self.state_panel.commit_pending()
        if self._dirty:
            answer = QMessageBox.question(
                self, "저장하지 않은 변경",
                "변경사항을 저장할까요?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.StandardButton.Save:
                self.store.save_graph(self.graph)
        self.chat.stop()
        event.accept()

"""스크린샷 캔버스 — 화면 위에 검출된 UI 요소와 클릭 지점을 겹쳐 보여준다.

리뷰의 핵심 화면이다. 사용자가 "이 화면이 뭔지" 판단하려면 스크린샷을 봐야 하고,
"어디를 눌렀는지" 알려면 클릭 좌표가 표시돼야 하며, "무엇이 검출됐는지" 보려면
요소 박스가 필요하다.

**요소를 클릭해 라벨을 붙일 수 있다.** LLM이 놓친 버튼 이름을 사용자가 직접
지정하면 TC 절차 문구가 "화면 (0.42, 0.31) 클릭"에서 "[강화하기] 버튼 클릭"으로
바뀐다 — 이게 TC 품질에 가장 크게 기여하는 사용자 입력이다.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from ..models import ElementKind, ScreenState, UIElement

#: 요소 종류별 색. 패널은 흐리게, 버튼은 선명하게 — 클릭 가능한 것이 눈에 띄어야 한다.
_COLORS: dict[ElementKind, QColor] = {
    ElementKind.PANEL: QColor(120, 120, 130, 160),
    ElementKind.BUTTON: QColor(60, 220, 120, 230),
    ElementKind.ICON: QColor(240, 190, 60, 230),
    ElementKind.TEXT: QColor(240, 130, 200, 200),
    ElementKind.TAB: QColor(80, 180, 240, 230),
    ElementKind.INPUT: QColor(255, 140, 80, 230),
    ElementKind.LIST_ITEM: QColor(160, 160, 220, 200),
    ElementKind.UNKNOWN: QColor(170, 170, 170, 190),
}
_LABELED = QColor(255, 215, 0, 255)     # 사람이 이름을 붙인 요소 — 금색
_ICON_MATCHED = QColor(120, 230, 160, 255)  # 아이콘 사전이 알아본 요소 — 연두
_NEEDS_ICON = QColor(255, 120, 90, 220)     # 미등록 아이콘 후보 — 주황 점선


def numpy_to_pixmap(bgr: np.ndarray) -> QPixmap:
    """BGR ndarray → QPixmap.

    ``.copy()``가 필수다. ``QImage``는 넘겨받은 버퍼를 **참조만** 하므로,
    numpy 배열이 GC되면 이미지가 깨지거나 프로세스가 죽는다.
    """
    if bgr.ndim == 2:
        rgb = np.stack([bgr] * 3, axis=-1)
    else:
        rgb = bgr[:, :, ::-1]
    rgb = np.ascontiguousarray(rgb)
    h, w, ch = rgb.shape
    image = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(image)


class _ElementItem(QGraphicsRectItem):
    """클릭 가능한 UI 요소 박스."""

    def __init__(self, index: int, element: UIElement, rect: QRectF, needs_icon: bool = False):
        super().__init__(rect)
        self.index = index
        self.element = element
        self.needs_icon = needs_icon
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setToolTip(self._tooltip())
        self._apply_style(selected=False, hover=False)

    def _tooltip(self) -> str:
        if self.element.source == "icon":
            return f"아이콘 사전이 인식: {self.element.label}\n더블클릭하면 수정합니다"
        if self.element.source == "user":
            return f"담당자 확정: {self.element.label}"
        if self.needs_icon:
            return "미등록 아이콘 — 더블클릭해 이름과 동작을 지정하세요"
        return self.element.display

    def _apply_style(self, selected: bool, hover: bool) -> None:
        """요소의 상태를 색과 선 모양으로 구분한다.

        담당자가 한눈에 알아야 하는 것은 셋이다 — 내가 확정한 것(금색),
        사전이 알아본 것(연두), **아직 등록이 필요한 것**(주황 점선).
        마지막 것이 보이지 않으면 무엇을 채워야 하는지 알 수 없다.
        """
        style = Qt.PenStyle.SolidLine
        if selected:
            color, width = _SELECTED, 3.0
        elif self.element.source == "user":
            color, width = _LABELED, 2.5
        elif self.element.source == "icon":
            color, width = _ICON_MATCHED, 2.5
        elif self.needs_icon:
            color, width, style = _NEEDS_ICON, 2.0, Qt.PenStyle.DashLine
        elif self.element.label:
            color, width = _LABELED, 2.2
        else:
            color, width = _COLORS.get(self.element.kind, _COLORS[ElementKind.UNKNOWN]), 1.6
        if hover:
            width += 1.5
        self.setPen(QPen(color, width, style))
        self.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 34 if hover else 0)))

    def hoverEnterEvent(self, event) -> None:  # noqa: N802 - Qt 시그니처
        self._apply_style(self.isSelected(), True)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # noqa: N802
        self._apply_style(self.isSelected(), False)
        super().hoverLeaveEvent(event)

    def set_selected(self, selected: bool) -> None:
        self.setSelected(selected)
        self._apply_style(selected, False)


class CanvasPanel(QGraphicsView):
    """스크린샷 + 오버레이 뷰어.

    **두 가지 모드가 있다.**

    * 기본 — 드래그로 화면을 이동(팬), 요소 클릭으로 선택
    * 영역 지정 — 드래그로 사각형을 그려 새 아이콘을 등록

    영역 지정 모드가 필요한 이유: OpenCV 검출은 완벽하지 않아 놓치는 아이콘이
    생긴다. 사람이 메울 수단이 없으면 그 아이콘은 영원히 등록할 수 없고,
    TC 절차에 좌표로 남는다.
    """

    element_clicked = Signal(int)   # 요소 인덱스
    element_double_clicked = Signal(int)
    #: 드래그로 지정한 영역 (정규화 x, y, w, h)
    region_selected = Signal(float, float, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor(28, 30, 36)))

        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._element_items: list[_ElementItem] = []
        self._show_elements = True
        self._selected_index: int | None = None

        self._region_mode = False
        self._drag_origin: QPointF | None = None
        self._rubber_band: QGraphicsRectItem | None = None

    # -- 표시 --------------------------------------------------------

    def show_state(
        self,
        image: np.ndarray | None,
        state: ScreenState | None,
        click: tuple[float, float] | None = None,
        needs_icon: set[int] | None = None,
    ) -> None:
        """화면 하나를 그린다.

        :param needs_icon: 미등록 아이콘 후보인 요소 인덱스. 주황 점선으로 표시해
            무엇을 채워야 하는지 보이게 한다.
        """
        self._scene.clear()
        self._pixmap_item = None
        self._element_items.clear()
        self._selected_index = None
        self._clear_rubber_band()

        if image is None:
            text = self._scene.addText("이 화면의 스크린샷을 불러올 수 없습니다")
            text.setDefaultTextColor(QColor(200, 200, 200))
            return

        pixmap = numpy_to_pixmap(image)
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        w, h = pixmap.width(), pixmap.height()

        if state is not None:
            self._add_elements(state, w, h, needs_icon or set())
        if click is not None:
            self._add_click_marker(click, w, h)

        self.fit()

    def _add_elements(
        self, state: ScreenState, w: int, h: int, needs_icon: set[int]
    ) -> None:
        # 큰 것부터 그려야 작은 요소가 위에 와서 클릭된다
        for idx, element in enumerate(state.elements_sorted()):
            x, y, ew, eh = element.rect.to_pixels(w, h)
            item = _ElementItem(idx, element, QRectF(x, y, ew, eh), needs_icon=idx in needs_icon)
            item.setVisible(self._show_elements)
            item.setZValue(100 - element.rect.area * 50)
            self._scene.addItem(item)
            self._element_items.append(item)

            if element.label:
                tag = QGraphicsSimpleTextItem(element.label)
                color = _ICON_MATCHED if element.source == "icon" else _LABELED
                tag.setBrush(QBrush(color))
                tag.setPos(x + 3, max(0, y - 17))
                tag.setZValue(200)
                tag.setVisible(self._show_elements)
                self._scene.addItem(tag)

    def _add_click_marker(self, click: tuple[float, float], w: int, h: int) -> None:
        cx, cy = click[0] * w, click[1] * h
        r = max(14.0, min(w, h) * 0.014)
        pen = QPen(QColor(255, 60, 60), 3.0)
        circle = self._scene.addEllipse(cx - r, cy - r, r * 2, r * 2, pen)
        circle.setZValue(300)
        self._scene.addLine(cx - r * 1.6, cy, cx + r * 1.6, cy, pen).setZValue(300)
        self._scene.addLine(cx, cy - r * 1.6, cx, cy + r * 1.6, pen).setZValue(300)

    # -- 상호작용 ----------------------------------------------------

    def set_elements_visible(self, visible: bool) -> None:
        self._show_elements = visible
        for item in self._scene.items():
            if isinstance(item, (_ElementItem, QGraphicsSimpleTextItem)):
                item.setVisible(visible)

    def select_element(self, index: int | None) -> None:
        for item in self._element_items:
            item.set_selected(item.index == index)
        self._selected_index = index

    def fit(self) -> None:
        if self._pixmap_item is not None:
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        """Ctrl+휠 = 확대/축소, 그냥 휠 = 스크롤."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.18 if event.angleDelta().y() > 0 else 1 / 1.18
            self.scale(factor, factor)
            event.accept()
        else:
            super().wheelEvent(event)

    # -- 영역 지정 모드 ----------------------------------------------

    def set_region_mode(self, enabled: bool) -> None:
        """드래그로 새 아이콘 영역을 지정하는 모드를 켜고 끈다."""
        self._region_mode = enabled
        self.setDragMode(
            QGraphicsView.DragMode.NoDrag if enabled else QGraphicsView.DragMode.ScrollHandDrag
        )
        self.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)
        self._clear_rubber_band()

    @property
    def region_mode(self) -> bool:
        return self._region_mode

    def _clear_rubber_band(self) -> None:
        if self._rubber_band is not None:
            self._scene.removeItem(self._rubber_band)
            self._rubber_band = None
        self._drag_origin = None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._region_mode and event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = self.mapToScene(event.pos())
            self._clear_rubber_band_item_only()
            pen = QPen(QColor(0, 200, 255), 2, Qt.PenStyle.DashLine)
            self._rubber_band = self._scene.addRect(
                QRectF(self._drag_origin, self._drag_origin),
                pen,
                QBrush(QColor(0, 200, 255, 40)),
            )
            self._rubber_band.setZValue(500)
            event.accept()
            return

        item = self.itemAt(event.pos())
        if isinstance(item, _ElementItem):
            self.select_element(item.index)
            self.element_clicked.emit(item.index)
        super().mousePressEvent(event)

    def _clear_rubber_band_item_only(self) -> None:
        if self._rubber_band is not None:
            self._scene.removeItem(self._rubber_band)
            self._rubber_band = None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._region_mode and self._drag_origin is not None and self._rubber_band is not None:
            self._rubber_band.setRect(
                QRectF(self._drag_origin, self.mapToScene(event.pos())).normalized()
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._region_mode and self._drag_origin is not None:
            end = self.mapToScene(event.pos())
            rect = QRectF(self._drag_origin, end).normalized()
            self._clear_rubber_band()
            self._emit_region(rect)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _emit_region(self, rect: QRectF) -> None:
        """지정된 영역을 정규화해 내보낸다. 너무 작으면 오조작으로 보고 무시한다."""
        if self._pixmap_item is None:
            return
        pixmap = self._pixmap_item.pixmap()
        w, h = pixmap.width(), pixmap.height()
        if w <= 0 or h <= 0:
            return
        # 화면 밖으로 나간 부분은 잘라낸다
        clipped = rect.intersected(QRectF(0, 0, w, h))
        if clipped.width() < 8 or clipped.height() < 8:
            return
        self.region_selected.emit(
            clipped.x() / w, clipped.y() / h, clipped.width() / w, clipped.height() / h
        )

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self._region_mode:
            event.accept()
            return
        item = self.itemAt(event.pos())
        if isinstance(item, _ElementItem):
            self.element_double_clicked.emit(item.index)
            event.accept()
            return
        self.fit()
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        """ESC로 영역 지정 모드를 빠져나온다."""
        if event.key() == Qt.Key.Key_Escape and self._region_mode:
            self.set_region_mode(False)
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # 창 크기가 바뀌면 다시 맞춘다. 사용자가 확대해둔 상태라면 유지하는 게
        # 나을 수도 있지만, 대부분은 전체를 보고 싶어 한다.
        self.fit()

    def scene_point(self, view_pos) -> QPointF:
        return self.mapToScene(view_pos)

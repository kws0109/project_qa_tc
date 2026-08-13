"""리뷰 GUI 진입점."""

from __future__ import annotations

import sys

from ..config import AppConfig
from ..storage import SessionStore

#: 밝은 배경 + 진한 텍스트. 스크린샷을 보는 도구라 캔버스만 어둡게 둔다 —
#: 전체가 어두우면 게임 화면의 밝기 판단이 흐려진다.
_STYLE = """
QMainWindow, QWidget { background: #f4f5f7; color: #20242a; }
QGroupBox {
    border: 1px solid #d6dae0; border-radius: 6px;
    margin-top: 12px; padding-top: 8px; font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 9px; padding: 0 4px; color: #4a5058; }
QListWidget, QTextBrowser, QPlainTextEdit, QLineEdit {
    background: #ffffff; border: 1px solid #d6dae0; border-radius: 5px;
}
QListWidget::item { padding: 5px 4px; border-bottom: 1px solid #eef0f3; }
QListWidget::item:selected { background: #dce9fb; color: #10305c; }
QPushButton {
    background: #ffffff; border: 1px solid #c8cdd4; border-radius: 5px;
    padding: 5px 12px;
}
QPushButton:hover { background: #eef2f7; }
QPushButton:disabled { color: #a8adb4; background: #f0f1f3; }
QToolBar { background: #eceef1; border-bottom: 1px solid #d6dae0; spacing: 6px; padding: 4px; }
QStatusBar { background: #eceef1; border-top: 1px solid #d6dae0; }
QSplitter::handle { background: #dfe3e8; }
"""


def run_review_app(store: SessionStore, cfg: AppConfig) -> int:
    """리뷰 창을 띄우고 종료될 때까지 대기한다."""
    from PySide6.QtWidgets import QApplication

    from .main_window import ReviewWindow

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("QATC 리뷰")
    app.setStyleSheet(_STYLE)

    window = ReviewWindow(store, cfg)
    window.show()
    try:
        return app.exec()
    finally:
        store.close()

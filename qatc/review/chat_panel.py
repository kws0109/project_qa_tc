"""LLM 채팅 패널.

**LLM 호출은 워커 스레드에서 한다.** 메인 스레드에서 호출하면 Qt 이벤트 루프가
막혀 창이 '응답 없음'이 되고 Windows가 화면을 흐리게 처리한다. 응답이 수 초에서
수십 초까지 걸리므로 반드시 분리해야 한다.

그래프는 워커 스레드에서 수정되지만, 결과 시그널은 메인 스레드에서 처리되므로
UI 갱신은 안전하다. 워커가 도는 동안 입력을 막아 동시 수정을 방지한다.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..llm.chat import ChatTurn, ReviewChat


class _ChatWorker(QThread):
    """한 번의 대화 왕복을 백그라운드에서 수행한다."""

    finished_turn = Signal(object)  # ChatTurn

    def __init__(self, chat: ReviewChat, text: str, focus_id: str | None):
        super().__init__()
        self.chat = chat
        self.text = text
        self.focus_id = focus_id

    def run(self) -> None:
        try:
            turn = self.chat.send(self.text, self.focus_id)
        except Exception as exc:  # 워커에서 예외가 나면 앱이 조용히 멈춘다
            turn = ChatTurn(reply="", error=f"{type(exc).__name__}: {exc}")
        self.finished_turn.emit(turn)


class ChatPanel(QWidget):
    """오른쪽 채팅 패널."""

    #: LLM이 도구로 그래프를 바꿨을 때. 메인 윈도우가 화면을 다시 그린다.
    graph_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.chat: ReviewChat | None = None
        self._worker: _ChatWorker | None = None
        self._focus_id: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self.status = QLabel("API 키가 없으면 채팅을 쓸 수 없습니다")
        self.status.setStyleSheet("color:#8a8f98; font-size:11px;")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.log = QTextBrowser()
        self.log.setOpenExternalLinks(False)
        layout.addWidget(self.log, 1)

        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("이 화면이 뭔지 물어보거나 알려주세요…")
        self.input.returnPressed.connect(self.send)
        self.send_btn = QPushButton("보내기")
        self.send_btn.clicked.connect(self.send)
        row.addWidget(self.input, 1)
        row.addWidget(self.send_btn)
        layout.addLayout(row)

        hint = QLabel(
            "예시:  이건 성유물 강화 화면이야  /  3번이랑 5번은 같은 화면이야  /  "
            "이 화면에서 뭘 테스트해야 할까?"
        )
        hint.setStyleSheet("color:#71757c; font-size:10px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.set_enabled(False)

    # -- 설정 --------------------------------------------------------

    def attach(self, chat: ReviewChat | None, reason: str = "") -> None:
        self.chat = chat
        if chat is None:
            self.set_enabled(False)
            self.status.setText(reason or "채팅을 사용할 수 없습니다")
            return
        self.set_enabled(True)
        self.status.setText("담당자와 LLM이 함께 화면을 정리합니다.")
        self._append(
            "system",
            "화면을 선택한 뒤 질문하면 그 화면의 스크린샷을 함께 보고 답합니다. "
            "확정한 내용은 즉시 저장됩니다.",
        )

    def set_focus_state(self, state_id: str | None) -> None:
        self._focus_id = state_id

    def set_enabled(self, enabled: bool) -> None:
        self.input.setEnabled(enabled)
        self.send_btn.setEnabled(enabled)

    # -- 대화 --------------------------------------------------------

    def send(self) -> None:
        if self.chat is None or self._worker is not None:
            return
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self._append("user", text)
        self.set_enabled(False)
        self.status.setText("응답을 기다리는 중…")

        self._worker = _ChatWorker(self.chat, text, self._focus_id)
        self._worker.finished_turn.connect(self._on_turn)
        self._worker.finished.connect(self._cleanup_worker)
        self._worker.start()

    def _on_turn(self, turn: ChatTurn) -> None:
        for effect in turn.effects:
            self._append("tool" if effect.ok else "tool_error", f"{effect.tool}: {effect.message}")
        if turn.reply:
            self._append("assistant", turn.reply)
        if turn.error:
            self._append("error", turn.error)

        self.set_enabled(True)
        self.status.setText("담당자와 LLM이 함께 화면을 정리합니다.")
        if turn.changed:
            self.graph_changed.emit()

    def _cleanup_worker(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _append(self, role: str, text: str) -> None:
        escaped = (
            text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\n", "<br>")
        )
        style = {
            "user": ("#1565c0", "담당자"),
            "assistant": ("#2e7d32", "LLM"),
            "tool": ("#6a1b9a", "적용됨"),
            "tool_error": ("#c62828", "도구 오류"),
            "error": ("#c62828", "오류"),
            "system": ("#78848f", "안내"),
        }.get(role, ("#333333", role))
        color, label = style
        self.log.append(
            f'<div style="margin:5px 0;">'
            f'<span style="color:{color}; font-weight:600;">{label}</span><br>'
            f'<span style="color:#20242a;">{escaped}</span></div>'
        )
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def stop(self) -> None:
        """창을 닫을 때 워커를 정리한다. 안 하면 프로세스가 안 끝난다."""
        if self._worker is not None:
            self._worker.quit()
            self._worker.wait(3000)
            self._worker = None

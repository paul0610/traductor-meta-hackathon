"""Subtitle band: what they say (English as it arrives, then Spanish), what I said, and one metrics line.

Always on top, click-through and never takes focus, so the call app keeps the
keyboard. `hide_from_capture` keeps it out of screen shares (WDA_EXCLUDEFROMCAPTURE);
it is off by default because the demo video has to show it.
"""

from __future__ import annotations

import ctypes

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

WDA_EXCLUDEFROMCAPTURE = 0x11
GREY = "#b9bec6"
WHITE = "#ffffff"
BLUE = "#8fd3ff"


def _tail(text: str, limit: int) -> str:
    return text if len(text) <= limit else "…" + text[-(limit - 1):]


class Overlay(QWidget):
    def __init__(self, *, hide_from_capture: bool = False) -> None:
        super().__init__()
        self.setWindowTitle("Interprete")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.hide_from_capture = hide_from_capture

        self._src = self._label(14, GREY)
        self._tgt = self._label(20, WHITE, bold=True)
        self._mine = self._label(12, BLUE)
        self._bar = self._label(10, GREY, family="Consolas")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 12, 22, 10)
        layout.setSpacing(4)
        for w in (self._src, self._tgt, self._mine, self._bar):
            layout.addWidget(w)
        self._place()

    @staticmethod
    def _label(size: int, color: str, *, bold: bool = False, family: str = "Segoe UI") -> QLabel:
        w = QLabel("")
        w.setWordWrap(True)
        w.setAlignment(Qt.AlignmentFlag.AlignCenter)
        w.setFont(QFont(family, size, QFont.Weight.Bold if bold else QFont.Weight.Normal))
        w.setStyleSheet(f"color: {color};")
        return w

    def _place(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        g = screen.availableGeometry()
        width, height = int(g.width() * 0.66), 210
        self.setGeometry(g.x() + (g.width() - width) // 2, g.y() + g.height() - height - 96, width, height)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 215))
        p.drawRoundedRect(QRectF(self.rect()), 12, 12)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.hide_from_capture:
            QTimer.singleShot(0, lambda: ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()),
                                                                                       WDA_EXCLUDEFROMCAPTURE))

    # --- content (all on the Qt thread: asyncio runs on the same loop through qasync) -----

    def their_partial(self, text: str) -> None:
        self._src.setText(_tail(text, 200))

    def their_final(self, text: str) -> None:
        self._src.setText(_tail(text, 200))

    def their_translation(self, tgt: str) -> None:
        self._tgt.setStyleSheet(f"color: {WHITE};")
        self._tgt.setText(_tail(tgt, 220))

    def their_failed(self, src: str) -> None:
        """Spark timed out: the source text in grey, so nothing said is lost."""
        self._tgt.setStyleSheet(f"color: {GREY};")
        self._tgt.setText(_tail(src, 220))

    def mine(self, es: str, en: str = "") -> None:
        self._mine.setText(_tail(f"{es}  →  {en}" if en else es, 180))

    def bar(self, text: str) -> None:
        self._bar.setText(text)

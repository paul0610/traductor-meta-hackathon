"""Subtitle band: what they say (English as it arrives, then Spanish), what I said, what my voice gave up.

Before the button it holds the guide instead: which camera to pick in the call, in English.
It is as tall as what it has to say and fades away in silence, so the call stays in view.
Always on top, click-through and never takes focus, so the call app keeps the keyboard.
`hide_from_capture` keeps it out of screen shares (WDA_EXCLUDEFROMCAPTURE); it is off by
default because the demo video has to show it.
"""

from __future__ import annotations

import ctypes
from html import escape

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget

from .texts import t

WDA_EXCLUDEFROMCAPTURE = 0x11
GREY = "#a4acb8"
WHITE = "#ffffff"
BLUE = "#8fd3ff"
DIM_BLUE = "#8ba9bf"
AMBER = "#f5c26b"
RED = "#e5484d"
WIDTH = 0.62                   # of the screen
BOTTOM_PX = 96
RADIUS = 14
IDLE_MS = 9000                 # this long with nothing new, and it goes
FADE_MS = 220


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
        self.setWindowOpacity(0.0)
        self.hide_from_capture = hide_from_capture

        self._alert = QLabel(t("THEY ARE TALKING TO YOU"))
        font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        self._alert.setFont(font)
        self._alert.setStyleSheet(f"color: {WHITE}; background: {RED}; border-radius: 9px; padding: 2px 14px;")
        self._guide = self._label(13, WHITE, text_format=Qt.TextFormat.RichText)
        self._src = self._label(13, GREY)
        self._tgt = self._label(21, WHITE, weight=QFont.Weight.DemiBold)
        self._rule = QFrame()
        self._rule.setFixedHeight(1)
        self._rule.setStyleSheet("background: rgba(255, 255, 255, 28);")
        self._mine = self._label(12, BLUE, text_format=Qt.TextFormat.RichText)
        self._unsaid = self._label(12, AMBER)
        self._colliding = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 14, 26, 14)
        layout.setSpacing(6)
        layout.addWidget(self._alert, 0, Qt.AlignmentFlag.AlignHCenter)
        for w in (self._guide, self._src, self._tgt, self._rule, self._mine, self._unsaid):
            layout.addWidget(w)

        self._fade = QPropertyAnimation(self, b"windowOpacity")
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.setDuration(FADE_MS)
        self._fade.finished.connect(self._faded)
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(IDLE_MS)
        self._idle.timeout.connect(self._go_away)
        self._arrange()

    @staticmethod
    def _label(size: int, color: str, *, weight: QFont.Weight = QFont.Weight.Normal,
               text_format: Qt.TextFormat = Qt.TextFormat.PlainText) -> QLabel:
        w = QLabel("")
        w.setWordWrap(True)
        w.setTextFormat(text_format)
        w.setAlignment(Qt.AlignmentFlag.AlignCenter)
        w.setFont(QFont("Segoe UI", size, weight))
        w.setStyleSheet(f"color: {color};")
        return w

    def _arrange(self) -> None:
        """Only what has something to say takes room, and the band grows upwards from the same spot."""
        self._alert.setVisible(self._colliding)
        for w in (self._guide, self._src, self._tgt, self._mine, self._unsaid):
            w.setVisible(bool(w.text()))
        top = bool(self._guide.text() or self._src.text() or self._tgt.text())
        bottom = bool(self._mine.text() or self._unsaid.text())
        self._rule.setVisible(top and bottom)
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        g = screen.availableGeometry()
        width = int(g.width() * WIDTH)
        height = self.layout().totalHeightForWidth(width)
        self.setGeometry(g.x() + (g.width() - width) // 2, g.y() + g.height() - height - BOTTOM_PX, width, height)

    def _changed(self) -> None:
        self._arrange()
        self._idle.start()
        if self._fade.endValue() != 1.0 or self.windowOpacity() < 1.0:
            self._fade_to(1.0)

    def _fade_to(self, opacity: float) -> None:
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(opacity)
        self._fade.start()

    def _go_away(self) -> None:
        if self._colliding or self._guide.text():    # what they said, what I left unsaid, or the guide: still needed
            self._idle.start()
        else:
            self._fade_to(0.0)

    def _faded(self) -> None:
        if self._fade.endValue() == 0.0:
            for w in (self._guide, self._src, self._tgt, self._mine, self._unsaid):
                w.clear()
            self._arrange()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(RED) if self._colliding else QColor(255, 255, 255, 26), 1.5))
        p.setBrush(QColor(13, 15, 20, 232))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), RADIUS, RADIUS)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.hide_from_capture:
            QTimer.singleShot(0, lambda: ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()),
                                                                                       WDA_EXCLUDEFROMCAPTURE))

    # --- content (all on the Qt thread: asyncio runs on the same loop through qasync) -----

    def guide(self, lines: list[str]) -> None:
        """The app's own lines, markup allowed. It stays until the button clears it with no lines."""
        self._guide.setText("<br>".join(lines))
        self._changed()

    def their_partial(self, text: str) -> None:
        self._src.setText(_tail(text, 180))
        self._changed()

    def their_final(self, text: str) -> None:
        self._src.setText(_tail(text, 180))
        self._changed()

    def their_translation(self, tgt: str) -> None:
        self._tgt.setStyleSheet(f"color: {WHITE};")
        self._tgt.setText(_tail(tgt, 200))
        self._changed()

    def their_failed(self, src: str) -> None:
        """Spark timed out: the source text in grey, so nothing said is lost."""
        self._tgt.setStyleSheet(f"color: {GREY};")
        self._tgt.setText(_tail(src, 200))
        self._changed()

    def mine(self, es: str, en: str = "") -> None:
        """Without English it is my Spanish still arriving, or the app saying something."""
        if en:
            text = f'<span style="color: {DIM_BLUE};">{escape(_tail(es, 90))}</span>&nbsp; → &nbsp;{escape(_tail(en, 90))}'
        else:
            text = escape(_tail(es, 180))
        self._mine.setText(text)
        self._changed()

    def unsaid(self, es: str) -> None:
        """What my voice gave up when they took the floor, to say again."""
        self._unsaid.setText(_tail(t("Not said: {text}").format(text=es), 180) if es else "")
        self._changed()

    def collision(self, on: bool) -> None:
        self._colliding = on
        self._changed()
        self.update()

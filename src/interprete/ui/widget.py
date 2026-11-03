"""The app's one button, the metrics line and the Meta panel, all on top and free to drag out of the way.

Left click starts from LISTO and stops from then on; right click stops from any state,
loading included. Stopping restores the microphone. The ring is the health of the lag, the
number the lag in seconds. What the app needs from you shows on the metrics line. The Meta
panel shows each call to Meta's models as it runs (`calls.py`); `RTT_META_PANEL=0` hides it.
The windows stay where they were dragged, from one run to the next.
"""

from __future__ import annotations

import asyncio
import ctypes
from typing import Callable

from PySide6.QtCore import QPoint, QRectF, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QWidget

from .calls import COLUMN_X, ROW_CHARS, CallLog
from .overlay import Overlay
from .sounds import Sounds
from .status import Health, Phase, sound_for

WDA_EXCLUDEFROMCAPTURE = 0x11
SIZE = 112
RING_PX = 8
DRAG_PX = 4                    # moved less than this, it was a click
PLACES = "logs/widget.ini"
RING = {Health.NONE: "#6b7280", Health.OK: "#30a46c", Health.WARN: "#f5a623", Health.BAD: "#e5484d"}
FIXED = {Phase.LOADING: "#6b7280", Phase.READY: "#8fd3ff", Phase.STARTING: "#8fd3ff", Phase.PAUSED: "#e5484d",
         Phase.MUTED: "#e5484d", Phase.RAW: "#f5a623", Phase.STOPPING: "#6b7280", Phase.OFF: "#3f4650"}
GLYPH = {Phase.LOADING: "…", Phase.STARTING: "…", Phase.PAUSED: "❚❚", Phase.MUTED: "✕", Phase.RAW: "!", Phase.STOPPING: "…",
         Phase.OFF: "■"}
RUNNING = (Phase.STARTING, Phase.LIVE, Phase.PAUSED, Phase.MUTED, Phase.RAW)
CALL_COLOR = {"live": "#8fd3ff", "done": "#d6d9de", "failed": "#e5484d"}
PANEL_PAD = 12
PANEL_TICK_MS = 200            # the seconds of a call still out count up on screen
FLAGS = (Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
         | Qt.WindowType.WindowDoesNotAcceptFocus)


class _Floating(QWidget):
    """Frameless, on top, never takes the keyboard from the call, moves when dragged."""

    def __init__(self, hide_from_capture: bool) -> None:
        super().__init__()
        self.setWindowFlags(FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.hide_from_capture = hide_from_capture
        self._press: QPoint | None = None
        self._dragged = False

    def place(self, default: QPoint) -> None:
        """Where it was left last time, unless that screen is gone."""
        saved = QSettings(PLACES, QSettings.Format.IniFormat).value(self.windowTitle())
        self.move(saved if isinstance(saved, QPoint) and QGuiApplication.screenAt(saved) else default)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.hide_from_capture:
            QTimer.singleShot(0, lambda: ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()),
                                                                                       WDA_EXCLUDEFROMCAPTURE))

    def mousePressEvent(self, event) -> None:
        self._press = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        self._dragged = False

    def mouseMoveEvent(self, event) -> None:
        if self._press is None:
            return
        target = event.globalPosition().toPoint() - self._press
        if (target - self.frameGeometry().topLeft()).manhattanLength() >= DRAG_PX or self._dragged:
            self._dragged = True
            self.move(target)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragged:
            QSettings(PLACES, QSettings.Format.IniFormat).setValue(self.windowTitle(), self.pos())
        self._press = None


class Button(_Floating):
    def __init__(self, *, hide_from_capture: bool = False) -> None:
        super().__init__(hide_from_capture)
        self.setWindowTitle("Interprete: boton")
        self.setFixedSize(SIZE, SIZE)
        self.phase = Phase.LOADING
        self.lag_s: float | None = None
        self.health = Health.NONE
        self.on_start: Callable[[], None] = lambda: None
        self.on_stop: Callable[[], None] = lambda: None
        screen = QApplication.primaryScreen()
        if screen is not None:
            g = screen.availableGeometry()
            self.place(QPoint(g.x() + g.width() - SIZE - 24, g.y() + 24))

    def set_phase(self, phase: Phase) -> None:
        if phase is not self.phase:
            self.phase = phase
            self.update()

    def set_vitals(self, lag_s: float | None, level: Health) -> None:
        self.lag_s, self.health = lag_s, level
        self.update()

    def clicked(self, left: bool) -> None:
        if not left:
            self.on_stop()
        elif self.phase is Phase.READY:
            self.on_start()
        elif self.phase in RUNNING:
            self.on_stop()

    def mouseReleaseEvent(self, event) -> None:
        if self._press is not None and not self._dragged:
            self.clicked(event.button() == Qt.MouseButton.LeftButton)
        super().mouseReleaseEvent(event)

    def _centre(self) -> str:
        if self.phase is Phase.READY:
            return "▶"
        if self.phase in (Phase.LIVE, Phase.PAUSED) and self.lag_s is not None:
            return f"{self.lag_s:.1f}"
        return GLYPH.get(self.phase, "–")

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(RING_PX / 2 + 1, RING_PX / 2 + 1, SIZE - RING_PX - 2, SIZE - RING_PX - 2)
        p.setBrush(QColor(12, 14, 18, 235))
        p.setPen(QPen(QColor(RING[self.health] if self.phase is Phase.LIVE else FIXED[self.phase]), RING_PX))
        p.drawEllipse(area)
        p.setPen(QColor("#ffffff"))
        p.setFont(QFont("Segoe UI", 21, QFont.Weight.Bold))
        p.drawText(QRectF(0, 18, SIZE, 50), Qt.AlignmentFlag.AlignCenter, self._centre())
        p.setPen(QColor("#b9bec6"))
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        p.drawText(QRectF(0, 66, SIZE, 18), Qt.AlignmentFlag.AlignCenter, self.phase.value)


class MetricsLine(_Floating):
    def __init__(self, *, hide_from_capture: bool = False) -> None:
        super().__init__(hide_from_capture)
        self.setWindowTitle("Interprete: metricas")
        self._label = QLabel("")
        self._label.setFont(QFont("Consolas", 10))
        self._label.setStyleSheet("color: #d6d9de;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.addWidget(self._label)
        screen = QApplication.primaryScreen()
        if screen is not None:
            g = screen.availableGeometry()
            self.place(QPoint(g.x() + 24, g.y() + 24))

    def set_text(self, text: str) -> None:
        self._label.setText(text)
        self.adjustSize()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 215))
        p.drawRoundedRect(QRectF(self.rect()), 8, 8)


class MetaPanel(_Floating):
    """Meta's models at work, a row per call: one still out counts its seconds until it returns."""

    def __init__(self, calls: CallLog, *, hide_from_capture: bool = False) -> None:
        super().__init__(hide_from_capture)
        # Opaque, unlike the others: with per-pixel alpha the windows behind it could be read through it, even at
        # an alpha of 250, and it sits on the screen the demo video records.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setWindowTitle("Interprete: Meta")
        self.calls = calls
        self._font = QFont("Consolas", 10)
        metrics = QFontMetrics(self._font)
        self._row = metrics.height() + 3
        self._char = metrics.horizontalAdvance("M")
        self.setFixedSize(metrics.horizontalAdvance("M" * ROW_CHARS) + 2 * PANEL_PAD,
                          (calls.keep + 1) * self._row + 2 * PANEL_PAD)
        self._tick = QTimer(self)
        self._tick.setInterval(PANEL_TICK_MS)
        self._tick.timeout.connect(self._ticked)
        screen = QApplication.primaryScreen()
        if screen is not None:
            g = screen.availableGeometry()
            self.place(QPoint(g.x() + 24, g.y() + 68))

    def changed(self) -> None:
        if self.calls.live() and not self._tick.isActive():
            self._tick.start()
        self.update()

    def _ticked(self) -> None:
        if not self.calls.live():
            self._tick.stop()
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(12, 14, 18))
        p.setFont(self._font)
        width = self.width() - 2 * PANEL_PAD
        p.setPen(QColor("#8b93a0"))
        p.drawText(QRectF(PANEL_PAD, PANEL_PAD, width, self._row), Qt.AlignmentFlag.AlignVCenter,
                   "META MODELS · LIVE CALLS")
        for i, (state, cells) in enumerate(self.calls.cells(), 1):
            p.setPen(QColor(CALL_COLOR[state]))
            for x, cell in zip(COLUMN_X, cells):
                p.drawText(QRectF(PANEL_PAD + x * self._char, PANEL_PAD + i * self._row, width, self._row),
                           Qt.AlignmentFlag.AlignVCenter, cell)


class Screen:
    """The windows as one thing for the chains; subtitle calls go straight to the band."""

    def __init__(self, *, hide_from_capture: bool = False, sounds: Sounds | None = None, meta_panel: bool = True) -> None:
        self.overlay = Overlay(hide_from_capture=hide_from_capture)
        self.button = Button(hide_from_capture=hide_from_capture)
        self.metrics = MetricsLine(hide_from_capture=hide_from_capture)
        self.calls = CallLog()
        self.panel = MetaPanel(self.calls, hide_from_capture=hide_from_capture) if meta_panel else None
        self.sounds = sounds

    def __getattr__(self, name: str):
        return getattr(self.overlay, name)

    def show(self) -> None:
        self.overlay.show()
        self.metrics.show()
        if self.panel is not None:
            self.panel.show()
        self.button.show()

    def call_start(self, key: tuple, model: str, route: str = "", text: str = "", *, timed: bool = True) -> None:
        self.calls.start(key, model, route, text, timed=timed)
        self._calls_changed()

    def call_update(self, key: tuple, text: str) -> None:
        self.calls.update(key, text)
        self._calls_changed()

    def call_end(self, key: tuple, text: str | None = None, *, failed: bool = False) -> None:
        self.calls.end(key, text, failed=failed)
        self._calls_changed()

    def _calls_changed(self) -> None:
        if self.panel is not None:
            self.panel.changed()

    def bar(self, text: str) -> None:
        self.metrics.set_text(text)

    def phase(self, phase: Phase) -> None:
        name = sound_for(self.button.phase, phase)
        if name is not None and self.sounds is not None:
            self.sounds.play(name)
        self.button.set_phase(phase)

    def vitals(self, lag_s: float | None, level: Health) -> None:
        self.button.set_vitals(lag_s, level)

    def on_stop(self, fn: Callable[[], None]) -> None:
        self.button.on_stop = fn

    async def go(self, stop: asyncio.Event) -> None:
        """Nothing is captured until the button is pressed."""
        start = asyncio.Event()
        self.button.on_start = start.set
        waiting = [asyncio.ensure_future(start.wait()), asyncio.ensure_future(stop.wait())]
        await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
        for w in waiting:
            w.cancel()

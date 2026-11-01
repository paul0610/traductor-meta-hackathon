"""The app's one button, and the metrics line, both on top and free to drag out of the way.

Left click starts from LISTO and stops from then on; right click stops from any state,
loading included. Stopping restores the microphone. The ring is the health of the lag, the
number the lag in seconds. Nothing here is a setting: what the app needs from you shows on
the metrics line. The windows stay where they were dragged, from one run to the next. In
LISTO, and only there, a card offers the optional step of cloning your voice.
"""

from __future__ import annotations

import asyncio
import ctypes
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QPoint, QRectF, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .overlay import Overlay
from .sounds import Sounds
from .status import Health, Phase, sound_for

if TYPE_CHECKING:
    from ..tts.own_voice import Enrollment, Prompt

WDA_EXCLUDEFROMCAPTURE = 0x11
SIZE = 112
CARD_W = 372
BUTTON_CSS = ("QPushButton {{ color: {text}; background: {back}; border: none; border-radius: 6px; padding: 7px 12px; }}"
              "QPushButton:hover {{ background: {hover}; }}")
SUGGESTED = BUTTON_CSS.format(text="#0c0e12", back="#8fd3ff", hover="#b5e3ff")
OTHER = BUTTON_CSS.format(text="#d6d9de", back="#2a2e35", hover="#3a3f48")
RING_PX = 8
DRAG_PX = 4                    # moved less than this, it was a click
PLACES = "logs/widget.ini"
RING = {Health.NONE: "#6b7280", Health.OK: "#30a46c", Health.WARN: "#f5a623", Health.BAD: "#e5484d"}
FIXED = {Phase.LOADING: "#6b7280", Phase.READY: "#8fd3ff", Phase.STARTING: "#8fd3ff", Phase.PAUSED: "#e5484d",
         Phase.MUTED: "#e5484d", Phase.RAW: "#f5a623", Phase.STOPPING: "#6b7280", Phase.OFF: "#3f4650"}
GLYPH = {Phase.LOADING: "…", Phase.STARTING: "…", Phase.PAUSED: "❚❚", Phase.MUTED: "✕", Phase.RAW: "!", Phase.STOPPING: "…",
         Phase.OFF: "■"}
RUNNING = (Phase.STARTING, Phase.LIVE, Phase.PAUSED, Phase.MUTED, Phase.RAW)
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


class VoiceCard(_Floating):
    def __init__(self, *, hide_from_capture: bool = False) -> None:
        super().__init__(hide_from_capture)
        self.setWindowTitle("Interprete: voz")
        self.setFixedWidth(CARD_W)
        self.on_action: Callable[[str], None] = lambda key: None
        self._actions: tuple[tuple[str, str], ...] = ()
        self._title = QLabel("")
        self._title.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        self._title.setStyleSheet("color: #ffffff;")
        self._body = QLabel("")
        self._body.setFont(QFont("Segoe UI", 10))
        self._body.setStyleSheet("color: #d6d9de;")
        self._body.setWordWrap(True)
        self._row = QHBoxLayout()
        self._row.setSpacing(8)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        layout.addWidget(self._title)
        layout.addWidget(self._body)
        layout.addLayout(self._row)
        screen = QApplication.primaryScreen()
        if screen is not None:
            g = screen.availableGeometry()
            self.place(QPoint(g.x() + g.width() - CARD_W - 24, g.y() + 24 + SIZE + 12))

    def show_prompt(self, prompt: Prompt | None) -> None:
        if prompt is None:
            self.hide()
            return
        self._title.setText(prompt.title)
        self._body.setText(prompt.body)
        if prompt.actions != self._actions:          # the timer redraws the card: a click must not land on a new button
            self._actions = prompt.actions
            self._buttons(prompt.suggests)
        self.adjustSize()
        self.show()

    def _buttons(self, suggests: bool) -> None:
        while self._row.count():
            item = self._row.takeAt(0).widget()
            item.setParent(None)
            item.deleteLater()
        for i, (label, key) in enumerate(self._actions):
            button = QPushButton(label)
            button.setFont(QFont("Segoe UI", 10))
            button.setStyleSheet(SUGGESTED if suggests and i == 0 else OTHER)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _=False, key=key: self.on_action(key))
            self._row.addWidget(button)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(12, 14, 18, 240))
        p.drawRoundedRect(QRectF(self.rect()), 10, 10)


class Screen:
    """The windows as one thing for the chains; subtitle calls go straight to the band."""

    def __init__(self, *, hide_from_capture: bool = False, sounds: Sounds | None = None) -> None:
        self.overlay = Overlay(hide_from_capture=hide_from_capture)
        self.button = Button(hide_from_capture=hide_from_capture)
        self.metrics = MetricsLine(hide_from_capture=hide_from_capture)
        self.card = VoiceCard(hide_from_capture=hide_from_capture)
        self.sounds = sounds
        self.enrollment: Enrollment | None = None

    def __getattr__(self, name: str):
        return getattr(self.overlay, name)

    def show(self) -> None:
        self.overlay.show()
        self.metrics.show()
        self.button.show()

    def bar(self, text: str) -> None:
        self.metrics.set_text(text)

    def phase(self, phase: Phase) -> None:
        name = sound_for(self.button.phase, phase)
        if name is not None:
            self.chime(name)
        self.button.set_phase(phase)

    def chime(self, name: str) -> None:
        if self.sounds is not None:
            self.sounds.play(name)

    def vitals(self, lag_s: float | None, level: Health) -> None:
        self.button.set_vitals(lag_s, level)

    def on_stop(self, fn: Callable[[], None]) -> None:
        self.button.on_stop = fn

    def offer(self, enrollment: Enrollment) -> None:
        self.enrollment = enrollment
        enrollment.on_prompt = self.card.show_prompt
        enrollment.chime = self.chime
        self.card.on_action = lambda key: asyncio.ensure_future(enrollment.act(key))
        enrollment.opening()

    async def go(self, stop: asyncio.Event) -> None:
        """Nothing is captured until the button is pressed, and it waits for a voice being recorded or decided."""
        start = asyncio.Event()

        def press() -> None:
            if self.enrollment is None or not self.enrollment.busy:
                start.set()

        self.button.on_start = press
        waiting = [asyncio.ensure_future(start.wait()), asyncio.ensure_future(stop.wait())]
        await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
        for w in waiting:
            w.cancel()
        self.card.hide()

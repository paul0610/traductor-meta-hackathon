"""My camera D seconds late, through the OBS Virtual Camera driver (SPEC §4.1).

The other side has to see my lips start when the English voice starts, so the video
waits exactly as long as the audio. One thread reads the webcam into a deque of
`(t_capture, frame)`, another sends the newest frame at least D old. Until a frame is
that old a title card goes out, never black. OBS itself never runs: only its driver
is used, and an open OBS with its own virtual camera would block ours.

`is_live` is read on every frame, so F9 cuts audio and video to live at the same
time. Coming back the history is dropped, as the audio does: it was already seen.

The webcam is opened by name. Windows keeps the Hello IR camera next to the HD one
and OpenCV only takes indices, so `ffmpeg -list_devices` gives the DirectShow order.
"""

from __future__ import annotations

import re
import subprocess
import threading
from collections import deque
from typing import Callable

import numpy as np

from ..clock import now

WIDTH = 1280
HEIGHT = 720
FPS = 30
REPORT_EVERY_S = 5.0
CARD_TEXT = ("AI interpreter on", "My video reaches you {n} s late", "to stay in sync with the voice")


class FrameDelay:
    """Frames by capture time. `push` runs in the capture thread, everything else in the sending one."""

    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s
        self._q: deque[tuple[float, np.ndarray]] = deque()

    def __len__(self) -> int:
        return len(self._q)

    def push(self, t_capture: float, frame: np.ndarray) -> None:
        self._q.append((t_capture, frame))

    def pick(self, t: float) -> tuple[float, np.ndarray] | None:
        """The newest frame at least D old, dropping what is older; None until there is one."""
        due = t - self.delay_s
        while len(self._q) > 1 and self._q[1][0] <= due:
            self._q.popleft()
        head = self._q[0] if self._q else None
        return head if head is not None and head[0] <= due else None

    def newest(self) -> tuple[float, np.ndarray] | None:
        return self._q[-1] if self._q else None

    def clear(self) -> None:
        self._q.clear()


def title_card(delay_s: float, width: int = WIDTH, height: int = HEIGHT) -> np.ndarray:
    import cv2

    card = np.full((height, width, 3), 32, np.uint8)
    for i, line in enumerate(CARD_TEXT):
        text = line.format(n=round(delay_s))
        (w, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 1.2, 2)
        cv2.putText(card, text, ((width - w) // 2, height // 2 - 50 + 60 * i), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
                    (235, 235, 235), 2, cv2.LINE_AA)
    return card


def dshow_index(name_sub: str) -> int:
    """Index OpenCV's DirectShow backend gives the camera whose name contains `name_sub`."""
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise RuntimeError("ffmpeg is not on the PATH, and the dub finds the webcam through it: winget install Gyan.FFmpeg") from None
    names = re.findall(r'"([^"]+)" \(video\)', r.stderr)
    for i, name in enumerate(names):
        if name_sub.lower() in name.lower():
            return i
    raise RuntimeError(f"no camera matching {name_sub!r} among {names}: set RTT_CAMERA_NAME to part of yours")


def open_webcam(name_sub: str, width: int = WIDTH, height: int = HEIGHT, fps: int = FPS):
    import cv2

    cap = cv2.VideoCapture(dshow_index(name_sub), cv2.CAP_DSHOW)
    if not cap.isOpened():
        raise RuntimeError(f"camera {name_sub!r} does not open: is it in use, or blocked in Windows privacy settings?")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    # last, or DirectShow keeps YUY2: 10 fps at 720p here, against 30 with MJPG
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    return cap


def measure_webcam(name_sub: str, seconds: float = 2.0) -> float:
    """Real frames per second at the size the dub uses; keeps nothing."""
    cap = open_webcam(name_sub)
    try:
        cap.read()                                                    # the first frame takes the sensor's start-up
        t0, n = now(), 0
        while now() - t0 < seconds:
            n += bool(cap.read()[0])
        return n / (now() - t0)
    finally:
        cap.release()


def open_virtual_camera(width: int = WIDTH, height: int = HEIGHT, fps: int = FPS):
    import pyvirtualcam

    try:
        return pyvirtualcam.Camera(width, height, fps, fmt=pyvirtualcam.PixelFormat.BGR, backend="obs")
    except RuntimeError as exc:
        raise RuntimeError(f"{exc}: the dub needs OBS Studio installed once, for its virtual camera driver") from exc


class VirtualCam:
    def __init__(self, delay_s: float, is_live: Callable[[], bool], *, name_sub: str = "HD UVC",
                 emit: Callable[..., None] | None = None, make_webcam=open_webcam,
                 make_output=open_virtual_camera, card: np.ndarray | None = None) -> None:
        self.delay_s = delay_s
        self.is_live = is_live
        self.name_sub = name_sub
        self.frames = FrameDelay(delay_s)
        self._emit = emit or (lambda ev, **f: None)
        self._make_webcam = make_webcam
        self._make_output = make_output
        self._card = card
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._cap = None
        self._out = None
        self.read = 0
        self.sent = 0
        self.lag_s = 0.0

    def start(self) -> None:
        """Raises if the driver or the webcam are missing, before any thread exists."""
        self._out = self._make_output()
        try:
            self._cap = self._make_webcam(self.name_sub)
        except Exception:
            self._out.close()
            raise
        if self._card is None:
            self._card = title_card(self.delay_s)
        self._threads = [threading.Thread(target=self._capture, name="video-in", daemon=True),
                         threading.Thread(target=self._send, name="video-out", daemon=True)]
        for th in self._threads:
            th.start()

    def stop(self) -> None:
        self._stop.set()
        for th in self._threads:
            th.join(timeout=2.0)
        if self._cap is not None:
            self._cap.release()
        if self._out is not None:
            self._out.close()

    def _capture(self) -> None:
        while not self._stop.is_set():
            ok, frame = self._cap.read()
            if ok:
                self.frames.push(now(), frame)
                self.read += 1
            elif self._stop.wait(0.01):
                return

    def _next(self, t: float, was_live: bool) -> tuple[np.ndarray, bool]:
        live = self.is_live()
        if was_live and not live:
            self.frames.clear()
        item = self.frames.newest() if live else self.frames.pick(t)
        if item is None:
            return self._card, live
        self.lag_s = t - item[0]
        return item[1], live

    def _send(self) -> None:
        live = False
        t_report = now() + REPORT_EVERY_S
        read0, sent0 = 0, 0
        while not self._stop.is_set():
            t = now()
            frame, live = self._next(t, live)
            self._out.send(frame)
            self.sent += 1
            self._out.sleep_until_next_frame()
            if t >= t_report:
                self._emit("video", fps_in=(self.read - read0) / REPORT_EVERY_S, fps_out=(self.sent - sent0) / REPORT_EVERY_S,
                           lag_s=self.lag_s, delay_s=self.delay_s, frames=len(self.frames), live=live)
                read0, sent0 = self.read, self.sent
                t_report = t + REPORT_EVERY_S

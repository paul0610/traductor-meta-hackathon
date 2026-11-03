"""Clap calibration of the dub: how far apart do my picture and voice reach the call? Nothing goes to Meta.

    uv run python scripts/calibrate_av_offset.py

Runs the dub's two delay lines, mic to cable and webcam to OBS Virtual Camera, and
watches what a call would get: `CABLE Output` and the virtual camera. Stay out of any
call while it runs, face the camera, keep still, and each time it says so bring your
hands together sharply in frame and leave them there a moment. It prints the value for
RTT_AV_OFFSET_MS and changes nothing; logs/clap_*.npz keeps the numbers it read.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import soxr

from interprete import config
from interprete.audio.cable_mixer import CableMixer
from interprete.audio.capture import MicCapture
from interprete.audio.delay import DelayLine
from interprete.audio.recorder import Recorder
from interprete.clock import now
from interprete.video.clap import audio_claps, offsets
from interprete.video.virtual_cam import VirtualCam, dshow_index

CLAPS = 6
MIN_CLAPS = 3
EVERY_S = 3.0
SCATTER_MAX_S = 0.3
SIDE = 160                     # motion is measured on a small grey frame
CHANGED = 25                   # of 255: sensor noise stays under this, hands do not


class Watcher(threading.Thread):
    """Reads the virtual camera as a call would, and keeps the share of pixels that changed between frames."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.cap = cv2.VideoCapture(dshow_index("OBS Virtual Camera"), cv2.CAP_DSHOW)
        self.halt = threading.Event()
        self.times: list[float] = []
        self.motion: list[float] = []

    def run(self) -> None:
        last = None
        while not self.halt.is_set():
            ok, frame = self.cap.read()
            t = now()
            if not ok:
                continue
            grey = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (SIDE, SIDE),
                              interpolation=cv2.INTER_AREA).astype(np.float32)
            if last is not None:
                change = np.abs(grey - last)
                if not change.any():
                    continue               # the driver sends 30 fps whatever the webcam gives: a repeat, not a stop
                self.times.append(t)
                self.motion.append(float((change > CHANGED).mean()))
            last = grey
        self.cap.release()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--claps", type=int, default=CLAPS)
    args = ap.parse_args()
    s = config.load()

    mixer = CableMixer(s.cable_name)
    delay = DelayLine(mixer.rate, s.delay_s)
    mixer.dub(delay, 0.0)                                # the voice at full level: there is no English over it
    mic = MicCapture(s.mic_name)
    mic.start()
    rs = None if mic.rate == mixer.rate else soxr.ResampleStream(mic.rate, mixer.rate, 1, dtype="float32")
    mic.add_consumer(lambda t, block: delay.write(block if rs is None else rs.resample_chunk(block)))
    video = VirtualCam(s.delay_s + s.av_offset_ms / 1000, lambda: False, name_sub=s.camera_name)
    video.start()
    mixer.start()
    watcher = Watcher()
    watcher.start()

    print(f"D = {s.delay_s} s, RTT_AV_OFFSET_MS = {s.av_offset_ms:.0f}.")
    print("Hands apart and in view. At each NOW! bring them together sharply and keep them together a second.")
    wait = threading.Event()
    t_begin = now()
    with Recorder("CABLE Output") as rec:
        wait.wait(2.0)
        for i in range(args.claps):
            print(f"  clap {i + 1} of {args.claps}: NOW!", flush=True)
            wait.wait(EVERY_S)
        print(f"Keep still {s.delay_s:.0f} s more: what goes in takes that long to come out.", flush=True)
        wait.wait(s.delay_s + 1.5)
    elapsed = now() - t_begin
    watcher.halt.set()
    watcher.join(timeout=2.0)
    video.stop()
    mixer.stop()

    t0, x = rec.signal()
    heard = audio_claps(x, int(rec.rate), t0)
    times, motion = np.array(watcher.times), np.array(watcher.motion)
    found = offsets(heard, times, motion)
    Path("logs").mkdir(exist_ok=True)
    np.savez(Path("logs") / f"clap_{time.strftime('%Y%m%d_%H%M%S')}.npz", t=times, motion=motion, heard=heard)
    middle = float(np.median(found)) if found else 0.0
    good = [v for v in found if abs(v - middle) <= SCATTER_MAX_S / 2]
    print(f"Heard {len(heard)}, seen {len(found)}, matching {len(good)}. The webcam gave {video.read / elapsed:.0f} fps.")
    if found:
        print("Audio minus video per clap (s): " + " ".join(f"{v:+.3f}" for v in found))
    if len(heard) < MIN_CLAPS:
        print("Too few claps heard: get closer to the microphone and clap louder.")
        return 1
    if len(found) < MIN_CLAPS:
        print("Too few claps seen: keep your hands in frame, apart before each clap.")
        return 1
    if len(good) < MIN_CLAPS:
        print(f"The claps differ by more than {SCATTER_MAX_S} s: bring your hands together sharply and do not part them at once.")
        return 1
    off = float(np.median(good))
    print(f"Offset {off:+.3f} s, spread {np.ptp(good):.3f} s, from {len(good)} of {len(found)} claps.")
    print(f"Put in .env: RTT_AV_OFFSET_MS={s.av_offset_ms + off * 1000:.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

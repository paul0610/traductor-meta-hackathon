"""Clap calibration of the dub: how far apart do my picture and voice reach the call? Nothing goes to Meta.

    uv run python scripts/calibrate_av_offset.py

Runs the dub's two delay lines, mic to cable and webcam to OBS Virtual Camera, and
watches what a call would get: `CABLE Output` and the virtual camera. Stay out of any
call while it runs, face the camera, keep still, and clap with your hands in frame
each time it says so. It prints the value for RTT_AV_OFFSET_MS and changes nothing.
"""

from __future__ import annotations

import argparse
import sys
import threading

import cv2
import numpy as np
import soxr

from interprete import config
from interprete.audio.cable_mixer import CableMixer
from interprete.audio.capture import MicCapture
from interprete.audio.delay import DelayLine
from interprete.audio.recorder import Recorder
from interprete.clock import now
from interprete.video.clap import audio_claps, offsets, video_claps
from interprete.video.virtual_cam import VirtualCam, dshow_index

CLAPS = 6
EVERY_S = 3.0
SCATTER_MAX_S = 0.3
SIDE = 160                     # motion is measured on a small grey frame


class Watcher(threading.Thread):
    """Reads the virtual camera as a call would, and keeps only how much changed between frames."""

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
            grey = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (SIDE, SIDE)).astype(np.float32)
            if last is not None:
                self.times.append(t)
                self.motion.append(float(np.abs(grey - last).mean()))
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

    print(f"D = {s.delay_s} s, RTT_AV_OFFSET_MS = {s.av_offset_ms:.0f}. Mira a la cámara, manos a la vista.")
    wait = threading.Event()
    t_begin = now()
    with Recorder("CABLE Output") as rec:
        wait.wait(2.0)
        for i in range(args.claps):
            print(f"  palmada {i + 1} de {args.claps}: ¡YA!", flush=True)
            wait.wait(EVERY_S)
        print(f"Quieto {s.delay_s:.0f} s más: lo que entra tarda eso en salir.", flush=True)
        wait.wait(s.delay_s + 1.5)
    elapsed = now() - t_begin
    watcher.halt.set()
    watcher.join(timeout=2.0)
    video.stop()
    mixer.stop()

    t0, x = rec.signal()
    heard = audio_claps(x, int(rec.rate), t0)
    seen = video_claps(np.array(watcher.times), np.array(watcher.motion))
    found = offsets(heard, seen)
    print(f"Oídas {len(heard)}, vistas {len(seen)}, emparejadas {len(found)}. La webcam dio {video.read / elapsed:.0f} fps.")
    if len(found) < 3:
        print("Muy pocas palmadas emparejadas: acércate al micrófono, deja las manos a la vista y repite.")
        return 1
    off = float(np.median(found))
    scatter = float(np.ptp(found))
    print("Audio menos video por palmada (s): " + " ".join(f"{v:+.3f}" for v in found))
    print(f"Desfase {off:+.3f} s, dispersión {scatter:.3f} s.")
    print(f"Pon en .env: RTT_AV_OFFSET_MS={s.av_offset_ms + off * 1000:.0f}")
    if scatter > SCATTER_MAX_S:
        print(f"Dispersión mayor a {SCATTER_MAX_S} s: repite con palmadas más marcadas antes de fiarte del valor.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

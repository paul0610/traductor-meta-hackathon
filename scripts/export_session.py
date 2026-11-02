"""Turns a run's log into a session the replay can play: only what it needs, and where it came from.

    uv run python scripts/export_session.py logs/app_<stamp>.jsonl demo/interview --out-wav logs/demo_es.wav --in-wav logs/demo_en.wav

Keeps when each word was heard, each clause committed and what Spark answered, on both sides, plus
the audio that went in. Times start at 0. The ASR's session ids become short ones. `about.json`
says when it was recorded, with which commit, and what the run measured, so the replay can say so.
Export right after recording, from a clean tree: the commit is read now.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from interprete.metrics.jsonl import read
from interprete.metrics.summary import summarize

KEEP_ASR = ("partial", "speechComplete")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("folder")
    ap.add_argument("--out-wav", required=True, help="the 16 kHz WAV that stood in for the microphone")
    ap.add_argument("--in-wav", default=None, help="the 16 kHz WAV that stood in for the other side")
    args = ap.parse_args()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no", "--", "src"], capture_output=True,
                           text=True).stdout.strip()
    if dirty:
        raise SystemExit("src/ has uncommitted changes: commit, record again, then export, so the session names its code.")
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()

    events = read(args.log)
    # zero is where the audio starts, not where the app started: its voice took half a minute to load before that
    t0 = min(e["t_start"] for e in events if e["ev"] == "state" and e.get("what") in ("fixture_start", "fixture_start_in"))
    ids: dict[str, str] = {}

    def short(uid: str) -> str:
        return ids.setdefault(uid, f"{uid.split(':', 1)[0]}:{len(ids)}")

    def rel(t):
        return None if t is None else round(t - t0, 4)

    out = []
    for e in events:
        if e["ev"] == "asr" and e.get("kind") in KEEP_ASR:
            out.append(dict(ev="asr", channel=e["channel"], kind=e["kind"], t_recv=rel(e["t_recv"]), text=e.get("text", "")))
        elif e["ev"] == "commit" and e.get("phase") is None:
            out.append(dict(ev="commit", channel=e["channel"], seq=e["seq"], commit_id=e["commit_id"], utterance_id=short(e["utterance_id"]),
                            text_src=e["text_src"], t_commit=rel(e["t_commit"]), t_src_start=rel(e["t_src_start"]),
                            t_src_end=rel(e.get("t_src_end"))))
        elif e["ev"] == "mt" and e.get("seq") is not None and not e.get("hedged"):
            row = dict(ev="mt", channel=e["channel"], seq=e["seq"], t=rel(e["t"]))
            if e.get("text_tgt"):
                row.update(t_mt=rel(e["t_mt"]), text_tgt=e["text_tgt"])
            else:
                row.update(failed="filler" if e.get("filler") else e.get("error", "error"))
            out.append(row)
        elif e["ev"] == "state" and e.get("what") in ("fixture_start", "fixture_start_in"):
            out.append(dict(ev="state", what=e["what"], t_start=rel(e["t_start"])))
    out.sort(key=lambda e: e.get("t_recv", e.get("t_commit", e.get("t_mt", e.get("t", e.get("t_start", 0))))))

    s = summarize(events)
    run = next(e for e in reversed(events) if e["ev"] == "run")
    about = dict(recorded=next(e for e in events if e["ev"] == "run")["wall"], commit=commit, delay_s=run.get("delay_s", 0.0),
                 mode=run.get("mode"), voice=run.get("tts_voice"),
                 audio={"out.wav": "the Spanish that went in for the microphone", "in.wav": "the other side, in English"},
                 measured={"out": {k: s["out"][k] for k in ("commits", "played", "yields", "cable_gaps", "commit_to_first_pcm_s",
                                                            "speech_end_to_first_pcm_s")},
                           "in": {k: s["incoming"][k] for k in ("commits", "subtitles", "speech_end_to_subtitle_s")}})
    folder = Path(args.folder)
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "session.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for e in out:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    (folder / "about.json").write_text(json.dumps(about, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    shutil.copyfile(args.out_wav, folder / "out.wav")
    if args.in_wav:
        shutil.copyfile(args.in_wav, folder / "in.wav")
    print(f"{folder}: {len(out)} events, commit {commit}, recorded {about['recorded']}")


if __name__ == "__main__":
    main()

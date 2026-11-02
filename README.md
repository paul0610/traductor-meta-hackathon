# traductor-meta-hackathon

**OS-level simultaneous interpreter, Spanish ⇄ English, for any video call** (Meet, Zoom, Teams), built on Meta's **Muse Voice Transcribe** (realtime ASR), **Muse Spark** (translation) and **MMS-TTS** (the voice). You speak Spanish, or Spanglish, into your normal microphone and the other side hears fluent English through a virtual microphone. They speak English and you read Spanish subtitles. The call app needs no plugin: it just picks a different microphone.

> **Pre-existing vs. built this week.** Before the hackathon I had a private prototype of the same idea built on other providers (Deepgram, gpt-4o-mini, Edge TTS). **Everything in this repository was written from scratch during the Meta Global AI Developer Hackathon (Oct 26 – Nov 4, 2026) on Meta models.** No code was copied from the prototype; only design lessons were carried over, and they are written out in [`docs/SPEC.md`](docs/SPEC.md). The commit history is the record. Earlier commits used a non-Meta text-to-speech engine; Meta's MMS-TTS replaced it on Nov 1 to comply with the rule that every model called at runtime must be Meta's.

Track: **Indie Developer** · Theme: **Build for Your World**. I understand English but I am slow to speak it under pressure (interviews, meetings). This is "work in English today", not "learn English".

> **Tested on Windows 11.** Every test, gate and measurement in this README, the recorded session in `demo/` and the demo video were made on one Windows 11 Pro PC (build 26200, AMD Ryzen 9 6900HX, 32 GB of RAM), with the voice on the CPU. The live interpreter runs only on Windows. The replay also installs on macOS and Linux, but it has not been run there yet. See [Setup](#setup).

## Status

Work in progress during the hackathon week. Daily logs with measured numbers live in [`docs/log/`](docs/log/).

| Piece | State |
|---|---|
| Repo, SPEC, contracts, clock, seq-ordered deadline scheduler | done |
| Realtime ASR client (Muse Voice Transcribe) | done |
| Outgoing consecutive chain: mic → ASR → Spark → TTS → virtual mic | done, measured with a real voice |
| Incoming chain: loopback → ASR → Spark → subtitles | done, measured on real system audio |
| Safety: silent rest state, F9 latch, F10 via Core Audio, watchdog, restore on exit | done, physical keys pending |
| Incremental clause commits (LA-2), both directions | done, measured on synthetic voices |
| Synchronized dub with fixed delay D | done; measured on synthetic voices and with my own voice (gate S passed, 30 of 30 clauses), picture and sound calibrated with real claps (+23 ms); tried in a Google Meet call with my phone as the other side, Nov 2 |
| Spanglish: English that falls into Spanish comes out as one English voice | done and measured on a synthetic voice; the ASR sometimes drops whole Spanish passages |
| Turns: their sustained speech takes the floor from my voice | done, measured with both fixtures talking over each other |
| Context: the whole conversation and a glossary reach Spark | done, measured on latency and on ambiguous clauses |
| One-button widget and draggable metrics line | done, used with a real mouse and real keys on the real windows; F7 and F10 not pressed, not yet used in a call |
| Replay of a recorded session: no key, no VB-CABLE, no OBS | done and tested on Windows; macOS and Linux not run |
| Meta end to end: MMS-TTS on this PC is the voice | done, judged by ear and measured with a real voice; heard on a phone through Google Meet, Nov 2 |

## How it works (short)

```
OUT: mic ─► Muse Voice Transcribe (es) ─► clause commits ─► Muse Spark (es→en) ─► MMS-TTS (local) ─► scheduler ─► VB-CABLE ─► call app
IN:  system loopback ─► Muse Voice Transcribe (en) ─► Muse Spark (en→es) ─► subtitle overlay
```

Design, contracts, clock model and safety rules: [`docs/SPEC.md`](docs/SPEC.md).

### Meta models used and where they run

| Model | What it does | Where it runs |
|---|---|---|
| Muse Voice Transcribe | Hears both sides, with turn and timing events | Meta Model API, cloud |
| Muse Spark | Translates both ways | Meta Model API, cloud |
| MMS-TTS (`facebook/mms-tts-eng`) | Speaks the English | Open weights, on this PC, CPU |

`scripts/make_fixtures.py` also uses `facebook/mms-tts-spa`, for test voices only.

Honest notes:
- **Meta's Model API has no text-to-speech model** (checked against the docs, the live model list and the cookbook on Oct 30), so the voice is Meta's open-weight MMS-TTS on this PC. The voice is plain and single-speaker, chosen because the rules require Meta models end to end. It has no backup: nothing that is not Meta's runs, even when something fails.
- The live interpreter requires Windows 11, [VB-CABLE](https://vb-audio.com/Cable/) and headphones (with speakers the mic would re-translate the other side's voice).

## See it in 5 minutes

This plays a recorded session again: a short job interview with **synthetic voices on both sides** (Meta's MMS-TTS), not a real call. The listening and translating were done by the real Muse Voice Transcribe and Muse Spark when it was recorded, on Nov 2, on Windows 11. No key, no VB-CABLE, no OBS. Install it for your system as in [Setup](#setup), then:

```
uv run python -m interprete.replay
```

The interviewer asks in English and I answer in Spanish. You hear the interviewer, my Spanish 14 dB lower, and the English interpretation, which MMS-TTS makes again on your computer at the moments the run produced it. The band at the bottom of the screen shows the interviewer's words in Spanish, and my Spanish with its English. Nothing reaches Meta's API, and [`demo/interview/about.json`](demo/interview/about.json) says when it was recorded, with which commit, and what that run measured. The replay takes a little over a minute, after about 20 s loading the voice.

`--no-overlay` prints the subtitles in the console instead, and `--wav out.wav` writes the audio to a file instead of the speakers. Ctrl-C stops it. `scripts/export_session.py` turns any run's log into a session like this one. Replay is `python -m interprete.replay`; to go live, see [Run it live](#run-it-live-windows-11).

## Setup

> **Only Windows 11 was tested.** The macOS and Linux steps below install the replay, and were checked with a dry run of the locked install, not on real machines.

| | Windows 11 | macOS 14+ on Apple silicon | Linux x86_64, glibc 2.34+ |
|---|---|---|---|
| Replay of the recorded interview | tested | installs in a dry run, not run yet | installs in a dry run, not run yet |
| Live interpreter in a call | tested | not available | not available |

The live interpreter is Windows-only: it needs a virtual microphone (VB-CABLE), the loopback of the system audio (WASAPI), Core Audio to give your microphone back, global hotkeys and, for the dub, OBS's virtual camera driver. On macOS and Linux those packages are not even installed.

Every system needs [uv](https://docs.astral.sh/uv/) and Git. `uv sync --locked` installs Python 3.13 by itself if it is missing, and the exact packages in `uv.lock`, pinned and resolved with a supply-chain cooldown: nothing published after Oct 22, 2026, written as a UTC time so the lock matches in every time zone (`uv run python scripts/check_lock.py` checks six). The first replay or call downloads the English voice, about 150 MB, with no account.

### Windows 11 (tested)

In PowerShell:

```powershell
winget install --id=astral-sh.uv -e
winget install --id=Git.Git -e
```

Open a new PowerShell window, so both are on the PATH, and:

```powershell
git clone https://github.com/paul0610/traductor-meta-hackathon.git
cd traductor-meta-hackathon
uv sync --locked
uv run python -m interprete.replay
```

That is all the replay needs. For live calls, also:

1. **VB-CABLE**, the virtual microphone: download it from [vb-audio.com/Cable](https://vb-audio.com/Cable/), unzip it, run `VBCABLE_Setup_x64.exe` as administrator, click *Install Driver* and restart Windows.
2. **A Meta API key**, from [dev.meta.ai](https://dev.meta.ai): run `copy .env.example .env` and paste the key after `MODEL_API_KEY=` in `.env`, which git ignores.
3. **Headphones.** With speakers, the microphone hears the other side and the app translates their voice back.
4. **Microphone access:** Settings › Privacy & security › Microphone › *Let desktop apps access your microphone*, on. For the dub, the same under Camera.
5. **For the synchronized dub only:** OBS Studio, installed once and left closed, for its virtual camera driver, and ffmpeg, which finds the webcam by name. Then set `RTT_DUB=1` and `RTT_CAMERA_NAME` in `.env`.
   ```powershell
   winget install --id=OBSProject.OBSStudio -e
   winget install --id=Gyan.FFmpeg -e
   ```
6. **Check it:** `uv run python scripts/warmcheck.py` tests everything a call needs and changes nothing. Then see [Run it live](#run-it-live-windows-11).

### macOS 14 or later, Apple silicon (replay only, not run yet)

In Terminal:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
xcode-select --install
```

The first installs uv (or `brew install uv`); the second installs Git, if macOS does not have it yet. Open a new terminal, and:

```bash
git clone https://github.com/paul0610/traductor-meta-hackathon.git
cd traductor-meta-hackathon
uv sync --locked
uv run python -m interprete.replay
```

Nothing else: on macOS the audio library ships inside the `sounddevice` package. Intel Macs cannot run it, because PyTorch publishes no build for them, and neither can macOS 13, for which the screen library, PySide6 6.12, publishes nothing. If the band does not show, `--no-overlay` prints the subtitles in the terminal.

### Linux x86_64 (replay only, not run yet)

It needs glibc 2.34 or later, as in Ubuntu 22.04, Debian 12 or Fedora 35; `ldd --version` shows yours. On Debian or Ubuntu:

```bash
sudo apt install git curl libportaudio2
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On Fedora the first line is `sudo dnf install git curl portaudio`. Open a new terminal, and:

```bash
git clone https://github.com/paul0610/traductor-meta-hackathon.git
cd traductor-meta-hackathon
uv sync --locked
uv run python -m interprete.replay
```

PyTorch comes from its CPU index, without the 2.5 GB of CUDA libraries in PyPI's build: the voice runs on the CPU. The band needs a desktop session. If Qt says it could not load the "xcb" platform plugin, the usual missing package is `libxcb-cursor0` (`xcb-util-cursor` on Fedora), or run with `--no-overlay`. If nothing sounds, `--wav out.wav` writes the audio to a file.

## Run it live (Windows 11)

Set it up as in [Setup › Windows 11](#windows-11-tested) first. Then double-click **`Interprete.bat`**: it starts both directions and the subtitle overlay. In your call app pick `CABLE Output` as the microphone, unmuted, with noise cancellation off, and with the dub pick `OBS Virtual Camera` as the camera. The other side hears the English voice, and with the dub your own voice 20 dB under it (`RTT_VOICE_UNDER_DB`). Your raw voice goes out alone only with F9, or by itself when the ASR is down for more than 3 s or the outgoing chain fails, so the other side is never left in silence.

Two optional checks: `uv run python scripts/test_cable.py` plays the voice into `CABLE Input` and verifies it on `CABLE Output`, and `uv run python scripts/asr_live.py` sends your microphone to Muse Voice Transcribe and prints its events.

| Key | Action |
|---|---|
| F7 | Tell the other side they are hearing an AI interpreter |
| F8 | Mute everything / unmute |
| F9 | Your raw voice on the cable, latched; F9 again to interpret again |
| F10 | F9, plus the physical mic back as the Windows default |

**The button.** Top right; drag it anywhere and it stays there next time. `CARGANDO`, then `LISTO`: nothing is captured until you click it. `CONECTANDO` lasts until the ASR and the camera are up, and the last 3 s of what you say meanwhile are kept. Then `EN VIVO`: the ring is the health of the sync (of the lag with no dub) and the number is the lag in seconds. `PAUSADO`: they took the floor. `MUDO`: F8. `VOZ CRUDA`: F9. A left click stops it once it runs, a right click from any state, loading included. It ends on `APAGADO`, and if the microphone could not be restored it says so.

**The metrics line.** `LAG │ SYNC │ CORR │ TURNO │ ES→EN │ META`. What is down shows there and stays: `ASR CAÍDO`, `SUBTÍTULOS CAÍDOS`, `SIN CÁMARA RETRASADA`, `SIN MÉTRICAS`, or the task that died. `META` counts from the click, not from loading.

**The subtitle band.** At the bottom of the screen, as tall as what it has to say. What they say in English in grey, and in Spanish in white; below, what you said and how it came out. It fades after 9 s of silence so the call stays in view. If they take the floor while your voice is speaking it says `TE ESTÁN HABLANDO`, and what your voice did not get to say stays on it in amber.

**Sounds.** A short cue when the button changes state, so you do not have to look at it: ready, live, muted and back, raw voice, they took the floor, stopping. They play on your headphones and never reach the cable. `RTT_SOUNDS=0` turns them off.

**The voice.** MMS-TTS cannot read digits, symbols, accents or punctuation: its tokenizer drops most of them in silence. Spark is asked to write numbers in words, and `tts/spoken.py` catches what gets through, so "$120" and "Núñez" are still said. `RTT_MMS_SPEAKING_RATE` sets the pace: lower is calmer, and the English then outlasts the Spanish it translates. A clause longer than its Spanish is sped up by 12 % at most: at 25 % it could no longer be understood, and few clauses need it.

To check it without speaking: `uv run python scripts/make_fixtures.py`, then `uv run python scripts/gate_60s.py` runs both chains on synthetic voices and judges the run from its log. `uv run python scripts/gate_sync.py` does the same for the dub, and also compares each clause's timing against the real word times of the fixture. With `--voice` it runs on your own voice and camera for 60 s, checks where each turn opens against the microphone, and leaves in `logs/` a 20 s clip of what the other side gets.

**Synchronized dub** (`RTT_DUB=1` or `--dub`): the other side gets my video and voice D seconds late, so the English voice starts and stops with my lips. It needs OBS Studio, installed once and left closed, and ffmpeg, as in [Setup](#windows-11-tested). It is off by default: set `RTT_DUB=1`, and `RTT_CAMERA_NAME` to part of your webcam's name. Pick `OBS Virtual Camera` in the call: it is there from `LISTO`, showing a card, so you can pick it before you click, and the webcam opens only with the click. Until then the band says, in English, which camera to pick and what each one costs. Run `uv run python scripts/calibrate_av_offset.py` once: it prints the offset between picture and voice, and you put it in `RTT_AV_OFFSET_MS`. The self-view shows the delayed video, and with a shared screen the cursor runs D seconds ahead of the voice, so turn the dub off to share.

**Level on the cable.** Windows has two sliders that scale everything the call hears: the one on `CABLE Input`, and the one it keeps for each app in the volume mixer, which it remembers between runs. The bar shows `CABLE 22 %` or `PYTHON 3 %` if either is low.

**Spanglish.** Say it in English, fall into Spanish when you get stuck, and one English voice comes out. "Cómo se dice" and "no sé" are dropped as fillers. Known limit: on a synthetic voice the ASR kept between 0 and 11 of 13 Spanish words inside English speech, and sometimes skips a passage entirely, so that passage is not interpreted. Terms in the glossary raised it to 10–13 of 13.

**Turns.** If the other side really starts talking (four words and a second of speech, so "yeah" does not count), my voice finishes the clause it is on and gives way. What it did not say stays on the band, to repeat. The floor comes back when they stop.

**Glossary.** Copy `glossary.example.txt` to `glossary.txt`. Terms reach Spark in both directions and the ASR as hints.

## Metrics

Every run writes one JSONL line per event to `logs/` (fields in SPEC §4). Numbers are reported per day in `docs/log/`.

Gates on the Windows 11 PC described at the top, default settings, on synthetic voices unless it says otherwise, Lima time. Two calls on Nov 2 went through Google Meet with my phone as the other side, muted, so they measure only my direction. Spark's latency changes from day to day, p95 1.4 to 1.9 s on Nov 1 and 2.3 to 3.3 s on Nov 2, so every number says when it was measured.

| Gate | When | Result |
|---|---|---|
| 0: Spanish in, English on the cable | Nov 2, 07:24, single run | 34 of 34 clauses played, 100 % of the bytes, 0 gaps |
| No dub: end of a phrase to the English voice | Nov 1 20:09, Nov 2 07:24 and 08:12, one run each | p50 3.0 to 3.4 s, p95 4.0 to 5.0 s |
| 60 s, both directions | Nov 1, 19:41, single run | commit to first PCM p50 2.04 s, p95 3.20 s, 0 gaps; one clause lost, see below |
| S: dub, D 7.0 s, p95 of sync error at most 0.5 s | Nov 1 19:36 to 20:11, 4 runs; Nov 2 07:27 to 07:37, 3 runs | 5 of 7 runs pass, at 0.25 to 0.45 s; the other two at 0.56 and 0.62 s. None over 1 s late, none early; one run lost a clause, see below |
| S with my own voice, 60 s, with the cap then at 25 % | Nov 1, 19:28, single run | passes: 0.147 s, 30 of 30 clauses played. Against the lips where a phrase opens: p95 0.80 s; 5 of 25 over 0.5 s, 1 over 1 s |
| MMS-TTS, whole clause ready | Nov 1, 164 clauses over 5 runs | p50 0.60 s, p95 0.83 s, 4 threads |
| The interview in `demo/` | Nov 2, 08:12, single run | 18 of 18 English clauses played, 0 gaps; their subtitles p50 2.90 s after each phrase |
| Google Meet call, my voice, no dub | Nov 2, 15:46, 10 min | 212 of 213 clauses played; first word of a phrase to its English p50 5.0 s, p95 6.8 s; end of a phrase to it p50 3.7 s |
| Google Meet call, my voice, dub with D 7.0 s | Nov 2, 16:46, 2 min | 42 clauses, p95 of sync error 0.54 s, video 7.04 s late; the webcam gave 7.5 frames per second there, against 30 in the pre-call check |

Known limits. When a clause is late it is because the English before it ran long, not because the voice or Spark were slow: late clauses were ready 1 s or more before their turn. A last word the clause before already translated can hold Spark past its limit; the listener misses nothing, but the gates count that clause as lost. Telling Spark it may answer nothing was worse: p95 went from 1.60 to 2.40 s and it dropped a real clause.

Reproduce them with `scripts/make_fixtures.py`, then `scripts/gate_60s.py` and `scripts/gate_sync.py`. Gate 0 is a plain run, `uv run python -m interprete.app --out-only --no-overlay --delay 0 --wav-out logs/fixture_es.wav`, whose summary counts the clauses that played.

## Roadmap (after the hackathon, no code this week)

- **Milestone 2:** indigenous languages of Peru (Aymara, Quechua) with Meta's open-weight Omnilingual ASR.
- **Milestone 3:** Peruvian Sign Language.

## Third-party models and licenses

The voice is [MMS-TTS](https://huggingface.co/facebook/mms-tts-eng) by Meta, CC BY-NC 4.0, downloaded on first use.

## License

PolyForm Noncommercial 1.0.0, see [LICENSE](LICENSE). Free for personal, educational, research and non-profit use. For commercial use write to paulrojas0610@gmail.com.

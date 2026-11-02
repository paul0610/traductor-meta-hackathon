# traductor-meta-hackathon

**OS-level simultaneous interpreter, Spanish ⇄ English, for any video call** (Meet, Zoom, Teams), built on Meta's **Muse Voice Transcribe** (realtime ASR), **Muse Spark** (translation) and **MMS-TTS** (the voice). You speak Spanish, or Spanglish, into your normal microphone and the other side hears fluent English through a virtual microphone. They speak English and you read Spanish subtitles. The call app needs no plugin: it just picks a different microphone.

> **Pre-existing vs. built this week.** Before the hackathon I had a private prototype of the same idea built on other providers (Deepgram, gpt-4o-mini, Edge TTS). **Everything in this repository was written from scratch during the Meta Global AI Developer Hackathon (Oct 26 – Nov 4, 2026) on Meta models.** No code was copied from the prototype; only design lessons were carried over, and they are written out in [`docs/SPEC.md`](docs/SPEC.md). The commit history is the record. Earlier commits used a non-Meta text-to-speech engine; Meta's MMS-TTS replaced it on Nov 1 to comply with the rule that every model called at runtime must be Meta's.

Track: **Indie Developer** · Theme: **Build for Your World**. I understand English but I am slow to speak it under pressure (interviews, meetings). This is "work in English today", not "learn English".

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
| Synchronized dub with fixed delay D | audio measured on synthetic voices, picture and sound calibrated with real claps (+23 ms); a real voice and a real call still to do |
| Spanglish: English that falls into Spanish comes out as one English voice | done and measured on a synthetic voice; the ASR sometimes drops whole Spanish passages |
| Turns: their sustained speech takes the floor from my voice | done, measured with both fixtures talking over each other |
| Context: the whole conversation and a glossary reach Spark | done, measured on latency and on ambiguous clauses |
| One-button widget and draggable metrics line | done, used with a real mouse and real keys on the real windows; F7 and F10 not pressed, not yet used in a call |
| Meta end to end: MMS-TTS on this PC is the voice | done, judged by ear and measured with a real voice; a real call still to do |

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
- Requires Windows 11, [VB-CABLE](https://vb-audio.com/Cable/) and headphones (with speakers the mic would re-translate the other side's voice).

## Run it

Requires Windows 11, Python 3.13, [uv](https://docs.astral.sh/uv/), headphones, and [VB-CABLE](https://vb-audio.com/Cable/) (restart Windows after installing it). OBS Studio is only for the synchronized dub. The English voice, about 150 MB, downloads on first use with no account. The microphone is the Windows default unless `RTT_MIC_NAME` says otherwise.

```powershell
uv sync --locked
copy .env.example .env   # then put your MODEL_API_KEY in .env, from https://dev.meta.ai
uv run python scripts/test_cable.py          # the voice -> CABLE Input, verified on CABLE Output
uv run python scripts/asr_live.py            # live mic -> Muse Voice Transcribe, events printed
```

Before a call, `uv run python scripts/warmcheck.py` tests everything a call needs and changes nothing. Then double-click **`Interprete.bat`**: it starts both directions and the subtitle overlay. In your call app pick `CABLE Output` as the microphone, unmuted, with noise cancellation off. The other side only hears the English voice; your raw voice reaches the cable only if you ask for it.

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

**The voice.** MMS-TTS only knows letters, apostrophes and hyphens: it loses digits, symbols, accents and punctuation in silence. Spark is asked to write numbers in words, and `tts/spoken.py` catches what gets through, so "$120" and "Núñez" are still said. `RTT_MMS_SPEAKING_RATE` sets the pace: lower is calmer, and the English then outlasts the Spanish it translates. A clause longer than its Spanish is sped up by 12 % at most: at 25 % it could no longer be understood, and few clauses need it.

To check it without speaking: `uv run python scripts/make_fixtures.py`, then `uv run python scripts/gate_60s.py` runs both chains on synthetic voices and judges the run from its log. `uv run python scripts/gate_sync.py` does the same for the dub, and also compares each clause's timing against the real word times of the fixture. With `--voice` it runs on your own voice and camera for 60 s, checks where each turn opens against the microphone, and leaves in `logs/` a 20 s clip of what the other side gets.

**Synchronized dub** (`RTT_DUB=1` or `--dub`): the other side gets my video and voice D seconds late, so the English voice starts and stops with my lips. Needs OBS Studio installed once for its virtual camera driver; OBS itself stays closed. Pick `OBS Virtual Camera` in the call, and run `uv run python scripts/calibrate_av_offset.py` once to line up picture and voice. The self-view shows the delayed video, and with a shared screen the cursor runs D seconds ahead of the voice, so turn the dub off to share.

**Level on the cable.** Windows has two sliders that scale everything the call hears: the one on `CABLE Input`, and the one it keeps for each app in the volume mixer, which it remembers between runs. The bar shows `CABLE 22 %` or `PYTHON 3 %` if either is low.

Dependencies are pinned exactly and locked (`uv.lock`), resolved with a supply-chain cooldown (`exclude-newer = "2026-10-22"`).

**Spanglish.** Say it in English, fall into Spanish when you get stuck, and one English voice comes out. "Cómo se dice" and "no sé" are dropped as fillers. Known limit: on a synthetic voice the ASR kept between 0 and 11 of 13 Spanish words inside English speech, and sometimes skips a passage entirely, so that passage is not interpreted. Terms in the glossary raised it to 10–13 of 13.

**Turns.** If the other side really starts talking (four words and a second of speech, so "yeah" does not count), my voice finishes the clause it is on and gives way. What it did not say stays on the band, to repeat. The floor comes back when they stop.

**Glossary.** Copy `glossary.example.txt` to `glossary.txt`. Terms reach Spark in both directions and the ASR as hints.

## Metrics

Every run writes one JSONL line per event to `logs/` (fields in SPEC §4). Numbers are reported per day in `docs/log/`.

Gates on this PC, default settings, Nov 1, on synthetic voices unless it says otherwise. A real call is still to come here.

| Gate | Result |
|---|---|
| 0: Spanish in, English on the cable | 34 of 34 clauses played, 100 % of the bytes, 0 gaps |
| 60 s, both directions | commit to first PCM p50 2.04 s, p95 3.20 s, 0 gaps; one clause lost, see below |
| S: dub, D 7.0 s, p95 of sync error at most 0.5 s | 0.25 to 0.45 s in 4 runs, none over 1 s late, none early; one run lost a clause, see below |
| S with my own voice, 60 s, with the cap then at 25 % | passes: 0.147 s, 30 of 30 clauses played. Against the lips where a phrase opens: p95 0.80 s; 5 of 25 over 0.5 s, 1 over 1 s |
| MMS-TTS, whole clause ready | p50 0.60 s, p95 0.83 s, 164 clauses, 4 threads |

Known limits. When a clause is late it is because the English before it ran long, not because the voice or Spark were slow: late clauses were ready 1 s or more before their turn. A last word the clause before already translated can hold Spark past its limit; the listener misses nothing, but the gates count that clause as lost. Telling Spark it may answer nothing was worse: p95 went from 1.60 to 2.40 s and it dropped real clauses.

Reproduce them with `scripts/make_fixtures.py`, then `scripts/gate_60s.py` and `scripts/gate_sync.py`.

## Roadmap (after the hackathon, no code this week)

- **Milestone 2:** indigenous languages of Peru (Aymara, Quechua) with Meta's open-weight Omnilingual ASR.
- **Milestone 3:** Peruvian Sign Language.

## Third-party models and licenses

The voice is [MMS-TTS](https://huggingface.co/facebook/mms-tts-eng) by Meta, CC BY-NC 4.0, downloaded on first use.

## License

PolyForm Noncommercial 1.0.0, see [LICENSE](LICENSE). Free for personal, educational, research and non-profit use. For commercial use write to paulrojas0610@gmail.com.

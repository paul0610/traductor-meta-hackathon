# traductor-meta-hackathon

**OS-level simultaneous interpreter, Spanish ⇄ English, for any video call** (Meet, Zoom, Teams), built on Meta's **Muse Voice Transcribe** (realtime ASR) and **Muse Spark** (translation). You speak Spanish, or Spanglish, into your normal microphone and the other side hears fluent English through a virtual microphone. They speak English and you read Spanish subtitles. The call app needs no plugin: it just picks a different microphone.

> **Pre-existing vs. built this week.** Before the hackathon I had a private prototype of the same idea built on other providers (Deepgram, gpt-4o-mini, Edge TTS). **Everything in this repository was written from scratch during the Meta Global AI Developer Hackathon (Oct 26 – Nov 4, 2026) on Meta models.** No code was copied from the prototype; only design lessons were carried over, and they are written out in [`docs/SPEC.md`](docs/SPEC.md). The commit history is the record.

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
| Synchronized dub with fixed delay D | audio measured on synthetic voices, delayed camera running through the OBS driver; clap calibration and a real call still to do |

## How it works (short)

```
OUT: mic ─► Muse Voice Transcribe (es) ─► clause commits ─► Muse Spark (es→en) ─► Pocket TTS ─► scheduler ─► VB-CABLE ─► call app
IN:  system loopback ─► Muse Voice Transcribe (en) ─► Muse Spark (en→es) ─► subtitle overlay
```

Design, contracts, clock model and safety rules: [`docs/SPEC.md`](docs/SPEC.md).

Honest notes:
- **Meta's Model API has no text-to-speech model** (checked against the docs, the live model list and the cookbook on Oct 30). Meta's open-weight MMS-TTS exists outside the API under a non-commercial license and is not used here. The English voice is Kyutai's Pocket TTS running on this PC, with Microsoft Edge TTS as its backup (`RTT_TTS_ENGINE`, `RTT_TTS_BACKUP`). Meta models do the listening (ASR, including turn and timing events) and the translating.
- Requires Windows 11, [VB-CABLE](https://vb-audio.com/Cable/) and headphones (with speakers the mic would re-translate the other side's voice).

## Run it

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync --locked
copy .env.example .env   # then put your MODEL_API_KEY in .env
uv run python scripts/test_cable.py          # Edge TTS -> CABLE Input, verified on CABLE Output
uv run python scripts/asr_live.py            # live mic -> Muse Voice Transcribe, events printed
```

Before a call, `uv run python scripts/warmcheck.py` tests everything a call needs and changes nothing. Then double-click **`Interprete.bat`**: it starts both directions and the subtitle overlay. In your call app pick `CABLE Output` as the microphone, unmuted, with noise cancellation off. The other side only hears the English voice; your raw voice reaches the cable only if you ask for it.

| Key | Action |
|---|---|
| F7 | Tell the other side they are hearing an AI interpreter |
| F8 | Mute everything / unmute |
| F9 | Your raw voice on the cable, latched; F9 again to interpret again |
| F10 | F9, plus the physical mic back as the Windows default |

To check it without speaking: `uv run python scripts/make_fixtures.py`, then `uv run python scripts/gate_60s.py --fixtures` runs both chains on synthetic voices and judges the run from its log. `uv run python scripts/gate_sync.py` does the same for the dub, and also compares each clause's timing against the real word times of the fixture.

**Synchronized dub** (`RTT_DUB=1` or `--dub`): the other side gets my video and voice D seconds late, so the English voice starts and stops with my lips. Needs OBS Studio installed once for its virtual camera driver; OBS itself stays closed. Pick `OBS Virtual Camera` in the call, and run `uv run python scripts/calibrate_av_offset.py` once to line up picture and voice. The self-view shows the delayed video, and with a shared screen the cursor runs D seconds ahead of the voice, so turn the dub off to share.

Dependencies are pinned exactly and locked (`uv.lock`), resolved with a supply-chain cooldown (`exclude-newer = "2026-10-22"`).

## Metrics

Every run writes one JSONL line per event to `logs/` (fields in SPEC §4). Numbers are reported per day in `docs/log/` and summarized here at submission.

## Roadmap (after the hackathon, no code this week)

- **Milestone 2:** indigenous languages of Peru (Aymara, Quechua) with Meta's open-weight Omnilingual ASR.
- **Milestone 3:** Peruvian Sign Language.

## Credits

The local voice is [Pocket TTS](https://huggingface.co/kyutai/pocket-tts) by Kyutai, CC BY 4.0. The default voice, `michael`, comes from the VCTK corpus, CC BY 4.0. Both download on first use. Some Pocket voices come from non-commercial sets, listed in [kyutai/tts-voices](https://huggingface.co/kyutai/tts-voices).

## License

MIT, see [LICENSE](LICENSE).

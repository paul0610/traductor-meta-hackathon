# traductor-meta-hackathon

**OS-level simultaneous interpreter, Spanish ⇄ English, for any video call** (Meet, Zoom, Teams), built on Meta's **Muse Voice Transcribe** (realtime ASR) and **Muse Spark** (translation). You speak Spanish, or Spanglish, into your normal microphone and the other side hears fluent English through a virtual microphone. They speak English and you read Spanish subtitles. The call app needs no plugin: it just picks a different microphone.

> **Pre-existing vs. built this week.** Before the hackathon I had a private prototype of the same idea built on other providers (Deepgram, gpt-4o-mini, Edge TTS). **Everything in this repository was written from scratch during the Meta Global AI Developer Hackathon (Oct 26 – Nov 4, 2026) on Meta models.** No code was copied from the prototype; only design lessons were carried over, and they are written out in [`docs/SPEC.md`](docs/SPEC.md). The commit history is the record.

Track: **Indie Developer** · Theme: **Build for Your World**. I understand English but I am slow to speak it under pressure (interviews, meetings). This is "work in English today", not "learn English".

## Status

Work in progress during the hackathon week. Daily logs with measured numbers live in [`docs/log/`](docs/log/).

| Piece | State |
|---|---|
| Repo, SPEC, contracts, clock, seq-ordered deadline scheduler | in progress |
| Realtime ASR client (Muse Voice Transcribe) | in progress |
| Outgoing consecutive chain: mic → ASR → Spark → TTS → virtual mic | in progress |
| Incoming chain: loopback → ASR → Spark → subtitles | planned |
| Incremental clause commits (LA-2) | planned |
| Synchronized dub with fixed delay D | planned |

## How it works (short)

```
OUT: mic ─► Muse Voice Transcribe (es) ─► clause commits ─► Muse Spark (es→en) ─► Edge TTS ─► scheduler ─► VB-CABLE ─► call app
IN:  system loopback ─► Muse Voice Transcribe (en) ─► Muse Spark (en→es) ─► subtitle overlay
```

Design, contracts, clock model and safety rules: [`docs/SPEC.md`](docs/SPEC.md).

Honest notes:
- **There is no Meta text-to-speech model.** The English voice is Microsoft Edge TTS. Meta models do the listening (ASR, including turn and timing events) and the translating.
- Requires Windows 11, [VB-CABLE](https://vb-audio.com/Cable/) and headphones (with speakers the mic would re-translate the other side's voice).

## Run it

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync --locked
copy .env.example .env   # then put your MODEL_API_KEY in .env
uv run python scripts/test_cable.py          # Edge TTS -> CABLE Input, verified on CABLE Output
uv run python scripts/asr_live.py            # live mic -> Muse Voice Transcribe, events printed
```

Then double-click **`Interprete.bat`** to start the outgoing interpreter and pick `CABLE Output` as the microphone in your call app. F9 cuts back to your own voice, F8 mutes.

Dependencies are pinned exactly and locked (`uv.lock`), resolved with a supply-chain cooldown (`exclude-newer = "2026-10-22"`).

## Metrics

Every run writes one JSONL line per event to `logs/` (fields in SPEC §4). Numbers are reported per day in `docs/log/` and summarized here at submission.

## Roadmap (after the hackathon, no code this week)

- **Milestone 2:** indigenous languages of Peru (Aymara, Quechua) with Meta's open-weight Omnilingual ASR.
- **Milestone 3:** Peruvian Sign Language.

## License

MIT, see [LICENSE](LICENSE).

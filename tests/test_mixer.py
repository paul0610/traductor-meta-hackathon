from types import SimpleNamespace

import numpy as np
import pytest

from interprete.audio import cable_mixer
from interprete.audio.cable_mixer import CableMixer, State
from interprete.audio.ring import Ring
from interprete.contracts import Channel, PcmChunk

RATE = 48000
OK = SimpleNamespace(output_underflow=False)


@pytest.fixture
def mixer(monkeypatch):
    dev = SimpleNamespace(index=0, name="CABLE Input", hostapi="Windows WASAPI", default_samplerate=RATE,
                          max_input_channels=0, max_output_channels=2)
    monkeypatch.setattr(cable_mixer.devices, "find", lambda *a, **k: dev)
    m = CableMixer(mic=Ring(RATE))
    m._state = m.rest
    return m


def block(m, voice=0.5):
    """One audio callback at a known clock time; the mic ring holds `voice`."""
    n = m.block
    m.mic.write(np.full(n, voice, np.float32))
    out = np.ones((n, m.channels), np.float32)
    m._callback(out, n, None, OK)
    return out


def tts(seq=0, value=0.25, dur=0.05):
    n = int(RATE * dur)
    pcm = (np.full(n, value) * 32768).astype("<i2").tobytes()
    return PcmChunk(utterance_id="out:s:0", commit_id=seq, seq=seq, channel=Channel.OUT, pcm=pcm,
                    sample_rate=RATE, duration_s=dur, t_src_start=0.0, t_src_end=None, deadline=0.0)


def test_a_chunk_planned_too_early_is_no_gap_if_the_audio_is_continuous(mixer):
    """The scheduler's plan drifts from the real end by a few ms; only real silence counts."""
    mixer.play(tts(0, dur=0.015), 1.0)
    mixer.play(tts(1, dur=0.015), 1.001)       # planned long before the first really ends
    for _ in range(4):
        block(mixer)
    assert mixer.cable_gaps == 0


def test_at_rest_the_receiver_hears_nothing_of_the_raw_voice(mixer):
    assert mixer.state is State.SILENT
    assert np.all(block(mixer) == 0.0)


def test_translated_voice_reaches_every_cable_channel(mixer):
    mixer.play(tts(), 0.0)
    out = block(mixer)
    assert mixer.state is State.TTS
    assert np.allclose(out[:, 0], 0.25, atol=1e-3)
    assert np.array_equal(out[:, 0], out[:, 1])


def test_after_the_clause_the_cable_goes_back_to_silence(mixer):
    mixer.play(tts(dur=0.005), 0.0)
    block(mixer)
    assert mixer.tts_idle_since is not None
    assert mixer.release_if_idle(0, mixer.tts_idle_since + 0.31)
    assert mixer.state is State.SILENT
    assert np.all(block(mixer) == 0.0)


def test_in_flight_work_keeps_the_cable(mixer):
    mixer.play(tts(dur=0.005), 0.0)
    block(mixer)
    assert not mixer.release_if_idle(1, mixer.tts_idle_since + 5.0)
    assert mixer.state is State.TTS


def test_f9_gives_live_voice_and_stays_latched_until_the_next_f9(mixer):
    mixer.play(tts(dur=0.5), 0.0)
    block(mixer)
    mixer.kill()
    assert mixer.state is State.PASSTHROUGH
    assert np.allclose(block(mixer)[:, 0], 0.5)
    mixer.play(tts(seq=1, dur=0.5), 0.0)       # handed over before the scheduler flushed
    assert np.allclose(block(mixer)[:, 0], 0.5)
    assert mixer.state is State.PASSTHROUGH
    mixer.unlatch()
    assert mixer.state is State.SILENT
    assert np.all(block(mixer) == 0.0)


def test_mute_silences_tts_too_and_restores_the_previous_state(mixer):
    mixer.play(tts(dur=0.5), 0.0)
    block(mixer)
    mixer.toggle_mute()
    assert np.all(block(mixer) == 0.0)
    mixer.toggle_mute()
    assert mixer.state is State.TTS


def test_asr_fallback_passes_live_voice_while_resting_only(mixer):
    mixer.set_rest(State.PASSTHROUGH)
    assert np.allclose(block(mixer)[:, 0], 0.5)
    mixer.set_rest(State.SILENT)
    assert np.all(block(mixer) == 0.0)
    mixer.kill()
    mixer.set_rest(State.PASSTHROUGH)
    mixer.set_rest(State.SILENT)
    assert mixer.state is State.PASSTHROUGH    # the F9 latch wins over the fallback


def test_release_empties_the_cable_and_returns_to_rest(mixer):
    mixer.play(tts(dur=0.5), 0.0)
    block(mixer)
    mixer.release("watchdog")
    assert mixer.state is State.SILENT
    assert np.all(block(mixer) == 0.0)
    assert not mixer.busy()

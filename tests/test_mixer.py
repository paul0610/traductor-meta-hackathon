from types import SimpleNamespace

import numpy as np
import pytest

from interprete.audio import cable_mixer
from interprete.audio.cable_mixer import CableMixer, State
from interprete.audio.delay import DelayLine
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


@pytest.fixture
def dub(mixer):
    mixer.dub(DelayLine(RATE, 0.05), -20.0)
    mixer._state = mixer.rest
    return mixer


def block_dub(m, voice=0.5):
    n = m.block
    m.delay.write(np.full(n, voice, np.float32))
    return block(m, voice)


def fill(m, voice=0.5):
    for _ in range(int(0.05 * RATE / m.block)):
        block_dub(m, voice)


def test_dub_is_silent_until_the_line_fills_then_my_voice_comes_attenuated(dub):
    assert np.all(block_dub(dub) == 0.0)
    fill(dub)
    assert dub.state is State.DUB
    assert np.allclose(block_dub(dub)[:, 0], 0.05, atol=1e-5)


def test_dub_mutes_my_voice_with_minus_infinity(mixer):
    mixer.dub(DelayLine(RATE, 0.05), float("-inf"))
    mixer._state = mixer.rest
    fill(mixer)
    assert np.all(block_dub(mixer) == 0.0)


def test_dub_stays_between_clauses_with_the_tts_on_top_of_my_voice(dub):
    fill(dub)
    dub.play(tts(dur=0.005), 0.0)
    out = block_dub(dub)
    assert dub.state is State.DUB
    assert out[0, 0] == pytest.approx(0.30, abs=1e-3)   # TTS + voice under
    dub.release_if_idle(0, 1e9)
    assert dub.state is State.DUB


def test_f9_leaves_the_dub_for_live_voice_and_the_return_repeats_nothing(dub):
    fill(dub)
    dub.kill()
    assert np.allclose(block_dub(dub, 0.9)[:, 0], 0.9)
    dub.unlatch()
    assert dub.state is State.DUB
    assert np.all(block_dub(dub, 0.5) == 0.0)           # silence, not what was already heard live


def test_mute_keeps_the_delay_running(dub):
    fill(dub)
    dub.toggle_mute()
    assert np.all(block_dub(dub) == 0.0)
    dub.toggle_mute()
    assert dub.state is State.DUB
    assert np.allclose(block_dub(dub)[:, 0], 0.05, atol=1e-5)


def test_asr_down_leaves_the_dub_for_live_voice_and_comes_back(dub):
    fill(dub)
    dub.set_rest(State.PASSTHROUGH)
    assert np.allclose(block_dub(dub, 0.9)[:, 0], 0.9)
    dub.set_rest(State.DUB)
    assert dub.state is State.DUB


def test_a_watchdog_release_empties_the_tts_and_stays_in_the_dub(dub):
    fill(dub)
    dub.play(tts(dur=0.5), 0.0)
    block_dub(dub)
    dub.release("watchdog")
    assert dub.state is State.DUB
    assert np.allclose(block_dub(dub)[:, 0], 0.05, atol=1e-5)
    assert not dub.busy()

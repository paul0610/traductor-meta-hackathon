import asyncio
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from interprete import app, config
from interprete.audio import devices

APIS = [{"name": "MME", "default_input_device": 1}, {"name": "Windows WASAPI", "default_input_device": 3}]
LONG_NAME = "Microphone Array (Some Long Vendor Name Audio)"


def endpoints(*names):
    """(name, inputs) in the order the host lists them: every one of them on WASAPI."""
    return [{"name": n, "hostapi": 1, "max_input_channels": 2, "max_output_channels": 0, "default_samplerate": 48000}
            for n in names]


@pytest.fixture
def sound(monkeypatch):
    def install(default, *names):
        listed = endpoints(*names)
        monkeypatch.setattr(devices.sd, "query_hostapis", lambda: APIS)
        monkeypatch.setattr(devices.sd, "query_devices", lambda i=None: listed if i is None else {"name": default})

    return install


def test_with_no_key_it_ends_in_one_line_that_says_what_to_do():
    env = {**os.environ, "MODEL_API_KEY": "", "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-m", "interprete.app", "--no-overlay"], capture_output=True, text=True, env=env,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
    line = r.stderr.strip()
    assert "\n" not in line
    for word in ("MODEL_API_KEY", ".env.example", "https://dev.meta.ai"):
        assert word in line


def settings(**over):
    return SimpleNamespace(**{"cable_name": "CABLE Input", "mic_name": "", **over})


def test_without_the_cable_it_says_where_to_get_it(monkeypatch):
    monkeypatch.setattr(config, "require_api_key", lambda: "key")
    monkeypatch.setattr(devices, "candidates", lambda *a: [])
    with pytest.raises(SystemExit, match="vb-audio.com"):
        app.preflight(SimpleNamespace(simulate=False), settings())


def test_a_voice_that_does_not_load_says_why_and_stops(capsys):
    events, said = [], []
    run = SimpleNamespace(emit=lambda ev, **f: events.append((ev, f)), ui=SimpleNamespace(mine=said.append),
                          stop=asyncio.Event(), voice_error=None)
    app.App.voice_failed(run, OSError("no route to huggingface.co.\nCheck your connection"))
    line = capsys.readouterr().err.strip()
    assert run.stop.is_set() and "\n" not in line and "internet" in line and "huggingface.co" in line
    assert events == [("state", {"what": "fatal", "task": "voice",
                                 "error": "OSError: no route to huggingface.co. Check your connection"})]
    assert said and run.voice_error


def test_a_mic_that_is_not_here_is_named(monkeypatch):
    monkeypatch.setattr(config, "require_api_key", lambda: "key")
    monkeypatch.setattr(devices, "candidates", lambda name, kind, *a: [] if kind == "input" else ["cable"])
    with pytest.raises(SystemExit, match="RTT_MIC_NAME='Realtek'"):
        app.preflight(SimpleNamespace(simulate=False), settings(mic_name="Realtek"))


def test_simulating_needs_no_key(monkeypatch):
    def no_key():
        raise AssertionError("simulate must not ask for a key")

    monkeypatch.setattr(config, "require_api_key", no_key)
    monkeypatch.setattr(devices, "candidates", lambda *a: ["found"])
    app.preflight(SimpleNamespace(simulate=True), settings())


def test_no_name_means_the_windows_default_mic(sound):
    sound("Headset Mic", "Webcam Mic", "Headset Mic")
    assert devices.find("", "input", ("CABLE",)).name == "Headset Mic"


def test_a_name_still_wins_over_the_default(sound):
    sound("Headset Mic", "Webcam Mic", "Headset Mic")
    assert devices.find("Webcam", "input", ("CABLE",)).name == "Webcam Mic"


def test_the_default_keeps_what_mme_keeps_of_a_long_name(sound):
    sound(LONG_NAME)
    assert devices.default_mic() == LONG_NAME[:31]


def test_the_cable_is_never_the_default_mic(sound):
    sound("CABLE Output (VB-Audio Virtual Cable)", "CABLE Output (VB-Audio Virtual Cable)", "Laptop Mic")
    assert devices.find("", "input", ("CABLE",)).name == "Laptop Mic"


def test_before_the_button_it_says_which_camera_to_pick_and_what_each_costs():
    dub = " ".join(app.camera_guide(7.0, 7.0, camera=True))
    assert "<b>Camera for this call: OBS Virtual Camera.</b>" in dub and "7 s late" in dub and "RTT_DUB=0" in dub
    live = " ".join(app.camera_guide(0.0, 7.0, camera=False))
    assert "<b>Camera for this call: your regular camera.</b>" in live and f"about {app.LIPS_LEAD_S} s" in live
    assert "RTT_DUB=1" in live
    broken = " ".join(app.camera_guide(7.0, 7.0, camera=False))
    assert "did not start" in broken and "lips will move 7 s before" in broken
    for lines in (app.camera_guide(7.0, 7.0, True), app.camera_guide(0.0, 7.0, False), app.camera_guide(7.0, 7.0, False)):
        assert lines[-1] == app.READY_EN

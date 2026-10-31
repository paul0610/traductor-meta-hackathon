import os
from types import SimpleNamespace

from interprete.audio import device_restore


class Control:
    def __init__(self, pid, volume):
        self.pid = pid
        self.volume = volume

    def QueryInterface(self, _):
        return self


class Sessions:
    def __init__(self, controls):
        self.controls = controls

    def GetCount(self):
        return len(self.controls)

    def GetSession(self, i):
        return self.controls[i]


def session(control):
    return SimpleNamespace(ProcessId=control.pid,
                           SimpleAudioVolume=SimpleNamespace(GetMasterVolume=lambda: control.volume))


def device(name, controls):
    return SimpleNamespace(FriendlyName=name,
                           AudioSessionManager=SimpleNamespace(GetSessionEnumerator=lambda: Sessions(controls)))


def with_devices(monkeypatch, devices):
    monkeypatch.setattr(device_restore.AudioUtilities, "GetAllDevices", lambda *a: devices)
    monkeypatch.setattr(device_restore, "AudioSession", session)


def test_it_reads_this_process_not_the_others_on_the_cable(monkeypatch):
    with_devices(monkeypatch, [device("Altavoces", [Control(os.getpid(), 1.0)]),
                               device("CABLE Input (VB-Audio)", [Control(1, 1.0), Control(os.getpid(), 0.03)])])
    assert device_restore.app_volume("CABLE Input") == 0.03


def test_no_stream_open_there_is_no_reading(monkeypatch):
    with_devices(monkeypatch, [device("CABLE Input (VB-Audio)", [Control(1, 1.0)])])
    assert device_restore.app_volume("CABLE Input") is None

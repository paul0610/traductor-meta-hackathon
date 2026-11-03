import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from interprete.setup import assistant, tools
from interprete.setup.tools import Tools, read_env, write_env
from interprete.video import virtual_cam

KEYS = ("MODEL_API_KEY", "RTT_DUB", "RTT_CAMERA_NAME", "RTT_DELAY_S")


@pytest.fixture(autouse=True)
def own_environment(monkeypatch):
    """write_env also sets the process environment: these tests must not leave a key or a dub behind."""
    for k in KEYS:
        monkeypatch.setenv(k, "")
        monkeypatch.delenv(k)


class Person(tools.Console):
    """Answers from a script, and keeps what was said to them and what was opened."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.said = []
        self.opened = []

    def say(self, text=""):
        self.said.append(text)

    def ask(self, question):
        self.said.append(question)
        return self.answers.pop(0) if self.answers else ""

    def secret(self, question):
        return self.ask(question)

    def open(self, target):
        self.opened.append(target)


def test_env_changes_only_those_lines_and_starts_from_the_example(tmp_path):
    example = tmp_path / ".env.example"
    example.write_text("# a comment that says MODEL_API_KEY=x\nMODEL_API_KEY=\nRTT_DUB=0\n", encoding="utf-8")
    env = tmp_path / ".env"
    write_env({"MODEL_API_KEY": "k1", "RTT_CAMERA_NAME": "HD Cam"}, env, example)
    assert not env.read_bytes().startswith(b"\xef\xbb\xbf")
    assert env.read_text(encoding="utf-8").splitlines() == [
        "# a comment that says MODEL_API_KEY=x", "MODEL_API_KEY=k1", "RTT_DUB=0", "RTT_CAMERA_NAME=HD Cam"]
    write_env({"RTT_DUB": "1"}, env, example)
    assert read_env(env) == {"MODEL_API_KEY": "k1", "RTT_DUB": "1", "RTT_CAMERA_NAME": "HD Cam"}
    assert tools.os.environ["RTT_DUB"] == "1"                 # a check started now reads the new value


def test_only_the_listed_tools_can_be_called_and_bad_arguments_come_back_as_errors():
    person = Person()
    t = Tools(person)
    assert "error" in t.call("call")
    assert "error" in t.call("__init__")
    assert "error" in t.call("open_settings", "not json")
    assert "error" in t.call("open_settings", '["sound"]')
    assert "error" in t.call("open_settings", '{"pagee": "sound"}')
    assert "error" in t.call("open_settings", '{"page": "registry"}')
    assert person.opened == []
    assert t.call("open_settings", '{"page": "sound"}') == {"opened": "sound"}
    assert person.opened == ["ms-settings:sound"]
    assert sorted(x["function"]["name"] for x in assistant.TOOLS) == sorted(tools.NAMES)


def test_a_denied_switch_for_desktop_apps_blocks_the_microphone(monkeypatch):
    import winreg

    values = {rf"{tools.CONSENT}\microphone": "Allow", rf"{tools.CONSENT}\microphone\NonPackaged": "Deny"}

    class Key:
        def __init__(self, hive, sub):
            if hive == winreg.HKEY_LOCAL_MACHINE or sub not in values:
                raise OSError("missing")
            self.sub = sub

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(winreg, "OpenKey", Key)
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (values[key.sub], 1))
    assert tools.consent("microphone") == {"allowed": False, "denied": ["desktop apps"]}
    values[rf"{tools.CONSENT}\microphone\NonPackaged"] = "Allow"
    assert tools.consent("microphone") == {"allowed": True, "denied": []}     # the device's own switch is missing


def test_vbcable_is_installed_by_the_person_from_its_page_and_then_checked(monkeypatch):
    seen = iter([[], [], ["CABLE Input (VB-Audio Virtual Cable)", "CABLE Output (VB-Audio Virtual Cable)"]])
    monkeypatch.setattr(tools, "cable_endpoints", lambda: next(seen))
    monkeypatch.setattr(tools.time, "sleep", lambda s: None)
    person = Person("", "")                                   # open the page: yes; Enter when done
    t = Tools(person)
    r = t.vbcable_install()
    assert r["installed"] and t.restart
    assert person.opened == [tools.VBCABLE_PAGE]


def test_vbcable_is_left_alone_when_the_person_says_no(monkeypatch):
    monkeypatch.setattr(tools, "cable_endpoints", lambda: [])
    person = Person("n")
    r = Tools(person).vbcable_install()
    assert not r["installed"] and person.opened == []


def test_the_dub_takes_the_webcam_the_person_picks_never_obs(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(virtual_cam, "dshow_cameras",
                        lambda: ["USB2.0 IR UVC WebCam", "OBS Virtual Camera", "USB2.0 HD UVC WebCam"])
    env = tmp_path / ".env"
    env.write_text("RTT_DUB=0\nRTT_DELAY_S=7.0\n", encoding="utf-8")
    person = Person("2", "")
    r = Tools(person, env).dub_camera()
    assert r == {"on": True, "camera": "USB2.0 HD UVC WebCam"}
    assert read_env(env)["RTT_DUB"] == "1" and read_env(env)["RTT_CAMERA_NAME"] == "USB2.0 HD UVC WebCam"
    assert "  3. OBS Virtual Camera" not in person.said


def test_the_key_is_asked_until_one_works_and_never_shown(monkeypatch, tmp_path):
    async def works(key):
        return (key == "good-key"), ("Hi, thanks for coming." if key == "good-key" else "401 unauthorized")

    monkeypatch.setattr(tools, "key_works", works)
    env = tmp_path / ".env"
    env.write_text("MODEL_API_KEY=old-key\nRTT_DUB=0\n", encoding="utf-8")
    person = Person("typo", "good-key")
    assert asyncio.run(tools.ensure_key(person, env)) == "good-key"
    assert read_env(env) == {"MODEL_API_KEY": "good-key", "RTT_DUB": "0"}
    assert not any("good-key" in line or "old-key" in line or "typo" in line for line in person.said)


def reply(content=None, *calls):
    tool_calls = [SimpleNamespace(id=f"call{i}", function=SimpleNamespace(name=name, arguments=args),
                                  model_dump=lambda i=i, name=name, args=args: {
                                      "id": f"call{i}", "type": "function", "function": {"name": name, "arguments": args}})
                  for i, (name, args) in enumerate(calls)]
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls or None))])


class Spark:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **request):
        self.requests.append(json.loads(json.dumps(request["messages"])))
        return self.replies.pop(0)


class Quiet(Tools):
    """Tools that touch nothing."""

    def vbcable_status(self):
        return {"installed": True, "found": ["CABLE Input", "CABLE Output"]}

    def microphone_access(self):
        return {"allowed": True, "denied": []}

    def default_output(self):
        return {"name": "Headphones", "is_cable": False, "looks_like_headphones": True}

    def dub_status(self):
        return {"on": False, "camera": "", "delay_s": 7.0}

    def shortcut(self):
        return {"created": False, "detail": "already on the desktop"}

    def run_check(self):
        return {"ready": True, "fails": [], "warnings": []}


def test_spark_drives_the_tools_is_reminded_once_and_ends_with_its_summary():
    spark = Spark(reply("First, VB-CABLE.", ("vbcable_status", "{}")),
                  reply("VB-CABLE is there."),
                  reply(None, ("run_check", "{}")),
                  reply(None, ("finish", '{"summary": "Ready for a call."}')))
    person = Person()
    t = Quiet(person)
    assert asyncio.run(assistant.drive(spark, t, person))
    assert list(t.done) == ["vbcable_status", "run_check", "finish"]
    assert t.summary == "Ready for a call."
    assert spark.requests[2][-1] == {"role": "user", "content": assistant.NUDGE}
    assert json.loads(spark.requests[1][-1]["content"]) == t.done["vbcable_status"]
    assert "\nSpark: First, VB-CABLE." in person.said


def test_two_turns_without_tools_before_the_check_hand_over_to_the_fixed_steps():
    spark = Spark(reply("Let me think."), reply("Still thinking."))
    assert not asyncio.run(assistant.drive(spark, Quiet(Person()), Person()))


def test_after_the_check_spark_is_asked_once_for_its_summary_and_then_the_run_ends():
    spark = Spark(reply(None, ("nonsense_tool", "{}")), reply(None, ("run_check", "{}")), reply("Wrapping up."),
                  reply("All set: pick CABLE Output in the call."))
    t = Quiet(Person())
    assert asyncio.run(assistant.drive(spark, t, Person()))
    assert "error" in json.loads(spark.requests[1][-1]["content"])          # the unknown tool was answered, not run
    assert spark.requests[3][-1] == {"role": "user", "content": assistant.FINISH}


def test_the_fixed_steps_check_everything_and_the_dub_waits_for_a_yes():
    person = Person("")                                        # the dub question: Enter means no
    t = Quiet(person)
    assistant.fixed_steps(t, person)
    assert list(t.done) == ["vbcable_status", "microphone_access", "default_output", "dub_status", "shortcut", "run_check"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shortcuts")
def test_the_shortcut_starts_the_launcher_in_its_folder(tmp_path):
    import comtypes.client

    shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
    path = tmp_path / "Interprete.lnk"
    tools.make_shortcut(shell, path)
    link = shell.CreateShortcut(str(path))
    assert Path(link.TargetPath) == tools.LAUNCHER
    assert Path(link.WorkingDirectory) == tools.LAUNCHER.parent

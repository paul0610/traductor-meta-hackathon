"""What the setup assistant can do on this PC: a short fixed list of tools, each one plain code.

Muse Spark picks the next tool and tells the person why; the tools do the work and report back in
a few words. Anything that changes the PC asks the person in the console first, so the model can
only propose. Only the names in `NAMES` can be called. VB-CABLE's license does not allow putting
its package inside another installer (readme.txt in the official pack), so its tool opens the
author's page, waits, and checks the result itself.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .. import config
from ..audio import devices
from ..audio.room import speakers_mode

ENV = config.ROOT / ".env"
EXAMPLE = config.ROOT / ".env.example"
LAUNCHER = config.ROOT / "Interprete.bat"
SHORTCUT = "Interprete ES-EN.lnk"
KEY_PAGE = "https://dev.meta.ai"
VBCABLE_PAGE = "https://vb-audio.com/Cable/"
SETTINGS = {"microphone": "ms-settings:privacy-microphone", "camera": "ms-settings:privacy-webcam",
            "sound": "ms-settings:sound"}
CONSENT = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"
WINGET_LINKS = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links"
OBS_EXE = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "obs-studio" / "bin" / "64bit" / "obs64.exe"
NAMES = ("vbcable_status", "vbcable_install", "microphone_access", "camera_access", "default_output", "open_settings",
         "ask_person", "dub_status", "dub_install", "dub_camera", "shortcut", "run_check", "finish")
CABLE_WAIT_S = 10              # VB-CABLE's endpoints show up a few seconds after its installer ends


class Console:
    """The person at the keyboard. Tests swap it for scripted answers."""

    def say(self, text: str = "") -> None:
        print(text, flush=True)

    def ask(self, question: str) -> str:
        return input(f"{question} ").strip()

    def secret(self, question: str) -> str:
        return getpass.getpass(f"{question} ").strip()

    def confirm(self, question: str, default: bool = True) -> bool:
        answer = self.ask(f"{question} {'[Y/n]' if default else '[y/N]'}").lower()
        return default if not answer else answer.startswith(("y", "s"))     # "sí" counts too

    def open(self, target: str) -> None:
        os.startfile(target)


def short(exc: BaseException) -> str:
    """An exception in one line, for the person and for Spark."""
    return re.sub(r"\s+", " ", str(exc) or type(exc).__name__)[:240]


def brief(result: dict) -> str:
    text = json.dumps(result, ensure_ascii=False)
    return text if len(text) <= 200 else text[:199] + "…"


def read_env(path: Path = ENV) -> dict[str, str]:
    if not path.exists():
        return {}
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    pairs = (line.split("=", 1) for line in lines if "=" in line and not line.lstrip().startswith("#"))
    return {k.strip(): v.strip() for k, v in pairs}


def write_env(values: dict[str, str], path: Path = ENV, example: Path = EXAMPLE) -> None:
    """Only these lines change, and the first time it starts from .env.example. UTF-8 without a BOM.

    The process sees them too, so a check started from here reads the new values: config loaded the
    old ones into the environment at import, and they win over the file.
    """
    lines = (path if path.exists() else example).read_text(encoding="utf-8-sig").splitlines()
    for key, value in values.items():
        at = next((i for i, line in enumerate(lines)
                   if not line.lstrip().startswith("#") and line.split("=", 1)[0].strip() == key), None)
        if at is None:
            lines.append(f"{key}={value}")
        else:
            lines[at] = f"{key}={value}"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ.update(values)


async def key_works(key: str) -> tuple[bool, str]:
    """A real translation with Muse Spark: the key, the network and the model in one go."""
    from ..mt.spark import Spark

    spark = Spark(key, timeout_s=20.0)
    try:
        r = await spark.translate("Hola, gracias por venir.")
    except Exception as exc:
        return False, short(exc)
    finally:
        await spark.client.close()                  # before asyncio.run closes the loop under it
    return True, r.translation


async def ensure_key(console: Console, path: Path = ENV) -> str | None:
    """First, because the assistant itself runs on Muse Spark. The key never reaches the screen."""
    key = read_env(path).get("MODEL_API_KEY", "")
    if key:
        ok, said = await key_works(key)
        if ok:
            console.say(f'Your Meta API key in .env works: Muse Spark translated a test sentence as "{said}"')
            return key
        console.say(f"The key in .env did not work: {said}")
    console.say(f"The interpreter runs on Meta's Model API. Get a key at {KEY_PAGE}")
    for _ in range(3):
        key = console.secret("Paste your Meta API key and press Enter (it does not show as you paste):")
        if not key:
            break
        ok, said = await key_works(key)
        if ok:
            write_env({"MODEL_API_KEY": key}, path)
            console.say(f'It works: Muse Spark translated a test sentence as "{said}". Saved in .env, on this PC only.')
            return key
        console.say(f"That key did not work: {said}")
    console.say("Without a working key the setup stops here. Run Install.bat again when you have one.")
    return None


def cable_endpoints() -> list[str]:
    """Live from Core Audio: a cable installed a minute ago shows up, unlike PortAudio's list made at start."""
    from pycaw.constants import AudioDeviceState
    from pycaw.utils import AudioUtilities

    return sorted({d.FriendlyName for d in AudioUtilities.GetAllDevices()
                   if d.FriendlyName and "VB-Audio" in d.FriendlyName and d.state == AudioDeviceState.Active})


def consent(capability: str) -> dict:
    """Windows' privacy switches for a device: the device's own, apps', and desktop apps'. Missing means allowed."""
    import winreg

    places = {"device": (winreg.HKEY_LOCAL_MACHINE, capability), "apps": (winreg.HKEY_CURRENT_USER, capability),
              "desktop apps": (winreg.HKEY_CURRENT_USER, rf"{capability}\NonPackaged")}
    denied = []
    for what, (hive, sub) in places.items():
        try:
            with winreg.OpenKey(hive, rf"{CONSENT}\{sub}") as key:
                if winreg.QueryValueEx(key, "Value")[0] == "Deny":
                    denied.append(what)
        except OSError:
            pass
    return {"allowed": not denied, "denied": denied}


def find_ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found is None and (WINGET_LINKS / "ffmpeg.exe").exists():
        os.environ["PATH"] = f"{WINGET_LINKS}{os.pathsep}{os.environ.get('PATH', '')}"     # winget just put it there
        found = shutil.which("ffmpeg")
    return found


def obs_running() -> bool:
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq obs64.exe", "/NH"], capture_output=True, text=True,
                       errors="replace")
    return "obs64.exe" in r.stdout.lower()


def virtual_camera_ready() -> bool:
    """Opens OBS's virtual camera driver for a moment, as the dub would. No webcam is touched."""
    try:
        from ..video.virtual_cam import open_virtual_camera

        open_virtual_camera().close()
        return True
    except (ImportError, OSError, RuntimeError):
        return False


def make_shortcut(shell, path: Path, target: Path = LAUNCHER) -> None:
    link = shell.CreateShortcut(str(path))
    link.TargetPath = str(target)
    link.WorkingDirectory = str(target.parent)
    link.Description = "Spanish-English interpreter for video calls"
    link.Save()


class Tools:
    """The assistant's hands. Each tool returns a small dict for Spark, and `done` keeps the last result of each."""

    def __init__(self, console: Console, env: Path = ENV) -> None:
        self.console = console
        self.env = env
        self.done: dict[str, dict] = {}
        self.restart = False            # VB-CABLE was installed now: Windows wants a restart before the first call
        self.summary = ""

    def call(self, name: str, raw: str | None = None) -> dict:
        """One tool by name, as Spark asked for it. Unknown names and bad arguments come back as errors."""
        if name not in NAMES:
            return {"error": f"there is no tool named {name!r}"}
        try:
            args = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {"error": "the arguments are not JSON"}
        if not isinstance(args, dict):
            return {"error": "the arguments must be a JSON object"}
        shown = ", ".join(f"{k}={v!r}" if len(repr(v)) <= 50 else f"{k}={repr(v)[:49]}…" for k, v in args.items())
        self.console.say(f"  -> {name}({shown})")
        try:
            result = getattr(self, name)(**args)
        except TypeError as exc:
            result = {"error": f"bad arguments: {short(exc)}"}
        except Exception as exc:
            result = {"error": short(exc)}
        self.console.say(f"  <- {brief(result)}")
        self.done[name] = result
        return result

    def vbcable_status(self) -> dict:
        names = cable_endpoints()
        both = any(n.startswith("CABLE Input") for n in names) and any(n.startswith("CABLE Output") for n in names)
        return {"installed": both, "found": names}

    def vbcable_install(self) -> dict:
        if self.vbcable_status()["installed"]:
            return {"installed": True, "detail": "already installed"}
        c = self.console
        c.say("VB-CABLE is free, from its author's page. Its license does not let other installers bundle it, so:")
        c.say("  1. On the page, download the driver pack (VBCABLE_Driver_Pack45.zip) and unzip it.")
        c.say("  2. Right-click VBCABLE_Setup_x64.exe, choose Run as administrator, and click Install Driver.")
        c.say("  3. When it says the installation is complete, come back here.")
        if not c.confirm("Open the VB-CABLE page in your browser?"):
            return {"installed": False, "detail": "the person chose not to install it now"}
        c.open(VBCABLE_PAGE)
        c.ask("Press Enter here when VB-CABLE's installer has finished.")
        for _ in range(CABLE_WAIT_S):
            if self.vbcable_status()["installed"]:
                self.restart = True
                return {"installed": True, "detail": "CABLE Input and CABLE Output are there; "
                                                     "VB-CABLE asks for a restart of Windows before the first call"}
            time.sleep(1)
        return {"installed": False, "detail": "not visible yet: restart Windows and run Install.bat again"}

    def microphone_access(self) -> dict:
        return consent("microphone")

    def camera_access(self) -> dict:
        return consent("webcam")

    def default_output(self) -> dict:
        from pycaw.utils import AudioUtilities

        name = AudioUtilities.GetSpeakers().FriendlyName or ""
        turns = speakers_mode(config.load().speakers, name)
        return {"name": name, "is_cable": "CABLE" in name.upper(), "looks_like_headphones": devices.looks_like_headphones(name),
                "call_goes_in_turns": turns,
                "note": "speakers: the mic waits while the PC plays; RTT_SPEAKERS=0 if these are headphones" if turns
                else "headphones: the person can talk over the other side"}

    def open_settings(self, page: str) -> dict:
        if page not in SETTINGS:
            return {"error": f"no settings page {page!r}; use one of {sorted(SETTINGS)}"}
        self.console.open(SETTINGS[page])
        self.console.ask("Press Enter here when you are done in Settings.")
        return {"opened": page}

    def ask_person(self, question: str) -> dict:
        self.console.say(question)
        return {"answer": self.console.ask(">")}

    def setting(self, name: str, default: str) -> str:
        """As the app will see it: the environment first, then .env."""
        return os.environ.get(name, read_env(self.env).get(name, default))

    def dub_status(self) -> dict:
        return {"on": self.setting("RTT_DUB", "0") == "1", "camera": self.setting("RTT_CAMERA_NAME", "HD UVC"),
                "delay_s": float(self.setting("RTT_DELAY_S", "7.0")),
                "obs_installed": OBS_EXE.exists(), "obs_running": obs_running(),
                "obs_virtual_camera": virtual_camera_ready(), "ffmpeg": find_ffmpeg() is not None}

    def dub_install(self) -> dict:
        status = self.dub_status()
        missing = [(name, package) for name, package, ok in (("OBS Studio", "OBSProject.OBSStudio", status["obs_installed"]),
                                                              ("FFmpeg", "Gyan.FFmpeg", status["ffmpeg"])) if not ok]
        if not missing:
            return {"installed": True, "detail": "OBS Studio and FFmpeg are already installed"}
        if shutil.which("winget") is None:
            return {"installed": False, "detail": "winget is missing: install OBS Studio and FFmpeg by hand, as in the README"}
        names = " and ".join(name for name, _ in missing)
        if not self.console.confirm(f"Install {names} with winget? OBS Studio asks for administrator rights; "
                                    "only its virtual camera driver is used, and OBS stays closed."):
            return {"installed": False, "detail": "the person chose not to install them"}
        failed = []
        for name, package in missing:
            self.console.say(f"Installing {name}...")
            r = subprocess.run(["winget", "install", "--id", package, "-e", "--accept-source-agreements",
                                "--accept-package-agreements"])
            if r.returncode:
                failed.append(f"{name}: winget exit code {r.returncode}")
        status = self.dub_status()
        return {"installed": status["obs_installed"] and status["ffmpeg"], "failed": failed,
                "obs_virtual_camera": status["obs_virtual_camera"], "obs_running": status["obs_running"]}

    def dub_camera(self) -> dict:
        from ..video.virtual_cam import dshow_cameras

        if find_ffmpeg() is None:
            return {"on": False, "detail": "FFmpeg is missing: dub_install comes first"}
        try:
            names = [n for n in dshow_cameras() if "OBS" not in n]
        except RuntimeError as exc:
            return {"on": False, "detail": short(exc)}
        if not names:
            return {"on": False, "detail": "no webcam found"}
        c = self.console
        pick = names[0]
        if len(names) > 1:
            c.say("Cameras on this PC (laptops often add an infrared one: pick the other):")
            for i, name in enumerate(names, 1):
                c.say(f"  {i}. {name}")
            answer = c.ask("Number of your webcam:")
            if not answer.isdigit() or not 1 <= int(answer) <= len(names):
                return {"on": False, "detail": f"no camera picked ({answer!r})"}
            pick = names[int(answer) - 1]
        delay = float(self.setting("RTT_DELAY_S", "7.0"))
        if not c.confirm(f'Turn on the dub with "{pick}"? Your video and voice will reach the call {delay:g} s late, '
                         "so the English matches your lips."):
            return {"on": False, "detail": "the person chose not to turn it on"}
        write_env({"RTT_DUB": "1", "RTT_CAMERA_NAME": pick}, self.env)
        return {"on": True, "camera": pick}

    def shortcut(self) -> dict:
        import comtypes.client

        shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
        path = Path(shell.SpecialFolders("Desktop")) / SHORTCUT
        if path.exists():
            return {"created": False, "detail": f"already on the desktop: {path.name}"}
        if not self.console.confirm("Put a shortcut to the interpreter on your desktop?"):
            return {"created": False, "detail": "the person chose not to"}
        make_shortcut(shell, path)
        return {"created": True, "path": str(path)}

    def run_check(self) -> dict:
        """scripts/warmcheck.py, line by line as it goes, so the person sees it work."""
        self.console.say("Running the pre-call check, scripts/warmcheck.py. The first time it downloads the voice, about 150 MB.")
        p = subprocess.Popen([sys.executable, str(config.ROOT / "scripts" / "warmcheck.py")], cwd=config.ROOT,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                             errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        lines = []
        for line in p.stdout:
            lines.append(line.rstrip())
            self.console.say(lines[-1])
        code = p.wait()
        result = {"ready": code == 0, "fails": [ln for ln in lines if ln.startswith("FAIL")],
                  "warnings": [ln for ln in lines if ln.startswith("WARN")]}
        if code not in (0, 1):
            result["error"] = f"the check stopped with exit code {code}"
        return result

    def finish(self, summary: str) -> dict:
        self.summary = summary
        return {"done": True}

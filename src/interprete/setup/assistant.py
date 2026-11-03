"""Muse Spark drives the setup through the tools in `tools.py`, and fixed steps take over if it cannot.

Spark sees each tool's result, decides the next one, tells the person what it is doing in a
sentence, and answers what they ask on the way. The API takes `tool_choice` "auto" only ("required"
got a 400 on Nov 2), so a turn can come back as text alone: Spark is reminded once to go on, or to
finish if the check already ran. Two such turns in a row before the check, too many turns or any
error, and `fixed_steps` does the rest; every tool skips what is already fine.
"""

from __future__ import annotations

import json

from .. import config
from .tools import Console, Tools

EFFORT = "low"                 # with "minimal" Spark described the first step instead of calling its tool, Nov 2
MAX_TURNS = 30
MAX_IDLE = 2                   # turns in a row without a tool call
NUDGE = "Go on with your next tool call. Call finish only after run_check."
FINISH = "Call finish now, with your summary."
SYSTEM = """You are the setup assistant of Interprete ES-EN, an interpreter for video calls on Windows 11: the person \
speaks Spanish and the other side hears English through a virtual microphone; the other side speaks English and the \
person reads Spanish subtitles. Get this PC ready for a first call, using only your tools.

Every call needs: VB-CABLE, the virtual microphone, installed; Windows letting desktop apps use the microphone; and a \
default output that is not CABLE Input. Speakers and headphones both work: on speakers the call goes in turns, the \
microphone waiting while the PC plays, and headphones let the person talk over the other side. Optional, only if the \
person wants it: the synchronized dub, which sends their video and voice 7 s late so the English matches their lips. \
It needs OBS Studio (only its virtual camera driver; OBS itself stays closed), FFmpeg, camera access and a webcam. Ask about the dub with ask_person, in one sentence with its cost. \
Offer the desktop shortcut. Always end with run_check, then finish.

Rules:
- Check before you change: call the read-only tool first, and skip what is already fine.
- Tools that change this PC ask the person themselves. If they decline, accept it and go on.
- Never say something is installed or ready unless a tool result says so.
- Every turn: one short plain sentence for the person, and the next tool call in that same turn. A turn without a \
tool call is only for the very end. Explain VB-CABLE, OBS or the dub in one sentence the first time they come up.
- Call run_check at most twice. Then call finish with a summary for the person: one sentence for each failure or \
warning with its fix, then what to pick in the call: microphone CABLE Output, and with the dub, camera OBS Virtual \
Camera.
- English only. Never ask for the API key: it is already set and working."""


def _tool(name: str, description: str, **properties: dict) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}}}


TOOLS = [
    _tool("vbcable_status", "Is VB-CABLE, the virtual microphone, installed? Read-only."),
    _tool("vbcable_install", "Show the person how to install VB-CABLE from its author's page, open the page if they "
                             "agree, wait for them, and check. Asks first."),
    _tool("microphone_access", "Do Windows' privacy settings let desktop apps use the microphone? Read-only."),
    _tool("camera_access", "Do Windows' privacy settings let desktop apps use the camera? Read-only. Only the dub needs it."),
    _tool("default_output", "Windows' default output device, and whether the call will go in turns on it. Read-only. "
                            "It must not be CABLE Input."),
    _tool("open_settings", "Open a Windows Settings page for the person to change something, and wait until they are done.",
          page={"type": "string", "enum": ["microphone", "camera", "sound"]}),
    _tool("ask_person", "Ask the person something and return their answer.", question={"type": "string"}),
    _tool("dub_status", "Is the synchronized dub on, with which camera and delay, and are OBS Studio, its virtual "
                        "camera and FFmpeg ready? Read-only."),
    _tool("dub_install", "Install OBS Studio and FFmpeg with winget, for the dub. Asks first."),
    _tool("dub_camera", "Let the person pick their webcam, and turn the dub on with it. Asks first."),
    _tool("shortcut", "Put a shortcut to the interpreter on the desktop. Asks first."),
    _tool("run_check", "Run the full pre-call check and return its failures and warnings."),
    _tool("finish", "End the setup with a short summary for the person.", summary={"type": "string"}),
]


async def drive(client, tools: Tools, console: Console, *, model: str = config.SPARK_MODEL) -> bool:
    """True when Spark got to the end; False hands the rest to `fixed_steps`. API errors propagate."""
    messages: list[dict] = [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": "Set up this PC for a first call."}]
    idle = 0
    for _ in range(MAX_TURNS):
        r = await client.chat.completions.create(model=model, messages=messages, tools=TOOLS, reasoning_effort=EFFORT)
        msg = r.choices[0].message
        if msg.content and msg.content.strip():
            console.say(f"\nSpark: {msg.content.strip()}")
        calls = msg.tool_calls or []
        if not calls:
            idle += 1
            if idle >= MAX_IDLE:
                return "run_check" in tools.done
            messages += [{"role": "assistant", "content": msg.content or ""},
                         {"role": "user", "content": FINISH if "run_check" in tools.done else NUDGE}]
            continue
        idle = 0
        messages.append({"role": "assistant", "content": msg.content, "tool_calls": [c.model_dump() for c in calls]})
        for c in calls:
            result = tools.call(c.function.name, c.function.arguments)
            messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(result, ensure_ascii=False)})
        if any(c.function.name == "finish" for c in calls):
            return True
    return False


def fixed_steps(tools: Tools, console: Console) -> None:
    """The same tools in a fixed order, for --no-spark or when Spark stops."""
    if not tools.call("vbcable_status").get("installed"):
        tools.call("vbcable_install")
    if not tools.call("microphone_access").get("allowed", True):
        tools.call("open_settings", '{"page": "microphone"}')
    if tools.call("default_output").get("is_cable"):
        tools.call("open_settings", '{"page": "sound"}')
    dub = tools.call("dub_status")
    missing = [name for name, ok in (("OBS Studio", dub.get("obs_installed")), ("FFmpeg", dub.get("ffmpeg"))) if not ok]
    if not dub.get("on") and "dub_camera" not in tools.done and console.confirm(
            f"Do you want the synchronized dub? Your video and voice reach the call {dub.get('delay_s', 7.0):g} s late, "
            "so the English matches your lips." + (f" It installs {' and '.join(missing)}." if missing else ""),
            default=False):
        tools.call("dub_install")
        if not tools.call("camera_access").get("allowed", True):
            tools.call("open_settings", '{"page": "camera"}')
        tools.call("dub_camera")
    tools.call("shortcut")
    tools.call("run_check")

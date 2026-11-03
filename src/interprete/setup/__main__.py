"""`uv run python -m interprete.setup`: the setup assistant, which Install.bat starts once the packages are in."""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys

from .. import config, utf8_output
from .assistant import drive, fixed_steps
from .tools import LAUNCHER, Console, Tools, ensure_key, short


async def spark_drives(key: str, tools: Tools, console: Console) -> bool:
    from openai import AsyncOpenAI

    async with AsyncOpenAI(base_url=config.API_BASE, api_key=key, timeout=60.0, max_retries=1) as client:
        return await drive(client, tools, console)


def main(argv: list[str] | None = None, console: Console | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m interprete.setup",
                                 description="Gets this PC ready for a first call. Anything that changes it asks first.")
    ap.add_argument("--no-spark", action="store_true", help="the same steps in a fixed order, without Muse Spark")
    args = ap.parse_args(argv)
    if console is None:                      # Spark's own words may carry any character
        utf8_output()
    console = console or Console()
    console.say("Setup assistant. " + ("Fixed steps, without Muse Spark." if args.no_spark else
                "Muse Spark picks each step and says why; the steps themselves are fixed code."))
    console.say("Anything that changes this PC asks you first. Ctrl-C stops it, and running it again picks up from there.\n")
    key = asyncio.run(ensure_key(console))
    if key is None:
        return 1
    tools = Tools(console)
    finished = False
    if not args.no_spark:
        try:
            finished = asyncio.run(spark_drives(key, tools, console))
        except Exception as exc:
            console.say(f"\nMuse Spark stopped answering: {short(exc)}")
        if not finished:
            console.say("\nThe fixed steps take it from here.")
    if not finished:
        fixed_steps(tools, console)
    console.say("")
    if tools.summary:
        console.say(tools.summary)
    ready = bool(tools.done.get("run_check", {}).get("ready"))
    if tools.restart:
        console.say("VB-CABLE was just installed: restart Windows, then start the interpreter from the desktop "
                    "shortcut or Interprete.bat.")
    elif ready:
        console.say("Ready. Start the interpreter from the desktop shortcut or Interprete.bat; the README's manual "
                    "says what to pick in the call.")
        if console.confirm("Start it now?"):
            subprocess.Popen(["cmd", "/c", "start", "", str(LAUNCHER)], cwd=config.ROOT)
    else:
        console.say("Not ready yet: fix what the check marked FAIL, then run Install.bat again. The README's "
                    "Troubleshooting table has each one.")
    return 0 if ready else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nSetup stopped. Run Install.bat again to pick up from there.")
        sys.exit(130)

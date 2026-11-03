"""OS-level simultaneous ES<->EN interpreter on Meta models."""

import sys


def utf8_output() -> None:
    """The console lines carry │ and →. A console takes them, but a pipe or a file (Git Bash, `> log.txt`) gets
    Windows' ANSI code page, where they do not exist, and the first print would kill the chain that made it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

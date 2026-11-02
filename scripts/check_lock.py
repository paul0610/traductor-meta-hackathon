"""The lock as a judge in another time zone would meet it: `uv sync --locked` must not ask for a new lock anywhere.

    uv run python scripts/check_lock.py

uv reads a date in `exclude-newer` in the machine's own time zone, so a lock can match here and fail in Tokyo.
This runs the same check `--locked` does, `uv lock --check`, offline, under several zones. Exit 1 if any fails.
"""

from __future__ import annotations

import os
import subprocess
import sys

ZONES = ("UTC", "Asia/Tokyo", "Europe/Madrid", "America/New_York", "America/Los_Angeles", "America/Lima")


def main() -> int:
    failed = []
    for zone in ZONES:
        r = subprocess.run(["uv", "lock", "--check", "--offline"], env={**os.environ, "TZ": zone},
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(f"{'OK   ' if r.returncode == 0 else 'FALLA'} {zone}")
        if r.returncode:
            failed.append(zone)
            print("      " + (r.stderr.strip().splitlines() or ["?"])[-1])
    print("the lock matches in every zone" if not failed else f"{len(failed)} zone(s) would fail `uv sync --locked`")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

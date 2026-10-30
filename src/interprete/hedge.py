"""Hedged request: if the first attempt fails or is slow, a second one races it and the first good answer wins."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


async def hedged(first_try: Callable[[], Awaitable[T]], second_try: Callable[[], Awaitable[T]], after_s: float,
                 on_hedge: Callable[[], None] | None = None) -> T:
    """Raises the last error if both fail; cancelling this cancels both."""
    first = asyncio.ensure_future(first_try())
    running: set[asyncio.Future] = {first}
    try:
        await asyncio.wait(running, timeout=after_s)
        if first.done() and first.exception() is None:
            return first.result()
        if on_hedge is not None:
            on_hedge()
        running = {f for f in running if not f.done()} | {asyncio.ensure_future(second_try())}
        error: BaseException | None = first.exception() if first.done() else None
        while running:
            done, running = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            for f in done:
                if f.exception() is None:
                    return f.result()
                error = f.exception()
        raise error
    finally:
        for f in running:
            f.cancel()

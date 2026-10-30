import asyncio

import pytest

from interprete.hedge import hedged


def attempts(*behaviours):
    """Each attempt sleeps, then returns or raises what its behaviour says."""
    it = iter(behaviours)
    started = []

    async def attempt():
        delay, result = next(it)
        started.append(result)
        await asyncio.sleep(delay)
        if isinstance(result, Exception):
            raise result
        return result

    return attempt, started


def run(attempt, after=0.02, seen=None):
    return asyncio.run(hedged(attempt, attempt, after, (lambda: seen.append(1)) if seen is not None else None))


def test_a_fast_answer_starts_no_second_attempt():
    attempt, started = attempts((0.0, "a"))
    assert run(attempt) == "a" and started == ["a"]


def test_a_stall_is_beaten_by_the_second_attempt():
    attempt, _ = attempts((5.0, "slow"), (0.0, "b"))
    seen = []
    assert run(attempt, seen=seen) == "b" and seen == [1]


def test_a_stall_that_fails_late_still_gets_the_second_answer():
    attempt, _ = attempts((0.05, RuntimeError("no audio")), (0.2, "b"))
    assert run(attempt) == "b"


def test_a_fast_failure_hedges_at_once():
    attempt, _ = attempts((0.0, RuntimeError("no audio")), (0.0, "b"))
    assert run(attempt, after=5.0) == "b"


def test_the_slow_first_can_still_win():
    attempt, _ = attempts((0.05, "a"), (5.0, "late"))
    assert run(attempt) == "a"


def test_both_failing_raises():
    attempt, _ = attempts((0.0, RuntimeError("one")), (0.0, ValueError("two")))
    with pytest.raises(ValueError):
        run(attempt)


def test_cancelling_cancels_both_attempts():
    cancelled = []

    async def attempt():
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.append(1)
            raise

    async def main():
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(hedged(attempt, attempt, 0.01), 0.05)

    asyncio.run(main())
    assert len(cancelled) == 2

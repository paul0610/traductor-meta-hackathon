import asyncio
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from interprete.ui.status import Health, Phase, health  # noqa: E402
from interprete.ui.widget import Button, Screen  # noqa: E402


@pytest.fixture(scope="module")
def qt():
    yield QApplication.instance() or QApplication([])


def test_no_clause_yet_has_no_health():
    assert health(None, 0.0, 6.0) is Health.NONE


@pytest.mark.parametrize("sync, level", [(0.3, Health.OK), (-0.4, Health.OK), (0.8, Health.WARN), (1.5, Health.BAD)])
def test_with_a_dub_the_ring_follows_the_sync(sync, level):
    assert health(9.0, sync, 6.0) is level


@pytest.mark.parametrize("lag, level", [(2.0, Health.OK), (5.0, Health.WARN), (7.0, Health.BAD)])
def test_without_a_dub_it_follows_the_lag(lag, level):
    assert health(lag, 0.0, 0.0) is level


def wired(qt):
    b = Button()
    calls = []
    b.on_start = lambda: calls.append("start")
    b.on_stop = lambda: calls.append("stop")
    return b, calls


def test_loading_ignores_a_left_click(qt):
    b, calls = wired(qt)
    b.clicked(True)
    assert calls == []


def test_ready_starts(qt):
    b, calls = wired(qt)
    b.set_phase(Phase.READY)
    b.clicked(True)
    assert calls == ["start"]


@pytest.mark.parametrize("phase", [Phase.LIVE, Phase.PAUSED, Phase.RAW])
def test_running_stops(qt, phase):
    b, calls = wired(qt)
    b.set_phase(phase)
    b.clicked(True)
    assert calls == ["stop"]


@pytest.mark.parametrize("phase", list(Phase))
def test_right_click_stops_from_anywhere(qt, phase):
    b, calls = wired(qt)
    b.set_phase(phase)
    b.clicked(False)
    assert calls == ["stop"]


def test_the_number_is_the_lag(qt):
    b, _ = wired(qt)
    b.set_phase(Phase.LIVE)
    b.set_vitals(2.34, Health.OK)
    assert b._centre() == "2.3"


@pytest.mark.parametrize("phase", list(Phase))
def test_every_phase_paints(qt, phase):
    b, _ = wired(qt)
    b.set_phase(phase)
    assert not b.grab().isNull()


def test_the_button_releases_go(qt):
    screen = Screen()

    async def go():
        stop = asyncio.Event()
        task = asyncio.create_task(screen.go(stop))
        await asyncio.sleep(0)
        screen.button.set_phase(Phase.READY)
        screen.button.clicked(True)
        await asyncio.wait_for(task, 1.0)
        assert not stop.is_set()

    asyncio.run(go())


def test_stopping_from_ready_releases_go(qt):
    screen = Screen()

    async def go():
        stop = asyncio.Event()
        task = asyncio.create_task(screen.go(stop))
        await asyncio.sleep(0)
        screen.button.clicked(False)
        await asyncio.wait_for(task, 1.0)
        assert stop.is_set()

    asyncio.run(go())

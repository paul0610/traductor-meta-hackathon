import asyncio
import os
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from interprete import app  # noqa: E402
from interprete.app import Outgoing  # noqa: E402
from interprete.tts.own_voice import OFFER, Prompt  # noqa: E402
from interprete.ui import widget  # noqa: E402
from interprete.ui import sounds  # noqa: E402
from interprete.ui.overlay import Overlay  # noqa: E402
from interprete.ui.status import Health, Phase, health, metrics_line, running, sound_for  # noqa: E402
from interprete.ui.widget import Button, MetricsLine, Screen, VoiceCard  # noqa: E402


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


@pytest.mark.parametrize("phase", widget.RUNNING)
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
        screen.on_stop(stop.set)
        task = asyncio.create_task(screen.go(stop))
        await asyncio.sleep(0)
        screen.button.clicked(False)
        await asyncio.wait_for(task, 1.0)
        assert stop.is_set()

    asyncio.run(go())


class Steps:
    """Stands in for the enrollment: the card only needs to be told what to show and where clicks go."""

    def __init__(self, prompt=OFFER):
        self.prompt = prompt
        self.on_prompt = lambda p: None
        self.acted = []

    @property
    def busy(self):
        return self.prompt is not None and self.prompt.busy

    def opening(self):
        self.on_prompt(self.prompt)

    async def act(self, key):
        self.acted.append(key)


def test_the_offer_shows_in_ready_and_goes_when_the_call_starts(qt):
    screen = Screen()

    async def go():
        stop = asyncio.Event()
        screen.offer(Steps())
        assert screen.card.isVisible()
        task = asyncio.create_task(screen.go(stop))
        await asyncio.sleep(0)
        screen.button.set_phase(Phase.READY)
        screen.button.clicked(True)
        await asyncio.wait_for(task, 1.0)
        assert not screen.card.isVisible()

    asyncio.run(go())


def test_the_button_waits_for_a_voice_being_recorded(qt):
    screen = Screen()
    steps = Steps(Prompt("Grabando", "", busy=True))

    async def go():
        stop = asyncio.Event()
        screen.offer(steps)
        task = asyncio.create_task(screen.go(stop))
        await asyncio.sleep(0)
        screen.button.set_phase(Phase.READY)
        screen.button.clicked(True)
        await asyncio.sleep(0.01)
        assert not task.done()
        steps.prompt = None
        screen.button.clicked(True)
        await asyncio.wait_for(task, 1.0)

    asyncio.run(go())


def test_a_card_button_reaches_the_step_it_names(qt):
    screen = Screen()
    steps = Steps()

    async def go():
        screen.offer(steps)
        buttons = screen.card.findChildren(widget.QPushButton)
        assert [b.text() for b in buttons] == [label for label, _ in OFFER.actions]
        buttons[0].click()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(go())
    assert steps.acted == ["script"]


def test_the_timer_keeps_the_buttons_it_already_drew(qt):
    card = VoiceCard()
    card.show_prompt(Prompt("Grabando · 1 s", "", (("Terminar", "finish"),), busy=True))
    first = card.findChildren(widget.QPushButton)[0]
    card.show_prompt(Prompt("Grabando · 2 s", "", (("Terminar", "finish"),), busy=True))
    assert card.findChildren(widget.QPushButton)[0] is first


def noticing(cloned, delay_s):
    sent = []
    return SimpleNamespace(policy=SimpleNamespace(take_seq=lambda: 7), mixer=SimpleNamespace(latched=False), delay_s=delay_s,
                           cloned=cloned, submit_commit=sent.append, emit=lambda *a, **k: None), sent


@pytest.mark.parametrize("delay_s", [0.0, 7.0])
def test_the_notice_says_when_the_voice_is_a_copy(delay_s):
    out, sent = noticing(True, delay_s)
    Outgoing.send_notice(out)
    assert app.NOTICE_OWN_EN in sent[0].text_tgt


@pytest.mark.parametrize("delay_s", [0.0, 7.0])
def test_with_a_catalog_voice_the_notice_is_the_usual_one(delay_s):
    out, sent = noticing(False, delay_s)
    Outgoing.send_notice(out)
    assert "copy" not in sent[0].text_tgt and "{own}" not in sent[0].text_tgt


@pytest.mark.parametrize("before, after, name", [
    (Phase.LOADING, Phase.READY, "ready"),
    (Phase.STARTING, Phase.LIVE, "live"),
    (Phase.LIVE, Phase.MUTED, "mute"),
    (Phase.MUTED, Phase.LIVE, "back"),
    (Phase.LIVE, Phase.RAW, "raw"),
    (Phase.RAW, Phase.LIVE, "back"),
    (Phase.LIVE, Phase.PAUSED, "floor"),
    (Phase.LIVE, Phase.STOPPING, "stop"),
])
def test_each_change_has_its_sound(before, after, name):
    assert sound_for(before, after) == name


@pytest.mark.parametrize("before, after", [(Phase.LIVE, Phase.LIVE), (Phase.PAUSED, Phase.LIVE), (Phase.READY, Phase.STARTING),
                                           (Phase.STOPPING, Phase.OFF)])
def test_the_rest_is_silent(before, after):
    assert sound_for(before, after) is None


def test_the_screen_plays_a_change_once(qt):
    played = []
    screen = Screen(sounds=SimpleNamespace(play=played.append))
    screen.phase(Phase.READY)
    screen.phase(Phase.READY)
    assert played == ["ready"]


def test_with_sounds_off_the_screen_is_silent(qt):
    Screen().phase(Phase.READY)


def test_every_cue_is_short_soft_and_does_not_click():
    for name, notes in sounds.CUES.items():
        x = sounds.cue(notes)
        assert len(x) / sounds.RATE < 0.7, name
        assert abs(float(abs(x).max()) - sounds.PEAK) < 1e-6, name
        assert abs(x[0]) < 0.01 and abs(x[-1]) < 0.02, name


def test_cues_reach_the_player_as_wav():
    sent = []
    s = sounds.Sounds(player=sent.append)
    s.play("live")
    for _ in range(100):
        if sent:
            break
        time.sleep(0.01)
    assert sent[0][:4] == b"RIFF"


def band(qt):
    return Overlay()


def test_the_band_takes_only_the_room_its_text_needs(qt):
    o = band(qt)
    o.mine("Cargando la voz…")
    small = o.height()
    o.their_final("Do you have experience with data?")
    o.their_translation("¿Tienes experiencia con datos? " * 8)
    assert o.height() > small
    assert o._rule.isVisibleTo(o)


def test_the_band_stays_on_the_same_line_as_it_grows(qt):
    o = band(qt)
    o.mine("uno")
    bottom = o.geometry().bottom()
    o.their_translation("texto largo " * 20)
    assert o.geometry().bottom() == bottom


def test_with_only_one_side_there_is_no_rule(qt):
    o = band(qt)
    o.mine("Hola")
    assert not o._rule.isVisibleTo(o)


def test_my_english_goes_beside_my_spanish_and_nothing_is_read_as_markup(qt):
    o = band(qt)
    o.mine("a <b>", "c & d")
    assert "a &lt;b&gt;" in o._mine.text() and "c &amp; d" in o._mine.text()
    o.their_translation("<i>tal cual</i>")
    assert o._tgt.textFormat() == widget.Qt.TextFormat.PlainText


def test_their_words_can_be_read_while_they_talk_over_me(qt):
    o = band(qt)
    o.collision(True)
    assert o._alert.isVisibleTo(o)
    o.collision(False)
    assert not o._alert.isVisibleTo(o)


def test_silence_clears_the_band_but_not_while_they_have_the_floor(qt):
    o = band(qt)
    o.their_translation("Hola")
    o.collision(True)
    o._go_away()
    assert o._fade.endValue() == 1.0
    o.collision(False)
    o._go_away()
    o._fade.setCurrentTime(0)
    o._fade.stop()
    o._faded()
    assert o._tgt.text() == "" and not o._tgt.isVisibleTo(o)


def test_right_click_works_while_loading(qt):
    screen = Screen()
    calls = []
    screen.on_stop(lambda: calls.append("stop"))
    assert screen.button.phase is Phase.LOADING
    screen.button.clicked(False)
    assert calls == ["stop"]


@pytest.mark.parametrize("phase", [Phase.LOADING, Phase.STOPPING, Phase.OFF])
def test_left_click_does_nothing_when_nothing_is_running(qt, phase):
    b, calls = wired(qt)
    b.set_phase(phase)
    b.clicked(True)
    assert calls == []


@pytest.mark.parametrize("flags, phase", [
    (dict(raw=True, muted=True, held=True, connected=True), Phase.RAW),
    (dict(raw=False, muted=True, held=True, connected=True), Phase.MUTED),
    (dict(raw=False, muted=False, held=True, connected=True), Phase.PAUSED),
    (dict(raw=False, muted=False, held=False, connected=False), Phase.STARTING),
    (dict(raw=False, muted=False, held=False, connected=True), Phase.LIVE),
])
def test_the_button_says_what_the_other_side_gets(flags, phase):
    assert running(**flags) is phase


def test_the_line_reads_as_the_plan_wrote_it():
    line = metrics_line(lags=[3.1, 2.4], sync_s=0.3, delay_s=7.0, late=False, corrections=3, theirs=True, cable="DUB",
                        minutes=12.4)
    assert line == "LAG 2.4s p95 3.1s │ SYNC +0.3s (D 7.0s) │ CORR 3 │ TURNO: ellos │ ES→EN DOBLAJE │ META 12 min"


def test_without_a_dub_there_is_no_sync():
    line = metrics_line(lags=[], sync_s=0.0, delay_s=0.0, late=False, corrections=0, theirs=False, cable="MUTE", minutes=0.0)
    assert line == "LAG -- │ CORR 0 │ TURNO: yo │ ES→EN MUDO │ META 0 min"


def test_three_late_clauses_propose_a_longer_delay():
    line = metrics_line(lags=[9.0], sync_s=1.2, delay_s=7.0, late=True, corrections=0, theirs=False, cable="DUB", minutes=1.0)
    assert "TARDE: reinicia con D 7.5" in line


def chain(**over):
    fields = dict(lags=[2.0], sync_err=0.0, delay_s=0.0, sync_late=0, policy=SimpleNamespace(corrections_blocked=2), floor=None,
                  mixer=SimpleNamespace(state=SimpleNamespace(value="TTS")), cable_warning="", video_warning="",
                  voice=SimpleNamespace(warning=""), t_start=0.0, asr=None, incoming=None, metrics=SimpleNamespace(ok=True),
                  fault="")
    return SimpleNamespace(**{**fields, **over})


def test_the_line_counts_real_corrections():
    assert "CORR 2" in Outgoing.bar_text(chain(), 60.0)


def test_meta_time_starts_when_the_button_is_pressed():
    assert "META 2 min" in Outgoing.bar_text(chain(t_start=1000.0), 1120.0)


def test_a_dead_task_stays_on_the_line():
    assert Outgoing.bar_text(chain(fault="ERROR out.asr: voz cruda en el cable"), 1.0).startswith("ERROR out.asr")


def test_what_is_down_shows_on_the_line():
    down = SimpleNamespace(t_down=5.0)
    line = Outgoing.bar_text(chain(asr=down, incoming=SimpleNamespace(asr=down), metrics=SimpleNamespace(ok=False)), 6.0)
    for word in ("ASR CAÍDO", "SUBTÍTULOS CAÍDOS", "SIN MÉTRICAS"):
        assert word in line


@pytest.mark.parametrize("make", [Button, MetricsLine, VoiceCard])
def test_a_window_stays_where_it_was_dragged(qt, tmp_path, monkeypatch, make):
    monkeypatch.setattr(widget, "PLACES", str(tmp_path / "widget.ini"))
    moved = make()
    target = moved.pos() + QPoint(10, 30)
    moved.move(target)
    moved._dragged = True
    moved.mouseReleaseEvent(None)
    assert make().pos() == target


def test_a_place_on_a_screen_that_is_gone_is_ignored(qt, tmp_path, monkeypatch):
    monkeypatch.setattr(widget, "PLACES", str(tmp_path / "widget.ini"))
    home = Button().pos()
    far = Button()
    far.move(QPoint(90000, 90000))
    far._dragged = True
    far.mouseReleaseEvent(None)
    assert Button().pos() == home

import ast
from pathlib import Path

import pytest

from interprete.ui import status, texts
from interprete.ui.status import CABLE, Phase, metrics_line

SRC = Path(__file__).resolve().parents[1] / "src" / "interprete"


@pytest.fixture
def spanish(monkeypatch):
    monkeypatch.setattr(texts, "LANG", "es")


def looked_up(path: Path) -> list[str]:
    """Every literal the code hands to `t` or `tr`, both arms of a conditional included."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("t", "tr") and node.args:
            arg = node.args[0]
            for value in (arg.body, arg.orelse) if isinstance(arg, ast.IfExp) else (arg,):
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found.append(value.value)
    return found


@pytest.mark.parametrize("path", [SRC / "app.py", SRC / "ui" / "status.py", SRC / "ui" / "overlay.py",
                                  SRC / "ui" / "widget.py"], ids=lambda p: p.name)
def test_every_text_the_code_shows_has_its_spanish(path):
    missing = [text for text in looked_up(path) if text not in texts.ES]
    assert looked_up(path) or path.name == "widget.py"
    assert not missing


def test_every_label_of_the_button_and_the_cable_has_its_spanish():
    assert not [p.value for p in Phase if p.value not in texts.ES]
    assert not [v for v in CABLE.values() if v not in texts.ES]


def test_english_unless_asked_and_spanish_when_asked(spanish):
    assert texts.t("LIVE") == "EN VIVO" and texts.t(Phase.LISTENING.value) == "MIC EN ESPERA"
    line = metrics_line(lags=[2.0], sync_s=0.2, delay_s=7.0, late=True, corrections=1, theirs=True, cable="DUB",
                        minutes=3.0)
    assert line == "LAG 2.0s p95 2.0s │ SYNC +0.2s (D 7.0s) TARDE: reinicia con D 7.5 │ CORR 1 │ TURNO: ellos │ " \
                   "ES→EN DOBLAJE │ META 3 min"
    texts.set_lang("en")
    assert texts.t("LIVE") == "LIVE" and status.metrics_line(lags=[], sync_s=0.0, delay_s=0.0, late=False,
                                                             corrections=0, theirs=False, cable="SILENT",
                                                             minutes=0.0) == "LAG -- │ CORR 0 │ TURN: me │ ES→EN STANDBY │ META 0 min"


@pytest.mark.parametrize("setting, lang", [("es", "es"), ("ES", "es"), ("es-PE", "es"), ("en", "en"), ("fr", "en"),
                                           ("", "en")])
def test_the_setting_picks_spanish_or_falls_back_to_english(monkeypatch, setting, lang):
    monkeypatch.setattr(texts, "LANG", "en")
    texts.set_lang(setting)
    assert texts.LANG == lang


def test_a_text_without_spanish_shows_in_english_rather_than_failing(spanish):
    assert texts.t("Something new") == "Something new"

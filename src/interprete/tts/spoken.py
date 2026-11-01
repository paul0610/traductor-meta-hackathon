"""Text as MMS can say it.

Its tokenizer keeps letters, apostrophes and hyphens and loses the rest in silence: "$120 on Nov
4" comes out "on nov", and "Núñez" comes out "nez". Spark is asked to write such things in words
(mt/spark.py); this is for what gets through, because a clause that loses a number says something
else than the person said.
"""

from __future__ import annotations

import re
import unicodedata

from num2words import num2words

LETTERS = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", (
    "ay", "bee", "see", "dee", "ee", "eff", "jee", "aitch", "eye", "jay", "kay", "ell", "em", "en", "oh", "pee", "cue", "ar",
    "ess", "tee", "you", "vee", "double you", "ex", "why", "zee")))
SAID_AS_WORDS = {"OK": "okay", "NASA": "nasa", "COVID": "covid", "PIN": "pin", "SQL": "sequel"}
CURRENCY = {"$": ("dollars", "cents"), "€": ("euros", "cents"), "£": ("pounds", "pence"), "S/": ("soles", "cents")}
SCALE = r"(?:\s?(thousand|million|billion|trillion))?"
NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
SYMBOLS = {"&": " and ", "+": " plus ", "=": " equals ", "@": " at ", "#": " number ", "%": " percent ", "/": " ", "_": " ",
           "–": " ", "—": " ", "’": "'"}


def _value(digits: str) -> int | float:
    plain = digits.replace(",", "")
    return float(plain) if "." in plain else int(plain)


def _words(value: int | float, **kwargs) -> str:
    return num2words(value, lang="en", **kwargs).replace(",", "")


def _money(m: re.Match) -> str:
    major, minor = CURRENCY[m[1]]
    if m[3]:
        return f"{_words(_value(m[2]))} {m[3]} {major}"
    whole, _, cents = m[2].replace(",", "").partition(".")
    said = f"{_words(int(whole))} {major}"
    return f"{said} and {_words(int(cents.ljust(2, '0')[:2]))} {minor}" if cents and int(cents) else said


def _minutes(minute: int) -> str:
    return ("oh " if minute < 10 else "") + _words(minute)


def _half_day(m: re.Match) -> str:
    minute = int(m[2] or 0)
    return f"{_words(int(m[1]))}{' ' + _minutes(minute) if minute else ''} {LETTERS[m[3].upper()]} em"


def _clock(m: re.Match) -> str:
    minute = int(m[2])
    return f"{_words(int(m[1]))} {_minutes(minute) if minute else "o'clock"}"


def _letters(m: re.Match) -> str:
    return SAID_AS_WORDS.get(m[1]) or " ".join(LETTERS[c] for c in m[1]) + (m[2] and "s")


def spoken(text: str) -> str:
    """Digits, symbols, accents and acronyms in words. Empty if nothing in it can be said."""
    text = text.replace("ñ", "ny").replace("Ñ", "Ny")
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = re.sub(rf"(\$|€|£|S/)\s?\.?\s?({NUMBER}){SCALE}", _money, text)
    text = re.sub(rf"({NUMBER})\s?%", lambda m: f"{_words(_value(m[1]))} percent", text)
    text = re.sub(r"\b(\d{1,2})(?::(\d{2}))?\s?([AaPp])\.?[Mm]\b\.?", _half_day, text)
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", _clock, text)
    text = re.sub(r"\b(\d+)(st|nd|rd|th)\b", lambda m: _words(int(m[1]), to="ordinal"), text)
    text = re.sub(r"\b(1[1-9]\d\d|20\d\d)\b(?![.,]\d)", lambda m: _words(int(m[1]), to="year"), text)
    text = re.sub(NUMBER, lambda m: _words(_value(m[0])), text)
    for symbol, said in SYMBOLS.items():
        text = text.replace(symbol, said)
    text = re.sub(r"\b([A-Z]{2,6})(s?)\b", _letters, text)
    return " ".join(text.split()) if re.search(r"[A-Za-z]", text) else ""

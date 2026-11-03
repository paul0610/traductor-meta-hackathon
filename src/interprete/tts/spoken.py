"""Text as MMS can say it.

Its tokenizer keeps letters, apostrophes and hyphens and loses the rest in silence: "$120 on Nov
4" comes out "on nov", and "Núñez" comes out "nez". Spark is asked to write such things in words
(mt/spark.py); this is for what gets through, because a clause that loses a number says something
else than the person said. What is left that the voice cannot say becomes a space, so the words on
either side of it do not run together: "Q3" would be read "qthree".
"""

from __future__ import annotations

import re
import threading
import unicodedata

from num2words import num2words

LETTERS = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", (
    "ay", "bee", "see", "dee", "ee", "eff", "jee", "aitch", "eye", "jay", "kay", "ell", "em", "en", "oh", "pee", "cue", "ar",
    "ess", "tee", "you", "vee", "double you", "ex", "why", "zee")))
SAID_AS_WORDS = {"OK": "okay", "NASA": "nasa", "COVID": "covid", "PIN": "pin", "SQL": "sequel"}
ROMAN = {"II": 2, "III": 3, "IV": 4, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "XI": 11, "XII": 12}
ABBREVIATIONS = {"mr": "mister", "mrs": "missus", "ms": "miz", "dr": "doctor", "vs": "versus", "etc": "etcetera",
                 "e.g.": "for example", "i.e.": "that is"}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
          "December")
SHORT_MONTHS = {m[:3]: m for m in MONTHS} | {"Sept": "September"}
CURRENCY = {"$": ("dollar", "dollars", "cent", "cents"), "€": ("euro", "euros", "cent", "cents"),
            "£": ("pound", "pounds", "penny", "pence"), "S/": ("sol", "soles", "cent", "cents")}
SCALES = {"k": "thousand", "thousand": "thousand", "m": "million", "mn": "million", "million": "million",
          "b": "billion", "bn": "billion", "billion": "billion", "trillion": "trillion"}
NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
SCALE = r"(?:\s?((?i:thousand|million|billion|trillion|bn|mn|k|m|b))\b)?"
YEAR = r"1[1-9]\d\d|20\d\d"
YEAR_CUES = r"(?i:in|since|by|from|to|until|till|of|year|before|after|around|during|early|late|mid)|" + "|".join(MONTHS)
SYMBOLS = {"&": " and ", "+": " plus ", "=": " equals ", "@": " at ", "#": " number ", "%": " percent "}
_ONE_AT_A_TIME = threading.Lock()   # num2words keeps a precision on one shared converter while it works


def _value(digits: str) -> int | float:
    plain = digits.replace(",", "")
    return float(plain) if "." in plain else int(plain)


def _words(value: int | float, **kwargs) -> str:
    with _ONE_AT_A_TIME:
        return num2words(value, lang="en", **kwargs).replace(",", "")


def _plural(words: str) -> str:
    return words[:-1] + "ies" if words.endswith("y") else words + "s"


def _money(m: re.Match) -> str:
    one, many, cent, cents = CURRENCY[m[1].rstrip(".")]
    if m[3]:
        return f" {_words(_value(m[2]))} {SCALES[m[3].lower()]} {many} "
    whole, _, fraction = m[2].replace(",", "").partition(".")
    said = f"{_words(int(whole))} {one if int(whole) == 1 else many}"
    if fraction and int(fraction):
        hundredths = int(fraction.ljust(2, "0")[:2])
        said += f" and {_words(hundredths)} {cent if hundredths == 1 else cents}"
    return f" {said} "


def _minutes(minute: int) -> str:
    return ("oh " if minute < 10 else "") + _words(minute)


def _half_day(m: re.Match) -> str:
    minute = int(m[2] or 0)
    return f" {_words(int(m[1]))}{' ' + _minutes(minute) if minute else ''} {LETTERS[m[3].upper()]} em "


def _clock(m: re.Match) -> str:
    minute = int(m[2])
    return f" {_words(int(m[1]))} {_minutes(minute) if minute else "o'clock"} "


def _letters(m: re.Match) -> str:
    return (SAID_AS_WORDS.get(m[1]) or " ".join(LETTERS[c] for c in m[1])) + (m[2] and "s")


ES_LETTERS = set(" abcdefghijklmnopqrstuvxyzáéíñóúü")      # the Spanish voice's own: no w, and no digits it says well


def _numero(m: re.Match) -> str:
    digits = m.group().replace(".", "")                        # 2.500 is two thousand five hundred in Spanish
    with _ONE_AT_A_TIME:
        return f" {num2words(int(digits), lang='es')} "


def spoken_es(text: str) -> str:
    """Spanish as MMS's Spanish voice says it. It keeps tildes and ñ, so "año" stays "año"; numbers become words,
    "%" becomes "por ciento", w is said as u, and anything else it cannot say becomes a space."""
    text = re.sub(r"\d{1,3}(?:\.\d{3})+|\d+", _numero, text.replace("%", " por ciento"))
    text = text.lower().replace("w", "u")
    return " ".join("".join(c if c in ES_LETTERS else " " for c in text).split())


def spoken(text: str) -> str:
    """Digits, symbols, accents and acronyms in words. Empty if nothing in it can be said."""
    text = unicodedata.normalize("NFC", text).replace("ñ", "ny").replace("Ñ", "Ny").replace("’", "'")
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = re.sub(r"(?i)\b(e\.g\.|i\.e\.)|\b(mrs|mr|ms|dr|vs|etc)\b\.?", lambda m: f" {ABBREVIATIONS[m[0].lower().rstrip('.') if m[2] else m[0].lower()]} ", text)
    text = re.sub(rf"\b({'|'.join(SHORT_MONTHS)}|{'|'.join(MONTHS)})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?!:)",
                  lambda m: f" {SHORT_MONTHS.get(m[1], m[1])} {_words(int(m[2]), to='ordinal')} ", text)
    text = re.sub(rf"({NUMBER})\s?[-–]\s?(?={NUMBER})", r"\1 to ", text)    # "10-15 people" is a range
    text = re.sub(rf"(\$|€|£|(?<![A-Za-z])S/\.?)\s?({NUMBER}){SCALE}", _money, text)
    text = re.sub(rf"({NUMBER})\s?%", lambda m: f" {_words(_value(m[1]))} percent ", text)
    text = re.sub(r"\b(\d{1,2})(?::(\d{2}))?\s?([AaPp])\.?[Mm]\b\.?", _half_day, text)
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", _clock, text)
    text = re.sub(r"\b(\d+)(st|nd|rd|th)\b", lambda m: f" {_words(int(m[1]), to='ordinal')} ", text)
    text = re.sub(r"\b(1[1-9]\d0|20\d0)s\b", lambda m: f" {_plural(_words(int(m[1]), to='year'))} ", text)
    text = re.sub(r"(?<![\d.,])'?\b([1-9]0)s\b", lambda m: f" {_plural(_words(int(m[1])))} ", text)
    text = re.sub(rf"\b({YEAR_CUES})\s+({YEAR})\b(?![.,]\d)", lambda m: f" {m[1]} {_words(int(m[2]), to='year')} ", text)
    text = re.sub(rf"(?<=th|st|nd|rd)\s*,\s*({YEAR})\b(?![.,]\d)", lambda m: f" {_words(int(m[1]), to='year')} ", text)
    text = re.sub(r"(?<![\w.,])\.(\d+)\b", lambda m: f" {_words(float('0.' + m[1])).replace('zero ', '', 1)} ", text)
    text = re.sub(NUMBER, lambda m: f" {_words(_value(m[0]))} ", text)
    text = re.sub(rf"\b({'|'.join(ROMAN)})\b", lambda m: f" {_words(ROMAN[m[1]])} ", text)
    for symbol, said in SYMBOLS.items():
        text = text.replace(symbol, said)
    text = re.sub(r"\b([A-Z]{2,6})(s?)\b", _letters, text)
    text = re.sub(r"[^A-Za-z' -]", " ", text)                       # what the voice cannot say: a space, never a join
    text = re.sub(r"(?<![A-Za-z])['-]+|['-]+(?![A-Za-z])", " ", text)  # an apostrophe or hyphen outside a word
    return " ".join(text.split()) if re.search(r"[A-Za-z]", text) else ""

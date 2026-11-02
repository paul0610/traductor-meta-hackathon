import threading

import pytest

from interprete.tts.spoken import spoken


@pytest.mark.parametrize("text, said", [
    ("It costs $120.", "It costs one hundred and twenty dollars"),
    ("$12.50 each", "twelve dollars and fifty cents each"),
    ("$1", "one dollar"),
    ("$1.01", "one dollar and one cent"),
    ("$2 million", "two million dollars"),
    ("$2.5M", "two point five million dollars"),
    ("$10K", "ten thousand dollars"),
    ("$3 Billion", "three billion dollars"),
    ("S/ 1500 al mes", "one thousand five hundred soles al mes"),
    ("S/. 20", "twenty soles"),
    ("about 40%", "about forty percent"),
    ("2.5 times", "two point five times"),
    ("pi is 3.1416", "pi is three point one four one six"),
    (".5 points", "point five points"),
    ("1,500 people", "one thousand five hundred people"),
    ("1,2,3", "one two three"),
    ("10-15 people", "ten to fifteen people"),
    ("the 1st and the 23rd", "the first and the twenty-third"),
    ("at 3:30 PM", "at three thirty pee em"),
    ("at 10:00", "at ten o'clock"),
    ("at 9:05 am", "at nine oh five ay em"),
    ("at 10:00 am", "at ten ay em"),
    ("at 3 PM", "at three pee em"),
    ("9-10 am", "nine to ten ay em"),
    ("7 8 9", "seven eight nine"),
    ("24/7", "twenty-four seven"),
])
def test_numbers_are_written_out(text, said):
    assert spoken(text) == said


@pytest.mark.parametrize("text, said", [
    ("in 2026", "in twenty twenty-six"),
    ("since 2019", "since twenty nineteen"),
    ("from 2024-2026", "from twenty twenty-four to twenty twenty-six"),
    ("November 4, 2026", "November fourth twenty twenty-six"),
    ("Nov 4", "November fourth"),
    ("2019 employees", "two thousand and nineteen employees"),   # a count, not a year
    ("1250 students", "one thousand two hundred and fifty students"),
    ("the 1990s", "the nineteen nineties"),
    ("the 2010s", "the twenty tens"),
    ("in her 20s", "in her twenties"),
])
def test_years_only_where_they_are_years(text, said):
    assert spoken(text) == said


@pytest.mark.parametrize("text, said", [
    ("Núñez y José", "Nunyez y Jose"),
    ("Peña y Güe", "Penya y Gue"),
    ("Nuñez", "Nunyez"),                      # the same name, with its tilde as a separate mark
    ("AI & ML", "ay eye and em ell"),
    ("two APIs", "two ay pee eyes"),
    ("PINs", "pins"),
    ("OK then", "okay then"),
    ("Phase II", "Phase two"),
    ("Mr. Smith and Dr. Núñez", "mister Smith and doctor Nunyez"),
    ("I am here", "I am here"),
    ("it’s fine", "it's fine"),
    ("a well-known co-worker", "a well-known co-worker"),
    ("the students' work", "the students work"),
])
def test_names_symbols_and_acronyms_survive(text, said):
    assert spoken(text) == said


@pytest.mark.parametrize("text, said", [
    ("Q3 results", "Q three results"),
    ("B2B", "B two B"),
    ("16GB", "sixteen jee bee"),
    ("US$5,000", "you ess five thousand dollars"),
])
def test_what_the_voice_drops_never_joins_two_words(text, said):
    assert spoken(text) == said


@pytest.mark.parametrize("text", ["", "...", "!?", "   "])
def test_nothing_to_say(text):
    assert spoken(text) == ""


def test_no_digit_or_symbol_is_left():
    said = spoken("Room 305, 12 people, 7.5 hours, $3, 40%, 2nd of 2026 at 10:15 (Q3), 1:1s & more")
    assert all(c.isalpha() or c in " '-" for c in said), said


def test_threads_get_the_same_words():
    """num2words keeps a precision on one shared converter: two decimals at once used to be able to mix."""
    texts = [f"{i}.{i * 7 % 1000:03d} and 3.14159" for i in range(1, 200)]
    alone = [spoken(t) for t in texts]
    together = [None] * len(texts)

    def run(i):
        together[i] = spoken(texts[i])

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(texts))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert together == alone

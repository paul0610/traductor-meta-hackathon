import pytest

from interprete.tts.spoken import spoken


@pytest.mark.parametrize("text, said", [
    ("It costs $120.", "It costs one hundred and twenty dollars."),
    ("$12.50 each", "twelve dollars and fifty cents each"),
    ("$2 million", "two million dollars"),
    ("S/ 1500 al mes", "one thousand five hundred soles al mes"),
    ("about 40%", "about forty percent"),
    ("2.5 times", "two point five times"),
    ("1,500 people", "one thousand five hundred people"),
    ("the 1st and the 23rd", "the first and the twenty-third"),
    ("in 2026", "in twenty twenty-six"),
    ("at 3:30 PM", "at three thirty pee em"),
    ("at 10:00", "at ten o'clock"),
    ("at 9:05 am", "at nine oh five ay em"),
    ("at 10:00 am", "at ten ay em"),
    ("at 3 PM", "at three pee em"),
    ("7 8 9", "seven eight nine"),
    ("24/7", "twenty-four seven"),
])
def test_numbers_are_written_out(text, said):
    assert spoken(text) == said


@pytest.mark.parametrize("text, said", [
    ("Núñez y José", "Nunez y Jose"),
    ("AI & ML", "ay eye and em ell"),
    ("two APIs", "two ay pee eyes"),
    ("OK then", "okay then"),
    ("I am here", "I am here"),
    ("it’s fine", "it's fine"),
    ("a well-known co-worker", "a well-known co-worker"),
])
def test_names_symbols_and_acronyms_survive(text, said):
    assert spoken(text) == said


@pytest.mark.parametrize("text", ["", "...", "!?", "   "])
def test_nothing_to_say(text):
    assert spoken(text) == ""


def test_no_digit_is_left():
    assert not any(c.isdigit() for c in spoken("Room 305, 12 people, 7.5 hours, $3, 40%, 2nd of 2026 at 10:15"))

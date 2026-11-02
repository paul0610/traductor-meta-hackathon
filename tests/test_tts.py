from interprete.tts.shaping import FAST_RATE_PCT, isochrony_pct

PACE = (0.047, 0.40)


def rate(text, src_s):
    return isochrony_pct(text, src_s, *PACE)


def test_a_clause_that_fits_keeps_the_normal_rate():
    assert rate("one two three", 1.0) == 0
    assert rate("one two three", 2.0) == 0     # never slower: it would add lag


def test_a_long_translation_of_a_short_clause_speeds_up():
    assert rate("one two three four five", 1.2) == FAST_RATE_PCT


def test_a_small_excess_is_not_worth_leaving_the_normal_rate():
    assert rate("one two three four", 1.2) == 0


def test_the_speed_up_is_capped():
    assert rate("one two three four five six seven eight nine", 0.5) == FAST_RATE_PCT


def test_unknown_source_length_changes_nothing():
    assert rate("Hi, a quick note.", None) == 0


def test_a_tiny_source_length_is_not_taken_at_face_value():
    assert rate("Sure.", 0.01) == 0


def test_length_is_judged_by_characters_not_words():
    assert rate("for mid-sized companies", 1.2) == FAST_RATE_PCT
    assert rate("so we go", 1.2) == 0

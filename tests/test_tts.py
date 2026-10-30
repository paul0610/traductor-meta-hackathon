from interprete.tts.edge import MAX_RATE_UP, isochrony_pct


def test_a_clause_that_fits_keeps_the_normal_rate():
    assert isochrony_pct("one two three", 1.0) == 0
    assert isochrony_pct("one two three", 2.0) == 0     # never slower: it would add lag


def test_a_long_translation_of_a_short_clause_speeds_up():
    assert isochrony_pct("one two three four", 1.2) == 11


def test_the_speed_up_is_capped():
    assert isochrony_pct("one two three four five six seven eight nine", 0.5) == int(MAX_RATE_UP * 100)


def test_unknown_source_length_changes_nothing():
    assert isochrony_pct("Hi, a quick note.", None) == 0


def test_a_tiny_source_length_is_not_taken_at_face_value():
    assert isochrony_pct("Sure.", 0.01) == 11

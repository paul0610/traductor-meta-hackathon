from interprete.clock import StreamClock
from interprete.contracts import AsrEvent, AsrKind, Channel
from interprete.policy.incremental import MAX_CLAUSE_S, MAX_WORDS, PAUSE_S, TO_FIRST_WORD_S, WORD_LAG_S, Incremental


class Script:
    """Feeds a policy the way the ASR would: one partial per step, 0.4 s of audio apart."""

    def __init__(self):
        self.clock = StreamClock()
        self.clock.start_session("S1")
        self.clock.mark_first_block(100.0)
        self.p = Incremental(Channel.OUT, self.clock.to_local)
        self.t = 1.0
        self.commits = []

    def send(self, kind, text=""):
        c = self.p.on_event(AsrEvent(Channel.OUT, kind, 100.0 + self.t + 0.2, "S1", turn_id="0", text=text, t_asr_s=self.t))
        if c is not None:
            self.commits.append(c)
        return c

    def start(self):
        return self.send(AsrKind.SPEECH_START)

    def partials(self, *texts, step=0.4):
        for text in texts:
            self.send(AsrKind.PARTIAL, text)
            self.t += step

    def grow(self, sentence, step=0.2):
        """One more word per partial, like the real stream; 5 words/s stays clear of the time cut."""
        w = sentence.split()
        self.partials(*(" ".join(w[:i]) for i in range(1, len(w) + 1)), step=step)

    def hold(self, seconds, step=0.2):
        """Silence after `grow`: the stream position moves on and no partial comes."""
        self.t += seconds - step
        return self.send(AsrKind.PROGRESS)

    def texts(self):
        return [c.text_src for c in self.commits]


def test_a_word_needs_two_partials_that_agree():
    s = Script()
    s.start()
    s.partials("Hoy trabajé,")
    assert s.commits == []
    s.partials("Hoy trabajé mucho, y")
    assert s.commits == []                       # "trabajé" and "trabajé mucho," differ only at word 3
    s.partials("Hoy trabajé mucho, y luego")
    assert s.texts() == ["Hoy trabajé mucho,"]


def test_punctuation_closes_a_clause_of_at_least_three_words():
    s = Script()
    s.start()
    s.grow("Sí, tengo cinco años, sobre todo en datos")
    assert s.commits[0].text_src == "Sí, tengo cinco años,"     # the comma after two words does not cut
    assert s.commits[0].boundary == "punct"


def test_a_conjunction_opens_the_next_clause():
    s = Script()
    s.start()
    s.grow("Terminé el reporte ayer pero nadie lo leyó todavía")
    assert s.commits[0].text_src == "Terminé el reporte ayer"
    assert s.commits[0].boundary == "conj"


def test_a_conjunction_too_early_does_not_cut():
    s = Script()
    s.start()
    s.grow("Yo y mi equipo terminamos todo el trabajo ayer temprano")
    assert s.commits[0].text_src != "Yo"
    assert not s.commits[0].text_src.endswith(" y")


def test_long_clause_without_boundary_is_cut_by_words():
    s = Script()
    s.start()
    s.grow("uno dos tres cuatro cinco seis siete ocho nueve diez once")
    assert s.commits[0].boundary == "words"
    assert len(s.commits[0].text_src.split()) == MAX_WORDS


def test_slow_speech_is_cut_by_time():
    s = Script()
    s.start()
    s.grow("esto lo digo muy muy despacio", step=MAX_CLAUSE_S / 3)
    assert s.commits[0].boundary == "time"


def test_a_forced_cut_backs_up_so_noun_and_adjective_stay_together():
    s = Script()
    s.start()
    s.grow("construyendo sistemas de datos para empresas medianas", step=0.4)
    assert s.commits[0].boundary == "time"
    assert s.commits[0].text_src == "construyendo sistemas de datos"


def test_a_forced_cut_with_no_opener_takes_everything():
    s = Script()
    s.start()
    s.grow("esto lo digo muy muy despacio", step=MAX_CLAUSE_S / 3 + 0.01)
    assert s.commits[0].text_src == "esto lo digo"


def test_speech_end_flushes_what_is_left_and_complete_adds_nothing():
    s = Script()
    s.start()
    s.grow("Tengo cinco años")
    assert s.commits == []
    s.send(AsrKind.SPEECH_END)
    assert s.texts() == ["Tengo cinco años"]
    assert s.commits[0].boundary == "speechEnd"
    assert s.send(AsrKind.SPEECH_COMPLETE, "Tengo cinco años.") is None


def test_speech_complete_commits_words_the_partials_never_showed():
    s = Script()
    s.start()
    s.grow("Tengo cinco años")
    s.send(AsrKind.SPEECH_END)
    c = s.send(AsrKind.SPEECH_COMPLETE, "Tengo cinco años de experiencia.")
    assert c.text_src == "de experiencia."
    assert c.boundary == "speechComplete"
    assert c.prefix_src == "Tengo cinco años"


def test_a_turn_with_no_partials_still_commits():
    s = Script()
    s.start()
    c = s.send(AsrKind.SPEECH_COMPLETE, "Hola.")
    assert c.text_src == "Hola."


def test_committed_words_are_never_revised_and_the_change_is_counted_once():
    s = Script()
    s.start()
    s.grow("Terminé el reporte ayer pero nadie lo")
    assert s.commits[0].text_src == "Terminé el reporte ayer"
    s.partials("Terminé los reportes ayer pero nadie lo leyó", "Terminé los reportes ayer pero nadie lo leyó nunca")
    assert s.p.corrections_blocked == 1
    assert s.commits[0].text_src == "Terminé el reporte ayer"


def test_commits_chain_in_order_with_prefix_and_ids():
    s = Script()
    s.start()
    s.grow("Terminé el reporte ayer pero nadie lo leyó, y eso me molestó bastante hoy")
    s.send(AsrKind.SPEECH_END)
    assert [c.seq for c in s.commits] == list(range(len(s.commits)))
    assert [c.commit_id for c in s.commits] == list(range(len(s.commits)))
    assert " ".join(s.texts()) == "Terminé el reporte ayer pero nadie lo leyó, y eso me molestó bastante hoy"
    assert s.commits[1].prefix_src == s.commits[0].text_src


def test_first_clause_starts_at_speech_start_later_ones_on_the_line_through_their_words():
    s = Script()
    s.start()
    s.grow("Terminé el reporte ayer pero nadie lo leyó", step=0.4)
    s.send(AsrKind.SPEECH_END)
    assert abs(s.commits[0].t_src_start - (101.0 + TO_FIRST_WORD_S)) < 1e-9
    assert s.commits[0].t_src_source == "speech_start"
    # "pero" first showed up in the 5th partial, audio position 1.0 + 4 × 0.4; evenly spaced words: right on the line
    assert abs(s.commits[1].t_src_start - (100.0 + 1.0 + 4 * 0.4 - WORD_LAG_S)) < 1e-9
    assert s.commits[1].t_src_source == "partial_arrival"
    # a clause ends where the next word would start
    assert abs(s.commits[1].t_src_end - (100.0 + 1.0 + 8 * 0.4 - WORD_LAG_S)) < 1e-9


def two_clauses(onsets):
    """Slow words, cut by time: three from 101.26, so the next clause has no opening before 102.01; the words place it near 102.1."""
    s = Script()
    s.p.onsets = onsets
    s.start()
    s.grow("Terminé el reporte ayer pero nadie lo leyó", step=0.6)
    s.send(AsrKind.SPEECH_END)
    return s


def test_a_clause_starts_where_the_mic_heard_its_phrase_open():
    s = two_clauses([102.05])
    assert s.commits[1].t_src_start == 102.05
    assert s.commits[1].t_src_source == "mic_onset"


def test_an_opening_is_taken_once():
    s = two_clauses([101.3])
    assert s.commits[0].t_src_start == 101.3
    assert s.commits[1].t_src_source == "partial_arrival"


def test_an_opening_inside_the_clause_before_is_not_the_next_ones():
    s = two_clauses([101.9])                         # a pause inside the first clause, not the start of the second
    assert s.commits[1].t_src_source == "partial_arrival"


def test_with_no_opening_near_the_words_decide():
    s = two_clauses([90.0, 120.0])
    assert "mic_onset" not in [c.t_src_source for c in s.commits]


def test_a_word_that_surfaces_late_is_pulled_back_by_its_neighbours():
    s = Script()
    s.start()
    words = "Terminé el reporte ayer pero nadie lo leyó".split()
    for i in range(1, 5):
        s.partials(" ".join(words[:i]), step=0.3)
    s.t += 0.9                                       # "pero" reaches a partial long after it was said
    first_carried_at = s.t
    s.partials(*(" ".join(words[:i]) for i in range(5, 9)), step=0.1)
    s.send(AsrKind.SPEECH_END)
    assert s.commits[1].text_src.startswith("pero")
    assert s.commits[1].t_src_start < 100.0 + first_carried_at - WORD_LAG_S - 0.2


def test_empty_turn_is_nothing():
    s = Script()
    s.start()
    assert s.send(AsrKind.SPEECH_COMPLETE, "  ") is None
    assert s.commits == []


def test_a_pause_closes_the_clause_with_its_last_word():
    s = Script()
    s.start()
    s.grow("Hoy terminé el reporte")
    assert s.commits == []
    s.hold(PAUSE_S)
    assert s.texts() == ["Hoy terminé el reporte"]
    assert s.commits[0].boundary == "pause"


def test_a_word_still_recent_does_not_close_it():
    s = Script()
    s.start()
    s.grow("Hoy terminé el reporte")
    assert s.hold(PAUSE_S - 0.05) is None


def test_a_pause_leaves_a_short_clause_open():
    s = Script()
    s.start()
    s.grow("Hoy terminé")
    assert s.hold(PAUSE_S * 3) is None
    s.grow("Hoy terminé el reporte del cliente grande")
    assert s.commits[0].text_src.startswith("Hoy terminé")


def test_a_pause_keeps_a_soft_opener_for_the_next_clause():
    s = Script()
    s.start()
    s.grow("Hoy hablé con el equipo de")
    s.hold(PAUSE_S)
    assert s.texts() == ["Hoy hablé con el equipo"]


def test_the_clause_after_a_pause_takes_up_where_it_stopped():
    s = Script()
    s.start()
    s.grow("Hoy terminé el reporte")
    s.hold(PAUSE_S)
    s.grow("Hoy terminé el reporte para todo el equipo mañana")
    s.send(AsrKind.SPEECH_END)
    assert " ".join(s.texts()) == "Hoy terminé el reporte para todo el equipo mañana"


def test_progress_outside_a_turn_does_nothing():
    s = Script()
    assert s.hold(PAUSE_S) is None
    assert s.p._turns == {}

"""La palabra de activación "Jarvis" reconocida en el texto (wake_text.py).

Python puro: ni audio, ni modelo, ni red. El reloj de la ventana es falso.
"""
import pytest

import wake_text
from wake_text import WakeGate, edit_distance, fold, is_wake_token, match_wake


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


# ── normalización y distancia ──────────────────────────────────────────────

def test_fold_lowercases_and_drops_accents():
    assert fold("JARVÍS") == "jarvis"
    assert fold("Jarvís") == "jarvis"
    assert fold("") == "" and fold(None) == ""


def test_edit_distance_is_cut_at_the_limit():
    assert edit_distance("jarvis", "jarvis") == 0
    assert edit_distance("jarvis", "jarbis") == 1
    assert edit_distance("jarvis", "arvis") == 1
    assert edit_distance("jarvis", "marvin", 1) == 2          # limit + 1: "far enough"
    assert edit_distance("jarvis", "x", 2) == 3


@pytest.mark.parametrize("word", ["Jarvis", "JARVIS", "Jarvís", "yarvis", "Harvis",
                                  "jarbis", "arvis", "jarvi", "Harbys", "jarvys"])
def test_wake_tokens(word):
    assert is_wake_token(word)


@pytest.mark.parametrize("word", ["jarabe", "marvin", "carta", "jar", "vis", "varias",
                                  "javier", "jarvisimo", ""])
def test_not_wake_tokens(word):
    assert not is_wake_token(word)


# ── coincidencia y recorte ─────────────────────────────────────────────────

@pytest.mark.parametrize("heard, rest", [
    ("Jarvis, enciende la luz", "enciende la luz"),
    ("jarvis enciende la luz", "enciende la luz"),
    ("JARVIS: apaga la calefacción", "apaga la calefacción"),
    ("Jarvís, apaga", "apaga"),
    ("Oye Jarvis, ¿está puesta la alarma?", "¿está puesta la alarma?"),
    ("Oye, Jarvis. Dime la hora", "Dime la hora"),
    ("hey jarvis qué tiempo hace", "qué tiempo hace"),
    ("Hey, Jarvis!  Para", "Para"),
    ("Hola Jarvis, buenos días", "buenos días"),
    ("Vale Jarvis, gracias", "gracias"),
    ("Hoy Jarvis está puesta la alarma", "está puesta la alarma"),   # "oye" mal oído
    ("Yarvis enciende la luz", "enciende la luz"),
    ("Harvis, hola", "hola"),
    ("Jarbis, para", "para"),
    ("Arvis, cancela", "cancela"),
    ("Jar vis dime la hora", "dime la hora"),                       # partido por Whisper
    ("Jarvis.   Dime", "Dime"),
    ("Jarvis", ""),
    ("¡Jarvis!", ""),
    ("  Hey, Jarvis.  ", ""),
])
def test_a_leading_name_is_matched_and_cut(heard, rest):
    assert match_wake(heard) == rest


@pytest.mark.parametrize("heard", [
    "enciende la luz",
    "dile a Jarvis que pare",            # solo si EMPIEZA por el nombre
    "me llamo Marvis",
    "oye qué tal",
    "hola, buenas tardes",
    "hoy hace calor",
    "jarabe de arce",
    "Jar. Vis",                           # una frase, no el nombre partido
    "Hey",
    "",
    None,
])
def test_anything_else_is_not_addressed_to_him(heard):
    assert match_wake(heard) is None


def test_a_filler_alone_before_other_words_is_not_enough():
    assert match_wake("oye enciende la luz") is None


def test_the_rest_keeps_its_own_case_and_punctuation():
    assert match_wake("Jarvis, Pon el Telediario, por favor.") == "Pon el Telediario, por favor."


# ── la ventana de conversación ─────────────────────────────────────────────

def test_a_phrase_without_the_word_is_dropped_while_the_window_is_closed():
    gate = WakeGate(20, Clock())
    assert gate.process("enciende la luz") is None
    assert gate.is_open is False


def test_an_accepted_phrase_loses_the_word_and_opens_the_window():
    clock = Clock()
    gate = WakeGate(20, clock)
    assert gate.process("Jarvis, enciende la luz") == "enciende la luz"
    assert gate.is_open
    clock.t += 10
    assert gate.process("y apaga la otra") == "y apaga la otra"


def test_the_window_closes_after_its_seconds():
    clock = Clock()
    gate = WakeGate(20, clock)
    gate.process("Jarvis, hola")
    clock.t += 20.5
    assert gate.process("y ahora qué") is None


def test_every_accepted_phrase_extends_the_window():
    clock = Clock()
    gate = WakeGate(20, clock)
    gate.process("Jarvis, uno")
    for _ in range(4):
        clock.t += 15                    # más de 20 s en total, nunca 20 seguidos
        assert gate.process("otra") == "otra"


def test_a_dropped_phrase_does_not_extend_it():
    clock = Clock()
    gate = WakeGate(20, clock)
    gate.process("Jarvis, uno")
    clock.t += 25
    assert gate.process("charla de la tele") is None
    clock.t += 1
    assert gate.process("otra") is None


def test_only_the_name_gives_nothing_but_opens_the_window():
    clock = Clock()
    gate = WakeGate(20, clock)
    assert gate.process("Jarvis.") == ""
    clock.t += 5
    assert gate.process("enciende la luz") == "enciende la luz"


def test_the_name_inside_the_window_is_still_cut():
    gate = WakeGate(20, Clock())
    gate.process("Jarvis, uno")
    assert gate.process("Jarvis, dos") == "dos"


def test_his_own_speech_keeps_the_window_open():
    clock = Clock()
    gate = WakeGate(20, clock)
    gate.process("Jarvis, cuéntame algo largo")
    clock.t += 15
    gate.keep_open()                     # está hablando
    clock.t += 15
    assert gate.is_open
    clock.t += 6
    assert not gate.is_open


def test_keep_open_does_not_open_a_closed_window():
    gate = WakeGate(20, Clock())
    gate.keep_open()
    assert not gate.is_open


def test_window_zero_means_the_word_every_time():
    gate = WakeGate(0, Clock())
    assert gate.process("Jarvis, uno") == "uno"
    assert gate.process("dos") is None


def test_close_ends_the_conversation():
    gate = WakeGate(20, Clock())
    gate.process("Jarvis, uno")
    gate.close()
    assert gate.process("dos") is None


def test_empty_text_is_ignored():
    gate = WakeGate(20, Clock())
    assert gate.process("") is None and gate.process("   ") is None and gate.process(None) is None


def test_the_module_has_no_dependencies():
    import sys
    src = open(wake_text.__file__, encoding="utf-8").read()
    for heavy in ("numpy", "faster_whisper", "openwakeword"):
        assert f"import {heavy}" not in src
    assert "wake_text" in sys.modules

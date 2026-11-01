"""Router de preguntas y base de datos: reglas y registro, sin red ni modelos."""

import pytest

from agents import router
from agents.router import ASK, CLAUDE, GEMINI, HA
from agents.store import QuestionStore


@pytest.mark.parametrize("text", [
    "enciende la luz del salón",
    "apaga las luces de la cocina",
    "sube las persianas del despacho",
    "pon la calefacción a 21 grados",
])
def test_home_commands_go_to_home_assistant(text):
    assert router.route(text).route == HA


@pytest.mark.parametrize("text", [
    "qué ves en la piscina",
    "hay alguien en el parking",
    "muéstrame la cámara del tejado",
])
def test_camera_questions_go_to_gemini(text):
    assert router.route(text).route == GEMINI


def test_recent_information_goes_to_gemini():
    assert router.route("qué noticias hay hoy").route == GEMINI


@pytest.mark.parametrize("text", [
    "explica por qué falla este test",
    "redacta un correo para el proveedor",
])
def test_reasoning_goes_to_claude(text):
    assert router.route(text).route == CLAUDE


def test_long_text_goes_to_claude():
    long_text = " ".join(["palabra"] * 30)
    assert router.route(long_text).route == CLAUDE


def test_unclear_question_asks_before_spending():
    assert router.route("gracias").route == ASK


def test_empty_question_asks():
    assert router.route("   ").route == ASK


def test_home_rule_wins_over_camera_when_both_appear():
    # Una orden de casa con la palabra «cámara» no debe gastar un modelo de visión.
    assert router.route("apaga la luz de la cámara del tejado").route == HA


def test_accents_do_not_change_the_route():
    assert router.route("enciende la luz") == router.route("enciende la luz")
    assert router.route("muéstrame la camara") == router.route("muestrame la cámara")


def test_decision_carries_a_reason():
    assert router.route("enciende la luz").reason == "orden de casa"


def test_log_and_rate_round_trip():
    store = QuestionStore(":memory:")
    qid = store.log("qué ves en la piscina", GEMINI, "imagen o cámara", latency_ms=800, now=1000.0)
    store.rate(qid, good=False)
    (stat,) = store.stats()
    assert (stat.route, stat.total, stat.rated, stat.good) == (GEMINI, 1, 1, 0)
    assert stat.success_rate == 0.0
    store.close()


def test_repeated_question_within_five_minutes_is_flagged():
    store = QuestionStore(":memory:")
    store.log("hay alguien en el parking", GEMINI, "imagen o cámara", now=1000.0)
    store.log("hay alguien en el parking", GEMINI, "imagen o cámara", now=1100.0)
    rows = store._db.execute("SELECT repeated FROM questions ORDER BY id").fetchall()
    assert [r["repeated"] for r in rows] == [0, 1]
    store.close()


def test_repeat_after_five_minutes_is_not_flagged():
    store = QuestionStore(":memory:")
    store.log("hay alguien en el parking", GEMINI, "imagen o cámara", now=1000.0)
    store.log("hay alguien en el parking", GEMINI, "imagen o cámara", now=1400.0)
    rows = store._db.execute("SELECT repeated FROM questions ORDER BY id").fetchall()
    assert [r["repeated"] for r in rows] == [0, 0]
    store.close()


def test_rating_unknown_question_fails_clearly():
    store = QuestionStore(":memory:")
    with pytest.raises(KeyError):
        store.rate(999, good=True)
    store.close()


def test_stats_without_ratings_have_no_success_rate():
    store = QuestionStore(":memory:")
    store.log("enciende la luz", HA, "orden de casa", now=1000.0)
    (stat,) = store.stats()
    assert stat.rated == 0 and stat.success_rate is None
    store.close()

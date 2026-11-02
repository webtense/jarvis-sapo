"""Lógica del agente JARVIS Router para Assist (ha/custom_components/jarvis_router).

Solo la parte pura: sin Home Assistant, sin red y sin modelos. El módulo no
importa nada de HA, así que se carga directamente desde su carpeta.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ha" / "custom_components" / "jarvis_router"))

import router_logic as rl  # noqa: E402


# --- lectura de la frase -------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("¿Qué ves en la cámara?", "que ves en la camara"),
    ("  Sí,   bien. ", "si bien"),
    ("Gemini!", "gemini"),
])
def test_normalise_quita_tildes_signos_y_espacios(text, expected):
    assert rl.normalise(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("bien", True),
    ("Muy bien.", True),
    ("perfecto", True),
    ("mal", False),
    ("No vale", False),
    ("qué hora es", None),
    ("bien, y mañana llueve", None),
])
def test_parse_rating_solo_frases_de_valoracion_exactas(text, expected):
    assert rl.parse_rating(text) is expected


@pytest.mark.parametrize("text, expected", [
    ("Gemini", rl.ROUTE_GEMINI),
    ("a Claude", rl.ROUTE_CLAUDE),
    ("pregúntaselo a Google", rl.ROUTE_GEMINI),
    ("Gemini o Claude", None),       # ambiguo
    ("hola qué tal", None),          # no es elección
    ("", None),
])
def test_parse_choice(text, expected):
    assert rl.parse_choice(text) == expected


def test_parse_choice_ignora_frases_largas_que_mencionan_google():
    frase = "busca en google la previsión del tiempo para mañana por la tarde en Barcelona"
    assert rl.parse_choice(frase) is None


# --- destinos -------------------------------------------------------------

def test_forward_ha_va_a_home_assistant_sin_nota():
    f = rl.forward_for(rl.ROUTE_HA, "apaga la luz", 7)
    assert (f.agent, f.note, f.question_id) == (rl.HA_AGENT, "", 7)


def test_forward_gemini_va_a_google_ai_sin_nota():
    f = rl.forward_for(rl.ROUTE_GEMINI, "qué ves", 3)
    assert (f.agent, f.note) == (rl.GEMINI_AGENT, "")


def test_claude_no_es_agente_de_assist_asi_que_responde_gemini_con_aviso():
    f = rl.forward_for(rl.ROUTE_CLAUDE, "explica el teorema", 5)
    assert f.agent == rl.GEMINI_AGENT
    assert f.note == rl.CLAUDE_NOTE


def test_ruta_desconocida_no_se_reenvia():
    with pytest.raises(ValueError):
        rl.forward_for("otra", "x", None)


def test_router_caido_responde_gemini_sin_id_para_valorar():
    f = rl.router_unavailable("hola")
    assert f.agent == rl.GEMINI_AGENT and f.question_id is None and f.note == rl.ROUTER_DOWN_NOTE


# --- turnos con estado ----------------------------------------------------

def test_sin_estado_una_pregunta_nueva_no_es_seguimiento():
    state = rl.DialogState()
    assert rl.plan_followup("qué hora es", state, now=100.0) is None


def test_pregunta_pendiente_se_contesta_con_la_eleccion_y_conserva_su_id():
    state = rl.DialogState(pending=rl.Pending("cuéntame un chiste", 42, at=100.0))
    plan = rl.plan_followup("Gemini", state, now=110.0)
    assert isinstance(plan, rl.Forward)
    assert plan.agent == rl.GEMINI_AGENT
    assert plan.text == "cuéntame un chiste"
    assert plan.question_id == 42
    assert state.pending is None


def test_pregunta_pendiente_elegir_claude_avisa_de_la_sustitucion():
    state = rl.DialogState(pending=rl.Pending("explica algo", 9, at=100.0))
    plan = rl.plan_followup("a Claude", state, now=101.0)
    assert plan.agent == rl.GEMINI_AGENT and plan.note == rl.CLAUDE_NOTE


def test_pregunta_pendiente_caducada_se_descarta_y_la_frase_es_nueva():
    state = rl.DialogState(pending=rl.Pending("algo", 1, at=100.0))
    now = 100.0 + rl.PENDING_TTL + 1
    assert rl.plan_followup("Gemini", state, now=now) is None
    assert state.pending is None


def test_pregunta_pendiente_con_otra_frase_se_descarta_y_no_se_reenvia():
    state = rl.DialogState(pending=rl.Pending("algo", 1, at=100.0))
    assert rl.plan_followup("apaga la luz del salón", state, now=101.0) is None
    assert state.pending is None


def test_bien_valora_la_ultima_respuesta_una_sola_vez():
    state = rl.DialogState(last=rl.Last(question_id=12, at=100.0))
    plan = rl.plan_followup("bien", state, now=160.0)
    assert plan == rl.Rate(question_id=12, good=True)
    assert state.last is None
    assert rl.plan_followup("bien", state, now=161.0) is None


def test_mal_valora_mal():
    state = rl.DialogState(last=rl.Last(question_id=4, at=100.0))
    assert rl.plan_followup("mal", state, now=101.0) == rl.Rate(question_id=4, good=False)


def test_valoracion_caducada_no_se_guarda():
    state = rl.DialogState(last=rl.Last(question_id=4, at=100.0))
    assert rl.plan_followup("bien", state, now=100.0 + rl.RATE_TTL + 1) is None


def test_valoracion_sin_id_no_se_guarda():
    state = rl.DialogState(last=rl.Last(question_id=None, at=100.0))
    assert rl.plan_followup("bien", state, now=101.0) is None

"""Agente de conversación «JARVIS Router» para Assist.

Assist le pasa cada frase; el agente pide destino al router de JARVIS y
reenvía a Home Assistant (órdenes de casa, sin modelo) o a Gemini. Si el
router pregunta («no encaja»), pregunta antes de gastar. Las valoraciones
«bien» / «mal» se guardan en la base de datos del router.
"""

from __future__ import annotations

import logging
import time

from homeassistant.components import conversation
from homeassistant.components.conversation import ConversationEntity, ConversationResult
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .router_client import RouterClient
from .router_logic import (
    ASK_TEXT,
    RATE_FAILED_TEXT,
    RATED_TEXT,
    ROUTE_ASK,
    Ask,
    DialogState,
    Forward,
    Last,
    Pending,
    Rate,
    forward_for,
    plan_followup,
    router_unavailable,
)

_LOGGER = logging.getLogger(__name__)
_MAX_TEXT = 1000
_AGENT_FAILED = "No he podido contestar en este momento."


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([JarvisRouterAgent(hass, config_entry)])


class JarvisRouterAgent(ConversationEntity):
    _attr_has_entity_name = True
    _attr_name = "JARVIS Router"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._attr_unique_id = entry.entry_id
        self._client = RouterClient(hass)
        self._dialogs: dict[str, DialogState] = {}

    @property
    def supported_languages(self) -> list[str] | str:
        return "*"

    async def _async_handle_message(self, user_input, chat_log) -> ConversationResult:
        text = user_input.text.strip()[:_MAX_TEXT]
        dialog = self._dialogs.setdefault(user_input.conversation_id or "default", DialogState())
        now = time.monotonic()

        plan = plan_followup(text, dialog, now)
        if isinstance(plan, Rate):
            saved = await self._client.rate(plan.question_id, plan.good)
            return self._reply(user_input, RATED_TEXT if saved else RATE_FAILED_TEXT)

        if plan is None:
            decision = await self._client.decide(text)
            if decision is None:
                plan = router_unavailable(text)
            elif decision.route == ROUTE_ASK:
                plan = Ask(ASK_TEXT, decision.id)
            else:
                plan = forward_for(decision.route, text, decision.id)

        if isinstance(plan, Ask):
            dialog.pending = Pending(text, plan.question_id, now)
            return self._reply(user_input, plan.question, continue_conversation=True)

        assert isinstance(plan, Forward)
        dialog.last = Last(plan.question_id, now)
        answer = await self._ask_agent(plan.agent, plan.text, user_input)
        return self._reply(user_input, " ".join(part for part in (plan.note, answer) if part))

    async def _ask_agent(self, agent: str, text: str, user_input) -> str:
        """Pregunta a otro agente de Assist. No se registra el texto en los logs."""
        try:
            result = await conversation.async_converse(
                self.hass,
                text,
                None,
                user_input.context,
                user_input.language,
                agent,
            )
        except Exception as err:  # noqa: BLE001 - cualquier fallo de un agente se contesta igual
            _LOGGER.warning("Agente %s falló: %s", agent, type(err).__name__, exc_info=True)
            return _AGENT_FAILED
        plain = result.response.speech.get("plain", {}).get("speech", "")
        return plain or _AGENT_FAILED

    def _reply(self, user_input, speech: str, *, continue_conversation: bool = False) -> ConversationResult:
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(speech)
        return ConversationResult(
            response=response,
            conversation_id=user_input.conversation_id,
            continue_conversation=continue_conversation,
        )


__all__ = ["GEMINI_AGENT", "JarvisRouterAgent", "async_setup_entry"]

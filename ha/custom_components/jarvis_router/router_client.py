"""Cliente del router de JARVIS (`/api/router/decide` y `/api/router/rate`).

Las credenciales y la CA viven en ficheros de la configuración de Home
Assistant (netrc 600 y certificado), nunca en el código ni en los logs.
Cualquier fallo devuelve None: el agente decide qué hacer sin el router.
"""

from __future__ import annotations

import asyncio
import logging
import netrc
import ssl
from dataclasses import dataclass

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

ROUTER_HOST = "192.168.69.225"
ROUTER_URL = f"https://{ROUTER_HOST}"
NETRC_PATH = "/config/jarvis_router.netrc"
CA_PATH = "/config/certs/jarvis-ca.crt"
ORIGIN = "https://jarvis.casa"
TIMEOUT = aiohttp.ClientTimeout(total=10)


@dataclass(frozen=True)
class Decision:
    id: int
    route: str


class RouterClient:
    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._auth: aiohttp.BasicAuth | None = None
        self._ssl: ssl.SSLContext | None = None

    async def _load(self) -> bool:
        """Lee netrc y CA una sola vez, fuera del bucle de eventos."""
        if self._auth is not None and self._ssl is not None:
            return True

        def read() -> tuple[aiohttp.BasicAuth, ssl.SSLContext] | None:
            login, _account, password = netrc.netrc(NETRC_PATH).authenticators(ROUTER_HOST) or (None, None, None)
            if not login or not password:
                return None
            return aiohttp.BasicAuth(login, password), ssl.create_default_context(cafile=CA_PATH)

        try:
            loaded = await self._hass.async_add_executor_job(read)
        except (OSError, netrc.NetrcParseError, ssl.SSLError):
            _LOGGER.warning("No se pueden leer las credenciales del router de JARVIS")
            return False
        if loaded is None:
            _LOGGER.warning("El netrc del router de JARVIS no tiene credenciales para %s", ROUTER_HOST)
            return False
        self._auth, self._ssl = loaded
        return True

    async def _post(self, path: str, body: dict) -> dict | None:
        if not await self._load():
            return None
        session = async_get_clientsession(self._hass)
        try:
            async with session.post(
                f"{ROUTER_URL}{path}",
                json=body,
                auth=self._auth,
                ssl=self._ssl,
                headers={"Origin": ORIGIN},
                timeout=TIMEOUT,
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning("Router de JARVIS %s respondió %s", path, resp.status)
                    return None
                data = await resp.json()
                return data if isinstance(data, dict) else None
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning("Router de JARVIS %s no responde: %s", path, type(err).__name__)
            return None

    async def decide(self, text: str) -> Decision | None:
        """Pide destino para una pregunta. El router la registra en su base de datos."""
        data = await self._post("/api/router/decide", {"text": text})
        if not data or not isinstance(data.get("id"), int) or not isinstance(data.get("route"), str):
            return None
        return Decision(id=data["id"], route=data["route"])

    async def rate(self, question_id: int, good: bool) -> bool:
        """Guarda si la respuesta fue buena. True si el router lo confirma."""
        data = await self._post("/api/router/rate", {"id": question_id, "good": good})
        return bool(data and data.get("ok") is True)

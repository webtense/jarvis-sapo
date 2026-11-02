"""Alta de la integración sin formulario: basta con crear la entrada.

Las credenciales del router no se piden aquí: están en /config/jarvis_router.netrc.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigFlow

from . import DOMAIN


class JarvisRouterConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="JARVIS Router", data={})

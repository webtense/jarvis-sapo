# Conectar JARVIS con la casa y con BTR (solo lectura)

JARVIS no se conecta a nada por defecto. Esta guía prepara dos conexiones
opcionales, las dos **solo de lectura**:

- **Casa (Home Assistant)**: `ha_readonly_mcp.py`, un servidor MCP pequeño de
  este repositorio.
- **BTR / ACI**: el MCP remoto de solo lectura que ya existe (HTTP).

Nada de esto está activo. Los ejemplos viven en `jarvis_home/connections.examples/`
y no se leen nunca; se activan copiando una entrada a tu
`data/jarvis/connections.json` (el fichero que es tuyo y que una actualización
no toca).

## Regla que no se negocia

**JARVIS nunca ejecuta acciones sobre la casa ni sobre BTR desde aquí.** No
enciende luces, no arma ni desarma la alarma, no toca calderas, no crea
reservas. Solo consulta. Está garantizado en el código, no en la educación del
modelo (ver "Qué puede y qué no").

## Qué puede y qué no

Home Assistant (herramientas `ha_*`):

| Puede | No puede |
|---|---|
| Listar entidades por dominio y por texto en nombre o id (`ha_list_entities`) | Llamar a servicios (`/api/services`), disparar eventos, cambiar estados |
| Estado de una entidad (`ha_get_state`) | Enviar ningún cuerpo: no hay ninguna petición POST en el código |
| Historial corto, 1 a 24 h, aclarado (`ha_get_history`) | Leer el registro de áreas (solo existe por POST); el "área" es una búsqueda de texto, p. ej. `salon` |
| Resumen: alarma, quién está en casa, sensores de energía (`ha_home_summary`) | Acceder a rutas distintas de `/api/states` e `/api/history` |

La garantía: la única función de red (`_get`) construye siempre la petición con
método GET y rechaza, antes de abrir ningún socket, cualquier ruta que no esté
en una lista corta. Un test lo demuestra. Aun así, **crea el token con un
usuario de Home Assistant de permisos mínimos** si puedes: es una segunda
barrera independiente del código.

Lo que devuelve Home Assistant (nombres, mensajes) es texto de terceros: se
limpia de caracteres de control, se trunca y se entrega marcado como dato, y la
persona de JARVIS lo cuenta sin obedecerlo.

BTR/ACI: lo que permita ese servidor, que es de solo lectura por su lado
(usuario `mcp_aci_ro`, según la memoria del proyecto). Esta guía no añade ni
quita nada ahí.

## Dónde guardar las credenciales

Nunca en el repositorio, ni en `connections.json`, ni en `.env` versionado.

- **Infisical** (recomendado): guarda `HA_URL` y `HA_TOKEN` (y
  `BTR_ACI_BASIC_AUTH`) como secretos y arranca JARVIS con ellos cargados en el
  entorno (en este equipo, el flujo habitual de `/btr-infiscal`).
- **Variable de entorno**: en el servicio, un fichero `EnvironmentFile` fuera
  del repo con permisos `600`, o `export` en la sesión que arranca JARVIS.

Los servidores MCP que JARVIS lanza heredan el entorno de JARVIS; si tu
`connections.json` usa un bloque `env`, no pongas ahí el valor real, deja que
lo herede.

> Comprueba qué entorno pasa realmente JARVIS a sus hijos: el cerebro limpia
> las variables `ANTHROPIC_*` y puede filtrar otras. Si tras activar la conexión
> la herramienta dice "Home Assistant is not configured", la variable no ha
> llegado al hijo; ese mensaje indica falta de configuración, no un fallo de red.

## Activar la casa

1. En Home Assistant: perfil de usuario, "Tokens de acceso de larga duración",
   crear uno. Cópialo directamente a Infisical; no lo pegues en ningún chat ni fichero.
2. Abre `jarvis_home/connections.examples/ha-readonly.json`, copia la entrada
   `casa` dentro de `mcpServers` en `data/jarvis/connections.json` y pon la ruta
   absoluta a `ha_readonly_mcp.py`.
3. Arranca JARVIS con `HA_URL` (por ejemplo `http://<tu-HA>:8123`) y `HA_TOKEN`
   en el entorno.
4. Reinicia el servidor y pregúntale "¿a qué estás conectado?". Si algo no
   arrancó, lo nombra.

Coste de contexto: cada herramienta se describe en cada turno (unos 250 tokens);
son cuatro, alrededor de 1.000.

## Activar BTR

1. Abre `jarvis_home/connections.examples/btr-aci-readonly.json`.
2. Sustituye `<URL_DEL_MCP_BTR_ACI>` por la URL real y calcula la cabecera
   `Basic base64(usuario:clave)` fuera del repo; guárdala en la variable
   `BTR_ACI_BASIC_AUTH`.
3. Copia la entrada `btr` a `data/jarvis/connections.json`.

No se ha podido comprobar que la versión de Claude Code que usa JARVIS expanda
`${VARIABLE}` dentro de `headers`. Si no lo hace, usa un puente stdio (por
ejemplo `mcp-remote`) que lea usuario y clave del entorno.

## Comprobaciones

- Las herramientas de Home Assistant dicen claramente cuando falta
  configuración, cuando el token es rechazado y cuando Home Assistant no
  responde, sin mostrar nunca la URL ni el token.
- La batería `tests/test_ha_readonly.py` usa un HTTP falso: no necesita red ni
  Home Assistant.

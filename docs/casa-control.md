# Controlar la casa con JARVIS: confirmación por voz y avisos hablados

Dos piezas opcionales, **las dos apagadas por defecto**:

- **Control** (`home_actions.py`): JARVIS puede *proponer* una acción sobre tu casa
  (encender una luz, poner la calefacción a 21 grados...). La dice entera en voz alta y
  **solo la ejecuta si contestas que sí**.
- **Avisos hablados** (`ha_watch.py`): JARVIS consulta Home Assistant en solo lectura y
  te avisa por voz cuando pasa algo (alarma disparada, alguien llega, una puerta lleva
  abierta demasiado, consumo alto).

Sin los ficheros de configuración de abajo, no hay acciones ni avisos: no es "todo
rechazado", es que no existe ningún camino de código que llegue a la red.

La conexión de lectura de siempre (`docs/connections-casa-btr.md`, `ha_readonly_mcp.py`)
no cambia: sigue siendo solo lectura y no puede escribir nada.

## Modelo de amenazas

Quién puede intentar que JARVIS toque tu casa sin que tú quieras, y qué lo impide.

| Quién o qué | Qué intenta | Qué lo impide |
|---|---|---|
| Una web, un README, un transcript de otra sesión o un mensaje de Home Assistant leídos por el cerebro | Que el cerebro proponga "abre la puerta del garaje" | El cerebro **solo propone**. Además, en el turno en que ha leído algo ajeno (web, fichero de un proyecto, otra sesión, Home Assistant) la herramienta `home_propose` se niega: tienes que pedirlo de nuevo, con tus palabras, en un turno limpio. Y aun proponiendo, nada se ejecuta sin tu "sí" hablado |
| El propio cerebro, engañado o equivocado | Ejecutar directamente | No hay ninguna herramienta que ejecute. La ejecución vive en una función del servidor que solo se alcanza desde tu "sí" (o desde el panel, si lo activas). Un test lo comprueba: `home_actions.perform` se llama en un único sitio |
| Una sesión de otro Claude Code (puente MCP) | Colar una acción | El puente no tiene ninguna ruta hacia el control de la casa, ni su token abre `home_propose` |
| Un "sí" dirigido al cerebro ("¿quieres que lo busque?" "sí") | Que cuente como "sí" a una acción | El "sí" solo vale si JARVIS acaba de decir la frase entera (cuenta desde que terminó de hablar), dentro de una ventana de 12 segundos y mientras nadie — ni el cerebro, ni otra sesión por el puente, ni un aviso de `ha_watch`, ni un aviso de un *run* — haya vuelto a hablar por medio. Solo valen palabras sueltas y deliberadas: en castellano "sí" (con tilde), "hazlo", "adelante", "confirma", "confirmado" (y "vale" solo acompañado de otra de estas); en inglés "yes", "confirm", "do it", "go ahead". "ok", "okay", "sure", "correcto", "confirmo", "si" sin tilde o un "vale" suelto **no** valen para una acción, aunque sí valgan para confirmar una memoria — son las palabras que una transcripción local (Whisper) aluciona más fácilmente sobre ruido o silencio. Activable `JARVIS_HOME_CONFIRM_STRICT=1` para exigir dos de esas palabras juntas ("sí, hazlo" / "yes, do it") |
| Un dominio peligroso | Alarma, cerraduras, cámaras, scripts, automatizaciones... | Prohibidos **por código** aunque los pongas en tu fichero: `alarm_control_panel`, `lock`, `camera`, `script`, `automation`, `shell_command`, `homeassistant.*`, `persistent_notification`. Cualquier otro dominio o servicio no listado en el código tampoco funciona |
| Un identificador manipulado | `LIGHT.salon`, `light.salon/../lock/unlock`, una "а" cirílica | Solo valen `a-z`, `0-9` y `_`, exactamente. Nada se normaliza: lo que no encaja se rechaza. La ruta del servicio se construye solo con dominio y servicio ya validados y se revisa una vez más antes de abrir el socket |
| Una redirección de Home Assistant (o de quien lo suplante) | Hacer que el token viaje a otro servidor | No se sigue ninguna redirección y no se usa proxy |
| El token de lectura robado o reutilizado | Servir para escribir | El token de acciones es **otro** (`HA_ACTIONS_TOKEN_FILE`). Si resulta ser igual que el de lectura, no se envía nada |
| Un run sin supervisión (con Bash) o el cerebro | Heredar el token de acciones por el entorno | `claude_env` quita todas las variables `HA_ACTIONS_*` del cerebro y de los runs |

### Lo que el código NO protege

Dilo claro, porque es lo más importante:

- **Un proceso de tu mismo usuario con Bash** (un run mal dirigido, una sesión de Claude
  Code con permisos, un script tuyo) puede leer el fichero del token (es `600`, pero es
  *tu* usuario), llamar él mismo a Home Assistant o editar tu lista blanca. Quitar las
  variables del entorno evita el reparto accidental, no esto.
- **Quien hable cerca del micrófono** puede decir "sí": no hay reconocimiento de voz de
  quién habla. Tampoco un anuncio de la tele, si el micrófono lo recoge. Por eso las
  acciones son pocas, la lista es corta y hay límite por minuto.
- **Home Assistant no tiene tokens de solo-esto**: un usuario no administrador sigue
  pudiendo llamar a servicios. Crear el token con un usuario no administrador reduce el
  daño, no lo anula. La lista blanca la aplica JARVIS, no Home Assistant.
- **El panel** (`/api/*`) se protege con el mismo origen/token que el resto del panel.
  Por eso confirmar desde el panel está desactivado salvo que tú lo actives.
- Una acción confirmada se hace con el estado que hay *ahora*: si la lista cambió entre la
  pregunta y el "sí", se rechaza; pero no se comprueba el estado físico de la casa.
- **Audio ambiente, una tele.** No hay reconocimiento de quién habla: una tele que diga
  "Jarvis, abre la persiana" y después "sí" basta para confirmar, salvo que el micrófono no
  oiga esa frase como wake word. Si vas a permitir un `cover` o un `switch` que de verdad
  importe, actívalo solo con `JARVIS_STT=local` y `JARVIS_WAKE_WORD=on` (`docs/stt-local.md`)
  — para que JARVIS solo escuche tras "Jarvis" — y además `JARVIS_HOME_CONFIRM_STRICT=1`, para
  que una sola palabra alucinada nunca baste.
- **Un *run* sin supervisión, con Bash, del mismo usuario.** Puede leer `/proc/<pid>/environ`
  de un proceso de JARVIS y los ficheros `600` (tokens, `home-actions.json`), llamar él mismo
  a Home Assistant sin pasar por nada de esto, y editar o reescribir `home-actions.json` o
  `home-actions.log` — la auditoría es falsificable por ese mismo usuario, no es una prueba
  ante un tercero. `claude_env` le quita las variables de acciones, pero un proceso que lee
  ficheros no las necesita. Si activas el control de la casa, considera un usuario de sistema
  **distinto** del tuyo para ejecutar JARVIS (y sus *runs*), de forma que ni el token ni el
  fichero de la lista blanca ni el log sean legibles o editables desde tu propia sesión de
  Bash habitual.

## Preparar el token aparte en Home Assistant

1. En Home Assistant: **Ajustes → Personas → Usuarios → Añadir usuario**. Nómbralo, por
   ejemplo, `jarvis-acciones`. **No** le des permisos de administrador y, si puedes, limita
   el acceso a la red local.
2. Inicia sesión como ese usuario, abre su **Perfil → Seguridad → Tokens de acceso de larga
   duración** y crea uno. Cópialo una sola vez.
3. Guárdalo en un fichero que solo tú puedas leer, **fuera del repositorio y de `data/`**,
   y distinto del fichero del token de lectura. Por ejemplo
   `~/.config/jarvis/ha_actions_token` con permisos `600`. Pega el token dentro sin
   escribirlo nunca en una línea de comandos que quede en el historial ni en un `.env`.
4. En el `.env` de JARVIS (no versionado) pon solo las rutas, nunca el token:

   ```
   HA_ACTIONS_TOKEN_FILE=/ruta/absoluta/ha_actions_token
   HA_ACTIONS_URL=<dirección de Home Assistant>     # opcional: si falta, se usa HA_URL
   ```

   El servidor rechaza un fichero legible por grupo u otros. **No existe** `HA_ACTIONS_TOKEN`
   (una variable con el token se hereda): solo el fichero.
5. Revoca el token desde ese mismo perfil si dudas de él. Rótalo de vez en cuando.

Si usas `http://` en una red local, el token viaja sin cifrar por esa red. Prefiere `https://`
o Tailscale.

## Usar el token de lectura para las acciones (decisión del usuario)

**No es lo recomendado.** Por defecto JARVIS exige un token de acciones distinto del de
lectura y se niega a enviar nada si ambos ficheros guardan el mismo valor. Si decides, con
conocimiento del riesgo, usar el token que ya tienes también para actuar, es un opt-in
explícito:

```
HA_ACTIONS_ALLOW_READ_TOKEN=1        # solo vale exactamente "1"; cualquier otro valor no activa nada
```

- Con la variable y **sin** `HA_ACTIONS_TOKEN_FILE`, las acciones usan el fichero de
  `HA_TOKEN_FILE`.
- Con la variable y `HA_ACTIONS_TOKEN_FILE` apuntando a un fichero con el mismo token (o al
  mismo fichero), JARVIS lo acepta en lugar de rechazarlo.
- Sin la variable nada cambia: se rechaza el mismo token y no hay respaldo.
- Siguen exigiéndose todas las comprobaciones del fichero (permisos `600`, fichero regular,
  sin redirecciones, sin proxy). `claude_env` quita la variable de los *runs* y del cerebro.

**Por qué no es lo recomendado.** Un token con permisos de escritura de un usuario
administrador que se filtre equivale a control total de Home Assistant, incluidos los
dominios que JARVIS prohíbe (alarma, cerraduras, cámaras, scripts, automatizaciones...). La
lista blanca la aplica JARVIS, no Home Assistant: quien tenga el token puede saltársela
llamando a Home Assistant directamente.

**Qué sigue protegiendo JARVIS** aunque el token sea el mismo: la confirmación hablada
(con las reglas de `bridge.py`), la lista blanca y los dominios prohibidos, el filtro del
resort (`ha-exclude.json`), el límite de acciones por minuto y la auditoría en
`home-actions.log`.

**Se ve siempre.** Mientras esté activo, el preflight y el log de arranque avisan de que las
acciones usan el token de lectura por decisión del usuario. El aviso no incluye rutas ni
valores. Para volver a lo seguro, crea el token aparte (sección anterior) y quita la variable.

## La lista blanca: `data/jarvis/home-actions.json`

Es tu fichero. JARVIS no lo crea ni lo edita. Hay un ejemplo inactivo en
`jarvis_home/connections.examples/home-actions.json` (cópialo, ponle `"enabled": true` y
**deja solo lo que quieras**).

```json
{
  "enabled": true,
  "panel_confirm": false,
  "entities": {
    "light.salon": "la luz del salón",
    "climate.salon": "la calefacción del salón"
  },
  "services": {
    "light": ["turn_on", "turn_off"],
    "climate": ["set_temperature"]
  },
  "limits": {
    "max_per_minute": 3,
    "climate": {"min_temp": 17, "max_temp": 23},
    "media_player": {"max_volume": 0.4}
  }
}
```

- `entities`: las entidades concretas, con el nombre que dirá JARVIS ("¿Apago la luz del
  salón, señor?"). Es *tu* palabra: no se usa el nombre que tenga en Home Assistant.
- `services`: por dominio, los servicios que permites. Solo vale lo que el código también
  permite:

  | Dominio | Servicios que el código puede permitir |
  |---|---|
  | `light` | `turn_on` (con `brightness_pct` 1-100 opcional), `turn_off`, `toggle` |
  | `switch` | `turn_on`, `turn_off`, `toggle` |
  | `scene` | `turn_on` |
  | `climate` | `set_temperature` (siempre con `temperature` dentro de `limits.climate`) |
  | `media_player` | `media_play`, `media_pause`, `media_play_pause`, `volume_set` (hasta `max_volume`, 0.5 por defecto), `volume_up`, `volume_down` |
  | `cover` | `open_cover`, `close_cover`, `stop_cover` (solo las entidades que listes) |

- `limits.climate`: obligatorio para poder tocar el termostato, entre 5 y 30 grados. Se
  redondea a medios grados. Sin límites, el termostato queda bloqueado.
- `limits.max_per_minute`: de 1 a 10 (3 por defecto). Pasado el límite, JARVIS dice que
  espera y no hace nada.
- `panel_confirm`: `false` por defecto. Si lo pones a `true`, también se puede confirmar
  desde el panel (`POST /api/home/<id>/confirm`); rechazar se puede siempre. **Pendiente:**
  el panel (`/dashboard`) todavía no tiene botones de Confirmar/Rechazar para una propuesta
  de acción sobre la casa — hoy solo se puede confirmar o rechazar llamando a esa API
  directamente (o de viva voz). El backend y `snapshot()["home"]` ya exponen lo necesario;
  falta la interfaz.
- El fichero debe ser tuyo, un fichero normal (no un enlace) y **no escribible por grupo ni
  otros** (`chmod 600`); si no, JARVIS lo ignora y lo dice en el log. Un fichero con
  `enabled` distinto de `true`, roto o sin entidades útiles deja el control apagado.
- Lo prohibido o desconocido que pongas se ignora y queda anotado en el log del servidor.

## Cómo funciona una acción

1. Le pides algo: "Jarvis, apaga la luz del salón".
2. El cerebro llama a `home_propose` con la entidad y el servicio. Se comprueba contra tu
   lista y, si no encaja, se rechaza y se anota. Si encaja, **no se hace nada** todavía.
3. JARVIS dice la acción entera: "¿Apago la luz del salón, señor?". Solo hay una acción
   pendiente a la vez; una nueva sustituye a la anterior.
4. Contestas "sí" (o "hazlo", "adelante", "confirma", "confirmado"; "vale" solo si va con
   otra de esas), con la ventana de 12 segundos desde que acabó la frase — y solo si nadie
   ha dicho nada más por medio (ver más abajo). Un "no" la descarta. Cualquier otra cosa se
   trata como una conversación normal. Con `JARVIS_HOME_CONFIRM_STRICT=1` hace falta decir
   dos de esas palabras juntas ("sí, hazlo"), nunca una sola.
5. Se vuelve a comprobar tu lista, el límite por minuto y que el registro se pueda escribir.
   Después se hace **una sola** petición `POST /api/services/<dominio>/<servicio>` con
   `entity_id` y los parámetros validados, y JARVIS dice "Hecho, señor." (o por qué no).
6. Una acción propuesta caduca a los 2 minutos.

El cerebro nunca sabe si se hizo: se le dice que está pendiente y que acabe su turno.

## Modo simulacro

Antes de fiarte, pruébalo: `JARVIS_HOME_DRYRUN=1` en el `.env` y reinicia JARVIS. Todo
funciona igual (pregunta, "sí", registro) pero no se envía ninguna petición ni hace falta
token: JARVIS dice "Simulacro, señor: no he tocado nada." y el registro lleva `"dryrun": true`.

## Auditoría: `data/jarvis/home-actions.log`

Una línea JSON por intento, fichero `600`, sin tokens ni direcciones. Se rota a
`home-actions.log.1` al pasar de 1 MB. **Si no se puede escribir, no se hace ninguna acción.**

```json
{"ts":"2026-10-21T09:12:03+00:00","entity":"light.salon","service":"light.turn_off","params":{},"confirmed_by":"voice","result":"ok","dryrun":false}
```

- `confirmed_by`: `voice`, `panel` o `none` (propuestas y rechazos del filtro).
- `result`: `proposed`, `ok`, `dryrun`, `rejected`, `expired`, `http_<código>`, `redirect`,
  `unreachable`, `refused: ...`. Nunca el cuerpo de la respuesta de Home Assistant.

Para verlo: `jq -c . data/jarvis/home-actions.log`.

## Avisos hablados: `data/jarvis/home-alerts.json`

Opt-in también. Ejemplo inactivo en `jarvis_home/connections.examples/home-alerts.json`.
Usa el cliente de **solo lectura** y el token de lectura (`HA_URL` y `HA_TOKEN_FILE` en el
entorno del servidor JARVIS, es decir, en su `.env`). No tiene ningún código que escriba.

| `type` | Avisa de |
|---|---|
| `alarm_triggered` | La alarma ha saltado. Es el único aviso **urgente** |
| `person` | Una persona concreta llega a casa o sale (`events`: `arrive`, `leave`) |
| `open_too_long` | Una puerta o ventana (`binary_sensor`, `cover`) lleva abierta más de `minutes` |
| `power_above` | Un sensor de potencia (W o kW) supera `above_w` |

Para no cansarte:

- **Solo avisa de cambios**: mientras la condición siga igual, no repite. Para volver a
  avisar tiene que haberse ido y volver.
- **Antirrebote** (`debounce_s`): la condición tiene que mantenerse ese tiempo (90 s por
  defecto en personas, 60 s en consumo). Un GPS que baila en el borde de casa no son tres
  llegadas.
- `min_interval_s` por regla y evento (mínimo 30 s), y `max_per_hour` en total — la alarma
  no cuenta en este último (merece repetirse si de verdad suena) pero sí tiene su propio
  tope, `alarm_max_per_hour` (10 por defecto, hasta 60): una regla de alarma que alterne
  entre `triggered` y `disarmed` podría si no hablar en URGENT cada `min_interval_s` sin
  límite. Al alcanzarlo queda anotado en el log del servidor.
- `unavailable`, `unknown`, una entidad que no existe o un Home Assistant caído son "sin
  información", nunca un cambio: que Home Assistant se reinicie no hace que todos "salgan".
- Los consumos usan una histéresis del 10 %: tras avisar, no vuelve a avisar hasta bajar de
  `above_w` × 0,9 y mantenerse.
- Al arrancar JARVIS, la posición de cada persona es solo punto de partida (no se avisa);
  una alarma ya disparada o una puerta abierta desde hace rato sí se avisan, una vez.
- Los avisos no urgentes esperan a una pausa y se agrupan; nunca pisan tu voz ni la de
  JARVIS. Si no hay ninguna pestaña escuchando, salen como notificación de escritorio.

Los nombres que dice JARVIS son los de tu fichero (o el id de la entidad), nunca los
"friendly names" de Home Assistant, que son texto de terceros.

## Puesta en marcha, paso a paso

1. Crea el usuario y el token aparte (arriba) y el fichero `600`.
2. Pon `HA_ACTIONS_TOKEN_FILE` (y `HA_ACTIONS_URL` si hace falta) en el `.env`.
3. Copia el ejemplo a `data/jarvis/home-actions.json`, `chmod 600`, deja una o dos entidades,
   `"enabled": true`.
4. Arranca con `JARVIS_HOME_DRYRUN=1`. Pide "apaga la luz del salón", di "sí" y mira
   `home-actions.log`.
5. Quita el simulacro cuando estés conforme.
6. Los avisos, aparte: `home-alerts.json` con una sola regla y sube desde ahí.

## Separar lo que no es tuyo

El mismo filtro de `docs/connections-casa-btr.md` protege también el control:
una entidad de `data/jarvis/ha-exclude.json` (o de `HA_EXCLUDE_FILE`) nunca se
ejecuta desde `home_actions.py`, aunque esté en tu propio
`home-actions.json` — es una segunda comprobación, independiente de la
primera. Mismo formato (`entity_ids` exactos, `patterns` con `re.fullmatch`,
`domains`), mismo fallo cerrado: si el fichero de exclusión existe pero no es
válido, toda acción se rechaza (se anota en `home-actions.log` como una
petición rechazada) hasta que vuelva a ser válido. Sin el fichero, el control
funciona exactamente igual que antes de esta sección.

Filtrar aquí no sustituye a separar bien las cuentas: es una lista que aplica
JARVIS, no un límite de Home Assistant. El usuario de acciones (`docs` más
abajo) sigue siendo la protección real si el token se usa fuera de JARVIS.

## Decisiones que son tuyas

JARVIS no puede decidirlas por ti:

- **Qué entidades permites** y con qué nombre las dirá.
- **Qué dominios**: por ejemplo, ¿persianas sí o no? ¿enchufes (cuidado con lo que haya
  detrás de un `switch`)?
- **Los límites de temperatura** (`min_temp`, `max_temp`) y el volumen máximo.
- **Cuántas acciones por minuto** toleras.
- **Si se puede confirmar desde el panel** (`panel_confirm`) o solo de viva voz.
- **Qué avisos quieres** y con qué umbrales (consumo, minutos de puerta abierta, a quién
  vigilar).
- Si montas la red de la casa de forma que un token de acciones merezca un usuario de
  Home Assistant con acceso restringido a tu red local.

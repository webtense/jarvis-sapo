> [English version](README.md)

> **jarvis-ubuntu** es un fork de [ethanplusai/jarvis](https://github.com/ethanplusai/jarvis) (© 2026 Ethan Rogers) para Ubuntu/Linux.
> Mantiene la licencia original: uso personal, educativo y **no comercial**; el uso comercial requiere licencia del autor.
> Estado: en desarrollo (ver [CHANGELOG](CHANGELOG.md)). Ubuntu: `scripts/install-ubuntu.sh` y después `scripts/start-ubuntu.sh`. Para que JARVIS pueda contestar permisos en otras sesiones, lanza `claude` con `scripts/jarvis-claude` (usa tmux; Wayland no permite pulsar teclas en otras ventanas).

# JARVIS

**Just A Rather Very Intelligent System: una voz para Claude Code.**

JARVIS es un mayordomo británico que se sienta encima del Claude Code que ya
pagas. Hablas con él. Plantea un proyecto contigo en voz alta, una pregunta
cada vez; cuando os habéis decidido por algo, escribe el diseño como un fichero
dentro de tu proyecto; después arranca una sesión real de Claude Code sobre él y
la conduce por las fases de plan, revisión y ejecución. Mientras corre, vigila
todas las sesiones de Claude Code de tu máquina y, cuando alguna se queda
esperando a una persona, te dice cuál, en voz alta, sin que tengas que mirar.

> "Así se hará, señor."

![Seis segundos del orbe de JARVIS mientras habla, en bucle. Dos mil partículas
mantienen la forma de una esfera azul hueca, unidas por líneas tenues entre las
que se acercan lo bastante; un borde brillante resalta su parte inferior. A lo
largo de cada frase, la esfera se hincha, se ilumina y se inclina hacia ti; luego
vuelve atrás y se contrae durante la pausa antes de la siguiente, tres veces,
mientras la cámara gira unos grados a su
alrededor.](docs/images/orb-speaking.gif)

*Lo que realmente miras mientras hablas con él: `frontend/src/orb.ts`,
renderizado en vivo. El audio que mueve el pulso es sintético (una envolvente
con forma de habla ajustada a una medición del analizador real, no una
grabación de su voz), pero cada píxel es ese fichero en ejecución. Se regenera
con `scripts/make_orb_loop.py`.*

![Un recorrido de veintitrés segundos por el panel de JARVIS, en bucle. Empieza
en Runs: un panel rojo "Needs Attention" sobre una ejecución fallida y otra que
agotó el tiempo, y después Active e History, con cada fila mostrando el
proyecto, el prompt, una etiqueta de estado, el tiempo transcurrido y los
tokens. Una ejecución se abre para mostrar su prompt, su coste, su modelo y su
transcripción en vivo. La pestaña Sessions muestra todas las conversaciones de
Claude Code de la máquina agrupadas por proyecto; al pulsar una bloqueada, la
columna derecha pasa de un recuento a una banda roja que dice "waiting on you
for 51m — permission prompt", con la pregunta que el CLI hizo realmente citada
debajo. Specs muestra un documento de diseño con grandes secciones numeradas
que se responden por voz. Projects detalla las conversaciones, ejecuciones y
progreso de construcción de un proyecto. Usage termina con los dos indicadores
de la suscripción: una ventana de cinco horas al 62 % y otra de siete días al
84 %.](docs/images/dashboard-walkthrough.gif)

*Todo el panel, recorrido con clics. Datos de ejemplo ficticios en todo
momento: los proyectos, prompts, personas y cifras de todas las capturas de
esta página son inventados.*

---

## Ubuntu

Fork `jarvis-ubuntu`. Lo que hay en el repo:

- **Instalación:** `scripts/install-ubuntu.sh`. Comprueba Python 3.11+ y Node 18+, instala los paquetes del sistema (pide `sudo`), crea `.venv`, instala Playwright/Chromium, descarga la voz local de Piper, hace `npm ci` en `frontend/`, genera `cert.pem`/`key.pem` y copia `.env.example` a `.env`. Si los paquetes ya están instalados, `JARVIS_SKIP_APT=1 scripts/install-ubuntu.sh` omite `apt` y `sudo`.
- **Arranque:** `scripts/start-ubuntu.sh`. El backend escucha solo en `127.0.0.1` y Vite en el puerto fijo 5173 (`http://localhost:5173`, panel en `/dashboard.html`).
- **Idioma:** `JARVIS_LANGUAGE=es` (castellano) o `en` en `.env`. Cambia la persona, la voz de Piper y el idioma que escucha el navegador.
- **Voz:** sin clave, con Piper local. La voz por defecto es `es_ES-davefx-medium`; se cambia con `PIPER_VOICE`.
- **Servicio:** `scripts/install-service.sh` instala JARVIS como servicio de sistema (arranca con el equipo y se reinicia si cae).
- **Voz de entrada local (opcional):** `JARVIS_STT=local` y `JARVIS_WAKE_WORD=on`; ver `docs/stt-local.md`. Desactivada por defecto.
- **Puente para sesiones de Claude Code (opcional):** `scripts/install-bridge.sh`; ver `docs/bridge.md`.
- **Casa y BTR en solo lectura (opcional):** ver `docs/connections-casa-btr.md`.
- **Acceso por túnel SSH:** desde otro equipo, `ssh -L 5173:127.0.0.1:5173 usuario@servidor` y abre `http://localhost:5173`. Si usas otro puerto local (por ejemplo `-L 5174:127.0.0.1:5173`), declara ese origen en `.env`: `JARVIS_ALLOWED_ORIGINS=http://localhost:5174`.
- **Permisos en otras sesiones:** para que JARVIS pueda contestar a los permisos de otras sesiones de Claude Code, lanza `claude` con `scripts/jarvis-claude` (usa tmux; Wayland no permite pulsar teclas en otras ventanas).

## Lo que cuesta

**No hay uso de API de IA, y no puede haberlo.** El cerebro de JARVIS es un
proceso de Claude Code que corre con *tu* suscripción de Claude, el mismo inicio
de sesión que usas en el terminal. No hay ninguna clave de API de Anthropic en
el camino de la voz, y no hay forma de ponerla ahí por accidente:

```python
# claude_env.py
SCRUBBED_ENV_PREFIXES = ("CLAUDE_CODE_", "ANTHROPIC_")
SCRUBBED_ENV_KEYS = {"CLAUDECODE"}
```

Todo proceso de Claude Code que JARVIS lanza (el cerebro y cada construcción)
se arranca mediante `claude_env.child_env()`, que antes elimina **todas** las
variables `ANTHROPIC_*` del entorno. Es deliberado y no es un adorno: el CLI
*prefiere* en silencio una `ANTHROPIC_API_KEY` heredada antes que tu inicio de
sesión, y `claude auth status` sigue informando `loggedIn: true` mientras la
facturación pasa discretamente a la clave. Por eso JARVIS quita la clave en vez
de fiarse de no pasarla. Deja una en tu `.env` si quieres: la comprobación de
arranque te avisará de que está ahí, y el cerebro seguirá sin verla nunca.

![El panel Subscription del dashboard, medido hace tres minutos: un indicador de
sesión de 5 horas al 62 por ciento que se reinicia a las 6:00 pm, y uno de
semana de 7 días al 84 por ciento que se reinicia el domingo a las 8:00 am.
Debajo, una nota titulada "what the CLI reports": dos ventanas y ningún límite
separado por
modelo.](docs/images/dashboard-usage.png)

*Así que el número que importa no es una cifra en dólares, sino cuánto de las
dos ventanas de tu suscripción se ha gastado. Datos de ejemplo ficticios.*

**Lo único que pagas es [Fish Audio](https://fish.audio/)**, que da voz a
JARVIS. Ten en cuenta que no hay alternativa: `tts.py` no devuelve nada sin
`FISH_API_KEY`, así que JARVIS se queda mudo y sus respuestas aparecen como
texto en el navegador. (En este fork, sin clave se usa la voz local de Piper;
ver la sección Ubuntu.) Si prefieres otro TTS, es un fichero pequeño y bien
aislado que sustituir; ver *Hazlo tuyo* más abajo.

## Qué hace

- **Plantea en voz alta.** La conversación es la fase de diseño. Hace una
  pregunta cada vez, ofrece dos o tres enfoques y no empieza nada hasta que
  hayáis acordado uno.
- **Escribe el diseño.** Lo que habéis acordado se guarda en disco como
  `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` *dentro del proyecto que
  se construye*, antes de lanzar un solo proceso. Puedes releerlo por sección
  numerada y aprobarlo por voz, o abrirlo en el dashboard.
- **Conduce la construcción.** Una construcción es una sesión real de
  `claude -p` a la que se entrega un encargo que le dice que escriba un plan por
  fases, lo revise contra el diseño y después lo ejecute tarea a tarea con
  desarrollo guiado por tests, marcando las casillas del plan a medida que
  avanza. "Hasta dónde ha llegado" se responde leyendo esas casillas, no
  adivinando.
- **Vigila todas las sesiones de Claude Code de la máquina**, no solo las
  suyas. Pregunta "¿qué sesiones mías están esperándome?" y lo comprueba en
  vivo. Puede publicar un mensaje en una de ellas y contestar a una petición de
  permiso de otra que corre en Terminal.app pulsando una sola tecla.
- **Te interrumpe cuando importa.** Una sesión que necesita a una persona se
  dice en voz alta de inmediato; una que simplemente ha terminado se agrupa en
  una sola frase en la siguiente pausa. Si nadie tiene abierta la pestaña del
  navegador, se convierte en una notificación de macOS.
- **Recuerda.** La memoria a largo plazo es una carpeta de ficheros Markdown
  planos, un dato por fichero, con un índice que el cerebro ve siempre. Puedes
  leerla y editarla en cualquier editor de texto.
- **Lo registra todo.** Cada proceso de Claude Code que JARVIS arranca es una
  *ejecución* (run): una fila en SQLite con su prompt, proyecto, estado, uso de
  tokens y el flujo completo de eventos. Míralas en vivo en `/dashboard`.

![La vista Runs del dashboard de JARVIS. Un panel rojo "Needs Attention"
contiene una ejecución fallida y otra que agotó el tiempo, con el código de
salida y el error impresos debajo. Más abajo, un panel Active con una ejecución
en curso y otra en cola, y después History. Cada fila muestra el proyecto, el
prompt que recibió la ejecución, una etiqueta de estado, cuánto tardó, los
tokens gastados y la
hora.](docs/images/dashboard-runs.png)

*La vista Runs. Cada proceso de Claude Code que JARVIS arranca es una fila aquí,
con el prompt que lo inició. Datos de ejemplo ficticios.*

El dashboard tiene seis pestañas: Runs, Sessions, Memory, Specs, Projects y
Usage. Usage muestra lo que queda de las ventanas de cinco horas y siete días de
tu suscripción, y quién lo ha gastado.

![La vista Sessions. A la izquierda, un panel "Needs You" con dos sesiones
bloqueadas y, debajo, todas las sesiones de Claude Code de la máquina agrupadas
por proyecto y etiquetadas como needs you, working, shell, idle, gone o
unknown. A la derecha, la sesión seleccionada: una banda roja que dice que
lleva 51 minutos esperándote en una petición de permiso, que solo tu pulsación
puede contestar y, citada debajo, la pregunta que el CLI hizo
realmente.](docs/images/dashboard-sessions.png)

*Sessions, con una sesión bloqueada abierta junto a la lista. El motivo por el
que una sesión está atascada son las palabras del propio CLI, no una
suposición. Datos de ejemplo ficticios.*

## Requisitos

- **macOS.** El control del terminal, el listado de ventanas, las capturas de
  pantalla y las notificaciones pasan todos por AppleScript. Hoy no hay camino
  para Linux ni Windows. (El fork `jarvis-ubuntu` aporta el camino de Linux;
  ver la sección Ubuntu.)
- **Google Chrome.** No es una preferencia, es una restricción. El micrófono usa
  la Web Speech API (`SpeechRecognition` / `webkitSpeechRecognition`, ver
  `frontend/src/voice.ts`), que Firefox nunca ha implementado. No hay
  transcripción en el servidor a la que recurrir.
- **Claude Code, instalado y con la sesión iniciada.** `npm install -g
  @anthropic-ai/claude-code` (2.1.224 o posterior); después ejecuta `claude` una
  vez e inicia sesión. Es sobre lo que corre JARVIS.
- **Python 3.11+** y **Node.js 18+**.
- **Una clave de API de Fish Audio.** Obligatoria; no hay voz de reserva.
  (En el fork para Ubuntu es opcional: sin ella se usa Piper en local.)

## Instalación

```bash
git clone <your fork of this repo> jarvis
cd jarvis

cp .env.example .env

pip install -r requirements.txt
python -m playwright install chromium    # for read_page / look_at_page

cd frontend && npm install && cd ..
```

**Rellena el `.env`.** `.env.example` lo documenta todo; en resumen, una clave
obligatoria y tres opcionales:

```env
FISH_API_KEY=...            # required, no fallback
# JARVIS_BRAIN_MODEL=sonnet # optional: the brain's model
# FISH_VOICE_ID=...         # optional: a different voice
# USER_NAME=Tony            # optional: what he calls you
```

**Genera los certificados.** No son opcionales:

```bash
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes -subj '/CN=localhost'
```

`server.py` sirve HTTPS siempre que `cert.pem` y `key.pem` estén a su lado, y
`frontend/vite.config.ts` redirige `/api` y `/ws` a `https://localhost:8340`.
Sin el par, el backend sirve HTTP plano, el proxy del servidor de desarrollo no
puede alcanzarlo y todas las llamadas de API y WebSocket desde el frontend
fallan con un 500 mientras la página en sí sigue cargando: una forma
desconcertante de perder una tarde. Genéralos una vez y olvídate.

Después, en dos terminales:

```bash
python server.py --host 127.0.0.1        # terminal 1
cd frontend && npm run dev               # terminal 2
```

Abre **Chrome** en `http://localhost:5173`, pulsa una vez en la página para
permitir el audio y habla. El dashboard está en
`http://localhost:5173/dashboard.html`.

> **Quién puede acceder.** `--host` vale por defecto `127.0.0.1`: solo esta
> máquina. Todo lo que JARVIS sirve actúa con toda tu autoridad: `POST
> /api/runs` lanza `claude --dangerously-skip-permissions`, y `/api/sessions`
> lee todas las conversaciones de Claude Code que tienes. Una comprobación
> estricta de `Origin` se interpone ante cada WebSocket y cada ruta que cambia
> estado, de modo que una página web que estés visitando no puede abrir
> `/ws/voice` y hablar como tú. No hay nada que configurar: la página es del
> mismo origen, así que el navegador envía un `Origin` que ninguna página puede
> falsificar.
>
> Un `Origin` solo es infalsificable cuando lo pone un *navegador*, sin embargo.
> Cualquier cosa que hable HTTP en crudo puede decir que es el dashboard, así
> que un cliente sin `Origin` (un script, el hijo MCP del propio cerebro) debe
> presentar en cambio el token de `<data-dir>/jarvis/tool-token`, y en la red el
> enlace al bucle local es el resto de la respuesta. `--host 0.0.0.0` sigue
> funcionando si lo dices en serio; define `JARVIS_ALLOWED_ORIGINS` con la
> dirección en la que realmente vayas a abrir la página, o el navegador también
> será rechazado.
>
> Esta es una de las cinco decisiones de confianza deliberadas de este diseño;
> consulta [En qué confía, y por qué](#en-qué-confía-y-por-qué) antes de
> ejecutarlo.

Una cosa más sobre Chrome: el permiso del micrófono se asigna al **origen,
puerto incluido**. Si reinicias Vite y acaba en el 5174, el permiso no te sigue,
y Chrome no volverá a preguntar: se queda denegado, en silencio. Si el
micrófono deja de funcionar cuando todo lo demás parece correcto, comprueba que
el puerto no se haya movido.

## Conexiones: trae las tuyas

JARVIS se entrega sin conectar a nada, porque lo útil no es nuestra suposición
de lo que usas, sino la puerta. Vale cualquier servidor MCP. Pon su bloque
`mcpServers` en `data/jarvis/connections.json`:

```json
{
  "mcpServers": {
    "notion": {
      "command": "npx",
      "args": ["-y", "@notionhq/notion-mcp-server"],
      "env": { "NOTION_TOKEN": "secret_..." }
    }
  }
}
```

Ese es el bloque del README del propio servidor, sin cambios. Reinicia JARVIS y
pregúntale **"¿a qué estás conectado?"**: responde a partir de lo que realmente
arrancó, nombra todo lo que no arrancó y por qué, y te dice lo que le cuesta.
(Unos 250 tokens de su contexto por herramienta, en cada turno; una cifra
medida, y contada sobre las herramientas que realmente cargaron, no sobre las
que querías cargar. No se descuenta de cuánto recuerda de tu conversación.)

Tus otros servidores MCP se ignoran deliberadamente. JARVIS corre con
`--strict-mcp-config`, así que nada de `~/.claude.json` ni de Claude Desktop le
llega a menos que lo pongas tú en ese fichero; conectar algo a JARVIS debe ser
algo que hiciste, no algo que heredaste.

Lo que devuelva un servidor conectado se trata como una página web: se
comunica, nunca se obedece. Respondiste por el código del servidor al
instalarlo; no escribiste la incidencia, el correo ni la página compartida que
te devuelve.

Consulta `skills/jarvis-setup/SKILL.md` para el recorrido más largo, incluido
qué hacer cuando un servidor no arranca.

Las primeras versiones de JARVIS venían conectadas a Calendario, Mail y Notas
de Apple. Eso se acabó: costaban tres avisos de permisos de AppleScript en el
primer arranque, antes de que le hubieras oído decir nada, para un asistente
cuyo trabajo es construir software. Una puerta que eliges vale más que tres que
elegimos nosotros.

## Cómo funciona

```
Microphone → Chrome Web Speech API → WebSocket → FastAPI (server.py)
                                                      │
                                                      ▼
                                        the brain (brain.py) — ONE long-lived
                                        `claude -p` process on your subscription
                                                      │
                    ┌─────────────────────────────────┼──────────────────────────────┐
                    ▼                                 ▼                              ▼
        speech.py → Fish Audio → speaker    MCP tools (jarvis_mcp.py       session_watch.py
                                             → POST /internal/tool)     (every Claude Code
                                                      │                  session on the machine)
                                                      ▼
                                        RunExecutor → run store (SQLite)
                                                      │
                                                      ▼
                                        /api/runs + /ws/runs → /dashboard
```

El cerebro es **un único proceso persistente**, no una petición por turno. Tu
transcripción se escribe en su entrada estándar; su respuesta vuelve en flujo y
se divide en frases según llega, de modo que las primeras palabras ya se están
pronunciando mientras el resto aún se escribe. Accede a las capacidades propias
de JARVIS como herramientas MCP a través de un canal stdio que reenvía a `POST
/internal/tool`. El conjunto exacto es la lista de permitidos que hay al
principio de `brain.py`, que es la lista que conviene leer en vez de citar aquí
un número.

Lo que *no* recibe es una forma de actuar sobre algo que acaba de leer en la
web. A un turno que usó `WebSearch`, `WebFetch`, `read_page` o `look_at_page` se
le deniega toda herramienta que actúe durante el resto de ese turno, y ninguna
herramienta que actúe se ejecuta si el turno no empezó contigo hablando. El
texto que llega de una página web, una captura de pantalla o la transcripción de
otra sesión no es de fiar y se trata como tal.

Todo lo que se lanza como trabajo real pasa por una única canalización
registrada, y en ella se mantienen dos invariantes:

1. **Una ejecución siempre llega a un estado terminal**: `succeeded`, `failed`,
   `timed_out` o `cancelled`. Nunca se queda en `running`.
2. **Toda transición de estado es una escritura en la base de datos antes de ser
   una notificación.** El WebSocket es una pista de invalidación de caché, nunca
   una fuente de verdad; el dashboard se reconcilia contra `/api/runs`.

| Capa | Tecnología |
|------|-----------|
| Backend | FastAPI + Python (`server.py`) |
| Frontend | Vite + TypeScript + Three.js (interfaz de voz), TS puro (dashboard) |
| Comunicación | WebSocket: mensajes JSON, audio MP3 en base64 |
| Cerebro | Un proceso `claude -p` de larga vida, Sonnet por defecto, con tu suscripción |
| Voz | Fish Audio, una petición por frase |
| Sistema | AppleScript: Terminal, Chrome, notificaciones, capturas de pantalla |
| Almacenamiento | SQLite para ejecuciones y uso; Markdown plano para la memoria |

### Ficheros clave

| Fichero | Para qué sirve |
|------|---------|
| `server.py` | El servidor: gestor de WebSocket, API HTTP y todos los manejadores de `/internal/tool` |
| `brain.py` | El cerebro de voz: arranque, turnos, reinicios y rotación de contexto |
| `claude_env.py` | El entorno que recibe cada hijo lanzado, incluida la limpieza de `ANTHROPIC_*` |
| `jarvis_mcp.py` | Servidor MCP por stdio que expone las herramientas de JARVIS al cerebro |
| `speech.py` | División en frases, rechazo de eco, interrupción y la cola de todo lo que dice JARVIS |
| `tts.py` | Síntesis con Fish Audio: toda la voz, en un fichero pequeño |
| `builds.py` | Diseño, encargo y plan: la canalización detrás de una construcción real de varias horas |
| `specs.py` | La superficie de revisión: releer un diseño por sección numerada y aprobarlo |
| `run_store.py` | Tablas SQLite `runs` / `run_events` y el enum de seis estados |
| `run_executor.py` | Lanza ejecuciones y conduce cada una hasta un estado terminal |
| `stream_parser.py` | Análisis puro de la salida stream-json de Claude Code (sin E/S) |
| `session_watch.py` | Vigila todas las sesiones de Claude Code de la máquina |
| `session_steer.py` | Publica un mensaje en el socket de buzón de una sesión en curso |
| `dialog.py` | Pulsa una tecla en la pestaña de Terminal que posee una sesión |
| `jarvis_memory.py` | La memoria como carpeta de ficheros Markdown, no como base de datos |
| `jarvis_home/CLAUDE.md` | La persona y las reglas de JARVIS: el fichero que editar para cambiar quién es |
| `preflight.py` | Comprobaciones del primer arranque: versión del CLI, sesión, Accesibilidad, clave de Fish |
| `data_paths.py` | La única fuente de verdad sobre dónde escribe JARVIS |
| `frontend/src/voice.ts` | Web Speech API, reproducción de audio |
| `frontend/src/orb.ts` | El orbe de partículas en Three.js |
| `frontend/src/dashboard/` | El dashboard (TS puro, sin framework) |

## En qué confía, y por qué

Cinco cosas de las siguientes son así a propósito. Cada una es un compromiso que
el diseño de JARVIS asumió deliberadamente, no un fallo a la espera de arreglo;
conócelas antes de ejecutarlo.

**El nombre de un directorio puede poner palabras en boca de JARVIS.** Para
decir *qué* sesión quiere decir ("hammer en Desktop", no "una de ellas"), JARVIS
compone un nombre hablado con nombres de directorio y el título de una
conversación, y admite hasta unos 60 caracteres de letras, dígitos, espacios y
puntuación ligera (`,.-/+'`) en él (`_said_name` en `server.py`; `_plain_phrase`
admite la misma clase para el `waitingFor` de una sesión). Cualquiera que pueda
crear un directorio en esta máquina, o escribir el campo `waitingFor` de un
fichero de registro de Claude Code, puede meter esas palabras en una frase que
JARVIS pronuncia en voz alta. No hay separador, ni `<`, `>`, `"` ni `=` en el
conjunto permitido, así que no puede falsificar una línea entera ni cerrar un
envoltorio: solo palabras, nunca una instrucción falsa con estructura. La
alternativa es un asistente que no puede nombrar lo que está mirando.

**La compuerta de contaminación detiene la acción, no la persuasión.** Todo lo
que JARVIS lee de una página web, un fichero, otra sesión, una ejecución, la
pantalla o un servidor MCP conectado se marca como no fiable, y toda herramienta
que actúe se deniega durante el resto de ese turno; para las dos herramientas
que escriben memoria, durante el resto de esa generación
(`_untrusted_content_refusal`, `TAINTING_TOOLS`, `MEMORY_WRITERS` en
`server.py`). Esto **no** significa que un texto sugerido por un atacante nunca
se actúe: nada rastrea de dónde vino una frase del contexto del cerebro, así
que una sugerencia plantada por algo que JARVIS leyó sigue ahí cuando vuelves a
hablar. Significa que tienes que pedirlo otra vez, con tus propias palabras, en
un turno que empezó limpio. Es la garantía más fuerte que este diseño puede
cumplir de verdad, no una afirmación de que la sugerencia haya desaparecido.

**Toda ejecución lanzada tiene todos los privilegios.** `JARVIS_SKIP_PERMISSIONS`
está activado por defecto, así que cada ejecución pasa
`--dangerously-skip-permissions` (`run_executor.py`): no hay TTY que conteste a
una petición de permiso, de modo que sin él una ejecución se cuelga para
siempre en vez de hacer su trabajo. Una ejecución es, por tanto, un proceso con
todos los privilegios en tu máquina, en el directorio que le hayas indicado,
limpio solo de las variables de entorno `ANTHROPIC_*`/`CLAUDE_CODE_*`
(`claude_env.py`), no de acceso al sistema de ficheros. Puede escribir todo lo
que tú podrías escribir a mano, incluido el registro de sesiones de Claude Code
y las transcripciones que el propio JARVIS lee después como palabras de otra
sesión.

**El bucle local es de fiar.** Cualquier cosa de esta máquina que pueda
presentar un `Origin` de `http://localhost:5173` a `5180`, o el propio puerto de
la API (`DEV_SERVER_PORTS`, `JARVIS_DEFAULT_HOST` en `web_auth.py`), recibe el
mismo acceso que una pestaña del navegador, incluido lanzar una ejecución,
porque es la misma ventana en la que caen los reinicios del servidor de
desarrollo de Vite. Cualquier otro proceso de tu máquina enlazado a uno de esos
puertos también lo recibe. `--host 0.0.0.0` extiende la misma confianza a tu red
local y muestra un aviso cuando lo haces. La otra puerta es el token de
portador de `<data-dir>/jarvis/tool-token`, escrito con permisos `0600`
(`data_paths.ensure_tool_token`) para que solo tu cuenta de usuario pueda
leerlo.

**Un acuse de audio perdido degrada la escucha hasta 45 segundos.** El
navegador acusa recibo de cada fragmento de voz al terminar de reproducirlo;
JARVIS lo usa para relajar el rechazo de eco en las respuestas de una o dos
palabras con las que la gente realmente interrumpe ("ahora", "sí"). Si la
pestaña se cuelga o el socket se cae, no llegan más acuses, esa relajación
queda desactivada y una respuesta corta que coincida con la última frase de
JARVIS puede descartarse como eco de él mismo, hasta que el vigilante
`ack_timeout` (45 segundos, `speech.py`) desiste del fragmento y lo da por
resuelto. Recarga la pestaña si parece que se ignora una respuesta corta.

## Hazlo tuyo

Esto es un punto de partida, no un electrodoméstico. La idea entera es que lo
clones y lo adaptes a lo que haces. Las costuras son deliberadamente obvias:

- **Su personalidad** vive en `jarvis_home/CLAUDE.md`: cómo habla, qué se niega
  a decir, qué hace cuando duda. Se copia a su directorio de datos en el primer
  arranque; edita esa copia y no se sobrescribe nunca, o edita la plantilla y
  llegará a una instalación nueva. Si quieres algo distinto de un mayordomo
  británico, es el único fichero que necesitas tocar. (En castellano, la
  persona está en `jarvis_home/CLAUDE.es.md`.)
- **Su voz** es `tts.py`: una llamada HTTP, unas sesenta líneas. Cambia a otro
  proveedor, o a un modelo local, sin tocar nada más.
- **Sus herramientas** son la tabla `TOOL_HANDLERS` de `server.py`, expuesta al
  cerebro mediante `jarvis_mcp.py` y controlada por una lista de permitidos en
  `brain.py`. Añadir una significa escribir un manejador y nombrarlo en esos dos
  sitios.
- **El aspecto** es `frontend/src/dashboard/theme/`: cuatro ficheros CSS, con
  los tokens primero. `frontend/dashboard-preview.html` renderiza todos los
  componentes en una sola página contra la hoja de estilos real, así que puedes
  rediseñar sin ejecutar el backend. Las capturas de arriba son renders de esa
  página.
- **El orbe** es `frontend/src/orb.ts`, Three.js autocontenido.

Las contribuciones son bienvenidas, y las más útiles son las que esto todavía
no puede hacer: integración con sistemas que no sean macOS, motores de TTS
alternativos y un cliente móvil. Abre una incidencia antes de un PR grande.

## Desarrollo

```bash
pytest
```

Eso ejecuta toda la batería (alrededor de 1.640 tests) y no toca ni la red ni
tu pantalla. Nueve tests de navegador están excluidos por defecto porque visitan
URL reales y `browser.py` lanza Chromium con `headless=False` a propósito, así
que abren ventanas en tu escritorio. Ejecútalos de forma deliberada:

```bash
pytest -m browser
```

Ningún test lanza un proceso `claude` real, y ninguno debería. `tests/conftest.py`
fija `JARVIS_BRAIN_AUTOSTART=0` para toda la batería.

Todo lo que JARVIS escribe (la base de datos SQLite, la carpeta de memoria, el
seguimiento de uso, el token interno de herramientas) vive bajo `data/`, que se
ignora por completo. Apunta `JARVIS_DATA_DIR` a otro sitio para ejecutar una
instancia sin tocar tus datos reales:

```bash
JARVIS_DATA_DIR=/tmp/jarvis-scratch python server.py --host 127.0.0.1
```

## Licencia

Gratis para uso personal y no comercial. El uso comercial requiere una licencia;
consulta [ethanplus.ai](https://ethanplus.ai) para solicitarla. Más detalles en
[LICENSE](LICENSE).

## Créditos

Creado por [Ethan](https://ethanplus.ai). Funciona con
[Claude Code](https://claude.com/claude-code) y
[Fish Audio](https://fish.audio).

Inspirado en la IA que lo empezó todo: el JARVIS de Tony Stark.

> **Aviso legal:** Este es un proyecto independiente de aficionados y no está
> afiliado, respaldado ni conectado con Marvel Entertainment, The Walt Disney
> Company ni ninguna entidad relacionada. El nombre y el personaje de JARVIS son
> propiedad de Marvel Entertainment.

# Puente MCP: que cualquier sesión de Claude Code hable contigo

El puente deja que una sesión de Claude Code (terminal, VS Code, Cowork) use la voz de JARVIS sin ser JARVIS. Hace cuatro cosas y ninguna más:

| Herramienta | Qué hace |
|---|---|
| `jarvis_speak(text)` | JARVIS lo dice en la próxima pausa. |
| `jarvis_ask_user(question, timeout_sec)` | Lo dice y devuelve tu siguiente respuesta (voz o panel), o `sin respuesta` si caduca. |
| `jarvis_notify(title, message)` | Notificación de escritorio (`notify-send` en Linux). |
| `jarvis_remember(fact)` | Propone un dato para la memoria. **No lo guarda**: tú confirmas. |

No hay ninguna herramienta que ejecute tareas, lance runs, escriba en el equipo ni pulse teclas. Las ideas vienen de `jarvis_speak`, `jarvis_ask_user` y `jarvis_remember` de lopadova/jarvis-desktop (MIT); aquí solo se toma la idea.

## Instalación

JARVIS tiene que estar en marcha (es el que recibe las llamadas). Después, en la máquina donde corre:

```bash
scripts/install-bridge.sh            # registra 'jarvis-bridge' en Claude Code, alcance user
scripts/install-bridge.sh --dry-run  # solo enseña el comando
scripts/install-bridge.sh --uninstall
```

El script usa la ruta absoluta del repositorio, el Python del `.venv` si existe y crea el token si falta. Funciona en las sesiones **nuevas** de Claude Code. El puente solo alcanza a un JARVIS en el mismo equipo (loopback). Si abres sesiones en otro equipo, no llegan.

Para que una sesión se presente con otro nombre: `JARVIS_BRIDGE_SESSION=mi-proyecto`. Por defecto usa el nombre de la carpeta donde corre.

Para que las sesiones sepan cuándo usarlo, copia `docs/bridge-skill.md` a `~/.claude/skills/jarvis-bridge/SKILL.md` (o pega su contenido en un `CLAUDE.md`).

## Cómo suena

Todo lo que dice una sesión va con su nombre delante, para que no se confunda con JARVIS:

- «La sesión mi-proyecto dice: la migración ha terminado.»
- «La sesión mi-proyecto pregunta: ¿borro la rama vieja? Lo que contestes se le enviará a ella.»
- «La sesión mi-proyecto propone que recuerde: el puerto de pruebas es el 8123. ¿La guardo?»

Con `JARVIS_LANGUAGE=en` las frases salen en inglés.

**Preguntas.** Mientras hay una pregunta abierta, lo siguiente que digas (cuando JARVIS termina de preguntar) se le entrega a la sesión y no llega al cerebro de JARVIS. Di «cancela» o «ignórala» para no contestar. También puedes contestar desde el panel (`POST /api/bridge/answer`, `GET /api/bridge/pending`). Solo hay una pregunta a la vez; la espera máxima es de 120 s (45 por defecto).

**Memoria.** Una propuesta queda pendiente en el proceso de JARVIS (máximo 20; la más antigua se descarta; se pierde al reiniciar). Para guardarla:

- por voz, justo después de que JARVIS la lea: «sí», «vale», «guárdalo» (o «no», «descártalo»). Solo cuentan respuestas de hasta cuatro palabras de una lista corta; una frase sobre otra cosa sigue su camino normal y la propuesta sigue pendiente. La decisión la toma una comparación de palabras, no el modelo;
- por el panel: `POST /api/bridge/memory/{id}/confirm` o `/reject`.

Al confirmar se escribe con el mismo escritor que usa JARVIS (`remember`), con el límite del índice y todas sus negativas.

Sin cliente de voz conectado, `jarvis_speak` y `jarvis_ask_user` caen a una notificación de escritorio.

## Modelo de amenazas

El texto que llega por el puente es **no confiable**: lo escribe un modelo que puede haber leído una web o un README hostil. Lo que se hace con él:

- **Token propio y de alcance limitado.** `data/bridge-token` (fichero 0600, creado con `O_EXCL`, adoptado sin seguir enlaces simbólicos), distinto del token de `/internal/tool`, que da poder total. Las rutas `/internal/bridge/*` solo aceptan el token del puente: ni el token de herramientas ni un `Origin` válido abren esa puerta, y el token del puente no abre nada fuera de ella (sigue haciendo falta `Origin` del panel o el token de herramientas). La comprobación está en `web_auth.OriginGuard` y otra vez en cada ruta.
- **Sin CORS.** No se añade ninguna cabecera CORS. Una página web no puede llamar al puente: no tiene el token y el navegador no se lo da.
- **El token nunca sale.** Ninguna API lo devuelve, no se escribe en logs y no va en la línea de `claude mcp add` (solo la ruta del fichero, y por tanto tampoco acaba en `~/.claude.json`).
- **Límites.** Petición ≤ 8 KB. Voz 500 caracteres, pregunta 400, título 80, mensaje 300, dato 300. Un texto largo se **rechaza**, no se recorta.
- **Frecuencia.** Por tipo y entre todas las sesiones: 6 avisos/min, 4 preguntas/min, 6 notificaciones/min, 5 propuestas/5 min. Un texto repetido en 30 s se ignora y no gasta cupo. Máximo 4 avisos del puente esperando a ser dichos.
- **No secuestra la voz.** `jarvis_speak` entra con prioridad baja (se agrupa y espera a una pausa); preguntas y propuestas con prioridad normal pero sin interrumpir. Nada del puente es urgente ni corta a nadie.
- **Texto limpio y atribuido.** Pasa por `strip_markdown_for_tts` (el mismo que las palabras del cerebro), se quitan caracteres de control y el nombre de la sesión se reduce a letras, números, espacio, punto y guion para que no cuele una frase en «La sesión X dice:».
- **No actúa.** Ninguna ruta del puente llama a las herramientas de JARVIS (`TOOL_HANDLERS`, `ACTING_TOOLS`). Un test lo comprueba sustituyendo todas por una que falla. La única escritura es la de memoria, y solo después de que el usuario confirme.
- **El cerebro no usa el puente.** Su `mcp.json` no lo incluye.

Lo que no cubre:

- Cualquier proceso local de tu usuario puede leer `data/bridge-token` (es lo que hace el servidor MCP) y por tanto hablar, preguntar o proponer. El límite de frecuencia acota el daño, y lo peor que puede hacer es molestar o proponer un dato que tú rechazas.
- Una pregunta abierta entrega tu siguiente frase a quien la hizo. Por eso la pregunta se anuncia diciendo que tu respuesta se le enviará, y «cancela» la cierra.
- Un proceso local que falsifique la cabecera `Origin` puede confirmar su propia propuesta por el panel. Es la limitación que ya tiene todo `/api` (el `Origin` solo es infalsificable en un navegador). La vía por voz no tiene ese problema. Si te importa, confirma solo por voz.
- Si JARVIS escucha en una dirección que no es loopback, el puente queda expuesto a la LAN con el token como única defensa. Déjalo en `127.0.0.1`.

## Qué no se ha probado

La CI prueba la lógica con fakes: autenticación, límites, preguntas, propuestas y el protocolo MCP del servidor. No prueba audio real, micrófono, notificaciones reales, `claude mcp add` ni una sesión de Claude Code de verdad.

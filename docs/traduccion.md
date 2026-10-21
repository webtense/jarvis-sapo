# Traducción al castellano: cómo funciona y qué se cuida

Con `JARVIS_LANGUAGE=es` JARVIS habla, escribe y muestra castellano de España.
Con `en` (o sin la variable) todo es idéntico al original, byte a byte: `t()`
devuelve el mismo objeto que recibe y ni siquiera ejecuta una expresión regular.

## Dónde se traduce

Las frases del código siguen en inglés y se traducen en las FRONTERAS, justo
donde salen hacia el usuario o hacia el cerebro:

| Capa | Mecanismo | Catálogo |
|------|-----------|----------|
| Servidor (voz, resultados de herramientas, ajustes) | `i18n.t()` | `i18n_es.py` (`PAIRS`) |
| Interfaz y panel | `t()`, `tp()`, `tc()`, `t1()` | `frontend/src/i18n-es.ts` |
| Persona del cerebro | `jarvis_home/CLAUDE.es.md` | mismas reglas y herramientas que `CLAUDE.md` |

La clave de cada pareja es la frase inglesa exacta. Si se cambia una frase
inglesa hay que cambiar su pareja: `tests/test_i18n.py` falla si una frase con
"sir" no la tiene, y `tests/test_i18n_catalog.py` hace lo mismo con la interfaz.

## Qué es dato y nunca se traduce

Lo que escribe el cerebro, una transcripción, un repositorio, una página web,
Home Assistant o un nombre de archivo, de carpeta o de proyecto es dato no
fiable. Una plantilla solo COPIA lo que captura. Por eso:

* Los marcadores `{x}` copian; `{x:t}` traduce lo capturado y solo se usa donde
  lo capturado es otra frase NUESTRA (`{rest}`, `{approval}`, `{caveat}`,
  `{said}`, `{problem}`). Nunca en un nombre: un archivo que se llame `later`,
  `nothing` o `Return` sale tal cual.
* `{x:req}` aplica la pareja solo si la captura se tradujo de verdad. Es lo que
  impide que "Something is broken, sir." salga como "Something está broken,
  señor.": la plantilla `{name} is {reason:req}, sir.` solo admite razones que
  el servidor traduce ("waiting on a dialog").
* Los textos de reserva que elige el propio servidor cuando un nombre no es
  ordinario ("that file", "that folder", "a directory", "an unnamed project",
  "that session", "A session", "one of them", "your browser", "your editor"...)
  se traducen en el punto donde se eligen (`_plain_name`, `_plain_phrase`,
  `_said_name`, `actions.py`), con `i18n.t(reserva)`. Si no hay una traducción
  única buena, `i18n.pick("that", "ese nombre")` da una palabra en cada idioma.

## El tratamiento ("señor", "señora" o ninguno)

El ajuste Tratamiento de Ajustes guarda `HONORIFIC` (`sir`, `ma'am` o `none`).
En castellano se aplica a la plantilla española ANTES de rellenarla, así que un
proyecto llamado "señor" nunca se reescribe:

* `sir` (por defecto): "Cancelado, señor."
* `ma'am`: "Cancelado, señora."
* `none`: "Cancelado." Desaparece el tratamiento con su coma: "Hecho, señor:
  listo" pasa a "Hecho: listo".

La variable se lee cada vez, y el endpoint de Ajustes la actualiza en caliente.
`tests/test_i18n.py` comprueba que NINGUNA pareja del catálogo queda mal sin el
tratamiento (comas sueltas, espacios dobles).

La persona (`CLAUDE.es.md`) es un texto que lee el cerebro y sigue diciendo
"señor"; no se parametriza.

## Números y concordancia

* Los recuentos pequeños se dicen con palabras (`_say_number`). `una` es la
  forma femenina por defecto (tareas, conversaciones, ejecuciones);
  `i18n.number_word(n, "m")` da "un" ante un nombre masculino y `"alone"` da
  "uno" ("y uno más").
* "Una sola tarea" no se cuenta como "una de una tarea": hay parejas literales
  para esas frases ("La única tarea aún no está hecha").
* En la interfaz, `t1(n, texto)` usa la entrada con contexto `one` cuando n es
  1 ("1 activo", "3 activos"); en inglés es el propio texto.
* `reset_clock`: las 0:00 se dicen "medianoche"; las 12:00, "las 12".
* "Intro" es el nombre de la tecla Return en el teclado español, y es el que se
  usa en todas partes (la voz, el catálogo y la persona).

## Frases que el usuario oye

Además de las que llevan "sir", se traducen las que llegan hasta él sin
tratamiento: los mensajes de `browser.PageError` ("esa página no ha cargado"),
las negativas de `steer_session` y `answer_dialog` ("No veo ninguna conversación
llamada..."), los avisos al retomar una frase interrumpida (`speech.BRIDGES_ES`,
`UNREAD_PREFIX_ES`) y los errores del proceso hijo del puente MCP
(`jarvis_bridge_mcp.py` lee `JARVIS_LANGUAGE` del entorno o, si no está, del
`.env` del repositorio).

"Empezar de cero" (lo que la persona enseña a decir cuando una memoria no se
puede guardar) lo reconoce también el servidor, sin acentos
(`FRESH_START_PHRASES_ES`).

## Lo que sigue en inglés, a propósito

* Nombres de herramientas, variables, rutas, comandos y identificadores.
* El resultado de las herramientas que describe sesiones al cerebro
  ("Recently using: ...", "needs you", "in a shell"...): no se oye tal cual; el
  cerebro lo cuenta con su persona, que habla castellano.
* Las instrucciones que una herramienta da al cerebro ("staged — I'll read it
  back ... Say briefly that it is going out").
* Las negativas del puente MCP hacia otra sesión (`bridge.py`) ya están en
  castellano con cualquier idioma: es una función solo de este fork.

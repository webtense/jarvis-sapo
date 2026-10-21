# JARVIS escribiendo en tu Google personal: confirmación por voz

Amplía `docs/google.md` (la conexión de solo lectura, que no cambia) con una
pieza opcional, **apagada por defecto**: JARVIS puede *proponer* una acción de
escritura en tu Google **personal** (crear o modificar un evento de Calendar,
crear/modificar/completar una tarea de Google Tasks, o crear/ampliar una nota
como documento de Google Docs en tu carpeta "JARVIS Notas" de Drive). La dice
entera en voz alta y **solo la ejecuta si contestas que sí** — el mismo patrón
de `docs/casa-control.md` (confirmación hablada, palabras estrictas, ventana
corta, límite por minuto, simulacro, auditoría), aplicado aquí a tu cuenta de
Google en vez de a tu casa.

Sin el fichero de política de abajo, no hay escritura: no es "todo
rechazado", es que no existe ningún camino de código que llegue a la red.

## Qué puede hacer y qué NO puede hacer nunca

| Puede | No puede nunca |
|---|---|
| Crear un evento de Calendar (título, inicio, fin o duración, lugar y descripción opcionales) | Borrar un evento |
| Modificar un evento que JARVIS pueda identificar por id (título, hora, lugar, descripción) | Invitar a nadie (`attendees`) ni enviar notificaciones a terceros (siempre `sendUpdates=none`) |
| Crear una tarea de Google Tasks (título, notas, fecha) | Borrar una tarea |
| Modificar título/notas/fecha de una tarea, y marcarla completada | — |
| Crear una nota como documento de Google Docs dentro de "JARVIS Notas" | Compartir nada, cambiar permisos, crear un enlace público |
| Ampliar una nota que JARVIS mismo creó, añadiendo texto al final | Tocar un fichero de Drive que JARVIS no haya creado él mismo |
| — | Enviar, borrar o etiquetar NADA en Gmail: esta conexión no toca el correo |

Google Keep no tiene API para cuentas personales; por eso las "notas" son
documentos de Google Docs, nunca notas de Keep.

Cualquier propuesta que incluya `attendees`, un campo de permisos/compartir, o
una dirección de correo de un tercero en cualquier campo de texto se rechaza
entera, antes de que se abra ningún socket — ver `google_actions.py`.

## Modelo de amenazas

Igual que `docs/casa-control.md`, adaptado:

| Quién o qué | Qué intenta | Qué lo impide |
|---|---|---|
| Un correo, una web, un documento o un transcript de otra sesión leídos por el cerebro | Que el cerebro proponga escribir algo en tu Google | El cerebro **solo propone** (`google_propose`). En el turno en que ha leído algo ajeno, la herramienta se niega: hay que pedirlo de nuevo, con tus palabras, en un turno limpio. Y aun proponiendo, nada se ejecuta sin tu "sí" hablado |
| El propio cerebro, engañado o equivocado | Ejecutar directamente | No hay ninguna herramienta que ejecute. La ejecución vive en una función del servidor que solo se alcanza desde tu "sí" (o desde el panel, si lo activas) |
| Un "sí" dirigido al cerebro | Que cuente como "sí" a una acción | Las mismas palabras estrictas que `docs/casa-control.md`: en castellano "sí" (con tilde), "hazlo", "adelante", "confirma", "confirmado" (y "vale" solo acompañado de otra); en inglés "yes", "confirm", "do it", "go ahead". "ok", "si" sin tilde, "vale" suelto **no** valen. Activable `JARVIS_GOOGLE_CONFIRM_STRICT=1` para exigir dos de esas palabras juntas |
| Un ámbito prohibido | Invitar, compartir, borrar, Gmail | Prohibido **por código**, no por configuración: ni siquiera existe el campo en el esquema que la herramienta acepta |
| Un identificador manipulado | `../../etc/passwd` como `doc_id`, un `task_list` no listado | Solo valen los caracteres de un id de Google (`A-Za-z0-9_-`); la ruta se construye con parámetros ya validados, nunca con texto crudo del cerebro, y además solo se envía si está en la **tabla cerrada** de (método, host, ruta, campos del cuerpo): ni un `DELETE`, ni `permissions`, ni Gmail están en ella. Se comprueba antes de leer el token |
| Una redirección de Google (o de quien lo suplante) | Hacer que el token viaje a otro servidor | No se sigue ninguna redirección y no se usa proxy |
| El token de lectura robado o reutilizado | Servir para escribir | El token de escritura es **otro** (`GOOGLE_WRITE_TOKEN_FILE`). Si resulta ser el mismo fichero, o tiene el mismo `access_token` o el mismo `refresh_token` que el de lectura, no se envía nada |
| Un run sin supervisión (con Bash) o el cerebro | Heredar el token de escritura por el entorno | `claude_env` quita todas las variables `GOOGLE_WRITE_*` del cerebro y de los runs |

### Lo que el código NO protege

- **Un proceso de tu mismo usuario con Bash** puede leer el fichero del token
  (es `600`, pero es *tu* usuario), llamar él mismo a la API de Google o
  editar tu lista blanca.
- **Un cambio sobre algo que ya existe** (`calendar_update`, `task_update`,
  `task_complete`, `note_append`) se identifica por un id que aporta el
  cerebro. JARVIS dice **qué cambiaría** ("¿Cambio ese evento del calendario:
  con el título Nuevo, empezando el jueves…?"), no el título del original, que
  no consulta. Como leer algo de Google tiñe ese turno, se hace en dos pasos:
  primero te cuenta lo que ha encontrado y, cuando se lo pides otra vez, lo
  propone. Si dudas de a cuál se refiere, di "no".
- **Quien hable cerca del micrófono** puede decir "sí": no hay reconocimiento
  de quién habla. Por eso las acciones son pocas y hay límite por minuto.
- Una apps OAuth en modo "Testing" (no publicada) en Google Cloud Console
  **podría** caducar el `refresh_token` a los 7 días — hipótesis sin
  confirmar, la misma que ya anota `docs/google.md` para la conexión de
  lectura; si te pasa, repite `scripts/google-auth.py --write`.

## Autorizar el token de escritura (un clic, lo lanzas tú)

**JARVIS no lo ejecuta por ti.** Este script abre tu navegador y pide tu
permiso; lánzalo tú mismo desde una terminal:

```
python3 scripts/google-auth.py --write
```

Pide solo los scopes mínimos para esta conexión:

- `openid`, `.../auth/userinfo.email` (identidad)
- `https://www.googleapis.com/auth/calendar.events` (eventos, nunca el
  calendario entero)
- `https://www.googleapis.com/auth/tasks` (la API de Tasks no ofrece un scope
  más estrecho que "eventos")
- `https://www.googleapis.com/auth/drive.file` (solo los ficheros que JARVIS
  mismo crea — la carpeta "JARVIS Notas" y los documentos dentro — nunca el
  resto de tu Drive)

El token queda en `~/.config/jarvis/google_write.json` (`700`/`600`,
escritura atómica), **distinto** del de lectura
(`~/.config/jarvis/google_readonly.json`). Apunta `GOOGLE_WRITE_TOKEN_FILE` a
esa ruta en tu `.env`. Sin `--write`, el script sigue pidiendo solo los scopes
de lectura de siempre — el comportamiento por defecto no cambia.

## El fichero de política: `data/jarvis/google-actions.json`

Es tu fichero. JARVIS no lo crea ni lo edita. Hay un ejemplo inactivo en
`jarvis_home/connections.examples/google-actions.json` (cópialo, ponle
`"enabled": true`).

```json
{
  "enabled": true,
  "panel_confirm": false,
  "calendars": ["primary"],
  "task_lists": ["@default"],
  "limits": {
    "max_per_minute": 3,
    "max_text_chars": 1500,
    "max_lead_days": 365
  }
}
```

- `calendars` / `task_lists`: los identificadores que JARVIS puede tocar. Por
  defecto solo tu calendario y tu lista de tareas principales (`"primary"` /
  `"@default"`).
- `limits.max_per_minute`: de 1 a 10 (3 por defecto).
- `limits.max_text_chars`: longitud máxima de una descripción, las notas de
  una tarea o el texto de una nota (1 a 4000, 1500 por defecto). Un título
  (120) y un lugar (120) tienen su propio tope fijo, para poder decirse enteros.
- `limits.max_lead_days`: cuántos días en el futuro puede estar un evento o
  una fecha de vencimiento (1 a 3650, 365 por defecto).
- `panel_confirm`: igual que en el control de la casa — `false` por defecto;
  si lo activas, también se puede confirmar desde `POST /api/google/<id>/confirm`.
- El fichero debe ser tuyo, un fichero normal y **no escribible por grupo ni
  otros** (`chmod 600`); si no, JARVIS lo ignora.

## Cómo funciona una propuesta

1. Le pides algo: "Jarvis, crea un evento mañana a las diez, cena con Marta".
2. El cerebro llama a `google_propose` con los datos exactos. Se valida
   contra tu política, los límites y la lista cerrada de campos. Si no
   encaja, se rechaza y se anota — **no se hace nada** todavía.
3. JARVIS dice la acción entera: "¿Creo el evento Cena con Marta, el jueves 22
   de octubre, de 21:00 a 22:30, en su calendario, señor?". Un texto largo
   (descripción, notas, el cuerpo de una nota) se lee hasta 250 caracteres y se
   dice cuántos quedan ("… y 150 caracteres más"). Solo hay una acción
   pendiente a la vez.
4. Contestas "sí" (o "hazlo", "adelante", "confirma", "confirmado"), dentro de
   una ventana corta desde que acabó la frase, y solo si nadie ha hablado por
   medio. Un "no" la descarta.
5. Se vuelve a comprobar tu política, el límite por minuto y que el registro
   se pueda escribir. Después se hace **una sola** petición a la API de
   Google correspondiente, con el cuerpo validado, y JARVIS dice "Hecho,
   señor." (o por qué no).
6. Una acción propuesta caduca a los 2 minutos.

El cerebro nunca sabe si se hizo: se le dice que está pendiente y que acabe su
turno.

## Detalles que conviene saber

- **Horas.** Una hora sin zona (`2026-10-22T21:00`) es tu hora local y JARVIS
  le escribe el desfase antes de enviarla; con `Z` o `+02:00` se respeta. Un
  evento de todo el día usa fechas (`2026-10-22`); su fin es el día siguiente
  al último, como en Calendar, y la frase hablada dice el último día real. Una
  tarea solo guarda la **fecha** de vencimiento.
- **Notas.** Una nota es un documento de Google Docs dentro de "JARVIS Notas":
  JARVIS crea la carpeta la primera vez (con `drive.file` solo ve lo que él
  mismo creó, de modo que una carpeta o un documento tuyos con ese nombre no
  cuentan). Ampliar una nota añade un párrafo **al final**, y antes comprueba
  que el documento es un Doc vivo (no en la papelera) dentro de esa carpeta;
  si no, no toca nada (`not_our_note`).
- **Ningún correo en los textos.** Cualquier campo de texto (título, lugar,
  descripción, notas, cuerpo) que contenga algo con forma de dirección de
  correo se rechaza entero: es la defensa contra una invitación o un reparto
  encubiertos. Si de verdad quieres escribir tu correo en una nota, hazlo tú.
- **Renovación del token.** Si Google contesta 401, JARVIS renueva el acceso
  con el `refresh_token` del fichero de escritura y repite la petición una sola
  vez (un 401 significa que no se ejecutó, así que no puede hacerse dos veces).
  Si el permiso ha caducado o se revocó, JARVIS dice que hay que renovarlo con
  `scripts/google-auth.py --write` (`reauth` en el registro).
- **Qué no comprueba ninguna prueba automática.** Los tests usan una red
  simulada: no se ha llamado a la API real de Google. La primera acción real
  conviene hacerla con `JARVIS_GOOGLE_DRYRUN=1` y, luego, con algo inocuo.

## Modo simulacro

`JARVIS_GOOGLE_DRYRUN=1` en el `.env` y reinicia JARVIS. Todo funciona igual
(pregunta, "sí", registro) pero no se envía ninguna petición ni hace falta
token: JARVIS dice "Simulacro, señor: no he tocado nada." y el registro lleva
`"dryrun": true`.

## Auditoría: `data/jarvis/google-actions.log`

Una línea JSON por intento, fichero `600`, sin tokens ni cuerpos de texto
largos (una nota o una descripción se registran como `"<N chars>"`, nunca su
contenido). Se rota a `.log.1` al pasar de 1 MB.

```json
{"ts":"2026-10-21T09:12:03+00:00","kind":"task_create","fields":{"title":"Comprar pan"},"confirmed_by":"voice","result":"ok","result_id":"t1","dryrun":false}
```

- `confirmed_by`: `voice`, `panel` o `none` (propuestas y rechazos).
- `result`: `proposed`, `ok`, `dryrun`, `rejected`, `expired`, `http_<código>`,
  `redirect`, `unreachable`, `reauth`, `not_our_note`, `unexpected`,
  `refused: ...`.

## Revocar el acceso

En cualquier momento, desde <https://myaccount.google.com/permissions> — la
misma app OAuth que la conexión de lectura, un solo lugar para quitar ambas.
También puedes borrar `~/.config/jarvis/google_write.json`: sin ese fichero,
no hay escritura, igual que sin `google-actions.json`.

## Separar lo que no es tuyo

Esta conexión es solo para tu cuenta **PERSONAL** de Google
(`webtense@gmail.com`), nunca para una cuenta de trabajo o corporativa del
grupo de turismo — la misma frontera que `docs/google.md` traza para la
conexión de lectura.

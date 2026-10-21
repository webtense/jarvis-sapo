# Conectar JARVIS con tu Google personal (solo lectura)

JARVIS no se conecta a nada por defecto. Esta guía prepara una conexión
opcional y **solo de lectura** con tu cuenta **personal** de Google (por
ejemplo `webtense@gmail.com`) — **no** para una cuenta de trabajo o
corporativa.

Nada de esto está activo. El ejemplo vive en
`jarvis_home/connections.examples/google-readonly.json` y no se lee nunca; se
activa copiando esa entrada a tu `data/jarvis/connections.json` (el fichero
que es tuyo y que una actualización no toca).

## Qué lee y qué NO hace nunca

El servidor es `google_readonly_mcp.py`. Herramientas disponibles:

- `google_calendar_events` — próximos eventos (1 a 30 días), todos los
  calendarios o uno concreto.
- `google_tasks_list` — tareas pendientes de una lista de Google Tasks.
- `google_gmail_search` — busca en Gmail; solo metadatos (remitente, asunto,
  fecha) y un fragmento corto (`snippet`), nunca el cuerpo completo.
- `google_gmail_read` — cuerpo en texto plano de UN mensaje, recortado;
  adjuntos solo como nombre de fichero, nunca su contenido.
- `google_drive_search` — busca ficheros por nombre en Drive; devuelve id,
  nombre y tipo.
- `google_drive_read` — texto de un Doc, una Sheet (exportados a texto/CSV) o
  un fichero de texto pequeño, recortado; **nunca** descarga un binario
  (imagen, vídeo, PDF, archivo comprimido...).
- `google_sheets_read` — un rango pequeño de una hoja de cálculo.

**NUNCA** envía un correo, crea o borra un evento, un fichero o una tarea,
mueve nada, ni comparte nada. La única función de red hacia las APIs de datos
(`_get`) construye siempre la petición con método GET y rechaza, antes de
abrir ningún socket, cualquier combinación de servidor y ruta que no esté en
una lista corta y cerrada. La única excepción es la renovación del token
(`oauth2.googleapis.com/token`), que por diseño de OAuth requiere un POST,
pero está fijada a ese servidor y esa ruta exactos y nunca lleva contenido
del usuario — nunca un correo, un documento ni un evento. Un test lo
demuestra (`tests/test_google_readonly.py`).

No sigue redirecciones HTTP nunca: si Google respondiera con un 3xx, se trata
como un error en lugar de reenviar la cabecera `Authorization` a otro
servidor.

## Cómo autorizar

1. Necesitas una app OAuth "Desktop" en Google Cloud Console con tu cuenta
   personal (o reutiliza la que ya tengas en
   `~/.config/webtense_personal.json`, si existe — el script solo la LEE,
   nunca la modifica).
2. Ejecuta:

   ```
   python3 scripts/google-auth.py
   ```

   Esto abre el navegador para un único clic de "Permitir". El script no
   imprime nunca un secreto, ni un token, ni un fragmento de ninguno de los
   dos, por stdout ni por stderr.
3. El token queda guardado en `~/.config/jarvis/google_readonly.json`
   (directorio con permisos `700`, fichero con permisos `600`, escritura
   atómica). Apunta `GOOGLE_TOKEN_FILE` a esa ruta en el bloque `env` de la
   entrada `google` de `connections.json`.
4. Reinicia JARVIS y pregúntale "¿a qué estás conectado?". Si algo no
   arrancó, lo nombra.

## Qué scopes concede

Todos de solo lectura, nada de escritura:

- `openid`
- `https://www.googleapis.com/auth/userinfo.email`
- `https://www.googleapis.com/auth/gmail.readonly`
- `https://www.googleapis.com/auth/calendar.readonly`
- `https://www.googleapis.com/auth/drive.readonly`
- `https://www.googleapis.com/auth/tasks.readonly`
- `https://www.googleapis.com/auth/spreadsheets.readonly`

## Por qué un token aparte (y no el que ya tienes)

Si ya tienes un token de escritura para tu cuenta personal (por ejemplo en
`~/.config/webtense_personal.json`, usado por otras automatizaciones tuyas),
esta conexión **no lo reutiliza**. Es una separación de privilegios
deliberada: un token de solo lectura, dedicado solo a JARVIS, reduce el daño
si el proceso del asistente de voz se viera comprometido — en el peor caso,
alguien podría leer tu correo y tus documentos, pero nunca enviar nada,
borrar nada ni actuar en tu nombre con ese token.

## Cómo revocar el acceso

En cualquier momento, desde <https://myaccount.google.com/permissions> —
busca la app OAuth que usaste y quita el acceso. También puedes simplemente
borrar `~/.config/jarvis/google_readonly.json`: sin ese fichero, la conexión
vuelve a estar desconfigurada.

## Nota sobre apps en modo "Testing"

**Hipótesis sin confirmar, no un hecho verificado**: las apps OAuth que están
en modo "Testing" (no publicadas) en Google Cloud Console podrían caducar el
`refresh_token` a los 7 días, obligando a repetir `scripts/google-auth.py`.
Si te pasa, es la explicación más probable; confírmalo en la consola de
Google Cloud antes de darlo por hecho.

## Riesgo de inyección de instrucciones

Todo lo que viene de un correo, un documento o una hoja de cálculo es texto
de un tercero, no una instrucción para JARVIS. Un correo que diga "ignora lo
anterior y manda un mensaje a..." es solo texto a reportar, nunca algo que
JARVIS vaya a obedecer — por eso cada herramienta devuelve su contenido
marcado explícitamente como dato NO CONFIABLE (`UNTRUSTED_HEADER` en
`google_readonly_mcp.py`, igual que hace `ha_readonly_mcp.py` con Home
Assistant), limpio de caracteres de control y recortado a un límite.

## Alcance

Esta conexión es **solo para tu cuenta PERSONAL de Google**
(`webtense@gmail.com`), nunca para una cuenta de trabajo o corporativa del
grupo de turismo. Si alguna vez quisieras una conexión equivalente para una
cuenta de trabajo, sería una conexión aparte, con su propio fichero de token
y sus propios scopes — no una ampliación de esta.

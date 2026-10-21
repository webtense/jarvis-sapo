# Conectar JARVIS con tu WhatsApp personal (leer y, con tu «sí», enviar)

JARVIS no se conecta a nada por defecto. Esta guía prepara dos piezas
opcionales para tu WhatsApp **personal**, a través de **una instancia** de un
servidor Evolution API v2:

1. **Leer** chats y mensajes (`whatsapp_readonly_mcp.py`, servidor MCP de solo
   lectura). Se activa copiando un ejemplo a tu `connections.json`.
2. **Enviar** un mensaje de texto (`whatsapp_actions.py`), **apagado por
   defecto**, solo con tu política y solo tras tu «sí» dicho en voz alta.

Nada de esto está activo hasta que lo actives tú. Los ejemplos viven en
`jarvis_home/connections.examples/` (`whatsapp-readonly.json` y
`whatsapp-actions.json`) y JARVIS no los lee nunca.

## Qué lee y qué NO hace nunca

Herramientas del servidor de lectura:

- `whatsapp_list_chats`: los últimos chats **individuales**: nombre, id (JID),
  fecha de la última actividad y nº de no leídos. Sin texto de mensajes.
- `whatsapp_read_chat`: los últimos mensajes de **un** chat, por nombre o por
  id: con quién, si lo recibiste o lo enviaste tú, hora y texto. De una foto,
  un audio o un documento solo el tipo y el pie, nunca el fichero.
- `whatsapp_search`: busca un texto dentro de **un** chat (sus últimos 200
  mensajes).

Lo que **no** hace: enviar, borrar, marcar como leído, cambiar la presencia
(«escribiendo…»), tocar la sesión o las instancias. No es una norma de
configuración: la tabla `_ALLOWED` de `whatsapp_readonly_mcp.py` solo contiene
dos consultas (`POST /chat/findChats/<instancia>` y
`POST /chat/findMessages/<instancia>`: Evolution hace sus consultas por POST) y
un `GET /instance/connectionState/<instancia>`. Se comprueba el método, la ruta
y la forma del cuerpo **antes de abrir el socket**; no hay en el fichero ninguna
ruta de envío que alguien pudiera activar. Tampoco sigue redirecciones (urllib
reenviaría la cabecera `apikey` al sitio que nombre el 3xx) ni usa proxy.
Los tests lo demuestran (`tests/test_whatsapp_readonly.py`).

Estados vacíos claros: «no hay credenciales» (con la variable que falta),
«instancia desconectada (estado: close)» y «no hay chats».

### Grupos: excluidos por defecto

Trabajas en un resort y tienes grupos laborales que **no** deben verse. Por eso:

- Los grupos (`…@g.us`) **no se listan ni se leen**. Solo se pueden leer los que
  tú permitas **por JID** en `read_groups` de tu política (vacía por defecto).
- Listas de difusión, estados y canales (`@broadcast`, `@newsletter`) no se
  muestran nunca.
- `exclude` oculta además contactos concretos: por JID, por número de teléfono
  o por un nombre (se oculta todo chat cuyo nombre **contenga** ese texto; es
  mejor ocultar de más que de menos). Un chat oculto responde «ese chat no está
  disponible», igual que uno que no existe.
- Si el fichero de política existe pero no es de fiar (no es tuyo, lo puede
  escribir otro usuario, no es JSON, una entrada de `exclude` no se entiende),
  **no se muestra nada**: falla cerrado.

## Credenciales

Un fichero JSON **600, tuyo y fuera del repositorio**, con la instancia personal
y **su propio token de instancia** (nunca la clave global del servidor):

```json
{"url": "https://tu-servidor-evolution", "instance": "TU_INSTANCIA", "apikey": "…"}
```

- `EVOLUTION_TOKEN_FILE`: lectura (y envío si no hay otro).
- `EVOLUTION_WRITE_TOKEN_FILE` (opcional): otro fichero para el envío. Si no
  existe la variable, se usa el mismo fichero.

Se rechaza un fichero legible por grupo u otros, que no sea un fichero normal o
que no sea tuyo. La URL debe ser `https://servidor[:puerto]` sin usuario, ruta ni
parámetros. **El nombre de la instancia sale solo de este fichero**, nunca de un
argumento de una herramienta: en un servidor compartido eso es lo que mantiene
fuera las demás instancias. Ningún mensaje de error incluye la clave, la URL, la
instancia ni contenido de mensajes.

## Activar la lectura

1. Crea el fichero de credenciales de arriba.
2. Copia la entrada `whatsapp` de `jarvis_home/connections.examples/whatsapp-readonly.json`
   dentro de tu `data/jarvis/connections.json` (no el fichero entero) y cambia
   las dos rutas.
3. Reinicia JARVIS y pregúntale «¿a qué estás conectado?».

El servidor de lectura usa `data/jarvis/whatsapp-actions.json` solo para saber
qué grupos permites y a quién ocultas (variable opcional `WHATSAPP_POLICY_FILE`
para otra ruta). Sin ese fichero rigen los valores por defecto: ningún grupo,
nadie oculto.

## La política: `data/jarvis/whatsapp-actions.json`

Es tu fichero (permisos 600; no puede poder escribirlo nadie más). JARVIS no lo
crea ni lo edita. Ejemplo inactivo en
`jarvis_home/connections.examples/whatsapp-actions.json`.

```json
{
  "enabled": false,
  "panel_confirm": false,
  "contacts": {"Mario": "34600000000@s.whatsapp.net"},
  "self": "",
  "read_groups": [],
  "exclude": [],
  "limits": {"max_per_minute": 2, "max_chars": 500}
}
```

- `enabled`: tiene que ser literalmente `true` para poder enviar. Cualquier otra
  cosa (falta, `false`, `"true"`, `1`) deja el envío apagado.
- `contacts`: el nombre **que dirá JARVIS** → el JID individual
  (`<número con prefijo de país, sin +>@s.whatsapp.net`). Cualquier valor que no
  sea exactamente eso (un grupo, una lista, un número suelto, un JID con algo
  detrás) se ignora y queda anotado.
- `self` (opcional): tu propio JID, para «mándame un mensaje a mí». Se pide como
  «yo», «mí mismo» o «myself».
- `read_groups`, `exclude`: ver arriba.
- `limits.max_per_minute` (1–6, por defecto 2) y `limits.max_chars` (1–1000, por
  defecto 500).
- `panel_confirm`: permite confirmar también desde el panel con un clic (ver
  abajo). Apagado por defecto.

## Enviar: cómo funciona

El cerebro **solo propone**, con la herramienta `whatsapp_propose`, de esquema
cerrado: `contact` (un nombre de tu lista) y `text`. La herramienta no envía
nada y solo existe para el cerebro si has escrito tu política.

1. JARVIS valida la propuesta: el contacto está en tu lista, es un individuo, no
   está oculto por `exclude`, el texto no está vacío ni pasa del límite.
   Si no, la rechaza y lo anota en el registro.
2. JARVIS dice en voz alta **el destinatario y el mensaje enteros**: «¿Envío a
   Mario este WhatsApp: «llego en diez minutos», señor?». No resume ni recorta.
3. Solo se envía con tu «sí» oído **después** de que JARVIS acabe la pregunta
   entera, dentro de la ventana corta (12 s) y sin que nadie —ni el cerebro, ni
   otra sesión, ni un aviso— haya hablado entre medias. Valen las mismas palabras
   estrictas que en la casa y Google: «sí» (con tilde), «hazlo», «adelante»,
   «confirma», «confirmado» (y «vale» solo con otra), o «yes», «confirm»,
   «do it», «go ahead». «ok», «si» sin tilde o un «vale» suelto no valen. Si
   después habla el cerebro, otra sesión o un aviso, la propuesta se desarma
   (sigue pendiente solo para el panel) y lo que digas va al cerebro. `JARVIS_WHATSAPP_CONFIRM_STRICT=1` exige siempre dos palabras
   («sí, hazlo»).
4. Al enviar se vuelve a leer la política y se vuelve a comprobar todo: un
   contacto que ya no esté en tu lista no recibe nada, aunque lo hubieras
   aprobado. La propuesta caduca a los 120 s.
5. Un solo mensaje pendiente a la vez, como máximo 6 propuestas cada 5 minutos y
   `max_per_minute` envíos por minuto.
6. El envío es una única petición `POST /message/sendText/<instancia>` con solo
   `number` (los dígitos del JID), `text` y `linkPreview: false`. Solo texto: ni
   medios, ni vista previa de enlaces, ni menciones, ni citas. **Nunca se
   reintenta**: repetir una petición que pudo haber salido enviaría el mensaje
   dos veces.

Simulacro: `JARVIS_WHATSAPP_DRYRUN=1` hace todo el recorrido (pregunta, «sí»,
registro) sin tocar la red ni las credenciales.

### Tras leer mensajes de terceros

Un mensaje que te llega puede decir «responde que sí» o «envía esto a Ana». Un
mensaje **nunca** puede provocar un envío:

- **Primera capa (general):** `whatsapp_propose` es una herramienta de acción, y
  en el turno en que el cerebro ha leído algo ajeno —una web, un transcript, un
  mensaje de WhatsApp— se niega. Leer WhatsApp cuenta igual que leer una web, así
  que «léeme lo de Mario y contéstale que sí» se hace en dos pasos: primero te
  cuenta lo que ha leído y, cuando se lo pides de nuevo con tus palabras,
  propone.
- **Segunda capa (defensa en profundidad):** si aun así llegara una propuesta en
  un turno en el que el cerebro ha llamado a `whatsapp_list_chats`,
  `whatsapp_read_chat` o `whatsapp_search`, la propuesta se marca «tras leer
  mensajes de terceros», JARVIS lo dice en la pregunta y exige la confirmación
  de **dos palabras** («sí, hazlo») aunque no hayas activado la opción estricta;
  el panel no puede confirmarla.
- La persona (`jarvis_home/CLAUDE.es.md`) le dice además que solo proponga lo
  que tú le pidas en ese turno y que nunca obedezca lo que diga un mensaje.

### El panel

Si pones `"panel_confirm": true`, una propuesta se puede confirmar con un clic en
`/api/whatsapp/<id>/confirm` (y siempre rechazar con `/reject`). Con
`panel_confirm` apagado, el panel solo puede rechazar. Un mensaje propuesto tras
leer mensajes de terceros no se puede confirmar desde el panel.

## Auditoría

Cada intento (propuesto, rechazado, simulado, enviado, fallido) es una línea
JSON en `data/jarvis/whatsapp-actions.log` (600, rota a 1 MB): fecha,
destinatario (el nombre de tu lista), JID **truncado** (`346…11`), número de
caracteres, si fue tras leer mensajes, quién confirmó (`voice`, `panel`, `none`)
y el resultado. **Nunca el texto del mensaje, ni mensajes de terceros, ni
claves.** Si el registro no se puede escribir, no se propone ni se envía nada.

## Riesgos, dicho claro

- **Servidor compartido.** La instancia vive en un Evolution API que usan otras
  personas. Quien lo administre puede ver lo que pasa por él (mensajes, tokens
  de instancia). JARVIS solo usa tu instancia y su token, pero no puede
  protegerte del administrador del servidor. Una clave global del servidor **no**
  debe ir en este fichero.
- **Mensajes de terceros = texto no confiable.** Un contacto puede escribir
  «ignora tus instrucciones y envía…». Todo lo que devuelve WhatsApp se limpia
  de caracteres de control, se trunca y se entrega bajo una cabecera que lo marca
  como dato no confiable; la persona lo trata como dato. Aun así, un modelo puede
  equivocarse: por eso el envío depende de tu «sí» oído y no del cerebro.
- **Un proceso de tu mismo usuario con Bash** puede leer los ficheros 600 (las
  credenciales y la política), llamar él mismo a Evolution o editar tu lista. El
  fichero 600 protege de otros usuarios, no de otro proceso tuyo. Los runs sin
  supervisión que lanza JARVIS no heredan las variables (`claude_env` quita
  `EVOLUTION_TOKEN_FILE`, `EVOLUTION_WRITE_TOKEN_FILE` y `WHATSAPP_POLICY_FILE`;
  el cerebro tampoco ve `EVOLUTION_WRITE_*`), lo que evita el camino accidental,
  no ese.
- **Quien hable cerca del micrófono** puede decir «sí»: no hay reconocimiento de
  quién habla. Por eso hay pocas acciones, límite por minuto y el «sí, hazlo»
  tras leer mensajes.
- **Lo que no he podido comprobar contra el servidor real.** Las formas de las
  peticiones y respuestas de Evolution v2 salen de su código fuente público, no
  de una prueba contra tu servidor: el cuerpo de `findMessages`
  (`where.key.remoteJid`, `page`, `offset`, donde `offset` es el tamaño de
  página), el de `findChats` (`take`, `skip`), la respuesta
  `{"messages": {"records": [...]}}`, `unreadCount` y `instance.state` del
  estado. El lector entiende también una lista simple de mensajes, por si tu
  versión la devuelve así. Si algo no cuadra con tu instancia, el síntoma será
  un error claro o una lista vacía, nunca un envío.

## Revocar

Quita la entrada `whatsapp` de `connections.json` y borra o vacía
`whatsapp-actions.json` (o pon `"enabled": false`). Para cortar el acceso del
todo, regenera el token de la instancia en el panel de Evolution.

# Pantalla de administración y botón MUTE

Dos cosas pequeñas que no cambian nada si no las usas:

- **`/admin.html`**: una pantalla en castellano (en inglés si `JARVIS_LANGUAGE=en`) para
  ver cómo está JARVIS y cambiar sus ajustes sin editar el `.env` a mano. Se llega desde el
  menú ☰ de la pantalla principal ("Administración") o escribiendo la dirección:
  `http://localhost:5173/admin.html` con el servidor de desarrollo, o `/admin` y
  `/admin.html` en el servidor compilado (mismo puerto que la página principal).
- **Botón MUTE**: un botón grande, siempre visible en la esquina inferior derecha de la
  pantalla principal, que silencia y vuelve a abrir el micrófono.

## Qué se puede cambiar y qué solo se mira

Solo **lo que es un selector de modo o un nombre** se puede escribir. Todo lo demás se
mira y nada más.

| Sección | Escribe | Solo lee |
|---|---|---|
| Perfil | `USER_NAME`, `HONORIFIC` (`sir`, `ma'am`, `none`, `name` = "Mi nombre"), `JARVIS_LANGUAGE` (`en`, `es`) | |
| Voz | proveedor y voz, con **Probar voz** (por las rutas de Ajustes que ya existían) | voz que suena ahora y avisos del proveedor |
| Escucha | `JARVIS_STT` (`browser`, `local`, `auto`), `JARVIS_STT_MODEL` (`tiny`, `base`, `small`), `JARVIS_WAKE_WORD` (`off`, `on`, `whisper`), `JARVIS_WAKE_WINDOW_SEC` (5 a 300), `JARVIS_BARGE_IN` (`off`, `on`), `JARVIS_BARGE_IN_RMS` (200 a 20000), `JARVIS_BARGE_IN_MS` (100 a 2000) | |
| Conexiones | nada | servidores MCP de `connections.json` (nombre, si es local o remoto y si responde), Home Assistant y Google |
| Casa | **Confirmar** o **Rechazar** una acción pendiente, solo si `panel_confirm` está activo | control activado o simulacro, recuento por dominio de la lista blanca, nº de entidades excluidas, últimas 20 líneas del registro |
| Sistema | botón **Reiniciar** (la ruta de siempre, `POST /api/restart`) | versión, tiempo en marcha, servicio, motor de voz y de escucha efectivos |

### Qué cuesta cada ajuste de escucha

- **Reconocimiento del navegador** (`browser`): gratis, pero es el de Chrome y envía tu
  audio a Google. En Firefox no existe.
- **Local** (`local`): Whisper en esta máquina, el audio no sale de ella. Gasta CPU en cada
  frase; `tiny` es lo más rápido y lo menos preciso, `small` lo más preciso y lo más lento.
- **Palabra de activación** `on` (openWakeWord, solo inglés, "Hey Jarvis") necesita su
  modelo; `whisper` no necesita modelo extra y ese es su coste: transcribe **todo** lo que
  oye para ver si empieza por "Jarvis".
- **Ventana de conversación** (`JARVIS_WAKE_WINDOW_SEC`): tras una frase aceptada, cuánto
  puedes seguir hablando sin repetir "Jarvis".
- **Interrumpir hablando encima** (`JARVIS_BARGE_IN`, con `_RMS` y `_MS`): sube el volumen
  mínimo si JARVIS se corta solo con su propio eco. La tecla Esc sigue siendo la forma fiable
  de callarle.

### Cuándo vale cada cambio

La respuesta de **Guardar** lo dice, no lo supone:

- `USER_NAME` y `HONORIFIC`: al momento.
- `JARVIS_STT_MODEL`, `JARVIS_WAKE_WORD`, `JARVIS_WAKE_WINDOW_SEC` y `JARVIS_BARGE_IN*`: la
  próxima vez que JARVIS empiece a escuchar.
- `JARVIS_LANGUAGE` y `JARVIS_STT`: **requieren reiniciar** (la persona del cerebro se
  eligió al arrancar, la interfaz se compila por idioma y el modo de escucha se anuncia a la
  página en su primer mensaje).

## Por qué la lista blanca de la casa NO se edita desde la pantalla

Es una decisión de seguridad, no una carencia. `home-actions.json` es un fichero tuyo, con
permisos 600, que JARVIS nunca crea ni modifica. Una pantalla web capaz de añadirle una
entidad convierte **un clic robado en una llave de la casa**: cualquier página abierta en
tu navegador que consiguiera ese clic (o un fallo en esta misma pantalla) podría ampliar lo
que JARVIS puede tocar. Para ampliarla, edita el fichero a mano y reinicia. La pantalla solo
cuenta lo que hay, para que puedas comprobar que es lo que crees.

La confirmación desde el panel es la excepción acotada: solo existe si **tú** escribiste
`"panel_confirm": true` en ese fichero, usa la misma ruta que el resto de la interfaz
(`/api/home/{id}/confirm` y `/reject`), pasa por las mismas comprobaciones (lista blanca,
límites por minuto, auditoría) y **Rechazar funciona siempre**. La lista se actualiza sola
cada pocos segundos mientras la miras, y no se repinta bajo el puntero ni con el foco en
ella, para que un clic nunca confirme una acción distinta de la que leíste.

## Seguridad de la pantalla

- **Esquema cerrado.** `POST /api/admin/settings` solo acepta las claves de la tabla de
  arriba, cada una con su lista de valores o su rango (`ADMIN_ENUM_VALUES`,
  `ADMIN_INT_RANGES` en `server.py`). Una clave ajena, un valor fuera de la lista o un
  número con signo, coma o exponente se rechaza **antes de escribir nada**: un formulario
  con un campo malo no deja medio guardado.
- **Nunca claves.** Ni escribe ni devuelve claves, tokens ni rutas de tokens.
  `FISH_API_KEY` sigue teniendo su propio campo en Ajustes.
- **Escritura por el mecanismo de siempre** (`_write_env_key`, el mismo de Ajustes) y sin
  registrar los valores: el registro dice qué claves se guardaron, no qué valían.
- **Mismo control de acceso que el resto.** Las rutas `/api/admin/*` están detrás de
  `web_auth.py` (Origin y Host); no hay CORS.
- **Lecturas por rutas fijas.** `admin_panel.py` abre cuatro ficheros, cada uno por la ruta
  que calcula el servidor (`connections.json`, `home-actions.json`, `ha-exclude.json`,
  `home-actions.log`). Ninguna ruta llega en una petición.
- **Sin URLs ni variables.** De un servidor MCP se da el nombre, si es local o remoto y si
  responde; nunca su dirección, su línea de órdenes ni su bloque `env`. Los remotos se
  sondean desde el servidor con un GET sin cabeceras (para no mandar su `Authorization` a
  ninguna parte), 2 s de espera y como mucho 8.
- **Texto ajeno como texto.** Nombres de entidades, de servidores y líneas del registro se
  limpian de caracteres de control en el servidor y se pintan con `textContent`. En la
  página nueva no aparece `innerHTML` ni `insertAdjacentHTML`.
- **Google.** Se dice si el fichero de credenciales existe, si sus permisos son correctos,
  si las credenciales están completas y qué permisos (ámbitos) concedieron. La cuenta no se
  muestra porque JARVIS no la guarda.

## El botón MUTE

- Siempre a la vista, grande, abajo a la derecha (no está en el menú ☰). El icono pequeño
  de la esquina superior sigue ahí y comparte estado con él.
- La etiqueta **es** el estado: `ESCUCHANDO` con un punto verde (micrófono abierto) o
  `MUTE` con un punto rojo (cerrado). No depende solo del color. Lleva `aria-pressed`, se
  alcanza con el tabulador y se activa con Espacio o Intro.
- **Atajo: la tecla M.** No choca con Esc (que sigue callando la voz de JARVIS), y se
  ignora si el foco está en un campo de texto, un selector o un cuadro editable, y si hay
  Ctrl, Alt o Meta pulsados.
- **Silenciado, no sale audio.** Con el reconocimiento del navegador se detiene el
  reconocedor y se descarta cualquier resultado que llegue tarde; con el local
  (`localmic.ts`) se deja de enviar audio por `/ws/mic` (y se avisa al servidor para que
  tire la frase a medias). Si silencias antes de que arranque la escucha, sigue
  silenciado cuando arranca.
- Silenciar no corta lo que JARVIS esté pensando o diciendo; solo cierra el micrófono.

## Qué no se ha comprobado a mano

Este cambio se desarrolló sin poder compilar ni ejecutar la suite en el equipo del autor:
lo verifica la CI (`tsc --noEmit`, `vite build`, pytest y el job `e2e` con Chromium y Firefox
reales). La lista de acciones pendientes con botones Confirmar/Rechazar y la prueba de
voz desde la pantalla no tienen prueba E2E automática; solo la tienen el botón MUTE y la
ausencia de audio con el micrófono cerrado.

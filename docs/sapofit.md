# SapoFit y Croac: consultar tu comida y entrenos por voz, y oír a Croac

JARVIS puede conectarse a **tu** SapoFit personal de dos maneras, las dos
opcionales y las dos de **solo lectura**:

1. **Consulta por voz** (`sapofit_readonly_mcp.py`, servidor MCP): "¿cuánta
   proteína llevo hoy?", "¿qué comí ayer?", "¿cómo va mi peso este mes?".
2. **Vigilante de Croac** (`sapofit_watch.py`): cuando el coach de SapoFit te
   escribe, JARVIS lo dice en voz alta, sin urgencia y sin pisarte.

Nada de esto está activo hasta que lo actives tú. Los ejemplos viven en
`jarvis_home/connections.examples/` (`sapofit-readonly.json` y
`sapofit-alerts.json`) y JARVIS no los lee nunca.

## Qué lee y qué NO hace nunca

Herramientas de la consulta (todas `GET`):

- `sapofit_today`: hoy, con energía frente al objetivo, proteína, hidratos,
  grasa, agua, peso y comidas hechas.
- `sapofit_food_log`: lo registrado un día (por defecto hoy): comida, hora,
  alimento, gramos y nutrientes, con los totales.
- `sapofit_weight`: las pesadas de los últimos N días (1 a 3650).
- `sapofit_workouts`: los últimos entrenos: fecha, nombre, duración, series,
  volumen y ejercicios.
- `sapofit_workout_stats`: este periodo frente al anterior y la adherencia
  semanal (1 a 90 días).
- `sapofit_streaks`: racha actual, mejor racha y si hoy cuenta.

Lo que **no** hace: no anota comidas, no cambia objetivos, no crea ni revoca
tokens, no escribe nada en SapoFit ni en Croac, no responde a Croac y no lee
ningún dato que no esté en la tabla de rutas. La tabla `_ALLOWED` de
`sapofit_readonly_mcp.py` solo contiene siete rutas `GET` con sus parámetros, y
el método, la ruta y los parámetros se comprueban **antes de abrir el socket**.
No sigue redirecciones y no usa proxy. Los tests lo demuestran
(`tests/test_sapofit_readonly.py`).

El vigilante de Croac tampoco escribe: solo pide el buzón de mensajes con la
misma clase de petición `GET`.

## Crear el token

1. En SapoFit ve a **Perfil > Salud** y crea un token personal.
2. Cópialo **una sola vez** a un fichero JSON que solo leas tú, fuera del
   repositorio:

```json
{"url": "https://sapofit.semillasdeti.com", "token": "sfk_…"}
```

Opcionalmente puede llevar `"expiresAt"` (fecha ISO). Si ya ha caducado,
JARVIS lo dice sin preguntar a SapoFit.

3. Ponle permisos de solo tu usuario:

```bash
chmod 600 ~/.config/jarvis/sapofit.json
```

El fichero se rechaza si es de otro usuario, si no es un fichero normal o si
es legible por grupo u otros. La URL debe ser `https`. Solo se admite `http`
si la dirección es de la red local privada (10.x, 172.16-31.x, 192.168.x o
`localhost`), porque ese tráfico no sale de casa.

Si cambias la contraseña de SapoFit, los tokens se revocan: crea uno nuevo y
actualiza el fichero. JARVIS dirá "token rechazado: créalo de nuevo en SapoFit
> Perfil > Salud" en vez de reintentar sin fin.

## Dónde se configura

Dos variables, porque hay dos procesos que la necesitan:

- **`SAPOFIT_TOKEN_FILE` en el `.env` de JARVIS**: lo usa el vigilante de
  Croac, que corre dentro del servidor.
- **`SAPOFIT_TOKEN_FILE` en el bloque `env` de la conexión** (en
  `data/jarvis/connections.json`): lo usa el servidor MCP que lanza el cerebro
  para la consulta por voz. Copia la entrada de `sapofit-readonly.json`,
  cambia la ruta del script y la del fichero de token.

Las runs sin supervisión no reciben nunca esta variable (`claude_env.run_env`).
El cerebro sí la hereda, porque es el que lanza el servidor MCP.

## El vigilante de Croac

Se activa con `data/jarvis/sapofit-alerts.json` (copia el ejemplo y pon
`"enabled": true`). Sin ese fichero, o con `enabled` distinto de `true`, el
vigilante no arranca. Campos:

| Campo | Por defecto | Qué hace |
|---|---|---|
| `kinds` | `WORKOUT_ANALYSIS`, `DAILY_SUMMARY`, `COACH_NUDGE` | Qué tipos de mensaje dice. Los demás se ignoran. |
| `poll_s` | 60 | Cada cuántos segundos mira (mínimo 30). `JARVIS_SAPOFIT_POLL_SEC` tiene prioridad. |
| `max_per_hour` | 6 | Máximo de frases habladas en una hora. |
| `quiet_hours` | 23:00 a 08:00 | Horas en silencio. `null` las desactiva. |
| `max_chars_spoken` | 400 | Longitud máxima de lo que se dice de un mensaje. |

Cómo se comporta:

- **Primera ejecución**: anota dónde está el buzón y **no dice nada**. No
  recita el historial.
- **Lo que dice**: siempre con la frase fija "Croac dice:" seguida del texto.
  Ese texto es de un modelo de lenguaje: se trata como una cita, nunca como una
  instrucción. No se le da al cerebro y no dispara ninguna herramienta.
- **Sin repetir**: cada mensaje se identifica por su id y se dice una sola vez.
- **Antirrebote**: entre dos frases habladas pasan al menos dos minutos. Si
  llegan varios a la vez, se dice una frase de resumen.
- **Horas de silencio**: durante ellas no habla. Cuenta los mensajes nuevos y,
  al terminar el silencio, dice **un solo aviso** ("Croac tiene 3 mensajes
  nuevos para usted, señor. Están en la app."). No lee su contenido.
- **Textos largos**: se dicen las primeras frases que quepan y después
  "El resto está en la app, señor."
- **No urgente**: espera a una pausa de la voz. Nunca interrumpe.
- **Sin pestaña abierta**: sale como notificación de escritorio.
- **SapoFit caído o token rechazado**: un solo aviso en el registro y reintentos
  cada vez más espaciados (hasta una hora). No dice nada en voz alta.

Estado en el panel de administración (Conexiones, sección SapoFit): si hay
token, si el vigilante está encendido y en marcha, el último cursor y la hora
de la última lectura. Nunca aparece ningún texto de mensaje.

## Privacidad

- **Los datos de salud pasan por el modelo.** Lo que devuelven las consultas
  (comidas, peso, entrenos) llega al modelo de lenguaje de JARVIS para poder
  contestarte. Eso ocurre igual que con cualquier otra conexión.
- **Quedan en `data/jarvis.db`.** Las respuestas que el cerebro guarda en el
  historial de la conversación, y los avisos de Croac que se dicen, quedan en la
  base de datos local de JARVIS. Ningún dato se envía a un tercero distinto de
  SapoFit y del proveedor del modelo.
- **El cursor no guarda texto.** `data/jarvis/sapofit-cursor.json` contiene el
  cursor, los ids ya dichos (los últimos 200), el número de mensajes pendientes
  y la hora de la última lectura. Ningún texto de Croac, y se escribe con
  permisos 600.
- **El token no se imprime nunca**: no aparece en los registros, en los mensajes
  de error ni en el panel.

## Cómo se prueba

Todo se prueba con respuestas falsas, sin tocar SapoFit ni tu token:
`tests/test_sapofit_readonly.py` (rutas, métodos, parámetros, permisos del
fichero, errores sin secretos, texto no confiable y parseo tolerante) y
`tests/test_sapofit_watch.py` (primera ejecución, cursor atómico, horas de
silencio, límite por hora, deduplicado, cortes, reintentos y que no llama al
cerebro).

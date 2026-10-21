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

> Controlar la casa con confirmación por voz y los avisos hablados son piezas
> aparte, opcionales y apagadas por defecto: ver `docs/casa-control.md`. Esta
> conexión sigue siendo solo lectura.

## Regla que no se negocia

**JARVIS nunca ejecuta acciones sobre la casa ni sobre BTR desde aquí.** No
enciende luces, no arma ni desarma la alarma, no toca calderas, no crea
reservas. Solo consulta. Esa garantía es de **este servidor MCP**: está en su
código, no en la educación del modelo (ver "Qué puede y qué no"). No cubre a un
proceso del mismo usuario con Bash que tenga el token: por eso el token no se
reparte (ver "Dónde guardar las credenciales").

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
en una lista corta, y no sigue redirecciones (una respuesta 3xx es un error; si
las siguiera, reenviaría el token a otro servidor). Un test lo demuestra.

Esto **no es una barrera de lectura frente a quien tenga el token**: Home
Assistant no tiene tokens de solo lectura, y un usuario no administrador también
puede llamar a servicios. Crear el token con un usuario no administrador reduce
el daño (es una mitigación parcial), pero no impide escribir. Lo que protege es
que el token solo lo vea este servidor MCP.

Lo que devuelve Home Assistant (nombres, mensajes) es texto de terceros: se
limpia de caracteres de control, se trunca y se entrega marcado como dato, y la
persona de JARVIS lo cuenta sin obedecerlo.

BTR/ACI: lo que permita ese servidor, que es de solo lectura por su lado
(usuario `mcp_aci_ro`, según la memoria del proyecto). Esta guía no añade ni
quita nada ahí.

## Dónde guardar las credenciales

Nunca en el repositorio, ni en `connections.json`, ni en `.env` versionado.

- **Home Assistant**: el token va en un **fichero con permisos `600`, fuera del
  repo y de `data/`** (por ejemplo `~/.config/jarvis/ha_token`), y el servidor
  lo lee con `HA_TOKEN_FILE`. Indícalo en el bloque `env` de la entrada `casa`
  de `connections.json`, junto con `HA_URL` (la dirección no es secreta). El
  servidor rechaza un fichero legible por grupo u otros. **No exportes
  `HA_TOKEN` en el entorno de JARVIS.** Si usas Infisical, que genere ese
  fichero en el arranque en vez de cargar la variable.
- **BTR/ACI**: `BTR_ACI_BASIC_AUTH` solo puede llegar a Claude Code por el
  entorno del cerebro (la expansión de `${...}` en `headers`); es el caso menos
  bueno, prefiere un puente stdio que lea un fichero `600`.

Qué protege el código y qué no:

- Los **runs sin supervisión** (`run_executor.py`) se lanzan con
  `--dangerously-skip-permissions` y tienen Bash. Por eso se les quitan
  `HA_URL`, `HA_TOKEN`, `HA_TOKEN_FILE` y `BTR_ACI_BASIC_AUTH`
  (`claude_env.run_env()`); un run desviado por un texto malicioso no hereda el
  token por el entorno.
- El cerebro **sí** hereda el entorno de JARVIS (así configura los servidores
  MCP que lanza), por eso la recomendación es el fichero: el token no está en
  ningún entorno.
- Un fichero `600` lo puede leer cualquier proceso de tu usuario, incluido un
  run con Bash que sepa la ruta. El fichero evita el reparto accidental por
  entorno; no es un aislamiento. Si eso te importa, ejecuta JARVIS con otro
  usuario del sistema o no actives la conexión.

> Si tras activar la conexión la herramienta dice "Home Assistant is not
> configured", la variable no ha llegado al hijo (revisa el bloque `env`); ese
> mensaje indica falta de configuración, no un fallo de red.

## Activar la casa

1. En Home Assistant: perfil de usuario, "Tokens de acceso de larga duración",
   crear uno. Guárdalo directamente en el fichero `600` (por ejemplo con
   `umask 077`); no lo pegues en ningún chat. Mejor, un usuario no administrador.
2. Abre `jarvis_home/connections.examples/ha-readonly.json`, copia la entrada
   `casa` dentro de `mcpServers` en `data/jarvis/connections.json` y pon la ruta
   absoluta a `ha_readonly_mcp.py`.
3. En el bloque `env` de esa entrada pon `HA_URL` (por ejemplo
   `http://<tu-HA>:8123`; si es `http://` sin TLS, el token viaja en claro por
   tu red, prefiere `https://`) y `HA_TOKEN_FILE` con la ruta del fichero.
4. Reinicia el servidor y pregúntale "¿a qué estás conectado?". Si algo no
   arrancó, lo nombra.

Coste de contexto: cada herramienta se describe en cada turno (unos 250 tokens);
son cuatro, alrededor de 1.000.

## Activar BTR

1. Abre `jarvis_home/connections.examples/btr-aci-readonly.json`.
2. Sustituye `<URL_DEL_MCP_BTR_ACI>` por la URL real y calcula la cabecera
   `Basic base64(usuario:clave)` fuera del repo; guárdala en la variable
   `BTR_ACI_BASIC_AUTH` (ver los límites arriba).
3. Copia la entrada `btr` a `data/jarvis/connections.json`.

No se ha podido comprobar que la versión de Claude Code que usa JARVIS expanda
`${VARIABLE}` dentro de `headers`. Si no lo hace, usa un puente stdio (por
ejemplo `mcp-remote`) que lea usuario y clave del entorno.

## Separar lo que no es tuyo

Si tu Home Assistant mezcla entidades que no son tuyas — por ejemplo las de un
trabajo, un edificio compartido o cualquier sistema que no deba controlarse
desde un asistente personal — puedes excluirlas de raíz, sin depender de la
educación del modelo: `ha_exclude.py`, usado por `ha_readonly_mcp.py`,
`ha_watch.py` y `home_actions.py` por igual.

El fichero (el tuyo, JARVIS no lo crea ni lo edita) va en
`data/jarvis/ha-exclude.json`, o en la ruta que indique `HA_EXCLUDE_FILE`
(variable de entorno del servidor `casa` en `connections.json`, y del propio
proceso de JARVIS para `ha_watch.py` y `home_actions.py`). Ejemplo inactivo en
`jarvis_home/connections.examples/ha-exclude.json`. Formato:

```json
{
  "entity_ids": ["light.habitacion_101"],
  "patterns": ["sensor\\.hab_[0-9]+_.*"],
  "domains": ["climate"]
}
```

- `entity_ids`: coincidencia EXACTA, en minúsculas, tal cual. Nada se
  normaliza para que un identificador cuele ni para que deje de coincidir.
- `patterns`: expresiones regulares comparadas con `re.fullmatch` (nunca
  `match` ni `search`), de longitud limitada y descartadas si parecen
  patológicas, para que un patrón mal escrito no pueda colgar el proceso.
- `domains`: dominios completos (`climate`, no `climate.algo`).

Las tres claves son opcionales. Sin `HA_EXCLUDE_FILE` y sin ese fichero en
`data/jarvis/`, el comportamiento es EXACTAMENTE el de siempre: no se excluye
nada.

**Fallo cerrado**: si `HA_EXCLUDE_FILE` está definido, o el fichero por
defecto existe, pero no se puede leer, no es JSON válido o tiene permisos de
grupo u otros, ninguna de las tres piezas muestra ni toca nada — las
herramientas de lectura dicen con claridad que el fichero de exclusión no es
válido y no enseñan nada, `ha_watch` no da ningún aviso y `home_actions`
rechaza cualquier acción, hasta que el fichero sea válido de nuevo.

**Esto no sustituye separar bien las cuentas.** Es un filtro dentro de JARVIS,
no una propiedad de Home Assistant ni del token: si ese mismo token de lectura
se usa fuera de JARVIS (otra integración, otro cliente, `curl` a mano), ve
exactamente lo mismo que sin este fichero. La separación real sigue siendo un
usuario de Home Assistant con permisos limitados, o dos instancias distintas.

## Comprobaciones

- Las herramientas de Home Assistant dicen claramente cuando falta
  configuración, cuando el token es rechazado y cuando Home Assistant no
  responde, sin mostrar nunca la URL ni el token.
- La batería `tests/test_ha_readonly.py` usa un HTTP falso: no necesita red ni
  Home Assistant.

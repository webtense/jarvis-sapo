"""Lo que la pantalla de administración puede LEER, y nada más.

Tres reglas gobiernan este módulo, y son la razón de que exista aparte de
`server.py`:

1. **Rutas fijas.** Cada fichero que se lee aquí se localiza con una función
   de `data_paths` (`connections.json`, `home-actions.json`,
   `ha-exclude.json`, `home-actions.log`). Ninguna ruta llega en una
   petición, ni siquiera como nombre de fichero dentro de una carpeta: la
   pantalla no elige qué se lee.

2. **Ni un secreto sale de aquí.** Nunca se devuelve una URL, una ruta de
   fichero de token, un valor de variable de entorno ni el contenido de un
   token. De un token se dice si el fichero existe, si sus permisos son los
   correctos y si las credenciales están completas — tres booleanos, no el
   token. La cuenta de Google NO se devuelve porque JARVIS no la guarda:
   `scripts/google-auth.py` escribe credenciales, no la dirección de correo,
   y afirmar una cuenta que no consta sería inventarla.

3. **Los recuentos se calculan aquí.** La página recibe números y nombres ya
   contados y ya limpios de caracteres de control; no recibe los ficheros.

Nada de este módulo escribe. La única escritura de la pantalla pasa por
`POST /api/admin/settings` en `server.py`, con esquema cerrado.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

import data_paths

# Topes. Un fichero del usuario puede ser cualquier cosa; la pantalla recibe
# una cantidad acotada de texto acotado pase lo que pase.
MAX_SERVERS = 40
MAX_NAMES = 300
MAX_PROBLEMS = 40
MAX_LOG_LINES = 20
LOG_TAIL_BYTES = 64 * 1024
NAME_MAX_CHARS = 120
GOOGLE_TOKEN_MAX_BYTES = 64 * 1024
# Claves de credenciales de Google de las que solo se informa "está / no está".
GOOGLE_CREDENTIAL_KEYS = ("access_token", "refresh_token", "client_id",
                          "client_secret")


def safe_text(value, limit: int = NAME_MAX_CHARS) -> str:
    """Texto de un fichero ajeno, listo para que la página lo pinte con
    `textContent`: sin caracteres de control y con una longitud."""
    if not isinstance(value, str):
        return ""
    cleaned = "".join(ch if ch.isprintable() else "?" for ch in value)
    return cleaned[:limit]


# ---------------------------------------------------------------------------
# Conexiones
# ---------------------------------------------------------------------------

def _token_file_state(path: str) -> str:
    """`ok`, `permissions`, `missing` o `unreadable`. Nunca el camino."""
    if not path:
        return "missing"
    try:
        st = os.stat(path)
    except OSError:
        return "missing"
    if not stat.S_ISREG(st.st_mode):
        return "unreadable"
    if st.st_mode & 0o077:
        return "permissions"
    if st.st_size <= 0:
        return "unreadable"
    return "ok"


def _entry_env(entry: dict) -> dict:
    env = entry.get("env")
    return env if isinstance(env, dict) else {}


def _command_present(entry: dict) -> bool:
    """¿Existe el programa que arrancaría este servidor MCP local? Es la
    prueba más ligera que hay y no ejecuta nada."""
    command = entry.get("command")
    if not isinstance(command, str) or not command:
        return False
    if shutil.which(command) is not None:
        return True
    try:
        return Path(command).is_file()
    except OSError:
        return False


def _is_remote(entry: dict) -> bool:
    url = entry.get("url")
    return isinstance(url, str) and bool(url)


def remote_probe_urls(servers: dict) -> dict:
    """{nombre: url} de los servidores MCP remotos, PARA USO DEL SERVIDOR.

    No es parte de ninguna respuesta: la URL de un servidor del usuario puede
    llevar un identificador privado en el camino y jamás vuelve al navegador.
    """
    out = {}
    for name in sorted(servers)[:MAX_SERVERS]:
        entry = servers[name]
        if isinstance(entry, dict) and _is_remote(entry):
            out[name] = entry["url"]
    return out


def _home_assistant_status(servers: dict) -> dict:
    """Estado de Home Assistant, a partir de lo que el usuario declaró.

    Las variables de HA viven en el bloque `env` del servidor MCP de la casa
    (y, para las acciones, en el entorno del propio JARVIS). Aquí solo se
    dice si están puestas y si el fichero del token es correcto.
    """
    url_set = bool(os.environ.get("HA_URL", "").strip())
    token_file = os.environ.get("HA_TOKEN_FILE", "").strip()
    for entry in servers.values():
        if not isinstance(entry, dict):
            continue
        env = _entry_env(entry)
        if isinstance(env.get("HA_URL"), str) and env["HA_URL"].strip():
            url_set = True
        candidate = env.get("HA_TOKEN_FILE")
        if isinstance(candidate, str) and candidate.strip() and not token_file:
            token_file = candidate.strip()
    actions_token = os.environ.get("HA_ACTIONS_TOKEN_FILE", "").strip()
    return {
        "configured": bool(url_set and token_file),
        "address_set": url_set,
        "read_token": _token_file_state(token_file),
        # El token de acciones es OTRO fichero a propósito; que falte no es
        # un fallo, es la configuración por defecto.
        "actions_token": _token_file_state(actions_token) if actions_token else "absent",
    }


def _google_status(servers: dict) -> dict:
    """Estado de Google: fichero de credenciales, permisos y qué piezas hay.

    Se abre el fichero y se mira QUÉ CLAVES tiene, nunca sus valores. Los
    ámbitos (`scopes`) sí se devuelven: son direcciones públicas de la API de
    Google que describen el permiso concedido, y verlas es justamente lo que
    permite comprobar que la conexión es de solo lectura.
    """
    token_file = os.environ.get("GOOGLE_TOKEN_FILE", "").strip()
    for entry in servers.values():
        if not isinstance(entry, dict):
            continue
        candidate = _entry_env(entry).get("GOOGLE_TOKEN_FILE")
        if isinstance(candidate, str) and candidate.strip() and not token_file:
            token_file = candidate.strip()
    state = _token_file_state(token_file) if token_file else "absent"
    out = {
        "configured": bool(token_file),
        "token_file": state,
        "credentials_complete": False,
        "scopes": [],
        # JARVIS no guarda la dirección de la cuenta en ninguna parte, así que
        # la pantalla dice eso y no se la inventa.
        "account_known": False,
    }
    if state != "ok":
        return out
    try:
        with open(token_file, "rb") as fh:
            raw = fh.read(GOOGLE_TOKEN_MAX_BYTES + 1)
    except OSError:
        out["token_file"] = "unreadable"
        return out
    if len(raw) > GOOGLE_TOKEN_MAX_BYTES:
        out["token_file"] = "unreadable"
        return out
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        out["token_file"] = "unreadable"
        return out
    if not isinstance(data, dict):
        out["token_file"] = "unreadable"
        return out
    out["credentials_complete"] = all(
        isinstance(data.get(k), str) and data[k] for k in GOOGLE_CREDENTIAL_KEYS)
    scopes = data.get("scopes")
    if isinstance(scopes, list):
        out["scopes"] = [safe_text(s, 160) for s in scopes[:20]
                         if isinstance(s, str)]
    return out


def connections_report(servers: dict, problems: list) -> dict:
    """Los servidores MCP declarados, sin una sola URL ni ruta.

    `servers` y `problems` los trae `server.declared_connections()`: este
    módulo no vuelve a interpretar el fichero, solo describe el resultado.
    """
    listed = []
    for name in sorted(servers)[:MAX_SERVERS]:
        entry = servers[name]
        if not isinstance(entry, dict):
            continue
        remote = _is_remote(entry)
        listed.append({
            "name": safe_text(name, 64),
            "kind": "remote" if remote else "local",
            # Un servidor remoto lo sondea `server.py` (es una espera de red);
            # de uno local la respuesta ya está aquí.
            "status": "unknown" if remote else (
                "ready" if _command_present(entry) else "missing"),
        })
    return {
        "servers": listed,
        "problems": [safe_text(p, 300) for p in problems[:MAX_PROBLEMS]],
        "home_assistant": _home_assistant_status(servers),
        "google": _google_status(servers),
    }


# ---------------------------------------------------------------------------
# La casa
# ---------------------------------------------------------------------------

def _exclusion_counts() -> dict:
    import ha_exclude
    try:
        excl = ha_exclude.load()
    except ha_exclude.ExcludeFileInvalid:
        return {"valid": False, "entity_ids": 0, "patterns": 0, "domains": 0}
    return {"valid": True, "entity_ids": len(excl.entity_ids),
            "patterns": len(excl.patterns), "domains": len(excl.domains)}


def _audit_tail() -> list:
    """Las últimas líneas del registro de acciones, con tres campos.

    El registro lo escribe `home_actions.audit`, que ya limpia lo que guarda;
    aun así se vuelve a limpiar y a acotar aquí, porque es un fichero en
    disco y lo que haya en él es dato, no verdad.
    """
    path = data_paths.home_actions_log_path()
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > LOG_TAIL_BYTES:
                fh.seek(size - LOG_TAIL_BYTES)
                fh.readline()              # la primera puede estar cortada
            raw = fh.read(LOG_TAIL_BYTES + 1024)
    except OSError:
        return []
    out = []
    for line in raw.decode("utf-8", "replace").splitlines()[-MAX_LOG_LINES:]:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        out.append({
            "ts": safe_text(entry.get("ts"), 40),
            "entity": safe_text(entry.get("entity"), 80),
            "result": safe_text(entry.get("result"), 80),
            "dryrun": entry.get("dryrun") is True,
        })
    return out


def home_report() -> dict:
    """La casa, de solo lectura: si está activa, qué puede tocar y qué pasó.

    La lista blanca NO se edita desde aquí. Es deliberado y está explicado en
    `docs/admin.md`: `home-actions.json` es un fichero del usuario con
    permisos 600 que JARVIS nunca crea ni modifica, y una pantalla web capaz
    de añadirle una entidad convierte un clic robado en una llave de la casa.
    """
    import home_actions
    report = {
        "enabled": False,
        "reason": "",
        "dryrun": home_actions.dryrun(),
        "confirm_strict": home_actions.confirm_strict(),
        # ¿Puede el usuario confirmar desde el panel (y no solo de viva voz)?
        # Lo dice SU fichero de lista blanca; aquí solo se lee.
        "panel_confirm": False,
        "by_domain": {},
        "names": [],
        "problems": [],
        "excluded": _exclusion_counts(),
        "log": _audit_tail(),
    }
    try:
        policy = home_actions.load_policy()
    except home_actions.Disabled as e:
        report["reason"] = safe_text(str(e), 300)
        return report
    except Exception:                                    # pragma: no cover
        report["reason"] = "home control could not be read"
        return report
    report["enabled"] = True
    report["panel_confirm"] = policy.panel_confirm is True
    counts: dict = {}
    for entity_id in policy.entities:
        domain = entity_id.split(".", 1)[0]
        counts[domain] = counts.get(domain, 0) + 1
    report["by_domain"] = dict(sorted(counts.items()))
    report["names"] = sorted(
        safe_text(name) for name in list(policy.entities.values())[:MAX_NAMES])
    report["problems"] = [safe_text(p, 300) for p in policy.problems[:MAX_PROBLEMS]]
    return report


# ---------------------------------------------------------------------------
# El sistema
# ---------------------------------------------------------------------------

def _changelog_version() -> str:
    """La versión de la última entrada del CHANGELOG. Se LEE, no se escribe."""
    path = Path(__file__).parent / "CHANGELOG.md"
    try:
        with open(path, encoding="utf-8") as fh:
            for _ in range(200):
                line = fh.readline()
                if not line:
                    break
                if line.startswith("## "):
                    return safe_text(line[3:].strip().split(" ", 1)[0], 40)
    except OSError:
        pass
    return ""


def version() -> str:
    return _changelog_version()


def service_state() -> str:
    """`systemd`, `manual` o `unknown`. Sin ejecutar `systemctl`: la variable
    `INVOCATION_ID` la pone systemd en cada unidad que arranca."""
    if os.environ.get("INVOCATION_ID"):
        return "systemd"
    return "manual"


def listening_report() -> dict:
    import stt_local
    return {
        "mode": stt_local.requested_mode(),
        "wake": stt_local.wake_mode(),
        "wake_window_sec": int(stt_local.wake_window_sec()),
        "barge_in": stt_local.barge_in_requested(),
        "model": safe_text(stt_local.model_name(), 40),
        "warnings": [safe_text(w, 300) for w in stt_local.problems()[:MAX_PROBLEMS]],
    }


def system_report(uptime_seconds: int, voice_provider: str) -> dict:
    import lang
    return {
        "version": version(),
        "uptime_seconds": int(uptime_seconds),
        "service": service_state(),
        "voice_provider": safe_text(voice_provider, 40),
        "language": lang.language(),
        "listening": listening_report(),
    }


# Lo que vale un ajuste que nadie ha tocado — el mismo valor que lee el
# módulo que lo usa, para que la pantalla no muestre un vacío donde hay un
# comportamiento.
SETTING_DEFAULTS = {
    "USER_NAME": "",
    "HONORIFIC": "sir",
    "JARVIS_LANGUAGE": "en",
    "JARVIS_STT": "browser",
    "JARVIS_STT_MODEL": "base",
    "JARVIS_WAKE_WORD": "off",
    "JARVIS_WAKE_WINDOW_SEC": "20",
    "JARVIS_BARGE_IN": "off",
    "JARVIS_BARGE_IN_RMS": "1200",
    "JARVIS_BARGE_IN_MS": "400",
}


def current_values(env_dict: dict) -> dict:
    """Lo que vale hoy cada ajuste de la pantalla.

    El `.env` manda sobre el entorno heredado: es el que la pantalla escribe
    y el que el usuario verá la próxima vez que arranque.
    """
    out = {}
    for key, fallback in SETTING_DEFAULTS.items():
        value = env_dict.get(key)
        if not isinstance(value, str) or not value.strip():
            value = os.environ.get(key, "")
        out[key] = safe_text(value.strip() or fallback, 200)
    return out

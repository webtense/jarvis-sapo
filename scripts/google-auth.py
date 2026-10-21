#!/usr/bin/env python3
"""Interactive OAuth authorisation for JARVIS's read-only Google connection.

Run this once (and again whenever google_readonly_mcp.py says the
authorisation has expired or been revoked) to produce the token file that
GOOGLE_TOKEN_FILE points at. It asks for ONLY the read-only scopes
google_readonly_mcp.py uses, never write access, and it is meant for the
user's PERSONAL Google account, not a work/corporate one.

It never prints a secret: not the client secret it reads, not the tokens it
receives, not any fragment of either. On success it prints nothing but a
short confirmation naming the file it wrote.

`google_auth_oauthlib` is imported lazily, inside `_run_flow`, so importing
this module (as the test suite does) never requires it to be installed.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/tasks.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

# Minimal scopes for google_actions.py's write connection (docs/google-escritura.md):
# calendar.events (create/update events, never a general calendar.readonly
# grant that would also widen reads), tasks (the Tasks API offers no
# narrower "events-only" scope), drive.file (sees only files THIS app
# creates — the JARVIS Notas folder and the docs in it, nothing else in the
# user's Drive), and the same identity scopes as the read-only connection.
WRITE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/drive.file",
]

DEFAULT_CLIENT_FROM = "~/.config/webtense_personal.json"
DEFAULT_TOKEN_OUT = "~/.config/jarvis/google_readonly.json"
DEFAULT_WRITE_TOKEN_OUT = "~/.config/jarvis/google_write.json"


def _fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def load_client_config(client_from: Path) -> dict:
    """Read client_id/client_secret from an existing OAuth client file.

    Accepts either the raw {"client_id":..., "client_secret":..., "token_uri":...}
    shape or Google's own {"installed": {...}} / {"web": {...}} download shape.
    Never writes to this file; never prints its content.
    """
    if not client_from.is_file():
        _fail(f"No existe el fichero de credenciales de cliente: {client_from}. "
              "Crea una app OAuth 'Desktop' en Google Cloud Console y pásala con --client-from.")
    try:
        raw = json.loads(client_from.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _fail("No se pudo leer o parsear el fichero de credenciales de cliente.")
    section = raw.get("installed") or raw.get("web") or raw
    client_id = section.get("client_id")
    client_secret = section.get("client_secret")
    token_uri = section.get("token_uri", "https://oauth2.googleapis.com/token")
    if not client_id or not client_secret:
        _fail("El fichero de credenciales de cliente no tiene client_id/client_secret.")
    return {"client_id": client_id, "client_secret": client_secret, "token_uri": token_uri}


def build_token_payload(client_config: dict, credentials, scopes=SCOPES) -> dict:
    """Pure function: given a client config and an object with the attributes
    google-auth credentials expose, produce the dict written to disk. Kept
    separate from `_run_flow` so it is testable with a fake credentials
    object, with no real OAuth flow involved."""
    return {
        "access_token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "client_id": client_config["client_id"],
        "client_secret": client_config["client_secret"],
        "token_uri": client_config["token_uri"],
        "scopes": list(getattr(credentials, "scopes", None) or scopes),
    }


def write_token_file(token_path: Path, payload: dict) -> None:
    """Directory 700, file 600, atomic write. Never prints `payload`."""
    token_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(token_path.parent, 0o700)
    fd, tmp_name = tempfile.mkstemp(dir=str(token_path.parent), prefix=".google_token_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, token_path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        _fail("No se pudo escribir el fichero de token.")


def _run_flow(client_config: dict, scopes=SCOPES):
    """The actual interactive OAuth flow. Imports google_auth_oauthlib lazily
    so the rest of this module (and its tests) never need it installed."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        _fail("Falta la dependencia 'google-auth-oauthlib'. Instálala con:\n"
              "  pip install google-auth-oauthlib")
    client_secrets = {"installed": {
        "client_id": client_config["client_id"],
        "client_secret": client_config["client_secret"],
        "token_uri": client_config["token_uri"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "redirect_uris": ["http://localhost"],
    }}
    flow = InstalledAppFlow.from_client_config(client_secrets, scopes=scopes)
    return flow.run_local_server(port=0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Autoriza la conexion personal de Google para JARVIS.")
    parser.add_argument("--client-from", default=DEFAULT_CLIENT_FROM,
                        help=f"Fichero del que reutilizar client_id/client_secret "
                             f"(por defecto {DEFAULT_CLIENT_FROM}).")
    parser.add_argument("--token-out", default=None,
                        help="Donde guardar el token nuevo (por defecto "
                             f"{DEFAULT_TOKEN_OUT}, o {DEFAULT_WRITE_TOKEN_OUT} con --write).")
    parser.add_argument("--write", action="store_true",
                        help="Pide los scopes de ESCRITURA (Calendar, Tasks, Drive.file) "
                             "para google_actions.py en vez de los de solo lectura "
                             "(comportamiento por defecto sin esta opción). Ver "
                             "docs/google-escritura.md.")
    args = parser.parse_args(argv)

    scopes = WRITE_SCOPES if args.write else SCOPES
    default_out = DEFAULT_WRITE_TOKEN_OUT if args.write else DEFAULT_TOKEN_OUT
    client_from = Path(os.path.expanduser(args.client_from))
    token_out = Path(os.path.expanduser(args.token_out or default_out))

    client_config = load_client_config(client_from)
    credentials = _run_flow(client_config, scopes)
    payload = build_token_payload(client_config, credentials, scopes)
    write_token_file(token_out, payload)
    kind = "de ESCRITURA" if args.write else "de solo lectura"
    print(f"Autorizacion {kind} completada. Token guardado en: {token_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""The JARVIS bridge, as a stdio MCP server for OTHER Claude Code sessions.

`jarvis_mcp.py` is the brain's own channel: it carries the tool token and
reaches JARVIS's acting tools. This one is for the sessions the user runs in
a terminal, in VS Code or in Cowork. They can do four things and nothing
else — say something aloud, ask the user a question and wait for the answer,
post a desktop notification, and PROPOSE something for JARVIS's memory (which
the user must confirm; this never writes it). There is no tool here that runs
a task or changes the machine, and the server it talks to has no route that
would.

It holds no state. It authenticates with its OWN token (the bridge token,
a 0600 file whose PATH arrives in the environment — the token itself is never
in the `claude mcp add` command line, in a log, or in any reply this process
writes) and forwards each call over loopback to `/internal/bridge/*`.

Hand-rolled JSON-RPC over stdin/stdout, like jarvis_mcp.py: no new dependency.
The loopback TLS rule (an unverified context for 127.0.0.1 ONLY) is the one in
jarvis_mcp.py and is reused rather than copied.

Environment:
  JARVIS_BRIDGE_URL         base URL, default http://127.0.0.1:8340/internal/bridge
  JARVIS_BRIDGE_TOKEN_FILE  path of the bridge token (required)
  JARVIS_BRIDGE_SESSION     how this session introduces itself (default: the
                            name of the folder it runs in)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

from jarvis_mcp import _ssl_context_for

PROTOCOL_VERSION = "2024-11-05"

# One call that is answered at once: speak, notify, remember.
TIMEOUT_SEC = 20.0

# `jarvis_ask_user` waits for a human. The server holds the call for at most
# SPEAK_WAIT_MAX (it first says the question) plus the answer's own timeout,
# and this child must wait LONGER than that — see jarvis_mcp.py's TIMEOUT_SEC
# comment for what happens when the client gives up first: the question stays
# live while the session is told JARVIS is unreachable. Mirrors bridge.py.
ASK_DEFAULT_SEC = 45.0
ASK_MIN_SEC = 5.0
ASK_MAX_SEC = 120.0
ASK_HEADROOM_SEC = 30.0 + 15.0

TOOL_SPECS = [
    {
        "name": "jarvis_speak",
        "description": (
            "Have JARVIS say something aloud to the user, at the next pause. Use "
            "it for a short heads-up: a long task has finished, a build broke. "
            "One or two sentences, plain text, at most 500 characters; "
            "repeats and rapid calls are refused. The user hears it introduced "
            "by this session's name."),
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string",
                                    "description": "What to say. Plain, short."}},
            "required": ["text"],
        },
    },
    {
        "name": "jarvis_ask_user",
        "description": (
            "Ask the user a question out loud and wait for his spoken (or typed) "
            "answer. Use it when you are blocked on a decision only he can make "
            "and he is away from the terminal. Returns his answer, or "
            "'sin respuesta' if he did not answer in time — then carry on with "
            "the safest option or stop; do not ask again at once. One question "
            "at a time, at most 400 characters."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string",
                             "description": "The question, self-contained."},
                "timeout_sec": {"type": "number",
                                "description": ("How long to wait for the answer, "
                                                "5 to 120 seconds (default 45).")},
            },
            "required": ["question"],
        },
    },
    {
        "name": "jarvis_notify",
        "description": (
            "Post a desktop notification: for something the user should see when "
            "he is back, not hear. A title (80 characters) and a message (300)."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short title."},
                "message": {"type": "string", "description": "The message."},
            },
            "required": ["title", "message"],
        },
    },
    {
        "name": "jarvis_remember",
        "description": (
            "Suggest ONE durable fact for JARVIS's long-term memory (at most 300 "
            "characters). It is NOT saved: JARVIS asks the user, and it enters "
            "memory only if he says yes. Never suggest secrets, tokens or "
            "passwords."),
        "inputSchema": {
            "type": "object",
            "properties": {"fact": {"type": "string",
                                    "description": "One fact, one sentence."}},
            "required": ["fact"],
        },
    },
]

# tool name -> (route, the arguments it may forward, in the server's names)
_ROUTES = {
    "jarvis_speak": ("speak", {"text": "text"}),
    "jarvis_ask_user": ("ask", {"question": "question",
                                "timeout_sec": "timeout_sec"}),
    "jarvis_notify": ("notify", {"title": "title", "message": "message"}),
    "jarvis_remember": ("remember", {"fact": "fact"}),
}


def _base_url() -> str:
    return os.getenv("JARVIS_BRIDGE_URL",
                     "http://127.0.0.1:8340/internal/bridge").rstrip("/")


def _token() -> str:
    path = os.getenv("JARVIS_BRIDGE_TOKEN_FILE", "")
    if not path:
        return ""
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _session_name() -> str:
    explicit = os.getenv("JARVIS_BRIDGE_SESSION", "").strip()
    if explicit:
        return explicit
    return os.path.basename(os.getcwd().rstrip("/")) or ""


def _ask_timeout(raw) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return ASK_DEFAULT_SEC
    if value != value:
        return ASK_DEFAULT_SEC
    return max(ASK_MIN_SEC, min(ASK_MAX_SEC, value))


def _forward(tool: str, arguments: dict) -> tuple[bool, str]:
    """One call to the server. Every failure becomes a sentence for the
    session, never a traceback — and never contains the token."""
    route, fields = _ROUTES[tool]
    payload = {server_key: arguments[key] for key, server_key in fields.items()
               if key in arguments}
    payload["session"] = _session_name()
    timeout = TIMEOUT_SEC
    if tool == "jarvis_ask_user":
        wait = _ask_timeout(arguments.get("timeout_sec"))
        payload["timeout_sec"] = wait
        timeout = wait + ASK_HEADROOM_SEC
    url = f"{_base_url()}/{route}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {_token()}"})
    try:
        with urllib.request.urlopen(
                req, timeout=timeout, context=_ssl_context_for(url)) as resp:
            reply = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, ("JARVIS rejected the bridge token — run "
                           "scripts/install-bridge.sh again.")
        return False, f"JARVIS refused the call ({e.code})."
    except (urllib.error.URLError, OSError, TimeoutError):
        return False, "The JARVIS server is unreachable."
    except ValueError:
        return False, "The JARVIS server sent something unreadable."
    if not isinstance(reply, dict):
        return False, "The JARVIS server sent something unreadable."
    return bool(reply.get("ok")), str(reply.get("text", ""))


def _result(rid, ok: bool, text: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid,
            "result": {"content": [{"type": "text", "text": text}],
                       "isError": not ok}}


def _error(rid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle(msg: dict) -> dict | None:
    """One JSON-RPC message in, at most one reply out (none for a
    notification: a message with no "id" member, whatever the method)."""
    method = msg.get("method")
    is_notification = "id" not in msg
    rid = msg.get("id")

    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "initialize":
        reply = {"jsonrpc": "2.0", "id": rid,
                 "result": {"protocolVersion": PROTOCOL_VERSION,
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "jarvis-bridge",
                                           "version": "1.0.0"}}}
    elif method == "ping":
        reply = {"jsonrpc": "2.0", "id": rid, "result": {}}
    elif method == "tools/list":
        reply = {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOL_SPECS}}
    elif method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        args = params.get("arguments") or {}
        if name not in _ROUTES:
            reply = _result(rid, False, f"Unknown tool: {name}")
        elif not isinstance(args, dict):
            reply = _result(rid, False, "Arguments must be an object.")
        else:
            ok, text = _forward(name, args)
            reply = _result(rid, ok, text)
    else:
        reply = _error(rid, -32601, f"Unknown method: {method}")

    return None if is_notification else reply


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue                      # a garbage line must not kill the server
        if not isinstance(msg, dict):
            continue
        try:
            reply = handle(msg)
        except Exception as e:            # never die mid-conversation
            reply = _error(msg.get("id"), -32603, f"Internal error: {e}")
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

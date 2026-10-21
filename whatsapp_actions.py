"""Sending a WhatsApp message from the user's PERSONAL number, the same
conservative way `google_actions.py` writes to Google and `home_actions.py`
controls the house.

Nothing here is on by default. With no `whatsapp-actions.json` (the user's own
policy, `data_paths.whatsapp_actions_path()`), or with `enabled` anything but
`true`, there is nothing to send with: not "all refused by a prompt", but no
code path that reaches the network.

Scope, on purpose and nothing more (see `docs/whatsapp.md`):

  * ONE kind of action: a plain text message to ONE individual who is in the
    user's `contacts` (name -> `<digits>@s.whatsapp.net`) or to his own number
    (`self`). Never a group (`@g.us`), never a broadcast list, never a number
    that is not on the list, never a contact he also hid with `exclude`.
  * Text only: no media, no link preview (`linkPreview` is always false), no
    mentions, no quoting. A length limit the user sets.

What this module guarantees:

  * Only a CLOSED request is ever made (`_ALLOWED`): `POST
    /message/sendText/<instance>` with exactly the keys `number`, `text`,
    `linkPreview`. `_request` checks it BEFORE a socket is opened. The destination is a number rebuilt from the validated
    JID, never text from the brain.
  * The brain's whole part is the PROPOSAL (`check`), which sends nothing. The
    spoken question, the "yes" and the act live in `server.py` and `bridge.py`.
    `perform` re-reads the policy and re-checks the action itself, so even a
    caller that skipped `check` cannot send to anyone off the list.
  * Its credentials come from `EVOLUTION_WRITE_TOKEN_FILE`, or, if that is not
    set, from the reading file `EVOLUTION_TOKEN_FILE` (see whatsapp_policy.py).
    Refused unless 600, the user's, and a plain `https://host` URL. The
    instance is whatever that file names, never a tool argument.
  * Every attempt is one JSONL line in `whatsapp-actions.log` with the date,
    the recipient's name, a truncated JID, how many characters, who confirmed
    and the result. NEVER the text of the message and never a token. If that
    log cannot be written, nothing is sent.
  * A frequency limit and a dry-run mode (`JARVIS_WHATSAPP_DRYRUN=1`).
  * No redirect is followed and no proxy is used. A send is never retried:
    repeating a request that may have gone through would send it twice.

What it does NOT protect against (docs/whatsapp.md): a process of the same
user with Bash can read the 600 credentials file and call Evolution itself;
ambient audio can say "yes"; and the Evolution server is shared, so whoever
administers it can read what passes through it.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import bridge
import data_paths
import i18n
import lang
import whatsapp_policy as wp

TIMEOUT_SEC = 15.0
LOG_MAX_BYTES = 1024 * 1024
RESPONSE_MAX_BYTES = 64 * 1024         # read and thrown away: never spoken
PROPOSALS_PER_5_MIN = 6
CONTACT_MAX = 60

_BAD_RE = re.compile("[\x00-\x09\x0b-\x1f\x7f-\x9f  ‪-‮⁦-⁩​-‏﻿]")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_NUMBER_RE = re.compile(r"[0-9]{6,20}")
_SELF_NAMES = frozenset({"yo", "mí", "mi", "mí mismo", "mi mismo", "me", "myself", "a mí",
                         "a mi", "a mí mismo", "a mi mismo"})


class Refused(Exception):
    """A request the policy will not carry out. Safe for the brain to read."""


class Disabled(Exception):
    """WhatsApp sending is not in use: no file, switched off, or a file that
    cannot be trusted."""


class WhatsAppActionError(Exception):
    """A failure whose message is safe to log (no URL, no token, no text)."""


# ---------------------------------------------------------------------------
# The policy and the validated action
# ---------------------------------------------------------------------------

def load_policy(path=None) -> wp.Policy:
    """The user's policy with sending switched on, or `Disabled`."""
    try:
        policy = wp.load(str(path) if path else str(data_paths.whatsapp_actions_path()))
    except wp.PolicyInvalid as e:
        raise Disabled(str(e)) from None
    if not policy.present:
        raise Disabled("There is no whatsapp-actions.json: WhatsApp sending is off.")
    if not policy.enabled:
        raise Disabled("whatsapp-actions.json has not been switched on (enabled: true).")
    return policy


@dataclass(frozen=True)
class Action:
    """One validated message: who (by the user's own name for them), to which
    JID, what, and whether the turn had read other people's messages."""
    contact: str          # how it is said and logged
    jid: str
    text: str
    tainted: bool = False
    asked: str = ""       # the name as it was asked for: what the policy is searched by

    def as_dict(self) -> dict:
        # No JID: it is rebuilt from the policy when the user says yes.
        return {"contact": self.asked or self.contact, "text": self.text,
                "tainted": self.tainted}


def _safe_key(key) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "?", str(key))[:40]


def check(policy: wp.Policy, contact, text, tainted: bool = False) -> Action:
    """The validated Action, or `Refused`. Sends nothing."""
    if not isinstance(contact, str) or not contact.strip():
        raise Refused("whatsapp_propose needs a contact name from the user's list.")
    if len(contact) > CONTACT_MAX:
        raise Refused("That is not a contact name.")
    key = wp.norm_name(contact)
    listed = policy.contacts.get(key)
    if listed:
        display, jid = listed
    elif key in _SELF_NAMES and policy.self_jid:
        display, jid = ("su propio número" if lang.language() == "es"
                        else "your own number"), policy.self_jid
    else:
        raise Refused("That contact is not in the user's list of allowed WhatsApp contacts.")
    if not wp.SEND_JID_RE.fullmatch(jid):          # a group, a list, anything but one person
        raise Refused("Messages go only to individual people, never to groups or lists.")
    if policy.is_excluded(jid, display):
        raise Refused("That contact is hidden by the user's own exclusion list.")
    return Action(display, jid, _clean_text(text, policy.max_chars), bool(tainted), contact)


def _clean_text(value, limit: int) -> str:
    if not isinstance(value, str):
        raise Refused("text must be a string.")
    if len(value) > limit * 4 + 16:                  # no regex over a megabyte
        raise Refused(f"The message is too long (max {limit} characters).")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = _BAD_RE.sub(" ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise Refused("The message cannot be empty.")
    if len(text) > limit:
        raise Refused(f"The message is too long (max {limit} characters).")
    return text


# ---------------------------------------------------------------------------
# What is said: the whole message, to the whole name, before anything happens
# ---------------------------------------------------------------------------

def question(action: Action) -> str:
    """The sentence JARVIS says before anything happens: who and exactly what.
    Built only from the validated Action. After reading other people's
    messages it also asks for the two-word confirmation."""
    if action.tainted:
        english = ("Shall I send {name} this WhatsApp: «{text}», sir? I have just "
                   "read messages from other people, so say yes, do it, to confirm.").format(
            name=action.contact, text=action.text)
    else:
        english = "Shall I send {name} this WhatsApp: «{text}», sir?".format(
            name=action.contact, text=action.text)
    return i18n.t(english)


# ---------------------------------------------------------------------------
# The closed request
# ---------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# ProxyHandler({}) = no proxy at all, whatever http_proxy says.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def _open(req: urllib.request.Request, timeout: float):
    """The network seam; tests replace it."""
    return _OPENER.open(req, timeout=timeout)  # noqa: S310 (checked by _check_call)


_SEND_PATH = "/message/sendText/{inst}"
_ALLOWED = (("POST", _SEND_PATH, frozenset({"number", "text", "linkPreview"})),)


def _check_call(method: str, path: str, body, inst: str) -> None:
    """Refuse anything but one text message to one number, no preview. Runs
    BEFORE a socket is opened."""
    if (method, path) != ("POST", _SEND_PATH.format(inst=inst)):
        raise WhatsAppActionError("not_permitted")
    if (not isinstance(body, dict) or set(body) != _ALLOWED[0][2]
            or not isinstance(body["number"], str) or not _NUMBER_RE.fullmatch(body["number"])
            or not isinstance(body["text"], str) or not body["text"]
            or body["linkPreview"] is not False):
        raise WhatsAppActionError("not_permitted")


def _request(body: dict):
    try:
        creds = wp.load_credentials(write=True)
    except wp.CredentialsError:
        raise WhatsAppActionError("credentials") from None
    path = _SEND_PATH.format(inst=creds.instance)
    _check_call("POST", path, body, creds.instance)
    req = urllib.request.Request(
        creds.base + path, method="POST",
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        headers={"apikey": creds.apikey, "Accept": "application/json",
                 "Content-Type": "application/json"})
    try:
        with _open(req, TIMEOUT_SEC) as resp:
            raw = resp.read(RESPONSE_MAX_BYTES)
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as e:
        if 300 <= e.code <= 399:
            raise WhatsAppActionError("redirect") from None
        raise WhatsAppActionError(f"http_{e.code}") from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise WhatsAppActionError("unreachable") from None
    if not (200 <= int(status) < 300):
        raise WhatsAppActionError(f"http_{int(status)}")
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}                # it went through; only the receipt is unreadable


@dataclass
class Outcome:
    ok: bool
    code: str            # "ok" | "dryrun" | "http_<n>" | "redirect" | "unreachable" | "credentials" | ...
    result_id: str = ""


def dryrun() -> bool:
    return os.environ.get("JARVIS_WHATSAPP_DRYRUN", "").strip().lower() in ("1", "true", "yes")


def confirm_strict() -> bool:
    """Opt-in, like the house and Google: a spoken "yes" must be two
    deliberate words. After reading other people's messages it is two words
    whether or not this is set."""
    return os.environ.get("JARVIS_WHATSAPP_CONFIRM_STRICT", "").strip().lower() in ("1", "true", "yes")


def perform(action: Action) -> Outcome:
    """Do it (or, in dry-run, do nothing). Never raises, never returns a body.
    Re-reads the policy and re-checks the action: a recipient that is not on
    the user's list today is never sent to, whatever got here."""
    if dryrun():
        return Outcome(True, "dryrun")
    try:
        policy = load_policy()
        again = check(policy, action.asked or action.contact, action.text, action.tainted)
        if again.jid != action.jid:
            return Outcome(False, "not_permitted")
        receipt = _request({"number": wp.jid_digits(again.jid), "text": again.text,
                            "linkPreview": False})
    except (Disabled, Refused):
        return Outcome(False, "not_permitted")
    except WhatsAppActionError as e:
        return Outcome(False, str(e)[:120])
    rid = ""
    if isinstance(receipt, dict):
        key = receipt.get("key")
        rid = str(key.get("id") if isinstance(key, dict) else "")[:40]
    return Outcome(True, "ok", re.sub(r"[^A-Za-z0-9_\-]", "?", rid))


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def _audit_path():
    return data_paths.whatsapp_actions_log_path()


def audit_ready() -> bool:
    """Can a line be appended? Checked BEFORE acting: no log, no message."""
    try:
        _append_line("")
        return True
    except OSError:
        return False


def _append_line(line: str) -> None:
    path = _audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size > LOG_MAX_BYTES:
            os.replace(path, str(path) + ".1")
    except FileNotFoundError:
        pass
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        if line:
            os.write(fd, (line + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def short_jid(jid: str) -> str:
    """Enough to recognise a number, not enough to dial it."""
    digits = wp.jid_digits(jid) if isinstance(jid, str) else ""
    if not digits.isdigit() or len(digits) < 6:
        return "?"
    return f"{digits[:3]}…{digits[-2:]}"


def audit(action: Optional[Action], *, confirmed_by: str, result: str,
          contact: str = "", chars: int = 0, tainted: bool = False) -> None:
    """One JSONL line: who it was for, how long it was, who confirmed it and
    how it ended. Never the text, never a token."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": "send",
        "to": _CTRL_RE.sub("?", action.contact if action else contact)[:40],
        "jid": short_jid(action.jid) if action else "",
        "chars": len(action.text) if action else max(0, int(chars)),
        "after_reading": bool(action.tainted if action else tainted),
        "confirmed_by": confirmed_by if confirmed_by in ("voice", "panel", "none") else "none",
        "result": _CTRL_RE.sub("?", str(result))[:160],
        "dryrun": dryrun(),
    }
    try:
        _append_line(json.dumps(entry, ensure_ascii=True, separators=(",", ":")))
    except OSError:
        pass          # the caller checked audit_ready() before acting


# ---------------------------------------------------------------------------
# Frequency
# ---------------------------------------------------------------------------

class Limits:
    """How often messages may be proposed and sent. One per server."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._proposals = bridge.RateLimiter(PROPOSALS_PER_5_MIN, 300.0, clock)
        self._acts: Optional[bridge.RateLimiter] = None
        self._acts_limit = 0

    def may_propose(self) -> bool:
        return self._proposals.allow()

    def may_act(self, per_minute: int) -> bool:
        if self._acts is None or per_minute != self._acts_limit:
            self._acts = bridge.RateLimiter(per_minute, 60.0, self._clock)
            self._acts_limit = per_minute
        return self._acts.allow()

"""What the user decided about his PERSONAL WhatsApp: the credentials file
and the policy file, read the same strict way by the two modules that touch
WhatsApp (`whatsapp_readonly_mcp.py` reads, `whatsapp_actions.py` sends).

Standard library only, because the reader runs as its own stdio process.

Two files, both the user's own, both outside the repository:

  * The credentials: a JSON object `{"url", "instance", "apikey"}` named by
    `EVOLUTION_TOKEN_FILE` (reading) or `EVOLUTION_WRITE_TOKEN_FILE` (sending;
    it falls back to the reading one, so one file is enough). Refused if it is
    not a regular file of the user's, or readable by group or others. The URL
    must be plain `https://host[:port]`: no credentials in it, no path, no
    query. The instance name is a short identifier and ONLY ever comes from
    this file, never from a tool argument: that is what keeps a shared
    Evolution server's other instances out of reach.
  * The policy: `data/jarvis/whatsapp-actions.json`. It is read by BOTH sides,
    but it means different things to each: for the reader a missing file just
    means the defaults (no groups, nothing hidden); for the sender a missing
    file, or `enabled` not literally `true`, means no sending at all.

Fail closed where it matters: a policy file that exists but cannot be trusted
(not the user's, writable by others, not JSON, a hide-list entry that cannot be
understood) raises `PolicyInvalid`, and the callers then show and send nothing
rather than guess.
"""

from __future__ import annotations

import json
import os
import re
import stat
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

POLICY_MAX_BYTES = 64 * 1024
CREDENTIALS_MAX_BYTES = 16 * 1024

# An individual chat. `@lid` is how newer WhatsApp versions name a contact
# whose number they hide; it is readable but never a destination, because
# sending is done by number.
DM_JID_RE = re.compile(r"[0-9]{5,20}@s\.whatsapp\.net")
LID_JID_RE = re.compile(r"[0-9]{5,25}@lid")
GROUP_JID_RE = re.compile(r"[0-9]{5,25}(?:-[0-9]{5,25})?@g\.us")
# The ONLY kind of JID a message may be sent to: an individual's number.
SEND_JID_RE = re.compile(r"[0-9]{6,20}@s\.whatsapp\.net")
INSTANCE_RE = re.compile(r"[A-Za-z0-9_\-]{1,64}")
_NAME_RE = re.compile(r"[\w .'\-]{1,40}", re.UNICODE)
_EXCLUDE_NAME_RE = re.compile(r"[\w .'\-]{1,60}", re.UNICODE)
_PHONE_RE = re.compile(r"\+?[0-9][0-9 ]{4,24}")

DEFAULT_PER_MINUTE = 2
MAX_PER_MINUTE = 6
DEFAULT_MAX_CHARS = 500
HARD_MAX_CHARS = 1000
MAX_LIST = 200


class PolicyInvalid(Exception):
    """The policy file exists but cannot be trusted. Safe to say aloud."""


class CredentialsError(Exception):
    """The credentials cannot be used. The message names the variable and the
    problem, never a path, a URL or a value."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def jid_digits(jid: str) -> str:
    return jid.split("@", 1)[0]


def jid_kind(jid) -> str:
    """'dm', 'lid', 'group' or '' for anything else (broadcast lists, status,
    newsletters, a malformed value)."""
    if not isinstance(jid, str) or not jid.isascii():
        return ""
    if DM_JID_RE.fullmatch(jid):
        return "dm"
    if LID_JID_RE.fullmatch(jid):
        return "lid"
    if GROUP_JID_RE.fullmatch(jid):
        return "group"
    return ""


def norm_name(value) -> str:
    return " ".join(str(value).casefold().split())


def _file_problem(path: str, what: str, mask: int) -> Optional[str]:
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return f"{what} cannot be read."
    if not stat.S_ISREG(st.st_mode):
        return f"{what} is not a regular file."
    if st.st_uid != os.getuid():
        return f"{what} is not owned by the user running JARVIS."
    if st.st_mode & mask:
        return (f"{what} must not be readable by group or others (chmod 600)."
                if mask == 0o077 else
                f"{what} must not be writable by group or others (chmod 600).")
    return None


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Credentials:
    base: str            # "https://host[:port]", nothing after it
    instance: str
    apikey: str = field(repr=False)


def credentials_path(write: bool = False) -> str:
    """The credentials file named by the environment. `write=True` prefers
    `EVOLUTION_WRITE_TOKEN_FILE` and falls back to `EVOLUTION_TOKEN_FILE`."""
    if write:
        path = os.environ.get("EVOLUTION_WRITE_TOKEN_FILE", "").strip()
        if path:
            return path
    return os.environ.get("EVOLUTION_TOKEN_FILE", "").strip()


def load_credentials(write: bool = False) -> Credentials:
    var = "EVOLUTION_WRITE_TOKEN_FILE" if (
        write and os.environ.get("EVOLUTION_WRITE_TOKEN_FILE", "").strip()) else "EVOLUTION_TOKEN_FILE"
    path = credentials_path(write)
    if not path:
        raise CredentialsError("EVOLUTION_TOKEN_FILE is not set: there are no WhatsApp credentials.")
    problem = _file_problem(path, var, 0o077)
    if problem == "missing":
        raise CredentialsError(f"{var} points to a file that does not exist.")
    if problem:
        raise CredentialsError(problem)
    try:
        with open(path, "rb") as fh:
            raw = fh.read(CREDENTIALS_MAX_BYTES + 1)
    except OSError:
        raise CredentialsError(f"{var} cannot be read.") from None
    if len(raw) > CREDENTIALS_MAX_BYTES:
        raise CredentialsError(f"{var} is too large.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise CredentialsError(f"{var} is not valid JSON.") from None
    if not isinstance(data, dict):
        raise CredentialsError(f"{var} must be a JSON object.")
    url, instance, apikey = data.get("url"), data.get("instance"), data.get("apikey")
    if not (isinstance(url, str) and isinstance(instance, str) and isinstance(apikey, str)
            and url and instance and apikey):
        raise CredentialsError(f"{var} needs url, instance and apikey.")
    if not INSTANCE_RE.fullmatch(instance):
        raise CredentialsError(f"{var} has an instance name that is not valid.")
    if not apikey.isascii() or len(apikey) > 512 or any(c in apikey for c in "\r\n\x00"):
        raise CredentialsError(f"{var} has an apikey that is not valid.")
    return Credentials(_clean_base(url, var), instance, apikey)


def _clean_base(url: str, var: str) -> str:
    try:
        parts = urllib.parse.urlsplit(url.strip())
        host = parts.hostname
        port = parts.port
    except ValueError:
        raise CredentialsError(f"{var} has a url that is not valid.") from None
    if (parts.scheme != "https" or not host or parts.username or parts.password
            or parts.path not in ("", "/") or parts.query or parts.fragment
            or not host.isascii() or not re.fullmatch(r"[A-Za-z0-9.\-]{1,253}", host)):
        raise CredentialsError(f"{var} must hold a plain https://host url.")
    return f"https://{host}" + (f":{port}" if port else "")


# ---------------------------------------------------------------------------
# The policy file
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Policy:
    present: bool = False
    enabled: bool = False
    panel_confirm: bool = False
    contacts: dict = field(default_factory=dict)        # normalised name -> (display, jid)
    self_jid: str = ""
    read_groups: frozenset = frozenset()
    exclude_jids: frozenset = frozenset()
    exclude_digits: frozenset = frozenset()
    exclude_names: tuple = ()
    max_per_minute: int = DEFAULT_PER_MINUTE
    max_chars: int = DEFAULT_MAX_CHARS
    problems: tuple = ()

    def group_readable(self, jid: str) -> bool:
        return jid in self.read_groups

    def is_excluded(self, jid: str, name: str = "") -> bool:
        """Hidden by the user's own `exclude` list: by exact JID, by phone
        number (the digits of the JID end with the listed ones) or by a name
        contained in the chat's name. Over-hiding is the safe direction."""
        if jid in self.exclude_jids:
            return True
        digits = jid_digits(jid)
        for wanted in self.exclude_digits:
            if digits == wanted or (len(wanted) >= 9 and digits.endswith(wanted)):
                return True
        folded = norm_name(name)
        return bool(folded) and any(n in folded for n in self.exclude_names)


def policy_path() -> str:
    env = os.environ.get("WHATSAPP_POLICY_FILE", "").strip()
    if env:
        return env
    import data_paths
    return str(data_paths.whatsapp_actions_path())


def load(path: Optional[str] = None) -> Policy:
    """The policy, the defaults when there is no file, or `PolicyInvalid`."""
    path = str(path or policy_path())
    problem = _file_problem(path, "whatsapp-actions.json", 0o022)
    if problem == "missing":
        return Policy()
    if problem:
        raise PolicyInvalid(problem)
    try:
        with open(path, "rb") as fh:
            raw = fh.read(POLICY_MAX_BYTES + 1)
    except OSError:
        raise PolicyInvalid("whatsapp-actions.json cannot be read.") from None
    if len(raw) > POLICY_MAX_BYTES:
        raise PolicyInvalid("whatsapp-actions.json is too large.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise PolicyInvalid("whatsapp-actions.json is not valid JSON.") from None
    if not isinstance(data, dict):
        raise PolicyInvalid("whatsapp-actions.json must be a JSON object.")
    return _parse(data)


def _parse(data: dict) -> Policy:
    problems: list[str] = []

    contacts: dict = {}
    raw_contacts = data.get("contacts", {})
    if not isinstance(raw_contacts, dict):
        problems.append("contacts: not an object; ignored.")
        raw_contacts = {}
    for name, jid in list(raw_contacts.items())[:MAX_LIST]:
        if (isinstance(name, str) and _NAME_RE.fullmatch(name.strip() or "\n")
                and isinstance(jid, str) and SEND_JID_RE.fullmatch(jid)):
            contacts[norm_name(name)] = (" ".join(name.split()), jid)
        else:
            problems.append("contacts: an invalid entry was ignored.")

    self_jid = data.get("self", "")
    if self_jid not in ("", None):
        if not (isinstance(self_jid, str) and SEND_JID_RE.fullmatch(self_jid)):
            problems.append("self: not a valid individual JID; ignored.")
            self_jid = ""
    else:
        self_jid = ""

    groups = set()
    listed = data.get("read_groups", [])
    if not isinstance(listed, list):
        problems.append("read_groups: not a list; no group will be read.")
        listed = []
    for jid in listed[:MAX_LIST]:
        if isinstance(jid, str) and GROUP_JID_RE.fullmatch(jid):
            groups.add(jid)
        else:
            problems.append("read_groups: an invalid entry was ignored.")

    exclude_jids, exclude_digits, exclude_names = set(), set(), []
    listed = data.get("exclude", [])
    if not isinstance(listed, list) or len(listed) > MAX_LIST:
        raise PolicyInvalid("whatsapp-actions.json: exclude must be a list.")
    for item in listed:
        # An entry that cannot be understood is not skipped: it might have been
        # meant to hide something, so the whole file is refused instead.
        if not isinstance(item, str):
            raise PolicyInvalid("whatsapp-actions.json: an exclude entry is not valid.")
        item = item.strip()
        if jid_kind(item):
            exclude_jids.add(item)
        elif _PHONE_RE.fullmatch(item):
            exclude_digits.add(re.sub(r"[^0-9]", "", item))
        elif _EXCLUDE_NAME_RE.fullmatch(item or "\n"):
            exclude_names.append(norm_name(item))
        else:
            raise PolicyInvalid("whatsapp-actions.json: an exclude entry is not valid.")

    limits = data.get("limits") if isinstance(data.get("limits"), dict) else {}
    per_min = limits.get("max_per_minute", DEFAULT_PER_MINUTE)
    if isinstance(per_min, bool) or not isinstance(per_min, int) or not 1 <= per_min <= MAX_PER_MINUTE:
        problems.append("limits.max_per_minute is not valid; using the default.")
        per_min = DEFAULT_PER_MINUTE
    max_chars = limits.get("max_chars", DEFAULT_MAX_CHARS)
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 1 <= max_chars <= HARD_MAX_CHARS:
        problems.append("limits.max_chars is not valid; using the default.")
        max_chars = DEFAULT_MAX_CHARS

    return Policy(
        present=True, enabled=data.get("enabled") is True,
        panel_confirm=data.get("panel_confirm") is True,
        contacts=contacts, self_jid=self_jid or "",
        read_groups=frozenset(groups), exclude_jids=frozenset(exclude_jids),
        exclude_digits=frozenset(exclude_digits), exclude_names=tuple(exclude_names),
        max_per_minute=per_min, max_chars=max_chars, problems=tuple(problems))

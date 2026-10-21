"""Writing to the user's PERSONAL Google account, the same conservative way
`home_actions.py` controls the house.

Nothing here is on by default. With no `google-actions.json` (the user's own
allow-list, `data_paths.google_actions_path()`) there are no actions: not
"all refused by a prompt", but no code path that reaches the network.

Scope, on purpose and nothing more (see `docs/google-escritura.md`):

  * Calendar: create an event, update one JARVIS can identify by id (title,
    time, location, description). No attendees, no `sendUpdates` other than
    `none`, no deletion, no sharing.
  * Tasks: create, update title/notes/due, mark complete. No deletion.
  * Notes: a Google Doc created by JARVIS inside a Drive folder named
    "JARVIS Notas" (created the first time, under the `drive.file` scope, so
    it only ever sees files it created itself). Can be appended to. Never
    shared, never a public link, never a file JARVIS did not create.
  * Nothing else. Gmail is untouched in this module.

What this module guarantees, the same way `home_actions.py` does:

  * Only a CLOSED list of hosts and paths, built by this code from validated
    parameters, is ever requested (`_PATHS`). Nothing the brain writes
    becomes a path or a raw request body.
  * A proposal containing `attendees`, a share/permission field, or any
    string that looks like a third party's email address is refused before
    any network call.
  * Its own token (`GOOGLE_WRITE_TOKEN_FILE`, a 0600 file). It is never the
    read token (`GOOGLE_TOKEN_FILE`): if both hold the same value, nothing is
    sent.
  * The user decides, not a model: this module only describes and performs an
    `Action`. The proposal, the spoken question and the "yes" live in
    `server.py` and `bridge.py`.
  * Every attempt is one JSONL line in `google-actions.log`, without a token
    or a full text body. If that log cannot be written, nothing is done.
  * A frequency limit and a dry-run mode (`JARVIS_GOOGLE_DRYRUN=1`).

What it does NOT protect against (see docs/google-escritura.md): a process of
the same user with Bash can read the 0600 token file, audio ambiente puede
decir "sí", and an OAuth app in "Testing" mode may expire its refresh token
after 7 days (an unconfirmed hypothesis).
"""

from __future__ import annotations

import hmac
import json
import re
import stat
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import bridge
import data_paths
import i18n
import lang

TIMEOUT_SEC = 10.0
POLICY_MAX_BYTES = 64 * 1024
LOG_MAX_BYTES = 1024 * 1024
RESPONSE_MAX_BYTES = 64 * 1024

DEFAULT_PER_MINUTE = 3
MAX_PER_MINUTE = 10
PROPOSALS_PER_5_MIN = 6

TITLE_MAX_HARD = 300
TEXT_MAX_HARD = 4000
DEFAULT_TEXT_MAX = 1500
DEFAULT_LEAD_DAYS = 365
LEAD_DAYS_HARD_MAX = 3650

NOTES_FOLDER_NAME = "JARVIS Notas"

CALENDAR_HOST = "www.googleapis.com"
TASKS_HOST = "tasks.googleapis.com"
DOCS_HOST = "docs.googleapis.com"

_ID_RE = re.compile(r"[A-Za-z0-9_@.\-]{1,512}")
_CAL_ID_RE = re.compile(r"[A-Za-z0-9_@.\-]{1,255}")
_NAME_BAD_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_DATETIME_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})?)?")

ACTIONS = frozenset({
    "calendar_create", "calendar_update",
    "task_create", "task_update", "task_complete",
    "note_create", "note_append",
})

# Fields each action may carry, and whether each is required.
_FIELDS: dict[str, dict[str, bool]] = {
    "calendar_create": {"calendar_id": False, "title": True, "start": True,
                        "end": False, "duration_minutes": False,
                        "location": False, "description": False},
    "calendar_update": {"calendar_id": False, "event_id": True, "title": False,
                        "start": False, "end": False, "location": False,
                        "description": False},
    "task_create": {"task_list": False, "title": True, "notes": False, "due": False},
    "task_update": {"task_list": False, "task_id": True, "title": False,
                    "notes": False, "due": False},
    "task_complete": {"task_list": False, "task_id": True},
    "note_create": {"title": True, "body": False},
    "note_append": {"doc_id": True, "text": True},
}

# Keys forbidden in ANY payload, whatever the action: no attendee, no sharing,
# no permission change reaches this module even if the brain tries.
_FORBIDDEN_KEYS = frozenset({
    "attendees", "sendUpdates", "send_updates", "permissions", "role",
    "type", "anyoneWithLink", "shared", "writersCanShare", "visibility",
})


class Refused(Exception):
    """A request the policy will not carry out. Safe for the brain to read."""


class Disabled(Exception):
    """Google write actions are not in use: no file, switched off, or a file
    that cannot be trusted."""


class GoogleActionError(Exception):
    """A failure whose message is safe to log (no URL, no token, no body)."""


# ---------------------------------------------------------------------------
# The allow-list
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Action:
    """One validated action: everything needed to describe it, log it and
    perform it, nothing from outside unchecked."""
    kind: str                      # one of ACTIONS
    fields: tuple = ()              # sorted ((name, value), ...), values are str/int
    name: str = ""                  # short label for the spoken sentence

    def as_dict(self) -> dict:
        return {"kind": self.kind, "fields": dict(self.fields)}

    def field(self, key: str, default=None):
        return dict(self.fields).get(key, default)


@dataclass
class Policy:
    calendars: frozenset
    task_lists: frozenset
    max_per_minute: int = DEFAULT_PER_MINUTE
    max_text_chars: int = DEFAULT_TEXT_MAX
    max_lead_days: int = DEFAULT_LEAD_DAYS
    panel_confirm: bool = False
    problems: list = field(default_factory=list)

    def check(self, kind, raw: dict) -> Action:
        """The validated Action, or `Refused`. Sends nothing."""
        if kind not in ACTIONS:
            raise Refused(f"{_safe_key(kind)} is not an action JARVIS can propose.")
        if not isinstance(raw, dict):
            raise Refused("The parameters must be an object.")
        _check_forbidden(raw)
        wanted = _FIELDS[kind]
        extra = sorted(k for k in raw if k not in wanted)
        if extra:
            raise Refused("Parameters not allowed for this action: "
                          + ", ".join(_safe_key(k) for k in extra) + ".")
        out: dict[str, object] = {}
        for key, required in wanted.items():
            value = raw.get(key)
            if value in (None, ""):
                if required:
                    raise Refused(f"{kind} needs {key}.")
                continue
            out[key] = self._one(kind, key, value)
        self._cross_check(kind, out)
        name = _label(kind, out)
        return Action(kind, tuple(sorted(out.items())), name)

    def _one(self, kind: str, key: str, value):
        if key in ("calendar_id",):
            cal = _clean_id(value, _CAL_ID_RE, "calendar_id")
            if cal not in self.calendars:
                raise Refused(f"{cal} is not in the user's list of allowed calendars.")
            return cal
        if key == "task_list":
            tl = _clean_id(value, _ID_RE, "task_list")
            if tl not in self.task_lists:
                raise Refused(f"{tl} is not in the user's list of allowed task lists.")
            return tl
        if key in ("event_id", "task_id", "doc_id"):
            return _clean_id(value, _ID_RE, key)
        if key in ("start", "end", "due"):
            return _clean_datetime(value, key, self.max_lead_days)
        if key == "duration_minutes":
            return _clean_minutes(value)
        if key in ("title", "location", "notes", "description", "body", "text"):
            limit = TITLE_MAX_HARD if key in ("title",) else self.max_text_chars
            return _clean_text(value, key, limit)
        raise Refused("That parameter is not allowed.")       # unreachable

    def _cross_check(self, kind: str, out: dict) -> None:
        if kind == "calendar_create" and "end" not in out and "duration_minutes" not in out:
            raise Refused("calendar_create needs end or duration_minutes.")
        if kind == "calendar_update" and len(out) <= (2 if "calendar_id" in out else 1):
            raise Refused("calendar_update needs at least one field to change.")
        if kind == "task_update" and len(out) <= (2 if "task_list" in out else 1):
            raise Refused("task_update needs at least one field to change.")


def _check_forbidden(raw: dict) -> None:
    bad = sorted(k for k in raw if k in _FORBIDDEN_KEYS)
    if bad:
        raise Refused("Not permitted on this connection: " +
                      ", ".join(_safe_key(k) for k in bad) + ".")
    for value in raw.values():
        if isinstance(value, str) and _EMAIL_RE.search(value):
            raise Refused("A proposal that contains an email address is refused: "
                          "no attendee, no sharing, ever.")


def _safe_key(key) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "?", str(key))[:40]


def _clean_id(value, pattern: re.Pattern, label: str) -> str:
    if not isinstance(value, str) or not value.isascii() or not pattern.fullmatch(value):
        raise Refused(f"That is not a valid {label}.")
    return value


def _clean_text(value, key: str, limit: int) -> str:
    if not isinstance(value, str):
        raise Refused(f"{key} must be text.")
    text = _NAME_BAD_RE.sub(" ", value).strip()
    if not text:
        raise Refused(f"{key} cannot be empty.")
    if len(text) > limit:
        raise Refused(f"{key} is too long (max {limit} characters).")
    return text


def _clean_datetime(value, key: str, max_lead_days: int) -> str:
    if not isinstance(value, str) or not _DATETIME_RE.fullmatch(value.strip()):
        raise Refused(f"{key} must be an ISO date or date-time "
                      "(e.g. 2026-10-21 or 2026-10-21T18:00:00+02:00).")
    value = value.strip()
    try:
        probe = value if "T" in value else value + "T00:00:00"
        probe = probe[:19]
        when = datetime.strptime(probe, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        raise Refused(f"{key} is not a valid date.") from None
    lead = max(1, min(max_lead_days, LEAD_DAYS_HARD_MAX))
    if when > datetime.now(timezone.utc) + timedelta(days=lead):
        raise Refused(f"{key} is further ahead than the {lead}-day limit the user set.")
    return value


def _clean_minutes(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Refused("duration_minutes must be a number.")
    minutes = int(value)
    if not 1 <= minutes <= 24 * 60 * 14:
        raise Refused("duration_minutes must be between 1 and 20160 (two weeks).")
    return minutes


def _label(kind: str, out: dict) -> str:
    if "title" in out:
        return out["title"][:60]
    if "doc_id" in out:
        return "la nota"
    if "event_id" in out:
        return "el evento"
    if "task_id" in out:
        return "la tarea"
    return "eso"


# ---------------------------------------------------------------------------
# Policy file
# ---------------------------------------------------------------------------

def _file_problem(path: str, what: str) -> Optional[str]:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return f"{what} cannot be read."
    if not stat.S_ISREG(st.st_mode):
        return f"{what} is not a regular file."
    if st.st_uid != os.getuid():
        return f"{what} is not owned by the user running JARVIS."
    if st.st_mode & 0o022:
        return f"{what} must not be writable by group or others (chmod 600)."
    return None


def load_policy(path=None) -> Policy:
    """The user's allow-list, or `Disabled` (never a half-trusted policy)."""
    path = str(path or data_paths.google_actions_path())
    problem = _file_problem(path, "google-actions.json")
    if problem == "missing":
        raise Disabled("There is no google-actions.json: Google writing is off.")
    if problem:
        raise Disabled(problem)
    try:
        with open(path, "rb") as fh:
            raw = fh.read(POLICY_MAX_BYTES + 1)
    except OSError:
        raise Disabled("google-actions.json cannot be read.") from None
    if len(raw) > POLICY_MAX_BYTES:
        raise Disabled("google-actions.json is too large.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise Disabled("google-actions.json is not valid JSON.") from None
    if not isinstance(data, dict):
        raise Disabled("google-actions.json must be a JSON object.")
    if data.get("enabled") is not True:
        raise Disabled("google-actions.json has not been switched on (enabled: true).")
    return _parse_policy(data)


def _parse_policy(data: dict) -> Policy:
    problems: list[str] = []
    cals = data.get("calendars", ["primary"])
    if not isinstance(cals, list) or not cals:
        problems.append("calendars: not a list; using [\"primary\"].")
        cals = ["primary"]
    calendars = set()
    for c in cals:
        if isinstance(c, str) and c.isascii() and _CAL_ID_RE.fullmatch(c):
            calendars.add(c)
        else:
            problems.append("calendars: an invalid id was ignored.")
    if not calendars:
        calendars = {"primary"}

    lists = data.get("task_lists", ["@default"])
    if not isinstance(lists, list) or not lists:
        problems.append("task_lists: not a list; using [\"@default\"].")
        lists = ["@default"]
    task_lists = set()
    for tl in lists:
        if isinstance(tl, str) and tl.isascii() and _ID_RE.fullmatch(tl):
            task_lists.add(tl)
        else:
            problems.append("task_lists: an invalid id was ignored.")
    if not task_lists:
        task_lists = {"@default"}

    limits = data.get("limits") if isinstance(data.get("limits"), dict) else {}
    per_min = limits.get("max_per_minute", DEFAULT_PER_MINUTE)
    if isinstance(per_min, bool) or not isinstance(per_min, int) or not 1 <= per_min <= MAX_PER_MINUTE:
        problems.append("limits.max_per_minute is not valid; using the default.")
        per_min = DEFAULT_PER_MINUTE

    max_text = limits.get("max_text_chars", DEFAULT_TEXT_MAX)
    if isinstance(max_text, bool) or not isinstance(max_text, int) or not 1 <= max_text <= TEXT_MAX_HARD:
        problems.append("limits.max_text_chars is not valid; using the default.")
        max_text = DEFAULT_TEXT_MAX

    lead = limits.get("max_lead_days", DEFAULT_LEAD_DAYS)
    if isinstance(lead, bool) or not isinstance(lead, int) or not 1 <= lead <= LEAD_DAYS_HARD_MAX:
        problems.append("limits.max_lead_days is not valid; using the default.")
        lead = DEFAULT_LEAD_DAYS

    return Policy(calendars=frozenset(calendars), task_lists=frozenset(task_lists),
                  max_per_minute=per_min, max_text_chars=max_text, max_lead_days=lead,
                  panel_confirm=data.get("panel_confirm") is True, problems=problems)


# ---------------------------------------------------------------------------
# What is said
# ---------------------------------------------------------------------------

def question(action: Action) -> str:
    """The whole action, as the sentence JARVIS says before anything happens.
    Built only from the validated Action: never raw text from the brain."""
    k = action.kind
    if k == "calendar_create":
        english = "Shall I create the event {name} in your calendar, sir?"
    elif k == "calendar_update":
        english = "Shall I update the event {name} in your calendar, sir?"
    elif k == "task_create":
        english = "Shall I add {name} to your tasks, sir?"
    elif k == "task_update":
        english = "Shall I update the task {name}, sir?"
    elif k == "task_complete":
        english = "Shall I mark the task {name} as complete, sir?"
    elif k == "note_create":
        english = "Shall I create a note titled {name} in your Drive, sir?"
    elif k == "note_append":
        english = "Shall I add that text to {name}, sir?"
    else:
        english = "Shall I do that in Google, sir?"            # unreachable
    return i18n.t(english.format(name=action.name))


# ---------------------------------------------------------------------------
# Credentials and the closed set of requests
# ---------------------------------------------------------------------------

def _read_secret_file(path: str, label: str) -> str:
    try:
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode):
            raise GoogleActionError(f"{label} is not a regular file.")
        if st.st_mode & 0o077:
            raise GoogleActionError(f"{label} must not be readable by group or others (chmod 600).")
        with open(path, encoding="utf-8") as fh:
            return fh.read(8192)
    except OSError:
        raise GoogleActionError(f"{label} cannot be read.") from None


def _token_file() -> str:
    path = os.environ.get("GOOGLE_WRITE_TOKEN_FILE", "").strip()
    if not path:
        raise GoogleActionError("GOOGLE_WRITE_TOKEN_FILE is not set.")
    return path


def _read_token_data() -> tuple[str, dict]:
    path = _token_file()
    raw = _read_secret_file(path, "GOOGLE_WRITE_TOKEN_FILE")
    try:
        data = json.loads(raw)
    except ValueError:
        raise GoogleActionError("GOOGLE_WRITE_TOKEN_FILE is not valid JSON.") from None
    if not isinstance(data, dict) or not data.get("access_token"):
        raise GoogleActionError("GOOGLE_WRITE_TOKEN_FILE has no access_token.")
    _refuse_the_read_token(data.get("access_token", ""), path)
    return path, data


def _refuse_the_read_token(write_access_token: str, write_path: str) -> None:
    """The write credential's file and access token must differ from the
    read-only connection's, the same defence `home_actions.py` applies."""
    read_path = os.environ.get("GOOGLE_TOKEN_FILE", "").strip()
    if not read_path:
        return
    try:
        if os.path.samefile(read_path, write_path):
            raise GoogleActionError("GOOGLE_WRITE_TOKEN_FILE must not be the read token's file.")
    except OSError:
        pass
    try:
        read_raw = _read_secret_file(read_path, "GOOGLE_TOKEN_FILE")
        read_data = json.loads(read_raw)
        read_token = read_data.get("access_token", "") if isinstance(read_data, dict) else ""
    except (GoogleActionError, ValueError):
        return
    if read_token and write_access_token and hmac.compare_digest(
            read_token.encode("utf-8"), write_access_token.encode("utf-8")):
        raise GoogleActionError("The write token must not be the read-only token.")


def _write_token_data(path: str, data: dict) -> None:
    import tempfile
    directory = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".google_write_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise GoogleActionError("GOOGLE_WRITE_TOKEN_FILE could not be updated.") from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def _open(req: urllib.request.Request, timeout: float):
    """The network seam; tests replace it."""
    return _OPENER.open(req, timeout=timeout)  # noqa: S310 (host/path checked by callers)


_TOKEN_URI = "https://oauth2.googleapis.com/token"  # gitleaks:allow (an endpoint, not a secret)
_TOKEN_HOST = "oauth2.googleapis.com"
_TOKEN_PATH = "/token"


def _refresh_access_token(data: dict) -> dict:
    refresh_token = data.get("refresh_token")
    client_id = data.get("client_id")
    client_secret = data.get("client_secret")
    if not (refresh_token and client_id and client_secret):
        raise GoogleActionError("GOOGLE_WRITE_TOKEN_FILE is missing fields needed to renew access.")
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token", "refresh_token": refresh_token,
        "client_id": client_id, "client_secret": client_secret}).encode("ascii")
    req = urllib.request.Request(_TOKEN_URI, data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    parsed = urllib.parse.urlparse(req.full_url)
    if parsed.hostname != _TOKEN_HOST or parsed.path != _TOKEN_PATH:
        raise GoogleActionError("Refusing to renew access: unexpected token endpoint.")
    try:
        with _open(req, TIMEOUT_SEC) as resp:
            raw = resp.read(8192)
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read(8192))
        except (ValueError, OSError):
            payload = {}
        if payload.get("error") == "invalid_grant":
            raise GoogleActionError("Google refused to renew write access: "
                                    "reauthorise with scripts/google-auth.py --write.") from None
        raise GoogleActionError("Google refused to renew write access (HTTP %d)." % e.code) from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise GoogleActionError("Google's authorisation server is unreachable.") from None
    try:
        payload = json.loads(raw)
    except ValueError:
        raise GoogleActionError("Google sent something unreadable while renewing access.") from None
    new_access = payload.get("access_token")
    if not new_access:
        raise GoogleActionError("Google refused to renew write access: "
                                "reauthorise with scripts/google-auth.py --write.")
    out = dict(data)
    out["access_token"] = new_access
    return out


def _request(method: str, host: str, path: str, *, query: dict | None = None,
            body: dict | None = None, _retry: bool = True):
    """The ONE function that reaches the Google write APIs. `host` and `path`
    must already be one of the closed set a caller below builds; nothing here
    accepts a path built from untrusted text."""
    token_path, data = _read_token_data()
    full = f"https://{host}{path}"
    if query:
        full += "?" + urllib.parse.urlencode(query)
    headers = {"Authorization": f"Bearer {data['access_token']}", "Accept": "application/json"}
    payload = None
    if body is not None:
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(full, data=payload, method=method, headers=headers)
    try:
        with _open(req, TIMEOUT_SEC) as resp:
            raw = resp.read(RESPONSE_MAX_BYTES)
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as e:
        if 300 <= e.code <= 399:
            raise GoogleActionError("redirect") from None
        if e.code in (401, 403) and _retry:
            refreshed = _refresh_access_token(data)
            _write_token_data(token_path, refreshed)
            return _request(method, host, path, query=query, body=body, _retry=False)
        raise GoogleActionError(f"http_{e.code}") from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise GoogleActionError("unreachable") from None
    if not (200 <= int(status) < 300):
        raise GoogleActionError(f"http_{int(status)}")
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        raise GoogleActionError("unreadable") from None


# --- the closed set of calls, one per kind ----------------------------------

def _calendar_create(f: dict) -> dict:
    cal = urllib.parse.quote(f.get("calendar_id", "primary"), safe="@.")
    start = f["start"]
    end = f.get("end") or _plus_minutes(start, f.get("duration_minutes", 60))
    body = {"summary": f["title"],
           "start": _time_field(start), "end": _time_field(end)}
    if "location" in f:
        body["location"] = f["location"]
    if "description" in f:
        body["description"] = f["description"]
    return _request("POST", CALENDAR_HOST, f"/calendar/v3/calendars/{cal}/events",
                    query={"sendUpdates": "none"}, body=body)


def _calendar_update(f: dict) -> dict:
    cal = urllib.parse.quote(f.get("calendar_id", "primary"), safe="@.")
    event = urllib.parse.quote(f["event_id"], safe="")
    body: dict = {}
    if "title" in f:
        body["summary"] = f["title"]
    if "start" in f:
        body["start"] = _time_field(f["start"])
    if "end" in f:
        body["end"] = _time_field(f["end"])
    if "location" in f:
        body["location"] = f["location"]
    if "description" in f:
        body["description"] = f["description"]
    return _request("PATCH", CALENDAR_HOST, f"/calendar/v3/calendars/{cal}/events/{event}",
                    query={"sendUpdates": "none"}, body=body)


def _time_field(value: str) -> dict:
    return {"dateTime": value} if "T" in value else {"date": value}


def _plus_minutes(start: str, minutes) -> str:
    minutes = int(minutes) if minutes else 60
    probe = start if "T" in start else start + "T00:00:00"
    base = probe[:19]
    when = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S")
    out = (when + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")
    suffix = start[19:] if "T" in start else ""
    return out + suffix if "T" in start else out.split("T")[0]


def _task_create(f: dict) -> dict:
    tl = urllib.parse.quote(f.get("task_list", "@default"), safe="@")
    body = {"title": f["title"]}
    if "notes" in f:
        body["notes"] = f["notes"]
    if "due" in f:
        body["due"] = f["due"]
    return _request("POST", TASKS_HOST, f"/tasks/v1/lists/{tl}/tasks", body=body)


def _task_update(f: dict) -> dict:
    tl = urllib.parse.quote(f.get("task_list", "@default"), safe="@")
    task = urllib.parse.quote(f["task_id"], safe="")
    body: dict = {}
    if "title" in f:
        body["title"] = f["title"]
    if "notes" in f:
        body["notes"] = f["notes"]
    if "due" in f:
        body["due"] = f["due"]
    return _request("PATCH", TASKS_HOST, f"/tasks/v1/lists/{tl}/tasks/{task}", body=body)


def _task_complete(f: dict) -> dict:
    tl = urllib.parse.quote(f.get("task_list", "@default"), safe="@")
    task = urllib.parse.quote(f["task_id"], safe="")
    return _request("PATCH", TASKS_HOST, f"/tasks/v1/lists/{tl}/tasks/{task}",
                    body={"status": "completed"})


def _notes_folder_id() -> str:
    """The id of "JARVIS Notas", creating it if this is the first note. Only
    ever searches/creates by this fixed, code-chosen name — never a name a
    caller supplies."""
    q = (f"name = '{NOTES_FOLDER_NAME}' and mimeType = "
        "'application/vnd.google-apps.folder' and trashed = false")
    found = _request("GET", CALENDAR_HOST, "/drive/v3/files",
                     query={"q": q, "fields": "files(id,name)", "pageSize": "1"})
    files = found.get("files", []) if isinstance(found, dict) else []
    if files:
        return files[0]["id"]
    created = _request("POST", CALENDAR_HOST, "/drive/v3/files",
                       body={"name": NOTES_FOLDER_NAME,
                            "mimeType": "application/vnd.google-apps.folder"})
    return created["id"]


def _note_create(f: dict) -> dict:
    folder = _notes_folder_id()
    doc = _request("POST", DOCS_HOST, "/v1/documents", body={"title": f["title"]})
    doc_id = doc["documentId"]
    _request("PATCH", CALENDAR_HOST, f"/drive/v3/files/{doc_id}",
             query={"addParents": folder, "removeParents": "root", "fields": "id,parents"})
    body = f.get("body")
    if body:
        _request("POST", DOCS_HOST, f"/v1/documents/{doc_id}:batchUpdate",
                 body={"requests": [{"insertText": {"location": {"index": 1}, "text": body}}]})
    return {"documentId": doc_id}


def _own_note_or_refuse(doc_id: str) -> int:
    """The doc's current end index, and proof it lives in "JARVIS Notas" (the
    only folder JARVIS ever files a note under): refuses otherwise, even
    though `drive.file` already limits visibility to files this app created."""
    folder = _notes_folder_id()
    meta = _request("GET", CALENDAR_HOST, f"/drive/v3/files/{doc_id}",
                    query={"fields": "id,parents,mimeType"})
    if (meta.get("mimeType") != "application/vnd.google-apps.document"
            or folder not in (meta.get("parents") or [])):
        raise GoogleActionError("not_our_note")
    doc = _request("GET", DOCS_HOST, f"/v1/documents/{doc_id}")
    body = doc.get("body", {}) if isinstance(doc, dict) else {}
    content = body.get("content", []) if isinstance(body, dict) else []
    end = 1
    for el in content:
        if isinstance(el, dict) and "endIndex" in el:
            end = max(end, el["endIndex"])
    return end


def _note_append(f: dict) -> dict:
    doc_id = f["doc_id"]
    end = _own_note_or_refuse(doc_id)
    index = max(1, end - 1)
    _request("POST", DOCS_HOST, f"/v1/documents/{doc_id}:batchUpdate",
             body={"requests": [{"insertText": {"location": {"index": index},
                                               "text": "\n" + f["text"]}}]})
    return {"documentId": doc_id}


_CALLS = {
    "calendar_create": _calendar_create,
    "calendar_update": _calendar_update,
    "task_create": _task_create,
    "task_update": _task_update,
    "task_complete": _task_complete,
    "note_create": _note_create,
    "note_append": _note_append,
}


@dataclass
class Outcome:
    ok: bool
    code: str
    result_id: str = ""


def dryrun() -> bool:
    return os.environ.get("JARVIS_GOOGLE_DRYRUN", "").strip().lower() in ("1", "true", "yes")


def confirm_strict() -> bool:
    """Opt-in, same idea as `home_actions.confirm_strict()`: a spoken "yes" to
    a Google write action must be two deliberate words, never one."""
    return os.environ.get("JARVIS_GOOGLE_CONFIRM_STRICT", "").strip().lower() in ("1", "true", "yes")


def perform(action: Action) -> Outcome:
    """Do it (or, in dry-run, do nothing). Never raises, never returns a body."""
    if action.kind not in _CALLS:
        return Outcome(False, "not_permitted")
    if dryrun():
        return Outcome(True, "dryrun")
    try:
        result = _CALLS[action.kind](dict(action.fields))
    except GoogleActionError as e:
        return Outcome(False, str(e)[:120])
    rid = ""
    if isinstance(result, dict):
        rid = str(result.get("id") or result.get("documentId") or "")[:80]
    return Outcome(True, "ok", rid)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

_CTRL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _audit_path():
    return data_paths.google_actions_log_path()


def audit_ready() -> bool:
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


def audit(action: Optional[Action], *, confirmed_by: str, result: str,
          kind: str = "", result_id: str = "") -> None:
    """One JSONL line: what was asked, what was confirmed, by whom, the id
    that came back, and never a secret or a long text body."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": action.kind if action else _CTRL_RE.sub("?", kind)[:40],
        "fields": _plain_fields(dict(action.fields)) if action else {},
        "confirmed_by": confirmed_by if confirmed_by in ("voice", "panel", "none") else "none",
        "result": _CTRL_RE.sub("?", str(result))[:160],
        "result_id": _CTRL_RE.sub("?", result_id)[:80],
        "dryrun": dryrun(),
    }
    try:
        _append_line(json.dumps(entry, ensure_ascii=True, separators=(",", ":")))
    except OSError:
        pass


def _plain_fields(fields: dict) -> dict:
    """Short, identifying fields only for the log: ids and short labels, never
    the full body of a note or a long description."""
    out = {}
    for k, v in list(fields.items())[:8]:
        if k in ("body", "description", "text", "notes"):
            out[k] = f"<{len(str(v))} chars>"
        else:
            out[_CTRL_RE.sub("?", str(k))[:30]] = _CTRL_RE.sub("?", str(v))[:80]
    return out


# ---------------------------------------------------------------------------
# Frequency
# ---------------------------------------------------------------------------

class Limits:
    """How often Google actions may be proposed and performed. One per server."""

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

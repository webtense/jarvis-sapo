"""Writing to the user's PERSONAL Google account, the same conservative way
`home_actions.py` controls the house.

Nothing here is on by default. With no `google-actions.json` (the user's own
allow-list, `data_paths.google_actions_path()`) there are no actions: not
"all refused by a prompt", but no code path that reaches the network.

Scope, on purpose and nothing more (see `docs/google-escritura.md`):

  * Calendar: create an event, update one JARVIS can identify by id (title,
    time, location, description). No attendees, `sendUpdates` is always
    `none`, no deletion, no sharing.
  * Tasks: create, update title/notes/due, mark complete. No deletion.
  * Notes: a Google Doc created by JARVIS inside a Drive folder named
    "JARVIS Notas" (created the first time, under the `drive.file` scope, so
    it only ever sees files it created itself). Can be appended to. Never
    shared, never a public link, never a file JARVIS did not create.
  * Nothing else. Gmail is untouched in this module.

What this module guarantees, the same way `home_actions.py` does:

  * Only a CLOSED table of (method, host, path, body keys) is ever requested
    (`_ALLOWED`). `_request` checks it BEFORE reading the token or opening a
    socket, so a path built from anything else is refused, not "sanitised".
    A DELETE, a permissions call or a Gmail host is not in the table.
  * Bodies are built here from validated fields. A proposal containing
    `attendees`, a share/permission field, or something that looks like a
    third party's email address in any text field is refused whole.
  * Its own token (`GOOGLE_WRITE_TOKEN_FILE`, a 0600 file). It is never the
    read token (`GOOGLE_TOKEN_FILE`): if both files hold the same access or
    refresh token, nothing is sent.
  * The user decides, not a model: this module only describes and performs an
    `Action`. The proposal, the spoken question and the "yes" live in
    `server.py` and `bridge.py`, and nothing in here is reachable from a tool
    call except the PROPOSAL (`Policy.check`), which sends nothing.
  * Every attempt is one JSONL line in `google-actions.log`, without a token
    or a full text body. If that log cannot be written, nothing is done.
  * A frequency limit and a dry-run mode (`JARVIS_GOOGLE_DRYRUN=1`).
  * No redirect is followed and no proxy is used: the bearer token goes to
    Google and to nobody in between.

What it does NOT protect against (docs/google-escritura.md): a process of the
same user with Bash can read the 0600 token file; ambient audio can say "yes";
an OAuth app left in "Testing" may lose its refresh token after 7 days (an
unconfirmed hypothesis); and an event, task or note is identified by an id the
brain supplies, so for an update the user hears what would change, not the
current title of what it is applied to.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import stat
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import bridge
import data_paths
import i18n
import lang

TIMEOUT_SEC = 10.0
POLICY_MAX_BYTES = 64 * 1024
LOG_MAX_BYTES = 1024 * 1024
RESPONSE_MAX_BYTES = 512 * 1024         # read and parsed, never spoken

DEFAULT_PER_MINUTE = 3
MAX_PER_MINUTE = 10
PROPOSALS_PER_5_MIN = 6

TITLE_MAX = 120                         # a title is spoken whole, so it is short
PLACE_MAX = 120
TEXT_MAX_HARD = 4000
DEFAULT_TEXT_MAX = 1500
DEFAULT_LEAD_DAYS = 365
LEAD_DAYS_HARD_MAX = 3650
MAX_DURATION_MINUTES = 24 * 60 * 14
SPOKEN_TEXT_MAX = 250                   # how much of a long text is read aloud

NOTES_FOLDER_NAME = "JARVIS Notas"
FOLDER_MIME = "application/vnd.google-apps.folder"
DOC_MIME = "application/vnd.google-apps.document"

# One host serves Calendar and Drive (`www.googleapis.com`).
CALENDAR_HOST = "www.googleapis.com"
DRIVE_HOST = "www.googleapis.com"
TASKS_HOST = "tasks.googleapis.com"
DOCS_HOST = "docs.googleapis.com"

_ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,200}")
_LIST_RE = re.compile(r"@default|[A-Za-z0-9_\-]{1,200}")
_CAL_ID_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_@.\-]{0,254}")
_NAME_BAD_RE = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_LONG_BAD_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f  ]")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_DATETIME_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"(T[0-9]{2}:[0-9]{2}(:[0-9]{2})?(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})?)?")

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

# Text fields: an email address in any of them refuses the proposal.
_TEXT_KEYS = ("title", "location", "notes", "description", "body", "text")
_MULTILINE_KEYS = ("notes", "description", "body", "text")

# Keys forbidden in ANY payload, whatever the action: no attendee, no sharing,
# no permission change reaches this module even if the brain tries.
_FORBIDDEN_KEYS = frozenset({
    "attendees", "sendUpdates", "send_updates", "permissions", "role",
    "type", "anyoneWithLink", "shared", "writersCanShare", "visibility",
    "delete", "trash", "cc", "bcc", "to", "recipients", "guests",
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

    def check(self, kind, raw) -> Action:
        """The validated Action, or `Refused`. Sends nothing."""
        if not isinstance(kind, str) or kind not in ACTIONS:
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
            if value is None or (isinstance(value, str) and not value.strip()):
                if required:
                    raise Refused(f"{kind} needs {key}.")
                continue
            out[key] = self._one(key, value)
        self._cross_check(kind, out)
        return Action(kind, tuple(sorted(out.items())))

    def _one(self, key: str, value):
        if key == "calendar_id":
            cal = _clean_id(value, _CAL_ID_RE, "calendar_id")
            if cal not in self.calendars:
                raise Refused(f"{cal} is not in the user's list of allowed calendars.")
            return cal
        if key == "task_list":
            tl = _clean_id(value, _LIST_RE, "task_list")
            if tl not in self.task_lists:
                raise Refused(f"{tl} is not in the user's list of allowed task lists.")
            return tl
        if key in ("event_id", "task_id", "doc_id"):
            return _clean_id(value, _ID_RE, key)
        if key in ("start", "end", "due"):
            return _clean_datetime(value, key, self.max_lead_days)
        if key == "duration_minutes":
            return _clean_minutes(value)
        if key in _TEXT_KEYS:
            if key == "title":
                limit = TITLE_MAX
            elif key == "location":
                limit = PLACE_MAX
            else:
                limit = self.max_text_chars
            return _clean_text(value, key, limit)
        raise Refused("That parameter is not allowed.")       # unreachable

    def _cross_check(self, kind: str, out: dict) -> None:
        if kind == "calendar_create":
            if "end" in out and "duration_minutes" in out:
                raise Refused("calendar_create takes end or duration_minutes, not both.")
            all_day = len(out["start"]) == 10
            if all_day and "duration_minutes" in out:
                raise Refused("An all-day event takes an end date, not a duration.")
            if not all_day and "end" not in out and "duration_minutes" not in out:
                raise Refused("calendar_create needs end or duration_minutes.")
        if kind in ("calendar_create", "calendar_update") and "start" in out and "end" in out:
            if (len(out["start"]) == 10) != (len(out["end"]) == 10):
                raise Refused("start and end must both be dates or both be date-times.")
            if _as_utc(out["end"]) <= _as_utc(out["start"]):
                raise Refused("The event must end after it starts.")
        if kind == "calendar_update" and len(out) <= (2 if "calendar_id" in out else 1):
            raise Refused("calendar_update needs at least one field to change.")
        if kind == "task_update" and len(out) <= (2 if "task_list" in out else 1):
            raise Refused("task_update needs at least one field to change.")


def _check_forbidden(raw: dict) -> None:
    bad = sorted(str(k) for k in raw if k in _FORBIDDEN_KEYS)
    if bad:
        raise Refused("Not permitted on this connection: " +
                      ", ".join(_safe_key(k) for k in bad) + ".")


def _safe_key(key) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "?", str(key))[:40]


def _clean_id(value, pattern: re.Pattern, label: str) -> str:
    if not isinstance(value, str) or not value.isascii() or not pattern.fullmatch(value):
        raise Refused(f"That is not a valid {label}.")
    return value


def _clean_text(value, key: str, limit: int) -> str:
    """Untrusted text, reduced to something safe to speak, show and send."""
    if not isinstance(value, str):
        raise Refused(f"{key} must be text.")
    if len(value) > limit * 4 + 16:                  # no regex over a megabyte
        raise Refused(f"{key} is too long (max {limit} characters).")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if key in _MULTILINE_KEYS:
        text = _LONG_BAD_RE.sub(" ", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" ?\n ?", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    else:
        text = re.sub(r"\s+", " ", _NAME_BAD_RE.sub(" ", text)).strip()
    if not text:
        raise Refused(f"{key} cannot be empty.")
    if len(text) > limit:
        raise Refused(f"{key} is too long (max {limit} characters).")
    if _EMAIL_RE.search(text):
        raise Refused("A proposal that contains an email address is refused: "
                      "no attendee, no sharing, ever.")
    return text


def _parse_when(value: str) -> datetime:
    """An aware datetime for a value `_clean_datetime` accepted. A bare date
    is midnight UTC (it is only ever compared, never sent as a time)."""
    if len(value) == 10:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value)


def _as_utc(value: str) -> datetime:
    return _parse_when(value).astimezone(timezone.utc)


def _clean_datetime(value, key: str, max_lead_days: int) -> str:
    """A date (`2026-10-21`) or a date-time with seconds and an offset
    (`2026-10-21T18:00:00+02:00`). A time given without an offset is the
    user's own local time: the offset is written in, so Google never has to
    guess a zone."""
    if (not isinstance(value, str) or not value.strip().isascii()
            or not _DATETIME_RE.fullmatch(value.strip())):
        raise Refused(f"{key} must be an ISO date or date-time "
                      "(e.g. 2026-10-21 or 2026-10-21T18:00:00+02:00).")
    value = value.strip()
    try:
        if "T" not in value:
            when = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            clean = value
        else:
            parsed = datetime.fromisoformat(value)
            aware = parsed if parsed.tzinfo is not None else parsed.astimezone()
            aware = aware.replace(microsecond=0)
            clean = aware.isoformat(timespec="seconds")
            when = aware
    except (ValueError, OverflowError, OSError):
        raise Refused(f"{key} is not a valid date.") from None
    lead = max(1, min(max_lead_days, LEAD_DAYS_HARD_MAX))
    if when.astimezone(timezone.utc) > datetime.now(timezone.utc) + timedelta(days=lead):
        raise Refused(f"{key} is further ahead than the {lead}-day limit the user set.")
    return clean


def _clean_minutes(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Refused("duration_minutes must be a number.")
    if value != value or value in (float("inf"), float("-inf")):
        raise Refused("duration_minutes must be a finite number.")
    minutes = int(value)
    if not 1 <= minutes <= MAX_DURATION_MINUTES:
        raise Refused(f"duration_minutes must be between 1 and {MAX_DURATION_MINUTES} (two weeks).")
    return minutes


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


def _id_list(data: dict, key: str, default: str, pattern: re.Pattern,
             problems: list) -> frozenset:
    listed = data.get(key, [default])
    if not isinstance(listed, list) or not listed:
        problems.append(f"{key}: not a list; using [\"{default}\"].")
        listed = [default]
    keep = set()
    for item in listed:
        if isinstance(item, str) and item.isascii() and pattern.fullmatch(item):
            keep.add(item)
        else:
            problems.append(f"{key}: an invalid id was ignored.")
    return frozenset(keep or {default})


def _parse_policy(data: dict) -> Policy:
    problems: list[str] = []
    calendars = _id_list(data, "calendars", "primary", _CAL_ID_RE, problems)
    task_lists = _id_list(data, "task_lists", "@default", _LIST_RE, problems)

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

    return Policy(calendars=calendars, task_lists=task_lists,
                  max_per_minute=per_min, max_text_chars=max_text, max_lead_days=lead,
                  panel_confirm=data.get("panel_confirm") is True, problems=problems)


# ---------------------------------------------------------------------------
# What is said
# ---------------------------------------------------------------------------
#
# The frames are English with their Spanish pair in i18n_es.py. What goes in a
# `{slot}` is either the user's own words as validated above (copied, never
# translated) or a phrase built here in the spoken language (a date, "and 40
# more characters"), so the whole action is heard before the user answers.

_MONTHS = {
    "en": ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"),
    "es": ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
           "agosto", "septiembre", "octubre", "noviembre", "diciembre"),
}
_WEEKDAYS = {
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "es": ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"),
}


def _es() -> bool:
    return lang.language() == "es"


def _day_bare(day: date) -> str:
    """"jueves 22 de octubre" / "Thursday 22 October": no article, no "on"."""
    if _es():
        return f"{_WEEKDAYS['es'][day.weekday()]} {day.day} de {_MONTHS['es'][day.month - 1]}"
    return f"{_WEEKDAYS['en'][day.weekday()]} {day.day} {_MONTHS['en'][day.month - 1]}"


def _day_text(day: date) -> str:
    return ("el " if _es() else "on ") + _day_bare(day)


def _day_of(value: str) -> date:
    return date.fromisoformat(value[:10])


def _clock_of(value: str) -> str:
    return value[11:16]


def _when_text(start: str, end: Optional[str]) -> str:
    """When, in words, from validated start/end (dates or date-times)."""
    es = _es()
    if len(start) == 10:                                  # all day; `end` is exclusive
        first = _day_of(start)
        last = _day_of(end) - timedelta(days=1) if end else first
        if last > first:
            if es:
                return f"del {_day_bare(first)} al {_day_bare(last)}, todo el día"
            return f"from {_day_bare(first)} to {_day_bare(last)}, all day"
        return f"{_day_text(first)}, {'todo el día' if es else 'all day'}"
    day = _day_text(_day_of(start))
    if not end:
        return f"{day} {'a las' if es else 'at'} {_clock_of(start)}"
    if _day_of(end) == _day_of(start):
        if es:
            return f"{day}, de {_clock_of(start)} a {_clock_of(end)}"
        return f"{day}, from {_clock_of(start)} to {_clock_of(end)}"
    if es:
        return (f"{day} a las {_clock_of(start)}, hasta {_day_text(_day_of(end))} "
                f"a las {_clock_of(end)}")
    return (f"{day} at {_clock_of(start)}, until {_day_text(_day_of(end))} "
            f"at {_clock_of(end)}")


def _excerpt(text: str) -> str:
    """Up to SPOKEN_TEXT_MAX characters of a text, then how much was left out,
    so a long text is never silently shortened."""
    one = " ".join(text.split())
    if len(one) <= SPOKEN_TEXT_MAX:
        return one
    left = len(one) - SPOKEN_TEXT_MAX
    cut = one[:SPOKEN_TEXT_MAX].rstrip()
    if _es():
        return f"{cut}… y {left} caracteres más"
    return f"{cut}… and {left} more characters"


def _end_for_speech(f: dict) -> Optional[str]:
    if f.get("end"):
        return f["end"]
    if f.get("duration_minutes") and len(f.get("start", "")) > 10:
        return _plus_minutes(f["start"], f["duration_minutes"])
    if len(f.get("start", "")) == 10:
        return (_day_of(f["start"]) + timedelta(days=1)).isoformat()
    return None


def _calendar_detail(f: dict, *, creating: bool) -> str:
    es = _es()
    parts = []
    if creating:
        parts.append(_when_text(f["start"], _end_for_speech(f)))
    else:
        if "title" in f:
            parts.append(f"con el título {f['title']}" if es else f"titled {f['title']}")
        if "start" in f:
            parts.append(("empezando " if es else "starting ")
                         + _when_text(f["start"], f.get("end")))
        elif "end" in f:
            parts.append(("terminando " if es else "ending ")
                         + _when_text(f["end"], None))
    if "location" in f:
        parts.append(f"en {f['location']}" if es else f"at {f['location']}")
    if "description" in f:
        parts.append(f"con la descripción {_excerpt(f['description'])}" if es
                     else f"with the description {_excerpt(f['description'])}")
    cal = f.get("calendar_id")
    if cal and cal != "primary":
        parts.append(f"en el calendario {cal}" if es else f"in the calendar {cal}")
    return ", ".join(parts)


def _task_detail(f: dict, *, with_title: bool) -> str:
    es = _es()
    parts = []
    if with_title and "title" in f:
        parts.append(f"con el título {f['title']}" if es else f"titled {f['title']}")
    if "due" in f:
        parts.append(f"para {_day_text(_day_of(f['due']))}" if es
                     else f"due {_day_text(_day_of(f['due']))}")
    if "notes" in f:
        parts.append(f"con las notas {_excerpt(f['notes'])}" if es
                     else f"with the notes {_excerpt(f['notes'])}")
    return ", ".join(parts)


def question(action: Action) -> str:
    """The whole action, as the sentence JARVIS says before anything happens.
    Built only from the validated Action: never raw text from the brain."""
    k, f = action.kind, dict(action.fields)
    if k == "calendar_create":
        english = ("Shall I create the event {name}, {detail}, in your "
                   "calendar, sir?").format(
            name=f["title"], detail=_calendar_detail(f, creating=True))
    elif k == "calendar_update":
        english = "Shall I change that calendar event: {detail}, sir?".format(
            detail=_calendar_detail(f, creating=False))
    elif k == "task_create":
        detail = _task_detail(f, with_title=False)
        if detail:
            english = "Shall I add {name} to your tasks, {detail}, sir?".format(
                name=f["title"], detail=detail)
        else:
            english = "Shall I add {name} to your tasks, sir?".format(name=f["title"])
    elif k == "task_update":
        english = "Shall I change that task: {detail}, sir?".format(
            detail=_task_detail(f, with_title=True))
    elif k == "task_complete":
        english = "Shall I mark that task as complete, sir?"
    elif k == "note_create":
        if "body" in f:
            english = ("Shall I create a note titled {name} in your Drive, "
                       "saying {detail}, sir?").format(
                name=f["title"], detail=_excerpt(f["body"]))
        else:
            english = ("Shall I create a note titled {name} in your Drive, sir?"
                       ).format(name=f["title"])
    elif k == "note_append":
        english = "Shall I add this to that note in your Drive: {detail}, sir?".format(
            detail=_excerpt(f["text"]))
    else:
        english = "Shall I do that in Google, sir?"            # unreachable
    return i18n.t(english)


# ---------------------------------------------------------------------------
# Credentials and the closed table of requests
# ---------------------------------------------------------------------------

def _read_secret_file(path: str, label: str) -> str:
    try:
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode):
            raise GoogleActionError(f"{label} is not a regular file.")
        if st.st_mode & 0o077:
            raise GoogleActionError(f"{label} must not be readable by group or others (chmod 600).")
        with open(path, encoding="utf-8") as fh:
            return fh.read(16384)
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
    _refuse_the_read_token(data, path)
    return path, data


def _same(a, b) -> bool:
    return bool(a) and bool(b) and isinstance(a, str) and isinstance(b, str) and \
        hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _refuse_the_read_token(write_data: dict, write_path: str) -> None:
    """The write credential must be a different grant from the read-only
    connection's, the same defence `home_actions.py` applies: not the same
    file, and neither the same access token nor the same refresh token."""
    read_path = os.environ.get("GOOGLE_TOKEN_FILE", "").strip()
    if not read_path:
        return
    try:
        if os.path.samefile(read_path, write_path):
            raise GoogleActionError("GOOGLE_WRITE_TOKEN_FILE must not be the read token's file.")
    except OSError:
        pass
    try:
        read_data = json.loads(_read_secret_file(read_path, "GOOGLE_TOKEN_FILE"))
    except (GoogleActionError, ValueError):
        return
    if not isinstance(read_data, dict):
        return
    for key in ("access_token", "refresh_token"):
        if _same(read_data.get(key), write_data.get(key)):
            raise GoogleActionError("The write token must not be the read-only token.")


def _write_token_data(path: str, data: dict) -> None:
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
    """Refuse every redirect: the default handler would resend the
    Authorization header to whatever host the 3xx names."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# ProxyHandler({}) = no proxy at all, whatever http_proxy says.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def _open(req: urllib.request.Request, timeout: float):
    """The network seam; tests replace it."""
    return _OPENER.open(req, timeout=timeout)  # noqa: S310 (host/path checked by _check_call)


_TOKEN_URI = "https://oauth2.googleapis.com/token"  # gitleaks:allow (an endpoint, not a secret)
_TOKEN_HOST = "oauth2.googleapis.com"
_TOKEN_PATH = "/token"


def _refresh_access_token(data: dict) -> dict:
    refresh_token = data.get("refresh_token")
    client_id = data.get("client_id")
    client_secret = data.get("client_secret")
    if not (refresh_token and client_id and client_secret):
        raise GoogleActionError("reauth")
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
        except (ValueError, OSError, AttributeError):
            payload = {}
        if isinstance(payload, dict) and payload.get("error") == "invalid_grant":
            raise GoogleActionError("reauth") from None
        raise GoogleActionError(f"token_http_{e.code}") from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise GoogleActionError("unreachable") from None
    try:
        payload = json.loads(raw)
    except ValueError:
        raise GoogleActionError("unreadable") from None
    new_access = payload.get("access_token") if isinstance(payload, dict) else None
    if not new_access:
        raise GoogleActionError("reauth")
    out = dict(data)
    out["access_token"] = new_access
    return out


# The ENTIRE surface: (method, host, path pattern, body keys). Anything else
# is refused before the token is read. Patterns are matched with `fullmatch`.
_CAL = r"[A-Za-z0-9_][A-Za-z0-9_@.\-]{0,254}"
_OBJ = r"[A-Za-z0-9_\-]{1,200}"
_LST = r"(?:@default|[A-Za-z0-9_\-]{1,200})"
_EVENT_KEYS = frozenset({"summary", "start", "end", "location", "description"})
_ALLOWED: tuple = (
    ("POST", CALENDAR_HOST, re.compile(rf"/calendar/v3/calendars/{_CAL}/events"), _EVENT_KEYS),
    ("PATCH", CALENDAR_HOST, re.compile(rf"/calendar/v3/calendars/{_CAL}/events/{_OBJ}"), _EVENT_KEYS),
    ("POST", TASKS_HOST, re.compile(rf"/tasks/v1/lists/{_LST}/tasks"),
     frozenset({"title", "notes", "due"})),
    ("PATCH", TASKS_HOST, re.compile(rf"/tasks/v1/lists/{_LST}/tasks/{_OBJ}"),
     frozenset({"title", "notes", "due", "status"})),
    ("GET", DRIVE_HOST, re.compile(r"/drive/v3/files"), frozenset()),
    ("GET", DRIVE_HOST, re.compile(rf"/drive/v3/files/{_OBJ}"), frozenset()),
    ("POST", DRIVE_HOST, re.compile(r"/drive/v3/files"),
     frozenset({"name", "mimeType", "parents"})),
    ("POST", DOCS_HOST, re.compile(rf"/v1/documents/{_OBJ}:batchUpdate"),
     frozenset({"requests"})),
)
_QUERY_KEYS = frozenset({"q", "fields", "pageSize", "sendUpdates"})


def _check_call(method: str, host: str, path: str, query, body) -> None:
    """Refuse anything outside `_ALLOWED`. Runs BEFORE the token is read, so a
    request that is not on the list never even loads a credential."""
    keys = None
    for m, h, pattern, body_keys in _ALLOWED:
        if m == method and h == host and pattern.fullmatch(path):
            keys = body_keys
            break
    if keys is None:
        raise GoogleActionError("not_permitted")
    if body is not None:
        if not isinstance(body, dict) or not set(body) <= keys:
            raise GoogleActionError("not_permitted")
    elif method in ("POST", "PATCH"):
        raise GoogleActionError("not_permitted")
    if query is not None and (not isinstance(query, dict) or not set(query) <= _QUERY_KEYS):
        raise GoogleActionError("not_permitted")
    if "/events" in path and method in ("POST", "PATCH"):
        # Calendar writes never notify a third party.
        if (query or {}).get("sendUpdates") != "none":
            raise GoogleActionError("not_permitted")
    elif query and "sendUpdates" in query:
        raise GoogleActionError("not_permitted")
    if path.endswith(":batchUpdate"):
        reqs = (body or {}).get("requests")
        if (not isinstance(reqs, list) or not 1 <= len(reqs) <= 2
                or not all(isinstance(r, dict) and set(r) == {"insertText"}
                           and isinstance(r["insertText"], dict)
                           and set(r["insertText"]) <= {"location", "endOfSegmentLocation", "text"}
                           for r in reqs)):
            raise GoogleActionError("not_permitted")
    if host == TASKS_HOST and body and "status" in body and body["status"] != "completed":
        raise GoogleActionError("not_permitted")
    if "/drive/v3/files" == path and method == "POST":
        if (body or {}).get("mimeType") not in (FOLDER_MIME, DOC_MIME):
            raise GoogleActionError("not_permitted")


def _request(method: str, host: str, path: str, *, query: dict | None = None,
             body: dict | None = None, _retry: bool = True):
    """The ONE function that reaches the Google write APIs. The call must be
    on `_ALLOWED`; nothing here accepts a path built from untrusted text."""
    _check_call(method, host, path, query, body)
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
        if e.code == 401 and _retry:
            # An expired access token: the request was refused, not run, so
            # repeating it after a renewal cannot do anything twice.
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

def _plus_minutes(start: str, minutes) -> str:
    """`start` (a date-time with an offset) plus a duration, same offset."""
    return (datetime.fromisoformat(start) + timedelta(minutes=int(minutes))
            ).isoformat(timespec="seconds")


def _time_field(value: str) -> dict:
    return {"dateTime": value} if "T" in value else {"date": value}


def _task_due(value: str) -> str:
    """Tasks keeps only the DATE of `due`, and wants an RFC 3339 timestamp."""
    return value[:10] + "T00:00:00.000Z"


def _quoted(value: str) -> str:
    return urllib.parse.quote(value, safe="@.")


def _calendar_create(f: dict) -> dict:
    cal = _quoted(f.get("calendar_id", "primary"))
    start = f["start"]
    if f.get("end"):
        end = f["end"]
    elif len(start) == 10:
        end = (_day_of(start) + timedelta(days=1)).isoformat()
    else:
        end = _plus_minutes(start, f["duration_minutes"])
    body = {"summary": f["title"], "start": _time_field(start), "end": _time_field(end)}
    if "location" in f:
        body["location"] = f["location"]
    if "description" in f:
        body["description"] = f["description"]
    return _request("POST", CALENDAR_HOST, f"/calendar/v3/calendars/{cal}/events",
                    query={"sendUpdates": "none"}, body=body)


def _calendar_update(f: dict) -> dict:
    cal = _quoted(f.get("calendar_id", "primary"))
    event = _quoted(f["event_id"])
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


def _task_create(f: dict) -> dict:
    tl = _quoted(f.get("task_list", "@default"))
    body = {"title": f["title"]}
    if "notes" in f:
        body["notes"] = f["notes"]
    if "due" in f:
        body["due"] = _task_due(f["due"])
    return _request("POST", TASKS_HOST, f"/tasks/v1/lists/{tl}/tasks", body=body)


def _task_update(f: dict) -> dict:
    tl = _quoted(f.get("task_list", "@default"))
    task = _quoted(f["task_id"])
    body: dict = {}
    if "title" in f:
        body["title"] = f["title"]
    if "notes" in f:
        body["notes"] = f["notes"]
    if "due" in f:
        body["due"] = _task_due(f["due"])
    return _request("PATCH", TASKS_HOST, f"/tasks/v1/lists/{tl}/tasks/{task}", body=body)


def _task_complete(f: dict) -> dict:
    tl = _quoted(f.get("task_list", "@default"))
    task = _quoted(f["task_id"])
    return _request("PATCH", TASKS_HOST, f"/tasks/v1/lists/{tl}/tasks/{task}",
                    body={"status": "completed"})


def _google_id(value) -> str:
    """An id Google sent back, checked before it goes into a path or a body."""
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise GoogleActionError("unexpected")
    return value


def _notes_folder_id(create: bool) -> Optional[str]:
    """The id of "JARVIS Notas". Searched by this module's fixed name only;
    created when `create` is true and it is not there yet. Under `drive.file`
    the search can only ever see folders JARVIS itself created."""
    q = (f"name = '{NOTES_FOLDER_NAME}' and mimeType = '{FOLDER_MIME}' "
         "and trashed = false")
    found = _request("GET", DRIVE_HOST, "/drive/v3/files",
                     query={"q": q, "fields": "files(id)", "pageSize": "1"})
    files = found.get("files") if isinstance(found, dict) else None
    if files:
        return _google_id(files[0].get("id"))
    if not create:
        return None
    made = _request("POST", DRIVE_HOST, "/drive/v3/files",
                    body={"name": NOTES_FOLDER_NAME, "mimeType": FOLDER_MIME})
    return _google_id(made.get("id") if isinstance(made, dict) else None)


def _note_create(f: dict) -> dict:
    folder = _notes_folder_id(create=True)
    doc = _request("POST", DRIVE_HOST, "/drive/v3/files",
                   body={"name": f["title"], "mimeType": DOC_MIME, "parents": [folder]})
    doc_id = _google_id(doc.get("id") if isinstance(doc, dict) else None)
    if "body" in f:
        _request("POST", DOCS_HOST, f"/v1/documents/{doc_id}:batchUpdate",
                 body={"requests": [{"insertText": {"location": {"index": 1},
                                                    "text": f["body"]}}]})
    return {"id": doc_id}


def _require_own_note(doc_id: str) -> None:
    """Proof that `doc_id` is a live Google Doc filed inside "JARVIS Notas",
    the only folder JARVIS files a note under. `drive.file` already limits
    what is visible to files this app created; this refuses the rest anyway."""
    folder = _notes_folder_id(create=False)
    if folder is None:
        raise GoogleActionError("not_our_note")
    meta = _request("GET", DRIVE_HOST, f"/drive/v3/files/{doc_id}",
                    query={"fields": "id,parents,mimeType,trashed"})
    parents = meta.get("parents") if isinstance(meta, dict) else None
    if (not isinstance(meta, dict)
            or meta.get("mimeType") != DOC_MIME or meta.get("trashed") is True
            or not isinstance(parents, list) or folder not in parents):
        raise GoogleActionError("not_our_note")


def _note_append(f: dict) -> dict:
    doc_id = f["doc_id"]
    _require_own_note(doc_id)
    _request("POST", DOCS_HOST, f"/v1/documents/{doc_id}:batchUpdate",
             body={"requests": [{"insertText": {"endOfSegmentLocation": {},
                                                "text": "\n" + f["text"]}}]})
    return {"id": doc_id}


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
    code: str            # "ok" | "dryrun" | "http_<n>" | "redirect" | "unreachable" | "reauth" | ...
    result_id: str = ""


def dryrun() -> bool:
    return os.environ.get("JARVIS_GOOGLE_DRYRUN", "").strip().lower() in ("1", "true", "yes")


def confirm_strict() -> bool:
    """Opt-in, same idea as `home_actions.confirm_strict()`: a spoken "yes" to
    a Google write action must be two deliberate words, never one."""
    return os.environ.get("JARVIS_GOOGLE_CONFIRM_STRICT", "").strip().lower() in ("1", "true", "yes")


def perform(action: Action) -> Outcome:
    """Do it (or, in dry-run, do nothing). Never raises, never returns a body."""
    call = _CALLS.get(action.kind)
    if call is None:
        return Outcome(False, "not_permitted")
    if dryrun():
        return Outcome(True, "dryrun")
    try:
        result = call(dict(action.fields))
    except GoogleActionError as e:
        return Outcome(False, str(e)[:120])
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        return Outcome(False, "unexpected")
    rid = ""
    if isinstance(result, dict):
        rid = str(result.get("id") or "")[:80]
    return Outcome(True, "ok", rid)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

_CTRL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _audit_path():
    return data_paths.google_actions_log_path()


def audit_ready() -> bool:
    """Can a line be appended? Checked BEFORE acting: no log, no action."""
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
        pass          # the caller checked audit_ready() before acting


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

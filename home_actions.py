"""Acting on the house, the most conservative way this code can be written.

Nothing here is on by default. With no `home-actions.json` (the user's own
allow-list, `data_paths.home_actions_path()`) there are no actions: not "all
refused by a prompt", but no code path that reaches the network.

What this module guarantees, and `tests/test_home_actions.py` pins:

  * Only what the user listed, and only the services the CODE permits for that
    domain. Some domains are forbidden outright (alarm, locks, cameras,
    scripts, automations, shell commands, `homeassistant.*`...): a file that
    lists them is ignored for them. An entity id or a service name that is not
    exactly `[a-z0-9_]` (capitals, `..`, a slash, a Cyrillic "a") is refused,
    never "normalised" into something that might match.
  * The only request this module can make is `POST /api/services/<domain>/<service>`
    with a domain and a service that are both in `PERMITTED`. The body is built
    here from the entity id and from parameters that were validated against a
    fixed range; whatever else a caller passes is refused, not forwarded.
    Redirects are never followed (urllib would resend the Authorization header
    to whatever host the 3xx names) and no proxy is used.
  * Its own token (`HA_ACTIONS_TOKEN_FILE`, a 0600 file). It is never the read
    token: if both files hold the same value, nothing is sent. The one
    exception is the user's explicit opt-in `HA_ACTIONS_ALLOW_READ_TOKEN=1`
    (see `allow_read_token`), which preflight and the startup log announce.
  * The user decides, not a model: this module only describes and performs an
    `Action`. The proposal, the spoken question and the "yes" live in
    `server.py` and `bridge.py`, and nothing in here is reachable from a tool
    call except the PROPOSAL (`Policy.check`), which sends nothing.
  * Every attempt (done, refused, simulated, failed) is one JSONL line in
    `home-actions.log`, without a token. If that log cannot be written,
    nothing is done.
  * A frequency limit (actions per minute) and a dry-run mode
    (`JARVIS_HOME_DRYRUN=1`: it says what it would do, touches nothing).

What it does NOT protect against, said plainly (docs/casa-control.md): a
process of the same user with Bash can read the 0600 token file and call Home
Assistant itself, or edit the allow-list. The runs JARVIS starts without
supervision do not inherit the variables (`claude_env`), which stops the
accidental route, not that one.
"""

from __future__ import annotations

import hmac
import json
import math
import os
import re
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import bridge
import data_paths
import ha_exclude
import i18n
import lang

TIMEOUT_SEC = 10.0
POLICY_MAX_BYTES = 64 * 1024
LOG_MAX_BYTES = 1024 * 1024
RESPONSE_MAX_BYTES = 64 * 1024         # read and thrown away: never spoken

# Refused by code even if the user's file lists them. Not a list of "dangerous
# things I thought of": anything not in PERMITTED is refused anyway; this is
# the explicit statement of what the user may not unlock.
FORBIDDEN_DOMAINS = frozenset({
    "alarm_control_panel", "lock", "camera", "script", "automation",
    "shell_command", "homeassistant", "persistent_notification",
})

# The whole of what can ever be asked for, whatever the file says.
PERMITTED: dict[str, frozenset] = {
    "light": frozenset({"turn_on", "turn_off", "toggle"}),
    "switch": frozenset({"turn_on", "turn_off", "toggle"}),
    "scene": frozenset({"turn_on"}),
    "climate": frozenset({"set_temperature"}),
    "media_player": frozenset({"media_play", "media_pause", "media_play_pause",
                               "volume_set", "volume_up", "volume_down"}),
    "cover": frozenset({"open_cover", "close_cover", "stop_cover"}),
}

# Parameters a service may carry, and nothing else.
PARAMS: dict[tuple[str, str], dict[str, bool]] = {
    # name -> required?
    ("light", "turn_on"): {"brightness_pct": False},
    ("climate", "set_temperature"): {"temperature": True},
    ("media_player", "volume_set"): {"volume_level": True},
}

# Hard bounds of what a configurable limit may be set to.
CLIMATE_HARD_MIN = 5.0
CLIMATE_HARD_MAX = 30.0
DEFAULT_MAX_VOLUME = 0.5
DEFAULT_PER_MINUTE = 3
MAX_PER_MINUTE = 10
PROPOSALS_PER_5_MIN = 6

_ENTITY_RE = re.compile(r"[a-z][a-z0-9_]{0,40}\.[a-z0-9_]{1,80}")
_SERVICE_RE = re.compile(r"[a-z][a-z0-9_]{0,40}")
_PATH_RE = re.compile(r"/api/services/[a-z][a-z0-9_]{0,40}/[a-z][a-z0-9_]{0,40}")
_NAME_BAD_RE = re.compile(r"[^\w ,.'\-]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")
NAME_MAX = 60


class Refused(Exception):
    """A request the policy will not carry out. The message is for the brain
    (a tool result), in English, with no path and no secret."""


class Disabled(Exception):
    """Home control is not in use: no file, switched off, or a file that cannot
    be trusted. The message says why, for the user's log."""


class HomeError(Exception):
    """A failure whose message is safe to log (no URL, no token)."""


# ---------------------------------------------------------------------------
# The allow-list
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Action:
    """One validated action: everything needed to describe it, to log it and to
    perform it, and nothing that came from outside unchecked."""
    entity_id: str
    domain: str
    service: str
    params: tuple = ()                    # sorted ((name, number), ...)
    name: str = ""

    @property
    def path(self) -> str:
        return f"/api/services/{self.domain}/{self.service}"

    def body(self) -> dict:
        body = {"entity_id": self.entity_id}
        body.update(dict(self.params))
        return body

    def as_dict(self) -> dict:
        return {"entity_id": self.entity_id, "service": self.service,
                "params": dict(self.params)}


@dataclass
class Policy:
    entities: dict                        # entity_id -> spoken name
    services: dict                        # domain -> frozenset of services
    min_temp: Optional[float] = None
    max_temp: Optional[float] = None
    max_volume: float = DEFAULT_MAX_VOLUME
    max_per_minute: int = DEFAULT_PER_MINUTE
    panel_confirm: bool = False
    problems: list = field(default_factory=list)

    def check(self, entity_id, service, params=None) -> Action:
        """The validated Action, or `Refused`. Sends nothing."""
        entity = _entity(entity_id)
        svc = _service(service)
        # Defence in depth: even an entity the user's own home-actions.json
        # lists is refused if it is also in the resort's exclusion file. No
        # network call has happened yet. If the exclusion file itself cannot
        # be trusted, every action is refused the same way (fail closed) --
        # see ha_exclude.load.
        try:
            excluded = ha_exclude.load().is_excluded(entity)
        except ha_exclude.ExcludeFileInvalid:
            raise Refused("the exclusion file is not valid; refusing.") from None
        if excluded:
            raise Refused("excluded")
        domain = entity.split(".", 1)[0]
        if domain in FORBIDDEN_DOMAINS:
            raise Refused(f"The domain {domain} is never controlled by JARVIS.")
        if domain not in PERMITTED:
            raise Refused(f"The domain {domain} is not one JARVIS can control.")
        if svc not in PERMITTED[domain]:
            raise Refused(f"{domain}.{svc} is not a permitted service.")
        if entity not in self.entities:
            raise Refused(f"{entity} is not in the user's list of allowed entities.")
        if svc not in self.services.get(domain, frozenset()):
            raise Refused(f"{domain}.{svc} is not in the user's list of allowed services.")
        clean = self._params(domain, svc, {} if params is None else params)
        return Action(entity, domain, svc, tuple(sorted(clean.items())),
                      self.entities[entity])

    def _params(self, domain: str, svc: str, given: dict) -> dict:
        wanted = PARAMS.get((domain, svc), {})
        if not isinstance(given, dict):
            raise Refused("The parameters must be an object.")
        extra = sorted(k for k in given if k not in wanted)
        if extra:
            raise Refused("Parameters not allowed for this service: "
                          + ", ".join(_safe_key(k) for k in extra) + ".")
        out: dict = {}
        for key, required in wanted.items():
            if key not in given or given[key] is None:
                if required:
                    raise Refused(f"{domain}.{svc} needs {key}.")
                continue
            out[key] = self._one(domain, svc, key, given[key])
        return out

    def _one(self, domain: str, svc: str, key: str, value):
        number = _number(value, key)
        if key == "temperature":
            if self.min_temp is None or self.max_temp is None:
                raise Refused("The user set no temperature limits, so the "
                              "thermostat cannot be changed.")
            number = round(number * 2) / 2        # half degrees, like a thermostat
            if not self.min_temp <= number <= self.max_temp:
                raise Refused(f"The temperature must be between {_fmt(self.min_temp)} "
                              f"and {_fmt(self.max_temp)}.")
            return number
        if key == "volume_level":
            number = round(number, 2)
            if not 0.0 <= number <= self.max_volume:
                raise Refused(f"The volume must be between 0 and {_fmt(self.max_volume)}.")
            return number
        if key == "brightness_pct":
            if number != int(number) or not 1 <= number <= 100:
                raise Refused("brightness_pct must be a whole number from 1 to 100.")
            return int(number)
        raise Refused("That parameter is not allowed.")           # unreachable


def _safe_key(key) -> str:
    return re.sub(r"[^a-z0-9_]", "?", str(key).lower())[:30]


def _fmt(x: float) -> str:
    return f"{x:g}"


def _number(value, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Refused(f"{key} must be a number.")
    value = float(value)
    if not math.isfinite(value):
        raise Refused(f"{key} must be a finite number.")
    return value


def _entity(raw) -> str:
    if not isinstance(raw, str) or not raw.isascii() or not _ENTITY_RE.fullmatch(raw):
        raise Refused("That is not a valid entity id: lowercase letters, digits "
                      "and underscores, as domain.name.")
    return raw


def _service(raw) -> str:
    if not isinstance(raw, str) or not raw.isascii() or not _SERVICE_RE.fullmatch(raw):
        raise Refused("That is not a valid service name.")
    return raw


def clean_name(raw, fallback: str) -> str:
    """A spoken name from the user's own file, reduced to words."""
    text = _NAME_BAD_RE.sub(" ", str(raw or ""))
    text = _SPACE_RE.sub(" ", text).strip(" .,'-")[:NAME_MAX].strip()
    return text or fallback


def _derived_name(entity_id: str) -> str:
    return entity_id.replace(".", " ").replace("_", " ")


def _file_problem(path: str, what: str, *, writable_check: bool) -> Optional[str]:
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
    if writable_check and st.st_mode & 0o022:
        return f"{what} must not be writable by group or others (chmod 600)."
    return None


def load_policy(path=None) -> Policy:
    """The user's allow-list, or `Disabled` (never a half-trusted policy)."""
    path = str(path or data_paths.home_actions_path())
    problem = _file_problem(path, "home-actions.json", writable_check=True)
    if problem == "missing":
        raise Disabled("There is no home-actions.json: home control is off.")
    if problem:
        raise Disabled(problem)
    try:
        with open(path, "rb") as fh:
            raw = fh.read(POLICY_MAX_BYTES + 1)
    except OSError:
        raise Disabled("home-actions.json cannot be read.") from None
    if len(raw) > POLICY_MAX_BYTES:
        raise Disabled("home-actions.json is too large.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise Disabled("home-actions.json is not valid JSON.") from None
    if not isinstance(data, dict):
        raise Disabled("home-actions.json must be a JSON object.")
    if data.get("enabled") is not True:
        raise Disabled("home-actions.json has not been switched on (enabled: true).")
    return _parse_policy(data)


def _parse_policy(data: dict) -> Policy:
    problems: list[str] = []
    wanted_services: dict[str, frozenset] = {}
    services = data.get("services")
    if not isinstance(services, dict):
        raise Disabled("home-actions.json has no \"services\" object.")
    for domain, names in services.items():
        if (not isinstance(domain, str) or domain in FORBIDDEN_DOMAINS
                or domain not in PERMITTED or not isinstance(names, list)):
            problems.append(f"services: {_safe_key(domain)} is not allowed; ignored.")
            continue
        keep = set()
        for n in names:
            if isinstance(n, str) and n in PERMITTED[domain]:
                keep.add(n)
            else:
                problems.append(f"services: {domain}.{_safe_key(n)} is not allowed; ignored.")
        if keep:
            wanted_services[domain] = frozenset(keep)

    entities: dict[str, str] = {}
    listed = data.get("entities")
    if not isinstance(listed, dict):
        raise Disabled("home-actions.json has no \"entities\" object.")
    for entity_id, spoken in listed.items():
        if (not isinstance(entity_id, str) or not entity_id.isascii()
                or not _ENTITY_RE.fullmatch(entity_id)):
            problems.append("entities: an id that is not valid was ignored.")
            continue
        domain = entity_id.split(".", 1)[0]
        if domain in FORBIDDEN_DOMAINS or domain not in PERMITTED:
            problems.append(f"entities: {entity_id} is in a domain JARVIS never controls; ignored.")
            continue
        if domain not in wanted_services:
            problems.append(f"entities: {entity_id} has no allowed service; ignored.")
            continue
        entities[entity_id] = clean_name(spoken, _derived_name(entity_id))
    if not entities:
        raise Disabled("home-actions.json lists no usable entity.")

    limits = data.get("limits") if isinstance(data.get("limits"), dict) else {}
    per_min = limits.get("max_per_minute", DEFAULT_PER_MINUTE)
    if isinstance(per_min, bool) or not isinstance(per_min, int) or not 1 <= per_min <= MAX_PER_MINUTE:
        problems.append("limits.max_per_minute is not valid; using the default.")
        per_min = DEFAULT_PER_MINUTE

    min_t = max_t = None
    climate = limits.get("climate") if isinstance(limits.get("climate"), dict) else {}
    lo, hi = climate.get("min_temp"), climate.get("max_temp")
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
           for v in (lo, hi)) and CLIMATE_HARD_MIN <= lo <= hi <= CLIMATE_HARD_MAX:
        min_t, max_t = float(lo), float(hi)
    elif "climate" in wanted_services:
        problems.append("limits.climate is missing or outside 5-30 degrees; "
                        "the thermostat stays blocked.")

    max_vol = DEFAULT_MAX_VOLUME
    mp = limits.get("media_player") if isinstance(limits.get("media_player"), dict) else {}
    if "max_volume" in mp:
        v = mp["max_volume"]
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= 1:
            max_vol = float(v)
        else:
            problems.append("limits.media_player.max_volume is not valid; using the default.")

    return Policy(entities=entities, services=wanted_services, min_temp=min_t,
                  max_temp=max_t, max_volume=max_vol, max_per_minute=per_min,
                  panel_confirm=data.get("panel_confirm") is True, problems=problems)


# ---------------------------------------------------------------------------
# What is said
# ---------------------------------------------------------------------------

# English frames; each has its Spanish pair in i18n_es.py.
_QUESTIONS = {
    ("light", "turn_on"): "Shall I turn on {name}, sir?",
    ("switch", "turn_on"): "Shall I turn on {name}, sir?",
    ("light", "turn_off"): "Shall I turn off {name}, sir?",
    ("switch", "turn_off"): "Shall I turn off {name}, sir?",
    ("light", "toggle"): "Shall I toggle {name}, sir?",
    ("switch", "toggle"): "Shall I toggle {name}, sir?",
    ("scene", "turn_on"): "Shall I activate {name}, sir?",
    ("media_player", "media_play"): "Shall I play {name}, sir?",
    ("media_player", "media_pause"): "Shall I pause {name}, sir?",
    ("media_player", "media_play_pause"): "Shall I play or pause {name}, sir?",
    ("media_player", "volume_up"): "Shall I raise the volume of {name}, sir?",
    ("media_player", "volume_down"): "Shall I lower the volume of {name}, sir?",
    ("cover", "open_cover"): "Shall I open {name}, sir?",
    ("cover", "close_cover"): "Shall I close {name}, sir?",
    ("cover", "stop_cover"): "Shall I stop {name}, sir?",
}


def _number_text(x: float) -> str:
    text = f"{x:g}"
    return text.replace(".", ",") if lang.language() == "es" else text


def question(action: Action) -> str:
    """The whole action, as the sentence JARVIS says before anything happens.
    Built only from the validated Action: the name is the user's own word from
    his file, never text from the brain or from Home Assistant."""
    params = dict(action.params)
    key = (action.domain, action.service)
    if key == ("light", "turn_on") and "brightness_pct" in params:
        english = "Shall I turn on {name} at {value} percent, sir?".format(
            name=action.name, value=params["brightness_pct"])
    elif key == ("climate", "set_temperature"):
        english = "Shall I set {name} to {value} degrees, sir?".format(
            name=action.name, value=_number_text(params["temperature"]))
    elif key == ("media_player", "volume_set"):
        english = "Shall I set the volume of {name} to {value} percent, sir?".format(
            name=action.name, value=round(params["volume_level"] * 100))
    else:
        english = _QUESTIONS[key].format(name=action.name)
    return i18n.t(english)


# ---------------------------------------------------------------------------
# Credentials and the one request
# ---------------------------------------------------------------------------

def _read_secret_file(path: str, label: str) -> str:
    """The token from a file only its owner can read. Messages never name the path."""
    try:
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode):
            raise HomeError(f"{label} is not a regular file.")
        if st.st_mode & 0o077:
            raise HomeError(f"{label} must not be readable by group or others (chmod 600).")
        with open(path, encoding="utf-8") as fh:
            return fh.read(4096).strip()
    except OSError:
        raise HomeError(f"{label} cannot be read.") from None


def allow_read_token() -> bool:
    """The user's explicit, opt-in decision to let the READ token also act
    (docs/casa-control.md). Only the exact value "1": "true", "yes" or an empty
    string do not count, so it cannot be switched on by accident."""
    return os.environ.get("HA_ACTIONS_ALLOW_READ_TOKEN", "").strip() == "1"


def _actions_token_source() -> tuple[str, str, bool]:
    """(path, label, falls_back_to_read) of the file holding the action token.
    Without HA_ACTIONS_TOKEN_FILE, and ONLY with the opt-in, the read token's
    file stands in for it."""
    token_file = os.environ.get("HA_ACTIONS_TOKEN_FILE", "").strip()
    if token_file:
        return token_file, "HA_ACTIONS_TOKEN_FILE", False
    if allow_read_token():
        read_file = os.environ.get("HA_TOKEN_FILE", "").strip()
        if read_file:
            return read_file, "HA_TOKEN_FILE", True
    raise HomeError("HA_ACTIONS_TOKEN_FILE is not set.")


def read_token_in_use() -> bool:
    """True when, by the user's opt-in, actions go out with the READ token (the
    fallback, or an actions file holding the same token). Reads files only to
    compare; never returns or logs a value. False on any doubt."""
    if not allow_read_token():
        return False
    try:
        path, label, fallback = _actions_token_source()
        token = _read_secret_file(path, label)
        return bool(token) and (fallback or _same_as_read_token(token, path))
    except HomeError:
        return False


def _config() -> tuple[str, str]:
    """(base url, token) of the ACTION credential. Only a file: there is no
    HA_ACTIONS_TOKEN variable on purpose, an environment is inherited."""
    token_file, label, _fallback = _actions_token_source()
    token = _read_secret_file(token_file, label)
    url = (os.environ.get("HA_ACTIONS_URL", "").strip()
           or os.environ.get("HA_URL", "").strip()).rstrip("/")
    if not url or not token:
        raise HomeError("Home Assistant is not configured for actions "
                        "(HA_ACTIONS_URL or HA_URL, and HA_ACTIONS_TOKEN_FILE).")
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme not in ("http", "https") or not parts.hostname
            or parts.username or parts.password or parts.query or parts.fragment):
        raise HomeError("The Home Assistant address is not a valid http(s) address.")
    if not allow_read_token():
        _refuse_the_read_token(token, token_file)
    return url, token


def _same_as_read_token(token: str, token_file: str) -> bool:
    """Is this the read token (same file, or same value as the file or HA_TOKEN)?"""
    read_file = os.environ.get("HA_TOKEN_FILE", "").strip()
    read_values = []
    if read_file:
        try:
            if os.path.samefile(read_file, token_file):
                return True
        except OSError:
            pass
        try:
            read_values.append(_read_secret_file(read_file, "HA_TOKEN_FILE"))
        except HomeError:
            pass
    inline = os.environ.get("HA_TOKEN", "").strip()
    if inline:
        read_values.append(inline)
    return any(value and hmac.compare_digest(value.encode("utf-8"), token.encode("utf-8"))
               for value in read_values)


def _refuse_the_read_token(token: str, token_file: str) -> None:
    """The action credential must be a different token from the read one."""
    read_file = os.environ.get("HA_TOKEN_FILE", "").strip()
    if read_file:
        try:
            if os.path.samefile(read_file, token_file):
                raise HomeError("HA_ACTIONS_TOKEN_FILE must not be the read token's file.")
        except OSError:
            pass
    if _same_as_read_token(token, token_file):
        raise HomeError("The action token must not be the read-only token: "
                        "create a separate long-lived token.")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect (see ha_readonly_mcp: the default handler would
    resend the Authorization header to the host the 3xx names)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# ProxyHandler({}) = no proxy at all: the write token goes to Home Assistant
# and to nobody in between, whatever http_proxy says.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def _open(req: urllib.request.Request, timeout: float):
    """The network seam; tests replace it."""
    return _OPENER.open(req, timeout=timeout)  # noqa: S310 (scheme checked in _config)


def _check_path(action: Action) -> None:
    """The last gate before a socket: the path must be exactly a permitted
    domain and service. Independent of `Policy.check` on purpose."""
    path = action.path
    parts = path.split("/")
    ok = (_PATH_RE.fullmatch(path) is not None and len(parts) == 5
          and parts[3] not in FORBIDDEN_DOMAINS
          and parts[3] in PERMITTED and parts[4] in PERMITTED[parts[3]]
          and action.entity_id.split(".", 1)[0] == parts[3])
    if not ok:
        raise HomeError("That service is not permitted.")


@dataclass
class Outcome:
    ok: bool
    code: str            # "ok" | "dryrun" | "http_<n>" | "redirect" | "unreachable" | "config" | ...


def dryrun() -> bool:
    return os.environ.get("JARVIS_HOME_DRYRUN", "").strip().lower() in ("1", "true", "yes")


def confirm_strict() -> bool:
    """Opt-in: a spoken "yes" to a house action must be two deliberate words
    ("sí, hazlo" / "yes, do it"), never one — see `bridge.classify_decision`
    and docs/casa-control.md's residual risk about a hallucinated or
    ambient-audio "yes"."""
    return os.environ.get("JARVIS_HOME_CONFIRM_STRICT", "").strip().lower() in ("1", "true", "yes")


def perform(action: Action) -> Outcome:
    """Do it (or, in dry-run, do nothing). Never raises, never returns a body."""
    try:
        _check_path(action)
    except HomeError:
        return Outcome(False, "not_permitted")
    if dryrun():
        return Outcome(True, "dryrun")
    try:
        base, token = _config()
    except HomeError as e:
        return Outcome(False, "config: " + str(e)[:120])
    data = json.dumps(action.body(), separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(base + action.path, data=data, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json",
        "Accept": "application/json"})
    try:
        with _open(req, TIMEOUT_SEC) as resp:
            resp.read(RESPONSE_MAX_BYTES)
            status = getattr(resp, "status", 200)
        if 200 <= int(status) < 300:
            return Outcome(True, "ok")
        return Outcome(False, f"http_{int(status)}")
    except urllib.error.HTTPError as e:
        if 300 <= e.code <= 399:
            return Outcome(False, "redirect")
        return Outcome(False, f"http_{e.code}")
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return Outcome(False, "unreachable")


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

_CTRL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _audit_path():
    return data_paths.home_actions_log_path()


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


def _plain_params(params) -> dict:
    """Parameters as they may appear in the log: short names, numbers only."""
    if not isinstance(params, dict):
        return {}
    return {_CTRL_RE.sub("?", str(k))[:30]: v for k, v in list(params.items())[:6]
            if isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v)}


def audit(action: Optional[Action], *, confirmed_by: str, result: str,
          entity_id: str = "", service: str = "", params=None) -> None:
    """One JSONL line. Only validated fields, control characters removed, no
    token, no URL, no response body. Without an Action (a refused or an
    expired request) the strings are cleaned and cut instead."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entity": action.entity_id if action else _CTRL_RE.sub("?", entity_id)[:80],
        "service": (f"{action.domain}.{action.service}" if action
                    else _CTRL_RE.sub("?", service)[:80]),
        "params": dict(action.params) if action else _plain_params(params),
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
    """How often actions may be proposed and performed. One per server."""

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

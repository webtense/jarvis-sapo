"""Spoken alerts from Home Assistant, read only and opt-in.

It polls Home Assistant every few seconds with the same read-only client and
the same token as `ha_readonly_mcp.py` (GET `/api/states/<entity>`, nothing
else, no redirects) and, according to the rules in the user's own
`home-alerts.json`, produces short sentences for JARVIS to say:

  * `alarm_triggered`  the alarm went off (the only URGENT one);
  * `person`           a named person arrived home / left home;
  * `open_too_long`    a door or window has been open more than N minutes;
  * `power_above`      consumption above a threshold, in watts.

No file, or `"enabled": false`, and nothing runs. Nothing here can act on the
house: it holds no write credential and has no code that sends a body.

Quiet by construction:
  * An alert is a CHANGE. While the condition stays as it is, it is never
    repeated; it can fire again only after the condition went away and came
    back.
  * Debounce: a condition has to hold for `debounce_s` before it counts (a
    phone's GPS flapping at the edge of the house is not three arrivals).
  * `min_interval_s` per rule and event, and `max_per_hour` for the whole
    watcher (the alarm is exempt from the latter, never from the former).
  * `unavailable` / `unknown` / a failed poll are "no information", never a
    change: Home Assistant restarting does not make everybody "leave".

Names come from the user's file (or are made from the entity id), never from
Home Assistant's friendly names, which are text from third parties.
The sentences are English frames with a Spanish pair in `i18n_es.py`, translated
at the boundary by `i18n.t()`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import data_paths
import ha_readonly_mcp

log = logging.getLogger("jarvis.ha_watch")

CONFIG_MAX_BYTES = 32 * 1024
MAX_RULES = 20
DEFAULT_INTERVAL_S = 30
MIN_INTERVAL_S = 10
MAX_INTERVAL_S = 3600
DEFAULT_MAX_PER_HOUR = 6
MAX_MAX_PER_HOUR = 30

_ID_RE = re.compile(r"[a-z0-9_]{1,30}")
_ENTITY_RE = re.compile(r"[a-z][a-z0-9_]{0,40}\.[a-z0-9_]{1,80}")
_NAME_BAD_RE = re.compile(r"[^\w ,.'\-]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")

TYPES = {
    "alarm_triggered": {"domains": {"alarm_control_panel"}, "debounce": 0, "interval": 60},
    "person": {"domains": {"person"}, "debounce": 90, "interval": 600},
    "open_too_long": {"domains": {"binary_sensor", "cover"}, "debounce": 0, "interval": 1800},
    "power_above": {"domains": {"sensor"}, "debounce": 60, "interval": 1800},
}
_OPEN_STATES = frozenset({"on", "open", "opening"})
_NO_INFO = frozenset({"unavailable", "unknown", "", "none"})

# English frames (each has its Spanish pair in i18n_es.py).
S_ALARM = "The alarm has gone off, sir."
S_ARRIVE = "{name} has arrived home, sir."
S_LEAVE = "{name} has left home, sir."
S_OPEN = "{name} has been open for {minutes} minutes, sir."
S_POWER = "{name} is drawing {value} watts, above your limit, sir."


class ConfigError(Exception):
    """The alerts file is unusable; the message is for the log."""


@dataclass(frozen=True)
class Rule:
    id: str
    type: str
    entity: str
    name: str
    debounce_s: float
    min_interval_s: float
    events: frozenset = frozenset()       # person: {"arrive", "leave"}
    minutes: int = 0                      # open_too_long
    above_w: float = 0.0                  # power_above


@dataclass(frozen=True)
class Config:
    interval_s: float
    max_per_hour: int
    rules: tuple

    @property
    def entities(self) -> list[str]:
        seen: list[str] = []
        for r in self.rules:
            if r.entity not in seen:
                seen.append(r.entity)
        return seen


@dataclass(frozen=True)
class Reading:
    """What one poll learned about one entity. Nothing else is kept."""
    state: str
    last_changed: Optional[float] = None      # epoch seconds
    unit: str = ""


@dataclass(frozen=True)
class Alert:
    rule_id: str
    text: str                  # an English frame, already filled: i18n.t() it
    urgent: bool = False


def _clean_name(raw, fallback: str) -> str:
    text = _NAME_BAD_RE.sub(" ", str(raw or ""))
    text = _SPACE_RE.sub(" ", text).strip(" .,'-")[:60].strip()
    return text or fallback


def _num(value, lo: float, hi: float, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or not lo <= value <= hi:
        raise ConfigError(f"{what} must be a number from {lo:g} to {hi:g}.")
    return float(value)


def parse_config(data) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("home-alerts.json must be a JSON object.")
    if data.get("enabled") is not True:
        raise ConfigError("home-alerts.json has not been switched on (enabled: true).")
    interval = _num(data.get("interval_s", DEFAULT_INTERVAL_S), MIN_INTERVAL_S,
                    MAX_INTERVAL_S, "interval_s")
    per_hour = int(_num(data.get("max_per_hour", DEFAULT_MAX_PER_HOUR), 1,
                        MAX_MAX_PER_HOUR, "max_per_hour"))
    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ConfigError("home-alerts.json has no rules.")
    if len(raw_rules) > MAX_RULES:
        raise ConfigError(f"At most {MAX_RULES} rules.")
    rules, ids = [], set()
    for raw in raw_rules:
        if not isinstance(raw, dict):
            raise ConfigError("Every rule must be an object.")
        rid = raw.get("id")
        if not isinstance(rid, str) or not _ID_RE.fullmatch(rid) or rid in ids:
            raise ConfigError("Every rule needs its own id (a-z, 0-9, _).")
        ids.add(rid)
        kind = raw.get("type")
        spec = TYPES.get(kind)
        if spec is None:
            raise ConfigError(f"Rule {rid}: unknown type.")
        entity = raw.get("entity")
        if (not isinstance(entity, str) or not entity.isascii()
                or not _ENTITY_RE.fullmatch(entity)
                or entity.split(".", 1)[0] not in spec["domains"]):
            raise ConfigError(f"Rule {rid}: the entity must be a "
                              f"{'/'.join(sorted(spec['domains']))}.")
        fallback = entity.split(".", 1)[1].replace("_", " ")
        debounce = _num(raw.get("debounce_s", spec["debounce"]), 0, 3600, f"{rid}.debounce_s")
        every = _num(raw.get("min_interval_s", spec["interval"]), 30, 86400,
                     f"{rid}.min_interval_s")
        events, minutes, above = frozenset(), 0, 0.0
        if kind == "person":
            ev = raw.get("events", ["arrive", "leave"])
            if (not isinstance(ev, list) or not ev
                    or any(e not in ("arrive", "leave") for e in ev)):
                raise ConfigError(f"Rule {rid}: events are arrive and/or leave.")
            events = frozenset(ev)
        elif kind == "open_too_long":
            minutes = int(_num(raw.get("minutes", 10), 2, 1440, f"{rid}.minutes"))
        elif kind == "power_above":
            above = _num(raw.get("above_w"), 1, 1_000_000, f"{rid}.above_w")
        rules.append(Rule(rid, kind, entity, _clean_name(raw.get("name"), fallback),
                          debounce, every, events, minutes, above))
    return Config(interval, per_hour, tuple(rules))


def load_config(path=None) -> Config:
    """The user's rules, or `ConfigError` (also when there is no file)."""
    path = str(path or data_paths.home_alerts_path())
    try:
        if os.path.getsize(path) > CONFIG_MAX_BYTES:
            raise ConfigError("home-alerts.json is too large.")
        with open(path, "rb") as fh:
            data = json.loads(fh.read(CONFIG_MAX_BYTES).decode("utf-8"))
    except FileNotFoundError:
        raise ConfigError("There is no home-alerts.json: alerts are off.") from None
    except (OSError, ValueError):
        raise ConfigError("home-alerts.json cannot be read as JSON.") from None
    return parse_config(data)


# ---------------------------------------------------------------------------
# The decisions (pure: a clock and readings in, alerts out)
# ---------------------------------------------------------------------------

@dataclass
class _State:
    confirmed: Optional[bool] = None
    pending: Optional[bool] = None
    pending_since: float = 0.0
    last_fire: dict = field(default_factory=dict)       # event -> time


def _watts(reading: Reading) -> Optional[float]:
    try:
        value = float(reading.state)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    unit = reading.unit.strip().lower()
    if unit == "w":
        return value
    if unit == "kw":
        return value * 1000.0
    return None                         # unknown unit: never guess


class Watcher:
    def __init__(self, config: Config, clock: Callable[[], float] = time.time):
        self.config = config
        self._clock = clock
        self._states = {r.id: _State() for r in config.rules}
        self._fired: deque[float] = deque()             # for max_per_hour
        self._latched_power: dict[str, bool] = {}

    # -- one rule's condition: True / False / None (no information) ----------
    def _condition(self, rule: Rule, reading: Optional[Reading], now: float) -> Optional[bool]:
        if reading is None or reading.state.strip().lower() in _NO_INFO:
            return None
        state = reading.state.strip().lower()
        if rule.type == "alarm_triggered":
            return state == "triggered"
        if rule.type == "person":
            return state == "home"
        if rule.type == "open_too_long":
            if state not in _OPEN_STATES:
                return False
            since = reading.last_changed if reading.last_changed is not None else now
            return now - since >= rule.minutes * 60
        if rule.type == "power_above":
            watts = _watts(reading)
            if watts is None:
                return None
            if watts > rule.above_w:
                return True
            # Hysteresis: it only counts as "back to normal" below 90 %.
            return False if watts < rule.above_w * 0.9 else self._states[rule.id].confirmed
        return None

    def observe(self, readings: dict[str, Optional[Reading]]) -> list[Alert]:
        now = self._clock()
        while self._fired and now - self._fired[0] >= 3600:
            self._fired.popleft()
        out: list[Alert] = []
        for rule in self.config.rules:
            st = self._states[rule.id]
            cond = self._condition(rule, readings.get(rule.entity), now)
            if cond is None:
                st.pending = None
                continue
            if st.confirmed is None:
                # First sight. A person's position is a baseline, not news;
                # everything else is judged from the start (an alarm that is
                # already going off, a door that has been open for an hour).
                if rule.type == "person":
                    st.confirmed = cond
                    continue
                st.confirmed = False
            if cond == st.confirmed:
                st.pending = None
                continue
            if st.pending != cond:
                st.pending, st.pending_since = cond, now
            if now - st.pending_since < rule.debounce_s:
                continue
            st.confirmed, st.pending = cond, None
            event = self._event(rule, cond)
            if event is None:
                continue
            if rule.type == "person" and event not in rule.events:
                continue
            if now - st.last_fire.get(event, -1e12) < rule.min_interval_s:
                continue
            urgent = rule.type == "alarm_triggered"
            if not urgent and len(self._fired) >= self.config.max_per_hour:
                log.info("ha_watch: hourly limit reached; dropping %s", rule.id)
                continue
            st.last_fire[event] = now
            if not urgent:
                self._fired.append(now)
            out.append(Alert(rule.id, self._text(rule, event, readings.get(rule.entity)), urgent))
        return out

    @staticmethod
    def _event(rule: Rule, cond: bool) -> Optional[str]:
        if rule.type == "person":
            return "arrive" if cond else "leave"
        return "on" if cond else None

    @staticmethod
    def _text(rule: Rule, event: str, reading: Optional[Reading]) -> str:
        if rule.type == "alarm_triggered":
            return S_ALARM
        if rule.type == "person":
            return (S_ARRIVE if event == "arrive" else S_LEAVE).format(name=rule.name)
        if rule.type == "open_too_long":
            return S_OPEN.format(name=rule.name, minutes=rule.minutes)
        watts = _watts(reading) if reading is not None else None
        return S_POWER.format(name=rule.name,
                              value=round(watts if watts is not None else rule.above_w))


# ---------------------------------------------------------------------------
# Reading Home Assistant (the read-only client) and the loop
# ---------------------------------------------------------------------------

class PollFailed(Exception):
    """Home Assistant could not be asked this time."""


def _parse_time(text) -> Optional[float]:
    if not isinstance(text, str):
        return None
    from datetime import datetime, timezone
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


def fetch_readings(entities: list[str]) -> dict[str, Optional[Reading]]:
    """GET each entity once. Blocking: run it in a thread. A missing entity
    (404) is "no information"; anything else wrong with Home Assistant fails
    the whole poll."""
    out: dict[str, Optional[Reading]] = {}
    for entity in entities:
        try:
            data = ha_readonly_mcp._get(f"/api/states/{entity}")
        except ha_readonly_mcp.HAError as e:
            if "404" in str(e):
                out[entity] = None
                continue
            raise PollFailed(str(e)) from None
        if not isinstance(data, dict):
            out[entity] = None
            continue
        attrs = data.get("attributes") if isinstance(data.get("attributes"), dict) else {}
        out[entity] = Reading(str(data.get("state", ""))[:60],
                              _parse_time(data.get("last_changed")),
                              str(attrs.get("unit_of_measurement", ""))[:12])
    return out


Fetch = Callable[[list[str]], Awaitable[dict]]
Deliver = Callable[[Alert], Awaitable[None]]


async def _fetch_in_thread(entities: list[str]) -> dict:
    return await asyncio.to_thread(fetch_readings, entities)


async def poll_once(watcher: Watcher, fetch: Fetch, deliver: Deliver) -> bool:
    """One round. False when Home Assistant could not be read (nothing said)."""
    try:
        readings = await fetch(watcher.config.entities)
    except PollFailed:
        return False
    for alert in watcher.observe(readings):
        try:
            await deliver(alert)
        except Exception:                          # a failed delivery never stops the watch
            log.warning("ha_watch: delivering an alert failed", exc_info=True)
    return True


async def run(watcher: Watcher, deliver: Deliver, *, fetch: Fetch = _fetch_in_thread,
              sleep=asyncio.sleep) -> None:
    """The loop. Failures are logged on the first and then every 20th, so a
    Home Assistant that is down does not fill the log, and never spoken."""
    failures = 0
    while True:
        try:
            ok = await poll_once(watcher, fetch, deliver)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("ha_watch: poll crashed", exc_info=True)
            ok = False
        if ok:
            if failures:
                log.info("ha_watch: Home Assistant is back after %d failed polls", failures)
            failures = 0
        else:
            failures += 1
            if failures == 1 or failures % 20 == 0:
                log.warning("ha_watch: Home Assistant could not be read (%d in a row)", failures)
        await sleep(watcher.config.interval_s)

"""Croac's messages (the SapoFit coach), spoken by JARVIS. Read only, opt-in.

Every JARVIS_SAPOFIT_POLL_SEC seconds (60 by default, never under 30) it asks
SapoFit's coach feed for what is new since the last cursor, and says the new
messages out loud in a fixed frame: "Croac says: ...". The frame is the whole
point: what Croac wrote is a language model's text, quoted, never an instruction.
Nothing it says is given to the brain, and nothing here can answer Croac or act
in SapoFit: the only request this module makes is the GET in
`sapofit_readonly_mcp.coach_feed`.

Quiet by construction, the same way ha_watch.py is:
  * the cursor is saved in data/jarvis/sapofit-cursor.json (600, atomic). A
    message id already spoken is never spoken twice;
  * the FIRST poll with no cursor only records where the feed is. It says
    nothing, so a fresh install does not recite the history;
  * at most `max_per_hour` spoken lines in any hour, and at least two minutes
    between two of them (a burst becomes one summary line, not a stream);
  * quiet hours (23:00 to 08:00 by default): nothing is said, the new messages
    are only counted, and the first poll after the quiet hours says ONE summary
    line ("Croac has 3 new messages for you, sir. They are in the app.");
  * a long message is cut to its first sentences and the user is told the rest
    is in the app;
  * the priority is NOT urgent: the sentence waits for a pause in the speech
    (`Priority.LOW`), it never interrupts the user. Without a browser tab it
    goes out as a desktop notification instead, like every other fallback;
  * SapoFit unreachable or a token refused: one warning in the log, then a
    retry that waits longer each time (up to an hour). Nothing is said.

The file it reads is the user's own, `sapofit-alerts.json`:
{"enabled": true, "kinds": [...], "poll_s": 60, "max_per_hour": 6,
 "quiet_hours": {"from": "23:00", "to": "08:00"}, "max_chars_spoken": 400}.
With no file, or `enabled` not literally true, nothing runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable, Optional

import data_paths
import sapofit_readonly_mcp as sr

log = logging.getLogger("jarvis.sapofit_watch")

CONFIG_MAX_BYTES = 16 * 1024
CURSOR_MAX_BYTES = 64 * 1024
DEFAULT_KINDS = ("WORKOUT_ANALYSIS", "DAILY_SUMMARY", "COACH_NUDGE")
DEFAULT_POLL_S = 60
MIN_POLL_S = 30
MAX_POLL_S = 3600
DEFAULT_MAX_PER_HOUR = 6
MAX_MAX_PER_HOUR = 30
DEFAULT_MAX_CHARS = 400
MIN_MAX_CHARS = 80
MAX_MAX_CHARS = 1000
MIN_GAP_S = 120
SEEN_KEEP = 200
BACKOFF_MAX_S = 3600
ENV_POLL = "JARVIS_SAPOFIT_POLL_SEC"
_HHMM_RE = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

# English frames. Each has its Spanish pair in i18n_es.py; the server passes
# them through i18n.t() at the boundary.
S_CROAC = "Croac says: {text}"
S_REST = "The rest is in the app, sir."
S_PENDING_ONE = "Croac has one new message for you, sir. It is in the app."
S_PENDING_MANY = "Croac has {n} new messages for you, sir. They are in the app."


class ConfigError(Exception):
    """The settings file is unusable; the message is for the log."""


@dataclass(frozen=True)
class Quiet:
    start: int          # minutes after midnight
    end: int


@dataclass(frozen=True)
class Config:
    kinds: tuple
    poll_s: int
    max_per_hour: int
    quiet: Optional[Quiet]
    max_chars: int


def _hhmm(value) -> int:
    if not isinstance(value, str) or not _HHMM_RE.fullmatch(value):
        raise ConfigError("quiet_hours must be HH:MM times.")
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _int(value, default: int, low: int, high: int, what: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ConfigError(f"{what} must be a whole number from {low} to {high}.")
    return value


def parse_config(data) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("sapofit-alerts.json must be a JSON object.")
    if data.get("enabled") is not True:
        raise ConfigError("sapofit-alerts.json has not been switched on (enabled: true).")
    raw_kinds = data.get("kinds", list(DEFAULT_KINDS))
    if not isinstance(raw_kinds, list) or not raw_kinds or len(raw_kinds) > 12:
        raise ConfigError("kinds must be a list of 1 to 12 kinds.")
    kinds = []
    for kind in raw_kinds:
        if not isinstance(kind, str) or not (kind in sr.KNOWN_KINDS or sr.REMINDER_KIND_RE.fullmatch(kind)):
            raise ConfigError("kinds has an unknown kind.")
        if kind not in kinds:
            kinds.append(kind)
    poll = _int(data.get("poll_s"), DEFAULT_POLL_S, MIN_POLL_S, MAX_POLL_S, "poll_s")
    per_hour = _int(data.get("max_per_hour"), DEFAULT_MAX_PER_HOUR, 1, MAX_MAX_PER_HOUR, "max_per_hour")
    max_chars = _int(data.get("max_chars_spoken"), DEFAULT_MAX_CHARS, MIN_MAX_CHARS,
                     MAX_MAX_CHARS, "max_chars_spoken")
    quiet = None
    if "quiet_hours" not in data:
        quiet = Quiet(_hhmm("23:00"), _hhmm("08:00"))
    elif data["quiet_hours"] is not None:
        block = data["quiet_hours"]
        if not isinstance(block, dict):
            raise ConfigError("quiet_hours must be an object with from and to.")
        start, end = _hhmm(block.get("from")), _hhmm(block.get("to"))
        if start != end:
            quiet = Quiet(start, end)
    return Config(tuple(kinds), poll, per_hour, quiet, max_chars)


def load_config(path=None) -> Config:
    """The user's settings, or `ConfigError` (also when there is no file)."""
    path = str(path or data_paths.sapofit_alerts_path())
    try:
        if os.path.getsize(path) > CONFIG_MAX_BYTES:
            raise ConfigError("sapofit-alerts.json is too large.")
        with open(path, "rb") as fh:
            data = json.loads(fh.read(CONFIG_MAX_BYTES).decode("utf-8"))
    except FileNotFoundError:
        raise ConfigError("There is no sapofit-alerts.json: the coach watch is off.") from None
    except (OSError, ValueError):
        raise ConfigError("sapofit-alerts.json cannot be read as JSON.") from None
    return parse_config(data)


def effective_poll_s(config: Config) -> int:
    """The environment wins over the file; never under MIN_POLL_S."""
    raw = os.environ.get(ENV_POLL, "").strip()
    try:
        value = int(raw) if raw else config.poll_s
    except ValueError:
        value = config.poll_s
    return max(MIN_POLL_S, min(value, MAX_POLL_S))


def in_quiet(quiet: Optional[Quiet], now: datetime) -> bool:
    if quiet is None:
        return False
    minute = now.hour * 60 + now.minute
    if quiet.start < quiet.end:
        return quiet.start <= minute < quiet.end
    return minute >= quiet.start or minute < quiet.end


def cut_for_speech(text: str, limit: int) -> tuple[str, bool]:
    """(text, was_cut): the first sentences that fit, or a hard cut."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text, False
    out = ""
    for sentence in _SENTENCE_END.split(text):
        candidate = (out + " " + sentence).strip()
        if len(candidate) > limit:
            break
        out = candidate
    if not out:
        out = text[:limit - 1].rstrip() + "…"
    return out, True


# --- the cursor file ------------------------------------------------------------

@dataclass
class State:
    cursor: Optional[str] = None
    seen: list = field(default_factory=list)
    pending: int = 0
    last_read_at: Optional[float] = None


def load_state(path=None) -> State:
    """The saved cursor, or an empty state (which means: a first run) when the
    file is missing, too big or not understood. Never raises."""
    path = str(path or data_paths.sapofit_cursor_path())
    try:
        if os.path.getsize(path) > CURSOR_MAX_BYTES:
            return State()
        with open(path, "rb") as fh:
            data = json.loads(fh.read(CURSOR_MAX_BYTES).decode("utf-8"))
    except (OSError, ValueError):
        return State()
    if not isinstance(data, dict):
        return State()
    cursor = data.get("cursor")
    if not (isinstance(cursor, str) and sr.CURSOR_RE.fullmatch(cursor)):
        cursor = None
    seen = [s for s in (data.get("seen") or []) if isinstance(s, str)][-SEEN_KEEP:]
    pending = data.get("pending")
    pending = pending if isinstance(pending, int) and not isinstance(pending, bool) and 0 <= pending <= 10000 else 0
    last = data.get("last_read_at")
    last = float(last) if isinstance(last, (int, float)) and not isinstance(last, bool) else None
    return State(cursor, seen, pending, last)


def save_state(state: State, path=None) -> bool:
    """Atomic, owner-only. Returns False (and logs) instead of raising."""
    path = str(path or data_paths.sapofit_cursor_path())
    doc = {"cursor": state.cursor, "seen": state.seen[-SEEN_KEEP:],
           "pending": state.pending, "last_read_at": state.last_read_at}
    tmp = None
    try:
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".sapofit_cursor_")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        return True
    except OSError:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        log.warning("sapofit_watch: could not save the cursor")
        return False


# --- the decisions (pure: a clock and the new items in, sentences out) ----------

class Watcher:
    def __init__(self, config: Config, state: State, *,
                 clock: Callable[[], float] = time.time,
                 local_now: Callable[[], datetime] = datetime.now,
                 prepare: Callable[[str], str] = lambda text: text):
        self.config = config
        self.state = state
        self._clock = clock
        self._local_now = local_now
        self._prepare = prepare
        self._spoken: list[float] = []            # times of spoken lines, last hour

    def _may_speak(self, now: float) -> bool:
        self._spoken = [t for t in self._spoken if now - t < 3600]
        if len(self._spoken) >= self.config.max_per_hour:
            return False
        return not self._spoken or now - self._spoken[-1] >= MIN_GAP_S

    def _speak_item(self, item: dict) -> str:
        cut_text, was_cut = cut_for_speech(self._prepare(item["text"]), self.config.max_chars)
        if was_cut:
            cut_text = cut_text + " " + S_REST
        return S_CROAC.format(text=cut_text)

    def observe(self, items: list[dict]) -> list[str]:
        """Sentences to say now (English frames). Updates the state."""
        now = self._clock()
        st = self.state
        seen = set(st.seen)
        fresh = []
        for item in items:
            item_id = item.get("id")
            if item_id in seen:
                continue
            seen.add(item_id)
            st.seen.append(item_id)
            if item.get("kind") in self.config.kinds:
                fresh.append(item)
        st.seen = st.seen[-SEEN_KEEP:]
        if not fresh:
            if st.pending and not in_quiet(self.config.quiet, self._local_now()) and self._may_speak(now):
                count, st.pending = st.pending, 0
                self._spoken.append(now)
                return [S_PENDING_ONE if count == 1 else S_PENDING_MANY.format(n=count)]
            return []
        if in_quiet(self.config.quiet, self._local_now()) or not self._may_speak(now):
            st.pending += len(fresh)
            return []
        if st.pending:
            count, st.pending = st.pending + len(fresh), 0
            self._spoken.append(now)
            return [S_PENDING_ONE if count == 1 else S_PENDING_MANY.format(n=count)]
        self._spoken.append(now)
        st.pending += len(fresh) - 1
        return [self._speak_item(fresh[0])]


# --- the loop -----------------------------------------------------------------

Fetch = Callable[[Optional[str], tuple], Awaitable[tuple]]
Deliver = Callable[[str], Awaitable[None]]


async def _fetch_in_thread(since: Optional[str], kinds: tuple) -> tuple:
    return await asyncio.to_thread(sr.coach_feed, since, kinds)


async def poll_once(watcher: Watcher, fetch: Fetch, deliver: Deliver, *, state_path=None) -> bool:
    """One round. False when SapoFit could not be read (nothing said)."""
    st = watcher.state
    items, cursor = await fetch(st.cursor, watcher.config.kinds)
    if st.cursor is None:
        # First run: record where the feed is, and say nothing.
        st.seen = [i.get("id") for i in items if isinstance(i.get("id"), str)][-SEEN_KEEP:]
        st.cursor = cursor or st.cursor
        st.last_read_at = watcher._clock()
        save_state(st, state_path)
        return True
    if cursor:
        st.cursor = cursor
    for sentence in watcher.observe(items):
        try:
            await deliver(sentence)
        except Exception:                          # a failed delivery never stops the watch
            log.warning("sapofit_watch: delivering a message failed", exc_info=True)
    st.last_read_at = watcher._clock()
    save_state(st, state_path)
    return True


async def run(watcher: Watcher, deliver: Deliver, *, fetch: Fetch = _fetch_in_thread,
              sleep=asyncio.sleep, state_path=None) -> None:
    """The loop. Failures are logged on the first and then every 20th, so a
    SapoFit that is down does not fill the log, and nothing is spoken."""
    failures = 0
    poll = effective_poll_s(watcher.config)
    while True:
        try:
            await poll_once(watcher, fetch, deliver, state_path=state_path)
            if failures:
                log.info("sapofit_watch: SapoFit is back after %d failed polls", failures)
            failures = 0
            wait = poll
        except asyncio.CancelledError:
            raise
        except sr.SapoFitError as e:
            failures += 1
            if failures == 1 or failures % 20 == 0:
                log.warning("sapofit_watch: %s (%d in a row)", e, failures)
            wait = min(BACKOFF_MAX_S, poll * (2 ** min(failures, 6)))
        except Exception:
            failures += 1
            log.warning("sapofit_watch: poll crashed", exc_info=True)
            wait = min(BACKOFF_MAX_S, poll * (2 ** min(failures, 6)))
        await sleep(wait)

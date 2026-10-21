"""The resort filter: entities `ha_readonly_mcp.py`, `ha_watch.py` and
`home_actions.py` must act as if they did not exist.

The user's Home Assistant mixes his own home with roughly 1,400 entities that
belong to his employer's resort (rooms, boilers, automations...). This module
is a small, dependency-free gate the three consumers share, so the rule lives
in one place: `data/jarvis/ha-exclude.json` (or `HA_EXCLUDE_FILE`), a JSON
object of the shape::

    {"entity_ids": ["light.habitacion_101"],
     "patterns": ["sensor\\.hab_[0-9]+_.*"],
     "domains": ["climate"]}

All three keys are optional and default to empty. `entity_ids` are compared
EXACTLY, lower-case, byte for byte — nothing here normalises an id to make it
match, and nothing normalises one to make it NOT match either. `patterns` are
matched with `re.fullmatch`, never `match` or `search` (a `$`-anchored
`.match()` can be fooled by a trailing newline; `fullmatch` needs no anchors
and cannot be). `domains` match the whole domain (`light`, not `light.foo`).

FAIL CLOSED: if `HA_EXCLUDE_FILE` is set, or the default path exists, but the
file cannot be read, is not valid JSON, is the wrong shape, or is readable by
group or others, `load()` raises `ExcludeFileInvalid` and every consumer must
show NOTHING rather than guess. With no variable and no file at the default
path, the filter is empty and behaviour is exactly as if this module did not
exist.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

MAX_BYTES = 2 * 1024 * 1024       # ~1,400 entities fits comfortably under 2 MB
MAX_ENTITY_IDS = 20_000
MAX_PATTERNS = 500
MAX_DOMAINS = 200
MAX_PATTERN_LEN = 200              # a pattern longer than this is refused, not run
PATTERN_TIMEOUT_PROBES = 2000      # see _safe_pattern: a cheap ReDoS tripwire

_ENTITY_ID_RE = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+")
_DOMAIN_RE = re.compile(r"[a-z0-9_]+")
# Characters that commonly build catastrophic backtracking: nested
# quantifiers, alternation inside a repeated group. This is a conservative
# tripwire, not a general ReDoS detector; combined with MAX_PATTERN_LEN and a
# compile-time probe it is enough to refuse a pathological pattern rather
# than hang the process evaluating one.
_SUSPICIOUS_RE = re.compile(r"(\([^)]*[+*][^)]*\)[+*])|(\{[0-9]+,\}.*\{[0-9]+,\})")


class ExcludeFileInvalid(Exception):
    """`HA_EXCLUDE_FILE` (or the default path) exists but cannot be trusted.
    The message is safe to show: it never names the path or quotes content."""


@dataclass(frozen=True)
class ExcludeSet:
    entity_ids: frozenset = field(default_factory=frozenset)
    patterns: tuple = field(default_factory=tuple)      # compiled re.Pattern
    domains: frozenset = field(default_factory=frozenset)

    def is_excluded(self, entity_id: str) -> bool:
        """Exact match only. `entity_id` must already be lower-case, exactly
        as the caller intends to look it up — this never folds case, strips
        whitespace beyond a trivial strip, or otherwise "helps" an id match
        that would not match a Home Assistant lookup."""
        if not isinstance(entity_id, str) or not entity_id:
            return False
        if entity_id in self.entity_ids:
            return True
        domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
        if domain and domain in self.domains:
            return True
        for pattern in self.patterns:
            if pattern.fullmatch(entity_id):
                return True
        return False


EMPTY = ExcludeSet()


def _default_path() -> Optional[Path]:
    try:
        import data_paths
        path = data_paths.ha_exclude_path()
    except Exception:                                   # pragma: no cover
        return None
    return path


def _safe_pattern(text: str) -> re.Pattern:
    """A compiled pattern, or `ExcludeFileInvalid` for anything that looks
    like it could run away. Length-capped and screened for the shapes that
    build catastrophic backtracking before it is ever matched against real
    input; `re.fullmatch` elsewhere never runs it against attacker data
    anyway (entity ids are short and code-controlled), but a corrupt or
    hostile exclude file should fail to LOAD, not fail to MATCH."""
    if not isinstance(text, str) or not text or len(text) > MAX_PATTERN_LEN:
        raise ExcludeFileInvalid("the exclusion file is not valid")
    if _SUSPICIOUS_RE.search(text):
        raise ExcludeFileInvalid("the exclusion file is not valid")
    try:
        compiled = re.compile(text)
    except re.error:
        raise ExcludeFileInvalid("the exclusion file is not valid") from None
    # A cheap, deterministic tripwire: fullmatch against a handful of inputs
    # engineered to be slow for classic catastrophic patterns (long runs of a
    # repeated character with no terminator). If this does not return near
    # instantly against short strings the pattern is not safe to keep.
    probe = "a" * 64 + "!"
    try:
        compiled.fullmatch(probe)
    except Exception:
        raise ExcludeFileInvalid("the exclusion file is not valid") from None
    return compiled


def _parse(data) -> ExcludeSet:
    if not isinstance(data, dict):
        raise ExcludeFileInvalid("the exclusion file is not valid")
    entity_ids_raw = data.get("entity_ids", [])
    patterns_raw = data.get("patterns", [])
    domains_raw = data.get("domains", [])
    if not isinstance(entity_ids_raw, list) or len(entity_ids_raw) > MAX_ENTITY_IDS:
        raise ExcludeFileInvalid("the exclusion file is not valid")
    if not isinstance(patterns_raw, list) or len(patterns_raw) > MAX_PATTERNS:
        raise ExcludeFileInvalid("the exclusion file is not valid")
    if not isinstance(domains_raw, list) or len(domains_raw) > MAX_DOMAINS:
        raise ExcludeFileInvalid("the exclusion file is not valid")

    entity_ids = set()
    for raw in entity_ids_raw:
        if not isinstance(raw, str) or not raw.isascii() or not _ENTITY_ID_RE.fullmatch(raw):
            raise ExcludeFileInvalid("the exclusion file is not valid")
        entity_ids.add(raw)

    domains = set()
    for raw in domains_raw:
        if not isinstance(raw, str) or not raw.isascii() or not _DOMAIN_RE.fullmatch(raw):
            raise ExcludeFileInvalid("the exclusion file is not valid")
        domains.add(raw)

    patterns = tuple(_safe_pattern(raw) for raw in patterns_raw)

    return ExcludeSet(frozenset(entity_ids), patterns, frozenset(domains))


def load(path=None) -> ExcludeSet:
    """The exclusion set to apply.

    FAIL CLOSED semantics live here, once, for every caller:

    * `HA_EXCLUDE_FILE` set (or `path` given) -> that file MUST be valid.
      Anything wrong with it raises `ExcludeFileInvalid`; callers must then
      show nothing, never fall back to "no filter".
    * No variable and no `path`, but the default
      `<data dir>/jarvis/ha-exclude.json` exists -> same rule, it must be
      valid.
    * Nothing set and nothing at the default path -> `EMPTY`: behaviour is
      identical to this module not existing.
    """
    explicit = path is not None
    if path is None:
        env = os.environ.get("HA_EXCLUDE_FILE", "").strip()
        if env:
            path = env
            explicit = True
        else:
            default = _default_path()
            if default is not None and default.is_file():
                path = default
    if path is None:
        return EMPTY
    path = str(path)
    try:
        st = os.stat(path)
    except OSError:
        if explicit:
            raise ExcludeFileInvalid("the exclusion file is not valid") from None
        return EMPTY
    if not stat.S_ISREG(st.st_mode):
        raise ExcludeFileInvalid("the exclusion file is not valid")
    if st.st_mode & 0o077:
        raise ExcludeFileInvalid("the exclusion file is not valid")
    if st.st_size > MAX_BYTES:
        raise ExcludeFileInvalid("the exclusion file is not valid")
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_BYTES + 1)
    except OSError:
        raise ExcludeFileInvalid("the exclusion file is not valid") from None
    if len(raw) > MAX_BYTES:
        raise ExcludeFileInvalid("the exclusion file is not valid")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ExcludeFileInvalid("the exclusion file is not valid") from None
    return _parse(data)


def is_excluded(entity_id: str, excl: Optional[ExcludeSet] = None) -> bool:
    """Convenience wrapper: `load()` once and reuse it per caller — this
    does not cache or re-read the file, callers decide that lifecycle."""
    return (excl if excl is not None else load()).is_excluded(entity_id)

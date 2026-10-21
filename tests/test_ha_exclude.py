"""ha_exclude: the resort filter shared by ha_readonly_mcp, ha_watch and
home_actions. No network, no real Home Assistant; everything here is a fake
file under an isolated JARVIS_DATA_DIR.
"""
import json
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import data_paths  # noqa: E402
import ha_exclude  # noqa: E402


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("HA_EXCLUDE_FILE", raising=False)
    import importlib
    importlib.reload(data_paths)
    return tmp_path


def _write(path: Path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(mode)
    return path


# --- default behaviour: nothing set, nothing excluded ----------------------

def test_with_no_variable_and_no_default_file_nothing_is_excluded(isolated):
    excl = ha_exclude.load()
    assert excl is ha_exclude.EMPTY
    assert not excl.is_excluded("light.anything")


def test_default_path_is_under_the_brain_home(isolated):
    assert data_paths.ha_exclude_path() == data_paths.brain_home() / "ha-exclude.json"


# --- exact matching, no normalisation ---------------------------------------

def test_entity_ids_match_exactly(isolated):
    _write(data_paths.ha_exclude_path(), {"entity_ids": ["light.habitacion_101"]})
    excl = ha_exclude.load()
    assert excl.is_excluded("light.habitacion_101")
    assert not excl.is_excluded("light.habitacion_102")


def test_uppercase_does_not_match_a_lowercase_entry(isolated):
    """Comparison is exact: nothing here folds case to make an id match, and
    nothing folds it to make one NOT match either."""
    _write(data_paths.ha_exclude_path(), {"entity_ids": ["light.habitacion_101"]})
    excl = ha_exclude.load()
    assert not excl.is_excluded("LIGHT.HABITACION_101")
    assert not excl.is_excluded("Light.Habitacion_101")


def test_a_path_with_dot_dot_does_not_coincidentally_match(isolated):
    _write(data_paths.ha_exclude_path(), {"entity_ids": ["light.habitacion_101"]})
    excl = ha_exclude.load()
    assert not excl.is_excluded("light.habitacion_101/../climate.salon")
    assert not excl.is_excluded("../light.habitacion_101")


def test_a_homoglyph_does_not_match(isolated):
    """A Cyrillic 'а' (U+0430) looks like Latin 'a' but is a different byte;
    nothing here normalises Unicode to paper over that."""
    cyrillic = "light.hаbitacion_101"          # Cyrillic а in "habitacion"
    assert cyrillic != "light.habitacion_101"
    _write(data_paths.ha_exclude_path(), {"entity_ids": ["light.habitacion_101"]})
    excl = ha_exclude.load()
    assert not excl.is_excluded(cyrillic)


def test_entity_ids_in_the_file_that_are_not_valid_shapes_are_refused(isolated):
    for bad in (["LIGHT.salon"], ["light..salon"], ["light"], ["light.sal/on"],
                ["light.hаbitacion"]):
        _write(data_paths.ha_exclude_path(), {"entity_ids": bad})
        with pytest.raises(ha_exclude.ExcludeFileInvalid):
            ha_exclude.load()


# --- patterns ----------------------------------------------------------------

def test_a_pattern_matches_with_fullmatch(isolated):
    _write(data_paths.ha_exclude_path(),
          {"patterns": [r"sensor\.hab_[0-9]+_temperatura"]})
    excl = ha_exclude.load()
    assert excl.is_excluded("sensor.hab_101_temperatura")
    assert not excl.is_excluded("sensor.hab_101_temperatura_extra")
    assert not excl.is_excluded("xsensor.hab_101_temperatura")


def test_a_pattern_that_is_too_long_is_refused(isolated):
    long_pattern = "a" * (ha_exclude.MAX_PATTERN_LEN + 1)
    _write(data_paths.ha_exclude_path(), {"patterns": [long_pattern]})
    with pytest.raises(ha_exclude.ExcludeFileInvalid):
        ha_exclude.load()


def test_an_invalid_regex_is_refused(isolated):
    _write(data_paths.ha_exclude_path(), {"patterns": ["("]})
    with pytest.raises(ha_exclude.ExcludeFileInvalid):
        ha_exclude.load()


def test_a_pathological_looking_pattern_is_refused_not_run(isolated):
    """A classic catastrophic-backtracking shape: refused at load time rather
    than evaluated, however short the input would be."""
    _write(data_paths.ha_exclude_path(), {"patterns": [r"(a+)+$"]})
    with pytest.raises(ha_exclude.ExcludeFileInvalid):
        ha_exclude.load()


def test_too_many_patterns_is_refused(isolated):
    _write(data_paths.ha_exclude_path(),
          {"patterns": ["a"] * (ha_exclude.MAX_PATTERNS + 1)})
    with pytest.raises(ha_exclude.ExcludeFileInvalid):
        ha_exclude.load()


# --- domains -------------------------------------------------------------------

def test_a_domain_excludes_the_whole_domain(isolated):
    _write(data_paths.ha_exclude_path(), {"domains": ["climate"]})
    excl = ha_exclude.load()
    assert excl.is_excluded("climate.habitacion_101")
    assert excl.is_excluded("climate.anything_else")
    assert not excl.is_excluded("light.habitacion_101")


def test_a_domain_with_a_dot_in_it_is_refused(isolated):
    _write(data_paths.ha_exclude_path(), {"domains": ["climate.habitacion"]})
    with pytest.raises(ha_exclude.ExcludeFileInvalid):
        ha_exclude.load()


# --- fail closed ---------------------------------------------------------------

def test_an_unreadable_file_at_the_default_path_fails_closed(isolated):
    path = data_paths.ha_exclude_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}")
    path.chmod(0o000)
    try:
        with pytest.raises(ha_exclude.ExcludeFileInvalid):
            ha_exclude.load()
    finally:
        path.chmod(0o600)                  # tidy up so cleanup can remove it


def test_broken_json_fails_closed(isolated):
    _write(data_paths.ha_exclude_path(), {})      # placeholder, overwritten next
    data_paths.ha_exclude_path().write_text("{not json")
    with pytest.raises(ha_exclude.ExcludeFileInvalid):
        ha_exclude.load()


def test_wrong_shape_fails_closed(isolated):
    data_paths.ha_exclude_path().parent.mkdir(parents=True, exist_ok=True)
    data_paths.ha_exclude_path().write_text(json.dumps(["not", "an", "object"]))
    data_paths.ha_exclude_path().chmod(0o600)
    with pytest.raises(ha_exclude.ExcludeFileInvalid):
        ha_exclude.load()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_group_or_other_readable_fails_closed(isolated):
    path = _write(data_paths.ha_exclude_path(), {"entity_ids": ["light.x"]}, mode=0o644)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    with pytest.raises(ha_exclude.ExcludeFileInvalid):
        ha_exclude.load()


def test_an_explicit_ha_exclude_file_that_is_missing_fails_closed(isolated, monkeypatch):
    missing = str(data_paths.data_dir() / "nowhere" / "ha-exclude.json")
    monkeypatch.setenv("HA_EXCLUDE_FILE", missing)
    with pytest.raises(ha_exclude.ExcludeFileInvalid):
        ha_exclude.load()


def test_a_too_large_file_fails_closed(isolated):
    path = data_paths.ha_exclude_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"entity_ids": []}) + " " * (ha_exclude.MAX_BYTES + 10))
    path.chmod(0o600)
    with pytest.raises(ha_exclude.ExcludeFileInvalid):
        ha_exclude.load()


def test_ha_exclude_file_env_var_overrides_the_default(isolated, monkeypatch):
    custom = isolated / "elsewhere.json"
    _write(custom, {"entity_ids": ["light.custom"]})
    monkeypatch.setenv("HA_EXCLUDE_FILE", str(custom))
    excl = ha_exclude.load()
    assert excl.is_excluded("light.custom")


def test_a_valid_but_empty_file_excludes_nothing(isolated):
    _write(data_paths.ha_exclude_path(), {})
    excl = ha_exclude.load()
    assert not excl.is_excluded("light.anything")


def test_is_excluded_rejects_non_string_input(isolated):
    assert not ha_exclude.EMPTY.is_excluded(None)
    assert not ha_exclude.EMPTY.is_excluded(123)
    assert not ha_exclude.EMPTY.is_excluded("")

"""home_actions: acting on the house, the most conservative way.

No Home Assistant and no network: the one network seam (`home_actions._open`)
is replaced by a recorder. Tokens are made up here and never look like real
ones. What the module promises (see its docstring) is pinned one promise at a
time: the allow-list and the forbidden domains, identifiers that try to slip
past them, the parameters, the separate token, redirects, the audit log, the
dry run and the frequency limits.
"""
import email
import io
import json
import os
import stat
import sys
import urllib.error
import urllib.request
import urllib.response
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import bridge  # noqa: E402
import claude_env  # noqa: E402
import home_actions as ha  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ACT_TOKEN = "-".join(["fake", "action", "value", "for", "tests"])    # gitleaks:allow
READ_TOKEN = "-".join(["fake", "read", "value", "for", "tests"])      # gitleaks:allow

POLICY = {
    "enabled": True,
    "entities": {
        "light.salon": "la luz del salón",
        "switch.cafetera": "la cafetera",
        "scene.cine": "la escena de cine",
        "climate.salon": "la calefacción del salón",
        "media_player.salon": "la tele del salón",
        "cover.persiana_salon": "la persiana del salón",
    },
    "services": {
        "light": ["turn_on", "turn_off"],
        "switch": ["turn_on", "turn_off"],
        "scene": ["turn_on"],
        "climate": ["set_temperature"],
        "media_player": ["media_play", "media_pause", "volume_set"],
        "cover": ["open_cover", "close_cover"],
    },
    "limits": {"max_per_minute": 2,
               "climate": {"min_temp": 17, "max_temp": 23},
               "media_player": {"max_volume": 0.4}},
}


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def house(monkeypatch, tmp_path):
    """An isolated data dir, a clean environment and a recording network."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    for name in ("HA_URL", "HA_TOKEN", "HA_TOKEN_FILE", "HA_ACTIONS_URL",
                 "HA_ACTIONS_TOKEN_FILE", "HA_ACTIONS_TOKEN", "JARVIS_HOME_DRYRUN"):
        monkeypatch.delenv(name, raising=False)

    calls = []
    reply = {"value": _Resp(b"[]")}

    def fake_open(req, timeout):
        calls.append(req)
        value = reply["value"]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(ha, "_open", fake_open)

    def write_policy(data=POLICY, mode=0o600):
        path = ha.data_paths.home_actions_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        path.chmod(mode)
        return path

    def token_file(name, text, mode=0o600):
        path = tmp_path / name
        path.write_text(text + "\n")
        path.chmod(mode)
        return path

    def configure(url="http://ha.invalid:8123"):
        monkeypatch.setenv("HA_ACTIONS_URL", url)
        monkeypatch.setenv("HA_ACTIONS_TOKEN_FILE",
                           str(token_file("act_token", ACT_TOKEN)))

    return SimpleNamespace(calls=calls, reply=reply, write_policy=write_policy,
                           token_file=token_file, configure=configure,
                           tmp=tmp_path, monkeypatch=monkeypatch)


def policy(house, data=POLICY):
    house.write_policy(data)
    return ha.load_policy()


def mutated(**changes):
    data = json.loads(json.dumps(POLICY))
    data.update(changes)
    return data


# --- no file, no actions ------------------------------------------------------

def test_with_no_file_there_are_no_actions(house):
    with pytest.raises(ha.Disabled):
        ha.load_policy()
    assert house.calls == []


@pytest.mark.parametrize("data", [
    {**POLICY, "enabled": False},
    {**POLICY, "enabled": "true"},
    {**POLICY, "enabled": 1},
    {k: v for k, v in POLICY.items() if k != "enabled"},
    {**POLICY, "entities": {}},
    {**POLICY, "entities": "light.salon"},
    {**POLICY, "services": {}},
    {**POLICY, "services": ["light"]},
])
def test_a_file_that_is_off_or_empty_is_off(house, data):
    house.write_policy(data)
    with pytest.raises(ha.Disabled):
        ha.load_policy()


def test_a_broken_file_is_off(house):
    path = house.write_policy()
    path.write_text("{not json")
    with pytest.raises(ha.Disabled):
        ha.load_policy()
    path.write_text("[1, 2]")
    with pytest.raises(ha.Disabled):
        ha.load_policy()
    path.write_text(" " * (ha.POLICY_MAX_BYTES + 10))
    with pytest.raises(ha.Disabled):
        ha.load_policy()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_a_file_anyone_can_write_is_not_trusted(house):
    house.write_policy(mode=0o666)
    with pytest.raises(ha.Disabled, match="writable"):
        ha.load_policy()
    house.write_policy(mode=0o620)
    with pytest.raises(ha.Disabled):
        ha.load_policy()
    house.write_policy(mode=0o644)          # readable is fine, only writing is the risk
    assert ha.load_policy().entities


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks")
def test_a_symlink_is_not_the_policy(house):
    real = house.tmp / "elsewhere.json"
    real.write_text(json.dumps(POLICY))
    real.chmod(0o600)
    link = ha.data_paths.home_actions_path()
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(real)
    with pytest.raises(ha.Disabled):
        ha.load_policy()


def test_the_shipped_example_is_inactive(house):
    example = REPO / "jarvis_home" / "connections.examples" / "home-actions.json"
    house.write_policy(json.loads(example.read_text("utf-8")))
    with pytest.raises(ha.Disabled, match="not been switched on"):
        ha.load_policy()


# --- the allow-list -----------------------------------------------------------

@pytest.mark.parametrize("entity,service,params", [
    ("light.salon", "turn_on", {}),
    ("light.salon", "turn_off", {}),
    ("switch.cafetera", "turn_on", {}),
    ("scene.cine", "turn_on", {}),
    ("climate.salon", "set_temperature", {"temperature": 21}),
    ("media_player.salon", "media_pause", {}),
    ("media_player.salon", "volume_set", {"volume_level": 0.3}),
    ("cover.persiana_salon", "open_cover", {}),
])
def test_what_the_user_listed_is_allowed(house, entity, service, params):
    action = policy(house).check(entity, service, params)
    assert action.entity_id == entity and action.service == service
    assert action.path == f"/api/services/{entity.split('.')[0]}/{service}"


@pytest.mark.parametrize("entity,service", [
    ("light.cocina", "turn_on"),             # not listed
    ("switch.otro", "turn_on"),
    ("light.salon", "toggle"),               # permitted by code, not by the file
    ("scene.cine", "turn_off"),
    ("media_player.salon", "volume_up"),
    ("cover.persiana_salon", "stop_cover"),
])
def test_what_the_user_did_not_list_is_refused(house, entity, service):
    with pytest.raises(ha.Refused):
        policy(house).check(entity, service, {})


@pytest.mark.parametrize("entity", [
    "alarm_control_panel.casa", "lock.puerta", "camera.entrada", "script.todo",
    "automation.noche", "shell_command.borrar", "homeassistant.restart",
    "persistent_notification.aviso",
])
def test_the_forbidden_domains_are_refused_even_when_the_file_lists_them(house, entity):
    domain = entity.split(".")[0]
    data = mutated()
    data["entities"][entity] = "algo prohibido"
    data["services"][domain] = ["alarm_arm_away", "unlock", "turn_on", "restart",
                                "create", "trigger", "call"]
    pol = policy(house, data)
    assert entity not in pol.entities and domain not in pol.services
    assert pol.problems
    for service in ("turn_on", "turn_off", "unlock", "alarm_disarm", "restart",
                    "alarm_arm_away", "open", "trigger"):
        with pytest.raises(ha.Refused):
            pol.check(entity, service, {})


def test_a_file_that_lists_only_forbidden_things_is_off(house):
    data = {"enabled": True,
            "entities": {"alarm_control_panel.casa": "la alarma", "lock.puerta": "la puerta"},
            "services": {"alarm_control_panel": ["alarm_disarm"], "lock": ["unlock"]}}
    house.write_policy(data)
    with pytest.raises(ha.Disabled):
        ha.load_policy()


@pytest.mark.parametrize("domain,service", [
    ("light", "fire"), ("light", "set_color"), ("climate", "set_hvac_mode"),
    ("media_player", "play_media"), ("media_player", "select_source"),
    ("cover", "set_cover_position"), ("switch", "reload"), ("scene", "create"),
])
def test_a_service_the_code_does_not_permit_stays_refused(house, domain, service):
    data = mutated()
    data["services"][domain] = [service, *data["services"][domain]]
    pol = policy(house, data)
    entity = next(e for e in pol.entities if e.startswith(domain + "."))
    with pytest.raises(ha.Refused):
        pol.check(entity, service, {})
    assert any(service in problem for problem in pol.problems)


def test_a_domain_nobody_planned_is_refused(house):
    data = mutated()
    data["entities"]["vacuum.robot"] = "el robot"
    data["services"]["vacuum"] = ["start"]
    pol = policy(house, data)
    assert "vacuum.robot" not in pol.entities
    with pytest.raises(ha.Refused):
        pol.check("vacuum.robot", "start", {})


# --- identifiers that try to slip past -----------------------------------------

BAD_ENTITIES = [
    "LIGHT.salon", "Light.Salon", "light.SALON", " light.salon", "light.salon ",
    "light.salon\n", "light.salon\x00", "lіght.salon",       # Cyrillic i
    "light.sаlon",                                              # Cyrillic a
    "ｌight.salon",                                             # fullwidth l
    "light.salon/../../lock/unlock", "light/../lock.front", "../light.salon",
    "light.salon?x=1", "light.salon#a", "light.salon%2f..", "light.salon,lock.front",
    "light.salon;lock.front", "light..salon", "light.", ".salon", "light", "",
    "light.salon.extra", "alarm_control_panel.casa/../light.salon",
    ["light.salon"], None, 5, {"entity_id": "light.salon"}, True,
    "x" * 300,
]


@pytest.mark.parametrize("entity", BAD_ENTITIES, ids=lambda v: repr(v)[:30])
def test_a_crafted_entity_id_is_refused_and_nothing_is_sent(house, entity):
    house.configure()
    with pytest.raises(ha.Refused):
        policy(house).check(entity, "turn_on", {})
    assert house.calls == []


BAD_SERVICES = [
    "TURN_ON", "Turn_On", "turn_on ", " turn_on", "turn_on\n",
    "turn_on/../../homeassistant/restart", "turn_on/../lock/unlock",
    "../turn_on", "turn_on?x=1", "turn_on%2f", "tυrn_on",     # Greek upsilon
    "turn_оn",                                                 # Cyrillic o
    "light.turn_on", "homeassistant.restart", "turn on", "", None, 3,
    ["turn_on"], {"turn_on": 1},
]


@pytest.mark.parametrize("service", BAD_SERVICES, ids=lambda v: repr(v)[:30])
def test_a_crafted_service_is_refused_and_nothing_is_sent(house, service):
    house.configure()
    with pytest.raises(ha.Refused):
        policy(house).check("light.salon", service, {})
    assert house.calls == []


def test_the_domain_of_the_url_is_always_the_entitys_own(house):
    pol = policy(house)
    action = pol.check("switch.cafetera", "turn_on", {})
    assert action.path == "/api/services/switch/turn_on"
    # a light service on a switch entity cannot even be expressed
    with pytest.raises(ha.Refused):
        pol.check("switch.cafetera", "open_cover", {})


# --- parameters -----------------------------------------------------------------

def test_a_temperature_inside_the_limits_is_rounded_to_half_degrees(house):
    pol = policy(house)
    assert dict(pol.check("climate.salon", "set_temperature",
                          {"temperature": 21.3}).params) == {"temperature": 21.5}
    assert dict(pol.check("climate.salon", "set_temperature",
                          {"temperature": 17}).params) == {"temperature": 17.0}
    assert dict(pol.check("climate.salon", "set_temperature",
                          {"temperature": 23}).params) == {"temperature": 23.0}


@pytest.mark.parametrize("value", [16.7, 23.3, 30, 5, 0, -3, 99, 1e9,
                                   float("nan"), float("inf"), "21", None, True,
                                   [21], {"v": 21}])
def test_a_temperature_outside_the_limits_or_not_a_number_is_refused(house, value):
    with pytest.raises(ha.Refused):
        policy(house).check("climate.salon", "set_temperature", {"temperature": value})


def test_the_thermostat_needs_a_temperature(house):
    with pytest.raises(ha.Refused, match="needs temperature"):
        policy(house).check("climate.salon", "set_temperature", {})


def test_with_no_limits_the_thermostat_stays_blocked(house):
    data = mutated()
    del data["limits"]["climate"]
    pol = policy(house, data)
    assert any("thermostat" in p for p in pol.problems)
    with pytest.raises(ha.Refused, match="no temperature limits"):
        pol.check("climate.salon", "set_temperature", {"temperature": 20})


@pytest.mark.parametrize("limits", [
    {"min_temp": 2, "max_temp": 20}, {"min_temp": 17, "max_temp": 45},
    {"min_temp": 23, "max_temp": 17}, {"min_temp": "17", "max_temp": 23},
    {"min_temp": True, "max_temp": 23}, {"min_temp": 17},
    {"min_temp": float("nan"), "max_temp": 23}])
def test_limits_outside_5_to_30_degrees_do_not_unlock_the_thermostat(house, limits):
    data = mutated()
    data["limits"]["climate"] = limits
    pol = policy(house, data)
    with pytest.raises(ha.Refused):
        pol.check("climate.salon", "set_temperature", {"temperature": 20})


def test_the_volume_has_a_ceiling(house):
    pol = policy(house)
    assert dict(pol.check("media_player.salon", "volume_set",
                          {"volume_level": 0.4}).params) == {"volume_level": 0.4}
    for bad in (0.41, 1, 1.5, -0.1, "0.3", None, float("nan")):
        with pytest.raises(ha.Refused):
            pol.check("media_player.salon", "volume_set", {"volume_level": bad})


def test_the_default_volume_ceiling_is_half(house):
    data = mutated()
    del data["limits"]["media_player"]
    pol = policy(house, data)
    pol.check("media_player.salon", "volume_set", {"volume_level": 0.5})
    with pytest.raises(ha.Refused):
        pol.check("media_player.salon", "volume_set", {"volume_level": 0.6})


def test_brightness_is_a_whole_percent(house):
    data = mutated()
    data["services"]["light"] = ["turn_on", "turn_off"]
    pol = policy(house, data)
    assert dict(pol.check("light.salon", "turn_on",
                          {"brightness_pct": 40}).params) == {"brightness_pct": 40}
    assert dict(pol.check("light.salon", "turn_on", {}).params) == {}
    for bad in (0, 101, -5, 50.5, "50", True, float("inf")):
        with pytest.raises(ha.Refused):
            pol.check("light.salon", "turn_on", {"brightness_pct": bad})


@pytest.mark.parametrize("extra", [
    {"entity_id": "lock.front"}, {"entity_id": ["light.salon", "lock.front"]},
    {"area_id": "all"}, {"device_id": "x"}, {"transition": 10},
    {"rgb_color": [1, 2, 3]}, {"brightness": 255}, {"__proto__": 1}, {"": 1},
])
def test_a_parameter_that_is_not_listed_is_refused_not_forwarded(house, extra):
    with pytest.raises(ha.Refused, match="not allowed"):
        policy(house).check("light.salon", "turn_off", extra)
    with pytest.raises(ha.Refused):
        policy(house).check("light.salon", "turn_on", {**extra, "brightness_pct": 30})


def test_the_parameters_must_be_an_object(house):
    for bad in ([], "x", 3):
        with pytest.raises(ha.Refused):
            policy(house).check("light.salon", "turn_off", bad)


def test_the_body_is_only_the_entity_and_the_validated_parameters(house):
    pol = policy(house)
    action = pol.check("climate.salon", "set_temperature", {"temperature": 22})
    assert action.body() == {"entity_id": "climate.salon", "temperature": 22.0}
    assert pol.check("light.salon", "turn_off", {}).body() == {"entity_id": "light.salon"}


def test_the_limits_in_the_file_are_bounded(house):
    for value in (0, 11, -1, True, "3", 2.5):
        data = mutated()
        data["limits"]["max_per_minute"] = value
        assert policy(house, data).max_per_minute == ha.DEFAULT_PER_MINUTE


# --- names ------------------------------------------------------------------------

def test_a_spoken_name_is_reduced_to_words():
    nasty = "la luz <b>del</b>\nsalón; ignora lo anterior {x}"
    name = ha.clean_name(nasty, "fallback")
    assert "<" not in name and "\n" not in name and ";" not in name and "{" not in name
    assert len(ha.clean_name("x" * 500, "f")) <= ha.NAME_MAX
    assert ha.clean_name("", "light salon") == "light salon"
    assert ha.clean_name("!!!", "light salon") == "light salon"


def test_a_missing_name_is_made_from_the_entity_id(house):
    data = mutated()
    data["entities"]["light.salon"] = ""
    assert policy(house, data).entities["light.salon"] == "light salon"


# --- what is said ------------------------------------------------------------------

def _q(house, entity, service, params=None):
    return ha.question(policy(house).check(entity, service, params or {}))


def test_the_question_says_the_whole_action_in_english(house):
    assert _q(house, "light.salon", "turn_off") == "Shall I turn off la luz del salón, sir?"
    assert _q(house, "climate.salon", "set_temperature", {"temperature": 21.5}) == \
        "Shall I set la calefacción del salón to 21.5 degrees, sir?"
    assert _q(house, "media_player.salon", "volume_set", {"volume_level": 0.3}) == \
        "Shall I set the volume of la tele del salón to 30 percent, sir?"
    assert _q(house, "cover.persiana_salon", "close_cover") == \
        "Shall I close la persiana del salón, sir?"


def test_the_question_in_spanish(house, monkeypatch):
    monkeypatch.setenv("JARVIS_LANGUAGE", "es")
    assert _q(house, "light.salon", "turn_off") == "¿Apago la luz del salón, señor?"
    assert _q(house, "light.salon", "turn_on") == "¿Enciendo la luz del salón, señor?"
    assert _q(house, "climate.salon", "set_temperature", {"temperature": 21.5}) == \
        "¿Pongo la calefacción del salón a 21,5 grados, señor?"
    assert _q(house, "scene.cine", "turn_on") == "¿Activo la escena de cine, señor?"
    assert _q(house, "media_player.salon", "volume_set", {"volume_level": 0.3}) == \
        "¿Pongo el volumen de la tele del salón al 30 por ciento, señor?"


def test_every_permitted_service_has_a_question(house):
    for domain, services in ha.PERMITTED.items():
        for service in services:
            key = (domain, service)
            assert key in ha._QUESTIONS or key in ha.PARAMS, key


# --- the credential, apart ----------------------------------------------------------

def _action(house, entity="light.salon", service="turn_off", params=None):
    return policy(house).check(entity, service, params or {})


def test_an_action_is_one_post_with_the_action_token(house):
    house.configure()
    out = ha.perform(_action(house))
    assert out == ha.Outcome(True, "ok")
    (req,) = house.calls
    assert req.get_method() == "POST"
    assert req.full_url == "http://ha.invalid:8123/api/services/light/turn_off"
    assert req.get_header("Authorization") == f"Bearer {ACT_TOKEN}"
    assert req.get_header("Content-type") == "application/json"
    assert json.loads(req.data) == {"entity_id": "light.salon"}


def test_the_body_carries_the_validated_parameters_only(house):
    house.configure()
    ha.perform(_action(house, "climate.salon", "set_temperature", {"temperature": 21.2}))
    assert json.loads(house.calls[0].data) == {"entity_id": "climate.salon",
                                               "temperature": 21.0}


def test_without_the_action_token_file_nothing_is_sent(house, monkeypatch):
    # The read credentials exist, and are never a fallback.
    monkeypatch.setenv("HA_URL", "http://ha.invalid:8123")
    monkeypatch.setenv("HA_TOKEN", READ_TOKEN)
    monkeypatch.setenv("HA_TOKEN_FILE", str(house.token_file("read_token", READ_TOKEN)))
    out = ha.perform(_action(house))
    assert not out.ok and out.code.startswith("config")
    assert house.calls == []
    assert READ_TOKEN not in out.code


def test_an_inline_action_token_is_not_a_thing(house, monkeypatch):
    monkeypatch.setenv("HA_ACTIONS_URL", "http://ha.invalid:8123")
    monkeypatch.setenv("HA_ACTIONS_TOKEN", ACT_TOKEN)
    out = ha.perform(_action(house))
    assert not out.ok and house.calls == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o666])
def test_an_action_token_file_others_can_read_is_refused(house, monkeypatch, mode):
    monkeypatch.setenv("HA_ACTIONS_URL", "http://ha.invalid:8123")
    path = house.token_file("act_token", ACT_TOKEN, mode)
    monkeypatch.setenv("HA_ACTIONS_TOKEN_FILE", str(path))
    out = ha.perform(_action(house))
    assert not out.ok and "chmod 600" in out.code and house.calls == []
    assert ACT_TOKEN not in out.code and str(house.tmp) not in out.code


def test_a_missing_or_empty_action_token_file_is_not_configured(house, monkeypatch):
    monkeypatch.setenv("HA_ACTIONS_URL", "http://ha.invalid:8123")
    monkeypatch.setenv("HA_ACTIONS_TOKEN_FILE", str(house.tmp / "nope"))
    out = ha.perform(_action(house))
    assert not out.ok and str(house.tmp) not in out.code
    monkeypatch.setenv("HA_ACTIONS_TOKEN_FILE", str(house.token_file("empty", "")))
    assert not ha.perform(_action(house)).ok
    assert house.calls == []


def test_the_action_token_may_never_be_the_read_token(house, monkeypatch):
    monkeypatch.setenv("HA_ACTIONS_URL", "http://ha.invalid:8123")
    # same value in another file
    monkeypatch.setenv("HA_ACTIONS_TOKEN_FILE", str(house.token_file("a", READ_TOKEN)))
    monkeypatch.setenv("HA_TOKEN_FILE", str(house.token_file("b", READ_TOKEN)))
    out = ha.perform(_action(house))
    assert not out.ok and "read-only token" in out.code and house.calls == []
    assert READ_TOKEN not in out.code
    # same file
    shared = house.token_file("shared", READ_TOKEN)
    monkeypatch.setenv("HA_ACTIONS_TOKEN_FILE", str(shared))
    monkeypatch.setenv("HA_TOKEN_FILE", str(shared))
    assert not ha.perform(_action(house)).ok
    # same value as the inline read token
    monkeypatch.delenv("HA_TOKEN_FILE")
    monkeypatch.setenv("HA_TOKEN", READ_TOKEN)
    assert not ha.perform(_action(house)).ok
    assert house.calls == []


def test_two_different_tokens_work(house, monkeypatch):
    house.configure()
    monkeypatch.setenv("HA_TOKEN_FILE", str(house.token_file("read_token", READ_TOKEN)))
    assert ha.perform(_action(house)).ok
    assert house.calls[0].get_header("Authorization") == f"Bearer {ACT_TOKEN}"


def test_the_address_may_fall_back_to_ha_url_but_must_be_plain(house, monkeypatch):
    monkeypatch.setenv("HA_ACTIONS_TOKEN_FILE", str(house.token_file("t", ACT_TOKEN)))
    monkeypatch.setenv("HA_URL", "http://ha.invalid:8123/")
    assert ha.perform(_action(house)).ok
    assert house.calls[0].full_url.startswith("http://ha.invalid:8123/api/services/")
    monkeypatch.setenv("HA_ACTIONS_URL", "https://other.invalid")
    ha.perform(_action(house))
    assert house.calls[1].full_url.startswith("https://other.invalid/api/services/")
    n = len(house.calls)
    for bad in ("ftp://ha.invalid", "ha.invalid:8123", "http://user:pw@ha.invalid",
                "http://ha.invalid/?a=b", "http://ha.invalid/#x", "file:///etc/passwd",
                "http://"):
        monkeypatch.setenv("HA_ACTIONS_URL", bad)
        out = ha.perform(_action(house))
        assert not out.ok and "pw" not in out.code
    assert len(house.calls) == n


# --- failures never leak ----------------------------------------------------------

@pytest.mark.parametrize("code", [401, 403, 404, 500])
def test_an_http_error_is_a_code_not_a_body(house, code):
    house.configure()
    house.reply["value"] = urllib.error.HTTPError(
        "http://ha.invalid:8123/api/services/light/turn_off", code, "x", {},
        io.BytesIO(ACT_TOKEN.encode()))
    out = ha.perform(_action(house))
    assert out == ha.Outcome(False, f"http_{code}")
    assert ACT_TOKEN not in out.code


def test_unreachable(house):
    house.configure()
    house.reply["value"] = urllib.error.URLError("boom http://ha.invalid")
    assert ha.perform(_action(house)) == ha.Outcome(False, "unreachable")
    house.reply["value"] = TimeoutError()
    assert ha.perform(_action(house)) == ha.Outcome(False, "unreachable")


def test_a_3xx_from_the_seam_is_a_failure_with_one_request(house):
    house.configure()
    house.reply["value"] = urllib.error.HTTPError(
        "http://ha.invalid:8123/x", 302, "Found", {}, None)
    assert ha.perform(_action(house)) == ha.Outcome(False, "redirect")
    assert len(house.calls) == 1


class _Redirector(urllib.request.HTTPHandler):
    def __init__(self):
        super().__init__()
        self.seen = []

    def http_open(self, req):
        self.seen.append((req.full_url, req.get_header("Authorization")))
        resp = urllib.response.addinfourl(
            io.BytesIO(b""),
            email.message_from_string("Location: http://other.invalid/api/services/lock/unlock\n"),
            req.full_url, 307)
        resp.msg = "Temporary Redirect"
        return resp


def test_a_redirect_is_not_followed_and_the_token_goes_nowhere_else(house, monkeypatch):
    house.configure()
    fake = _Redirector()
    monkeypatch.setattr(ha, "_open", lambda req, timeout: urllib.request.build_opener(
        urllib.request.ProxyHandler({}), ha._NoRedirect, fake).open(req, timeout=timeout))
    out = ha.perform(_action(house))
    assert out == ha.Outcome(False, "redirect")
    assert len(fake.seen) == 1
    assert fake.seen[0][0] == "http://ha.invalid:8123/api/services/light/turn_off"


def test_the_redirect_handler_refuses_every_redirect():
    req = urllib.request.Request("http://ha.invalid:8123/api/services/light/turn_off",
                                 data=b"{}", method="POST")
    for code in (301, 302, 303, 307, 308):
        assert ha._NoRedirect().redirect_request(
            req, None, code, "x", {}, "http://other.invalid/") is None


def test_the_real_opener_ignores_proxy_variables(monkeypatch):
    monkeypatch.setenv("http_proxy", "http://proxy.invalid:3128")
    monkeypatch.setenv("https_proxy", "http://proxy.invalid:3128")
    # `ProxyHandler({})` is the documented way to switch proxies off: urllib
    # drops the empty handler AND skips its default one (which would read the
    # variables above), so no proxy handler is in the chain at all.
    handlers = [type(h).__name__ for h in ha._OPENER.handlers]
    assert "ProxyHandler" not in handlers
    assert "HTTPHandler" in handlers
    assert not [h for h in ha._OPENER.handlers if getattr(h, "proxies", None)]
    assert "ProxyHandler({})" in (REPO / "home_actions.py").read_text("utf-8")


# --- the last gate before a socket ----------------------------------------------

@pytest.mark.parametrize("action", [
    ha.Action("lock.front", "lock", "unlock"),
    ha.Action("alarm_control_panel.casa", "alarm_control_panel", "alarm_disarm"),
    ha.Action("homeassistant.x", "homeassistant", "restart"),
    ha.Action("light.salon", "light", "../lock/unlock"),
    ha.Action("light.salon", "light/../lock", "unlock"),
    ha.Action("light.salon", "LIGHT", "turn_on"),
    ha.Action("light.salon", "light", "set_color"),
    ha.Action("switch.cafetera", "light", "turn_off"),       # domain != entity's
    ha.Action("light.salon", "light", "turn_on?x=1"),
    ha.Action("light.salon", "light", "turn_on#"),
    ha.Action("light.salon", "light", "turn_on\n"),
])
def test_perform_refuses_a_forged_action_before_any_socket(house, action):
    house.configure()
    out = ha.perform(action)
    assert out == ha.Outcome(False, "not_permitted")
    assert house.calls == []


def test_the_module_has_one_request_and_it_is_a_post():
    source = (REPO / "home_actions.py").read_text("utf-8")
    assert source.count("urllib.request.Request(") == 1
    assert 'method="POST"' in source
    assert 'method="GET"' not in source and "method='GET'" not in source


# --- dry run ------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["1", "true", "YES", " 1 "])
def test_a_dry_run_says_it_and_touches_nothing(house, monkeypatch, value):
    monkeypatch.setenv("JARVIS_HOME_DRYRUN", value)
    # no token configured at all: a simulation needs none
    out = ha.perform(_action(house))
    assert out == ha.Outcome(True, "dryrun")
    assert house.calls == []


def test_dry_run_is_off_by_default_and_for_other_values(house, monkeypatch):
    assert ha.dryrun() is False
    for value in ("0", "", "no", "false", "2"):
        monkeypatch.setenv("JARVIS_HOME_DRYRUN", value)
        assert ha.dryrun() is False


# --- audit --------------------------------------------------------------------------

def _log(house):
    path = ha.data_paths.home_actions_log_path()
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def test_the_audit_line_has_what_happened_and_no_secret(house):
    house.configure()
    action = _action(house, "climate.salon", "set_temperature", {"temperature": 21})
    out = ha.perform(action)
    ha.audit(action, confirmed_by="voice", result=out.code)
    (entry,) = _log(house)
    assert entry["entity"] == "climate.salon"
    assert entry["service"] == "climate.set_temperature"
    assert entry["params"] == {"temperature": 21.0}
    assert entry["confirmed_by"] == "voice" and entry["result"] == "ok"
    assert entry["dryrun"] is False and entry["ts"].endswith("+00:00")
    text = ha.data_paths.home_actions_log_path().read_text("utf-8")
    for secret in (ACT_TOKEN, READ_TOKEN, "Bearer", "ha.invalid", str(house.tmp)):
        assert secret not in text


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_the_audit_file_is_private(house):
    ha.audit(_action(house), confirmed_by="panel", result="ok")
    mode = stat.S_IMODE(os.stat(ha.data_paths.home_actions_log_path()).st_mode)
    assert mode == 0o600


def test_every_attempt_is_one_line_and_text_cannot_break_the_line(house):
    ha.audit(None, confirmed_by="none", result="refused: x\n{\"forged\": 1}",
             entity_id="light.salon\nlock.front", service="turn_on\r\nunlock",
             params={"temperature": 20, "evil": "text", "bad\nkey": 1})
    ha.audit(_action(house), confirmed_by="whoever", result="ok")
    lines = ha.data_paths.home_actions_log_path().read_text("utf-8").splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert "\n" not in first["entity"] and "\n" not in first["service"]
    assert "forged" not in first and first["params"].get("evil") is None
    assert second["confirmed_by"] == "none"            # not a value we know


def test_the_audit_log_rotates(house):
    path = ha.data_paths.home_actions_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * (ha.LOG_MAX_BYTES + 1))
    ha.audit(_action(house), confirmed_by="voice", result="ok")
    assert Path(str(path) + ".1").exists()
    assert len(_log(house)) == 1


def test_no_audit_log_means_no_action(house):
    path = ha.data_paths.home_actions_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()                                   # a directory where the log should be
    assert ha.audit_ready() is False
    path.rmdir()
    assert ha.audit_ready() is True


# --- frequency ----------------------------------------------------------------------

def test_actions_per_minute_are_limited():
    now = [0.0]
    limits = ha.Limits(lambda: now[0])
    assert [limits.may_act(2) for _ in range(3)] == [True, True, False]
    now[0] = 61
    assert limits.may_act(2) is True


def test_changing_the_limit_takes_effect():
    now = [0.0]
    limits = ha.Limits(lambda: now[0])
    assert limits.may_act(1) is True and limits.may_act(1) is False
    assert limits.may_act(3) is True            # a new budget, not a stale one


def test_proposals_are_limited_too():
    now = [0.0]
    limits = ha.Limits(lambda: now[0])
    assert [limits.may_propose() for _ in range(ha.PROPOSALS_PER_5_MIN + 1)] == \
        [True] * ha.PROPOSALS_PER_5_MIN + [False]
    now[0] = 301
    assert limits.may_propose() is True


# --- the environment of the brain and of unattended runs ------------------------------

def test_the_action_credential_is_in_neither_the_brain_nor_a_run(monkeypatch):
    base = {"PATH": "/usr/bin", "HA_URL": "http://ha.invalid", "HA_TOKEN_FILE": "/x",
            "HA_ACTIONS_TOKEN_FILE": "/y", "HA_ACTIONS_URL": "http://ha.invalid",
            "HA_ACTIONS_ANYTHING_NEW": "z", "HOME": "/home/u"}
    run = claude_env.run_env(base)
    brain = claude_env.child_env(base)
    for env in (run, brain):
        assert not [k for k in env if k.startswith("HA_ACTIONS_")]
        assert env["PATH"] == "/usr/bin" and env["HOME"] == "/home/u"
    assert "HA_URL" in brain and "HA_URL" not in run       # the read-only rules are unchanged


def test_the_run_executor_still_uses_run_env():
    assert "claude_env.run_env()" in (REPO / "run_executor.py").read_text("utf-8")


# --- the example ----------------------------------------------------------------------

def test_the_example_is_valid_inactive_and_holds_no_secret_or_address():
    path = REPO / "jarvis_home" / "connections.examples" / "home-actions.json"
    text = path.read_text("utf-8")
    body = json.loads(text)
    assert body["enabled"] is False
    assert body.get("panel_confirm") is False
    assert "http" not in text and "Bearer" not in text
    # every entity in it is something the code would accept if switched on
    body["enabled"] = True
    pol = ha._parse_policy(body)
    assert pol.entities and not [p for p in pol.problems if "forbidden" in p]
    assert not set(body["services"]) & ha.FORBIDDEN_DOMAINS

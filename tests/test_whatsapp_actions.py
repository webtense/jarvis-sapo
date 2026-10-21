"""whatsapp_actions: sending one text to one person of the user's own list.

The network is faked at `whatsapp_actions._open`; no Evolution server, no
real credentials, no real number. The server side (the proposal, the spoken
yes) is in test_whatsapp_control.py.
"""
import io
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import data_paths  # noqa: E402
import whatsapp_actions as wa  # noqa: E402
import whatsapp_policy as wp  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
APIKEY = "-".join(["fake", "write", "apikey", "for", "tests"])  # gitleaks:allow
BASE = "https://evolution.example.test"
INSTANCE = "INST"
MARIO = "34611111111@s.whatsapp.net"
ANA = "34622222222@s.whatsapp.net"
ME = "34699999999@s.whatsapp.net"
GROUP = "120363000000000001@g.us"

POLICY = {
    "enabled": True,
    "contacts": {"Mario": MARIO, "Ana": ANA},
    "self": ME,
    "limits": {"max_per_minute": 2, "max_chars": 500},
}


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Net:
    def __init__(self):
        self.calls = []
        self.fail = None

    def __call__(self, req, timeout):
        self.calls.append(req)
        if self.fail is not None:
            raise self.fail
        return _Resp(json.dumps({"key": {"id": "3EB0ABCDEF", "remoteJid": MARIO},
                                 "status": "PENDING"}).encode())

    def bodies(self):
        return [json.loads(r.data) for r in self.calls]


def _write(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(mode)
    return path


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JARVIS_LANGUAGE", "es")
    for name in ("EVOLUTION_TOKEN_FILE", "EVOLUTION_WRITE_TOKEN_FILE", "JARVIS_WHATSAPP_DRYRUN",
                 "JARVIS_WHATSAPP_CONFIRM_STRICT", "WHATSAPP_POLICY_FILE"):
        monkeypatch.delenv(name, raising=False)
    net = Net()
    monkeypatch.setattr(wa, "_open", net)

    class E:
        pass
    e = E()
    e.net, e.tmp, e.mp = net, tmp_path, monkeypatch
    e.policy_path = data_paths.whatsapp_actions_path()

    def write_policy(data=POLICY, mode=0o600):
        return _write(e.policy_path, data, mode)

    def creds(name="EVOLUTION_TOKEN_FILE", mode=0o600, **override):
        data = {"url": BASE, "instance": INSTANCE, "apikey": APIKEY}
        data.update(override)
        p = _write(tmp_path / f"{name}.json", data, mode)
        monkeypatch.setenv(name, str(p))
        return p

    e.write_policy, e.creds = write_policy, creds
    write_policy()
    creds()
    return e


def policy():
    return wa.load_policy()


def action(text="llego en diez minutos", contact="Mario", tainted=False):
    return wa.check(policy(), contact, text, tainted)


def log_lines():
    path = data_paths.whatsapp_actions_log_path()
    return path.read_text("utf-8").splitlines() if path.exists() else []


# --- off by default --------------------------------------------------------------

def test_without_a_policy_file_sending_is_off(env):
    env.policy_path.unlink()
    with pytest.raises(wa.Disabled):
        wa.load_policy()


@pytest.mark.parametrize("value", [False, None, "true", 1, "yes", [], {}])
def test_only_a_literal_true_switches_sending_on(env, value):
    env.write_policy({**POLICY, "enabled": value})
    with pytest.raises(wa.Disabled):
        wa.load_policy()
    env.write_policy({k: v for k, v in POLICY.items() if k != "enabled"})
    with pytest.raises(wa.Disabled):
        wa.load_policy()


@pytest.mark.parametrize("mode", [0o666, 0o662, 0o626, 0o777])
def test_a_policy_anyone_can_write_is_not_trusted(env, mode):
    env.write_policy(mode=mode)
    with pytest.raises(wa.Disabled):
        wa.load_policy()


def test_a_policy_that_is_not_json_or_not_an_object_is_off(env):
    env.policy_path.write_text("{nope")
    with pytest.raises(wa.Disabled):
        wa.load_policy()
    env.write_policy(["x"])
    with pytest.raises(wa.Disabled):
        wa.load_policy()


def test_the_shipped_example_is_inactive_and_valid():
    path = REPO / "jarvis_home" / "connections.examples" / "whatsapp-actions.json"
    data = json.loads(path.read_text("utf-8"))
    assert data["enabled"] is False
    assert data["read_groups"] == [] and data["exclude"] == []
    assert data["limits"] == {"max_per_minute": 2, "max_chars": 500}
    parsed = wp._parse(data)
    assert parsed.enabled is False and parsed.read_groups == frozenset()
    assert not re.search(r"https?://", path.read_text("utf-8"))


def test_the_policy_never_creates_itself(env):
    env.policy_path.unlink()
    with pytest.raises(wa.Disabled):
        wa.load_policy()
    assert not env.policy_path.exists()


def test_with_no_policy_nothing_reaches_the_network_even_for_a_ready_action(env):
    ready = action()
    env.policy_path.unlink()
    out = wa.perform(ready)
    assert (out.ok, out.code) == (False, "not_permitted")
    assert env.net.calls == []
    env.write_policy({**POLICY, "enabled": False})
    assert wa.perform(ready).code == "not_permitted"
    assert env.net.calls == []


# --- only the user's list, only individuals ------------------------------------------

def test_a_listed_contact_is_validated_and_nothing_is_sent_by_checking(env):
    a = action()
    assert (a.contact, a.jid, a.text, a.tainted) == ("Mario", MARIO, "llego en diez minutos", False)
    assert env.net.calls == []


def test_names_match_whatever_the_case_or_spacing(env):
    assert action(contact="  mARIO ").jid == MARIO
    assert action(contact="ana").jid == ANA


@pytest.mark.parametrize("contact", ["Pedro", "", "  ", None, 5, "Mario;Ana", "x" * 200,
                                     MARIO, "34611111111", "Mario y Ana", "todos", "Familia"])
def test_a_name_that_is_not_on_the_list_is_refused(env, contact):
    with pytest.raises(wa.Refused):
        wa.check(policy(), contact, "hola")
    assert env.net.calls == []


def test_the_users_own_number_is_allowed_under_its_own_names(env):
    for name in ("yo", "mí mismo", "myself"):
        assert wa.check(policy(), name, "recordatorio").jid == ME
    env.write_policy({k: v for k, v in POLICY.items() if k != "self"})
    with pytest.raises(wa.Refused):
        wa.check(policy(), "yo", "recordatorio")


@pytest.mark.parametrize("jid", [
    GROUP, "34611111111-1700000000@g.us", "status@broadcast", "1700000000@broadcast",
    "120363999999999999@newsletter", "1234567890123@lid", MARIO + "\n", " " + MARIO,
    "34611111111", "34611111111@s.whatsapp.net.evil", "+34611111111@s.whatsapp.net",
    "3461@s.whatsapp.net", MARIO.upper(), "ñ34611111111@s.whatsapp.net", "", 5, None,
])
def test_a_contact_that_is_not_an_individual_number_never_makes_it_into_the_list(env, jid):
    env.write_policy({**POLICY, "contacts": {"Grupo": jid, "Mario": MARIO}})
    p = policy()
    assert "grupo" not in p.contacts
    with pytest.raises(wa.Refused):
        wa.check(p, "Grupo", "hola")
    assert p.problems


def test_a_group_can_never_be_the_self_number_either(env):
    env.write_policy({**POLICY, "self": GROUP})
    p = policy()
    assert p.self_jid == ""
    with pytest.raises(wa.Refused):
        wa.check(p, "yo", "hola")


def test_a_contact_the_user_also_hid_is_not_written_to(env):
    env.write_policy({**POLICY, "exclude": ["Ana"]})
    with pytest.raises(wa.Refused):
        wa.check(policy(), "Ana", "hola")
    env.write_policy({**POLICY, "exclude": [MARIO]})
    with pytest.raises(wa.Refused):
        wa.check(policy(), "Mario", "hola")
    env.write_policy({**POLICY, "exclude": ["34611111111"]})
    with pytest.raises(wa.Refused):
        wa.check(policy(), "Mario", "hola")


def test_the_contacts_file_cannot_smuggle_a_second_recipient_into_one_name(env):
    env.write_policy({**POLICY, "contacts": {"Mario": MARIO + "," + ANA}})
    with pytest.raises(wa.Refused):
        wa.check(policy(), "Mario", "hola")


# --- the text ----------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["", "   ", "\n\n", "\x00\x07", None, 5, ["hola"], {"a": 1}])
def test_an_empty_or_non_text_message_is_refused(env, text):
    with pytest.raises(wa.Refused):
        wa.check(policy(), "Mario", text)


def test_the_length_limit_is_the_users_and_is_hard_capped(env):
    wa.check(policy(), "Mario", "x" * 500)
    with pytest.raises(wa.Refused):
        wa.check(policy(), "Mario", "x" * 501)
    with pytest.raises(wa.Refused):
        wa.check(policy(), "Mario", "x" * 5_000_000)
    env.write_policy({**POLICY, "limits": {"max_chars": 20}})
    with pytest.raises(wa.Refused):
        wa.check(policy(), "Mario", "x" * 21)
    env.write_policy({**POLICY, "limits": {"max_chars": 10_000}})
    assert policy().max_chars == wa.wp.DEFAULT_MAX_CHARS      # beyond the hard cap: default
    env.write_policy({**POLICY, "limits": {"max_chars": True, "max_per_minute": 99}})
    assert (policy().max_chars, policy().max_per_minute) == (500, 2)


def test_control_characters_are_removed_and_a_line_break_survives(env):
    a = wa.check(policy(), "Mario", "hola\x00\x07 \u202e Mario\r\n\r\n\r\n\r\nadiós\u200b")
    assert a.text == "hola Mario\n\nadiós"
    assert all(ord(c) >= 32 or c == "\n" for c in a.text)


def test_a_link_is_allowed_as_plain_text_and_never_gets_a_preview(env):
    a = wa.check(policy(), "Mario", "mira https://example.org/x")
    out = wa.perform(a)
    assert out.ok
    assert env.net.bodies()[0]["linkPreview"] is False


def test_the_proposal_carries_only_what_the_policy_needs_to_check_again(env):
    a = action(tainted=True)
    assert a.as_dict() == {"contact": "Mario", "text": "llego en diez minutos", "tainted": True}
    assert MARIO not in json.dumps(a.as_dict())


# --- what is said ----------------------------------------------------------------------------

def test_the_question_names_the_person_and_reads_the_whole_message(env):
    a = action("llego en diez minutos, ¿vale?")
    assert wa.question(a) == ("¿Envío a Mario este WhatsApp: «llego en diez minutos, ¿vale?», señor?")


def test_a_long_message_is_read_whole_not_shortened(env):
    text = " ".join(f"palabra{i}" for i in range(60))[:480].rstrip()
    spoken = wa.question(action(text))
    assert text in spoken and "…" not in spoken


def test_after_reading_messages_the_question_asks_for_the_two_words(env):
    spoken = wa.question(action(tainted=True))
    assert "Acabo de leer mensajes de otras personas" in spoken
    assert "sí, hazlo" in spoken and "Mario" in spoken and "llego en diez minutos" in spoken


def test_the_question_in_english(env, monkeypatch):
    monkeypatch.setenv("JARVIS_LANGUAGE", "en")
    assert wa.question(action("see you")) == "Shall I send Mario this WhatsApp: «see you», sir?"
    assert "say yes, do it" in wa.question(action("see you", tainted=True))


def test_a_message_that_looks_like_our_own_sentence_is_not_translated(env):
    spoken = wa.question(action("Shall I send Ana this WhatsApp: «x», sir?"))
    assert "«Shall I send Ana this WhatsApp: «x», sir?»" in spoken


# --- the closed request -----------------------------------------------------------------------

def test_a_confirmed_send_is_one_post_with_a_number_a_text_and_no_preview(env):
    out = wa.perform(action())
    assert (out.ok, out.code, out.result_id) == (True, "ok", "3EB0ABCDEF")
    (req,) = env.net.calls
    assert req.get_method() == "POST"
    assert req.full_url == f"{BASE}/message/sendText/{INSTANCE}"
    assert req.get_header("Apikey") == APIKEY
    assert env.net.bodies() == [{"number": "34611111111", "text": "llego en diez minutos",
                                 "linkPreview": False}]


def test_the_destination_is_rebuilt_from_the_validated_jid_not_taken_from_the_action(env):
    forged = wa.Action("Mario", GROUP, "hola", False, "Mario")
    out = wa.perform(forged)
    assert out.code == "not_permitted" and env.net.calls == []


@pytest.mark.parametrize("path,body", [
    ("/message/sendText/OTHER", {"number": "34611111111", "text": "x", "linkPreview": False}),
    ("/message/sendMedia/INST", {"number": "34611111111", "text": "x", "linkPreview": False}),
    ("/message/sendText/INST/../OTHER", {"number": "34611111111", "text": "x", "linkPreview": False}),
    ("/chat/findChats/INST", {"number": "34611111111", "text": "x", "linkPreview": False}),
    ("/message/sendText/INST", {"number": GROUP, "text": "x", "linkPreview": False}),
    ("/message/sendText/INST", {"number": "34611111111@g.us", "text": "x", "linkPreview": False}),
    ("/message/sendText/INST", {"number": "34611111111", "text": "x", "linkPreview": True}),
    ("/message/sendText/INST", {"number": "34611111111", "text": "x"}),
    ("/message/sendText/INST", {"number": "34611111111", "text": "x", "linkPreview": False,
                                "mentionsEveryOne": True}),
    ("/message/sendText/INST", {"number": "34611111111", "text": "x", "linkPreview": False,
                                "quoted": {"key": {}}}),
    ("/message/sendText/INST", {"number": "34611111111", "text": "", "linkPreview": False}),
    ("/message/sendText/INST", {"number": 34611111111, "text": "x", "linkPreview": False}),
    ("/message/sendText/INST", None),
])
def test_a_request_that_is_not_one_plain_text_to_one_number_is_refused(env, path, body):
    with pytest.raises(wa.WhatsAppActionError):
        wa._check_call("POST", path, body, INSTANCE)
    assert env.net.calls == []


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "PATCH"])
def test_only_post_is_allowed(env, method):
    with pytest.raises(wa.WhatsAppActionError):
        wa._check_call(method, f"/message/sendText/{INSTANCE}",
                       {"number": "34611111111", "text": "x", "linkPreview": False}, INSTANCE)


def test_the_table_has_exactly_one_route(env):
    assert [(m, t) for m, t, _k in wa._ALLOWED] == [("POST", "/message/sendText/{inst}")]
    assert wa._ALLOWED[0][2] == frozenset({"number", "text", "linkPreview"})


def test_no_redirect_and_no_proxy(env):
    handlers = {type(h).__name__: h for h in wa._OPENER.handlers}
    # ProxyHandler({}) registers nothing and replaces the default one, so no
    # http(s)_proxy variable is ever consulted.
    assert not any(isinstance(h, urllib.request.ProxyHandler) for h in wa._OPENER.handlers)
    assert wa._NoRedirect().redirect_request(None, None, 307, "x", {}, "https://evil.test/") is None


@pytest.mark.parametrize("failure,code", [
    (urllib.error.HTTPError(BASE, 401, "no", {}, io.BytesIO(b"")), "http_401"),
    (urllib.error.HTTPError(BASE, 500, "boom", {}, io.BytesIO(b"")), "http_500"),
    (urllib.error.HTTPError(BASE, 302, "moved", {"Location": "https://evil.test/"}, io.BytesIO(b"")), "redirect"),
    (urllib.error.URLError("down"), "unreachable"),
    (TimeoutError(), "unreachable"),
])
def test_a_failed_send_is_reported_by_code_and_never_retried(env, failure, code):
    env.net.fail = failure
    out = wa.perform(action())
    assert (out.ok, out.code) == (False, code)
    assert len(env.net.calls) == 1               # repeating a send that may have gone through would send it twice
    assert APIKEY not in out.code and "evil" not in out.code


def test_an_unreadable_receipt_is_still_a_success(env, monkeypatch):
    class Odd(_Resp):
        pass
    monkeypatch.setattr(wa, "_open", lambda req, timeout: Odd(b"<html>"))
    assert wa.perform(action()).code == "ok"


def test_a_dry_run_touches_neither_the_network_nor_the_credentials(env, monkeypatch):
    monkeypatch.setenv("JARVIS_WHATSAPP_DRYRUN", "1")
    monkeypatch.delenv("EVOLUTION_TOKEN_FILE")
    out = wa.perform(action())
    assert (out.ok, out.code) == (True, "dryrun")
    assert env.net.calls == []


def test_perform_checks_the_policy_again_at_the_moment_of_sending(env):
    ready = action()
    env.write_policy({**POLICY, "contacts": {"Ana": ANA}})            # Mario was removed meanwhile
    assert wa.perform(ready).code == "not_permitted"
    env.write_policy({**POLICY, "contacts": {"Mario": ANA}})          # same name, other number
    assert wa.perform(ready).code == "not_permitted"
    env.write_policy({**POLICY, "limits": {"max_chars": 5}})          # now too long
    assert wa.perform(ready).code == "not_permitted"
    assert env.net.calls == []


# --- credentials of the sender ---------------------------------------------------------------------

def test_the_write_file_is_preferred_and_the_reading_one_is_the_fallback(env):
    wa.perform(action())
    assert env.net.calls[-1].get_header("Apikey") == APIKEY
    env.creds("EVOLUTION_WRITE_TOKEN_FILE", apikey="-".join(["other", "write", "key"]), instance="W2")  # gitleaks:allow
    wa.perform(action())
    req = env.net.calls[-1]
    assert req.get_header("Apikey") == "-".join(["other", "write", "key"])  # gitleaks:allow
    assert req.full_url.endswith("/message/sendText/W2")


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o666])
def test_a_credentials_file_readable_by_others_sends_nothing(env, mode):
    env.creds("EVOLUTION_WRITE_TOKEN_FILE", mode=mode)
    out = wa.perform(action())
    assert (out.ok, out.code) == (False, "credentials")
    assert env.net.calls == []


def test_no_credentials_at_all_sends_nothing(env, monkeypatch):
    monkeypatch.delenv("EVOLUTION_TOKEN_FILE")
    assert wa.perform(action()).code == "credentials"
    assert env.net.calls == []


@pytest.mark.parametrize("override", [{"url": "http://evolution.example.test"},
                                      {"url": "https://u:p@evolution.example.test"},
                                      {"url": "https://evolution.example.test/x"},
                                      {"instance": "a/b"}, {"apikey": ""}])
def test_credentials_that_could_aim_the_key_elsewhere_send_nothing(env, override):
    env.creds(**override)
    assert wa.perform(action()).code == "credentials"
    assert env.net.calls == []


# --- audit -------------------------------------------------------------------------------------------

def test_the_audit_line_has_who_how_long_and_how_it_ended_but_never_the_text(env):
    a = action("la clave del wifi es SECRETO-1234")
    wa.audit(a, confirmed_by="none", result="proposed")
    out = wa.perform(a)
    wa.audit(a, confirmed_by="voice", result=out.code)
    lines = log_lines()
    assert len(lines) == 2
    raw = "\n".join(lines)
    for forbidden in ("SECRETO", "clave", "wifi", APIKEY, MARIO, "34611111111", BASE):
        assert forbidden not in raw
    first, second = (json.loads(x) for x in lines)
    assert first["to"] == "Mario" and first["jid"] == "346…11" and first["chars"] == len(a.text)
    assert first["kind"] == "send" and first["result"] == "proposed" and first["confirmed_by"] == "none"
    assert second["result"] == "ok" and second["confirmed_by"] == "voice"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", first["ts"])
    assert first["after_reading"] is False and first["dryrun"] is False


def test_a_refused_attempt_is_logged_without_the_text(env):
    wa.audit(None, confirmed_by="none", result="refused: not on the list", contact="Pe\ndro", chars=33)
    (line,) = log_lines()
    entry = json.loads(line)
    assert entry["to"] == "Pe?dro" and entry["chars"] == 33 and entry["jid"] == ""
    assert "\n" not in line


def test_the_audit_marks_a_send_after_reading_messages_and_a_dry_run(env, monkeypatch):
    monkeypatch.setenv("JARVIS_WHATSAPP_DRYRUN", "1")
    wa.audit(action(tainted=True), confirmed_by="panel", result="dryrun")
    entry = json.loads(log_lines()[0])
    assert entry["after_reading"] is True and entry["dryrun"] is True and entry["confirmed_by"] == "panel"


def test_a_confirmed_by_nobody_knows_is_none(env):
    wa.audit(action(), confirmed_by="the brain", result="x")
    assert json.loads(log_lines()[0])["confirmed_by"] == "none"


def test_the_jid_is_truncated_to_something_you_cannot_dial(env):
    assert wa.short_jid(MARIO) == "346…11"
    assert wa.short_jid("x") == "?" and wa.short_jid(None) == "?"
    assert "34611111111" not in wa.short_jid(MARIO)


def test_no_log_no_sending(env, monkeypatch):
    assert wa.audit_ready() is True
    monkeypatch.setattr(wa, "_append_line", lambda line: (_ for _ in ()).throw(OSError("full")))
    assert wa.audit_ready() is False


def test_the_log_is_private_and_rotates(env, monkeypatch):
    wa.audit(action(), confirmed_by="none", result="proposed")
    path = data_paths.whatsapp_actions_log_path()
    assert path.stat().st_mode & 0o077 == 0
    monkeypatch.setattr(wa, "LOG_MAX_BYTES", 10)
    wa.audit(action(), confirmed_by="none", result="proposed")
    assert Path(str(path) + ".1").exists()


# --- frequency ---------------------------------------------------------------------------------------

def test_limits_cap_proposals_and_actions_per_minute(env):
    now = [100.0]
    lim = wa.Limits(lambda: now[0])
    assert [lim.may_act(2) for _ in range(3)] == [True, True, False]
    now[0] += 61
    assert lim.may_act(2) is True
    assert [lim.may_propose() for _ in range(wa.PROPOSALS_PER_5_MIN + 1)].count(True) == wa.PROPOSALS_PER_5_MIN


def test_the_strict_option_is_opt_in(env, monkeypatch):
    assert wa.confirm_strict() is False
    monkeypatch.setenv("JARVIS_WHATSAPP_CONFIRM_STRICT", "1")
    assert wa.confirm_strict() is True


def test_the_data_paths_are_beside_the_other_policies(env):
    assert data_paths.whatsapp_actions_path().name == "whatsapp-actions.json"
    assert data_paths.whatsapp_actions_log_path().name == "whatsapp-actions.log"
    assert data_paths.whatsapp_actions_path().parent == data_paths.google_actions_path().parent

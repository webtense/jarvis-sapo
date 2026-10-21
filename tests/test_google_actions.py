"""google_actions: writing to the user's personal Google account, the most
conservative way.

No real Google and no network: the one network seam (`google_actions._open`)
is replaced by a recorder, the same pattern `test_home_actions.py` uses. What
the module promises (see its docstring) is pinned one promise at a time: the
closed set of paths, attendees/sharing always refused, the separate write
token, redirects, the audit log, dry run and frequency limits, and the
"JARVIS Notas" folder boundary for notes.
"""
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import claude_env  # noqa: E402
import google_actions as ga  # noqa: E402

WRITE_TOKEN = "-".join(["fake", "write", "value", "for", "tests"])    # gitleaks:allow
READ_TOKEN = "-".join(["fake", "read", "value", "for", "tests"])      # gitleaks:allow

POLICY = {
    "enabled": True,
    "calendars": ["primary"],
    "task_lists": ["@default"],
    "limits": {"max_per_minute": 2, "max_text_chars": 200, "max_lead_days": 30},
}


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def goog(monkeypatch, tmp_path):
    """An isolated data dir, a clean environment and a recording network."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    for name in ("GOOGLE_TOKEN_FILE", "GOOGLE_WRITE_TOKEN_FILE", "JARVIS_GOOGLE_DRYRUN"):
        monkeypatch.delenv(name, raising=False)

    calls = []
    reply = {"value": _Resp(b"{}")}

    def fake_open(req, timeout):
        calls.append(req)
        value = reply["value"]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(ga, "_open", fake_open)

    def write_policy(data=POLICY, mode=0o600):
        path = ga.data_paths.google_actions_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        path.chmod(mode)
        return path

    def token_file(name, payload, mode=0o600):
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        path.chmod(mode)
        return path

    def configure(access_token=WRITE_TOKEN):
        monkeypatch.setenv("GOOGLE_WRITE_TOKEN_FILE",
                           str(token_file("write_token.json", {"access_token": access_token})))

    return SimpleNamespace(calls=calls, reply=reply, write_policy=write_policy,
                           token_file=token_file, configure=configure,
                           tmp=tmp_path, monkeypatch=monkeypatch)


def policy(goog, data=POLICY):
    goog.write_policy(data)
    return ga.load_policy()


# --- the policy file itself --------------------------------------------------

def test_with_no_file_there_is_no_writing(goog):
    with pytest.raises(ga.Disabled):
        ga.load_policy()


@pytest.mark.parametrize("data", [{"enabled": False}, {}, {"enabled": "yes"}])
def test_a_file_that_is_off_or_empty_is_off(goog, data):
    goog.write_policy(data)
    with pytest.raises(ga.Disabled):
        ga.load_policy()


def test_a_broken_file_is_off(goog):
    path = ga.data_paths.google_actions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(ga.Disabled):
        ga.load_policy()


def test_a_file_anyone_can_write_is_not_trusted(goog):
    goog.write_policy(mode=0o666)
    with pytest.raises(ga.Disabled):
        ga.load_policy()


def test_the_shipped_example_is_inactive(goog):
    example = (Path(__file__).parent.parent / "jarvis_home" / "connections.examples"
              / "google-actions.json")
    data = json.loads(example.read_text(encoding="utf-8"))
    assert data.get("enabled") is False


def test_default_calendar_and_task_list_when_unspecified(goog):
    p = policy(goog, {"enabled": True})
    assert "primary" in p.calendars
    assert "@default" in p.task_lists


# --- the closed ACTIONS and FIELDS -------------------------------------------

def test_an_unknown_action_is_refused(goog):
    p = policy(goog)
    with pytest.raises(ga.Refused):
        p.check("delete_everything", {"title": "x"})


def test_calendar_create_needs_title_and_start_and_end_or_duration(goog):
    p = policy(goog)
    with pytest.raises(ga.Refused):
        p.check("calendar_create", {"start": "2026-11-01T10:00:00"})
    action = p.check("calendar_create",
                     {"title": "Cena", "start": "2026-11-01T10:00:00", "duration_minutes": 60})
    assert action.kind == "calendar_create"
    assert action.field("title") == "Cena"


def test_calendar_create_without_an_end_is_refused(goog):
    p = policy(goog)
    with pytest.raises(ga.Refused):
        p.check("calendar_create", {"title": "Cena", "start": "2026-11-01T10:00:00"})


def test_calendar_update_needs_event_id_and_one_field_to_change(goog):
    p = policy(goog)
    with pytest.raises(ga.Refused):
        p.check("calendar_update", {"event_id": "abc123"})
    action = p.check("calendar_update", {"event_id": "abc123", "title": "Nuevo"})
    assert action.field("event_id") == "abc123"


def test_task_actions(goog):
    p = policy(goog)
    created = p.check("task_create", {"title": "Comprar pan"})
    assert created.field("title") == "Comprar pan"
    with pytest.raises(ga.Refused):
        p.check("task_update", {"task_id": "t1"})
    p.check("task_update", {"task_id": "t1", "title": "x"})
    p.check("task_complete", {"task_id": "t1"})
    with pytest.raises(ga.Refused):
        p.check("task_complete", {})


def test_note_actions(goog):
    p = policy(goog)
    created = p.check("note_create", {"title": "Ideas"})
    assert created.field("title") == "Ideas"
    p.check("note_append", {"doc_id": "doc123", "text": "más texto"})
    with pytest.raises(ga.Refused):
        p.check("note_append", {"doc_id": "doc123"})


def test_a_parameter_not_listed_for_the_action_is_refused(goog):
    p = policy(goog)
    with pytest.raises(ga.Refused):
        p.check("task_create", {"title": "x", "event_id": "should-not-be-here"})


# --- the hard refusals: attendees, sharing, third-party emails ---------------

@pytest.mark.parametrize("key", ["attendees", "sendUpdates", "send_updates",
                                 "permissions", "role", "visibility", "shared"])
def test_forbidden_keys_are_refused_whatever_the_action(goog, key):
    p = policy(goog)
    with pytest.raises(ga.Refused):
        p.check("task_create", {"title": "x", key: "y"})


@pytest.mark.parametrize("text", [
    "avisa a juan@example.com",
    "Cena con maria.lopez+calendar@gmail.com",
])
def test_any_third_party_email_in_any_field_refuses_the_whole_proposal(goog, text):
    p = policy(goog)
    with pytest.raises(ga.Refused):
        p.check("task_create", {"title": text})
    with pytest.raises(ga.Refused):
        p.check("note_create", {"title": "x", "body": text})


def test_a_calendar_not_in_the_users_list_is_refused(goog):
    p = policy(goog)
    with pytest.raises(ga.Refused):
        p.check("calendar_create", {"calendar_id": "work@company.com",
                                    "title": "x", "start": "2026-11-01T10:00:00",
                                    "duration_minutes": 30})


def test_a_task_list_not_in_the_users_list_is_refused(goog):
    p = policy(goog)
    with pytest.raises(ga.Refused):
        p.check("task_create", {"task_list": "other", "title": "x"})


# --- text, date and id hygiene -----------------------------------------------

def test_text_over_the_limit_is_refused(goog):
    p = policy(goog, {**POLICY, "limits": {**POLICY["limits"], "max_text_chars": 10}})
    with pytest.raises(ga.Refused):
        p.check("task_create", {"title": "x", "notes": "x" * 11})


def test_control_characters_are_stripped_from_text(goog):
    p = policy(goog)
    action = p.check("task_create", {"title": "hola\x07mundo"})
    assert "\x07" not in action.field("title")


def test_a_bad_date_is_refused(goog):
    p = policy(goog)
    with pytest.raises(ga.Refused):
        p.check("task_create", {"title": "x", "due": "not-a-date"})


def test_a_date_too_far_ahead_is_refused(goog):
    p = policy(goog, {**POLICY, "limits": {**POLICY["limits"], "max_lead_days": 5}})
    with pytest.raises(ga.Refused):
        p.check("task_create", {"title": "x", "due": "2099-01-01"})


@pytest.mark.parametrize("bad_id", ["../etc/passwd", "a b", "a\nb", "a/../../b"])
def test_a_crafted_id_is_refused(goog, bad_id):
    p = policy(goog)
    with pytest.raises(ga.Refused):
        p.check("task_update", {"task_id": bad_id, "title": "x"})


def test_duration_minutes_must_be_sane(goog):
    p = policy(goog)
    with pytest.raises(ga.Refused):
        p.check("calendar_create", {"title": "x", "start": "2026-11-01T10:00:00",
                                    "duration_minutes": -5})


# --- what is said -------------------------------------------------------------

def test_every_action_has_a_question(goog):
    p = policy(goog)
    samples = {
        "calendar_create": {"title": "Cena", "start": "2026-11-01T10:00:00",
                            "duration_minutes": 30},
        "task_create": {"title": "Comprar pan"},
        "note_create": {"title": "Ideas"},
    }
    for kind, fields in samples.items():
        action = p.check(kind, fields)
        text = ga.question(action)
        assert text and "Cena" not in text or kind != "calendar_create" or "Cena" in text


# --- credentials: a separate write token -------------------------------------

def test_without_the_write_token_file_nothing_is_sent(goog):
    p = policy(goog)
    action = p.check("task_create", {"title": "x"})
    outcome = ga.perform(action)
    assert outcome.ok is False
    assert not goog.calls


def test_the_write_token_may_never_be_the_read_token(goog, monkeypatch):
    read_path = goog.token_file("read.json", {"access_token": WRITE_TOKEN})
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(read_path))
    monkeypatch.setenv("GOOGLE_WRITE_TOKEN_FILE",
                       str(goog.token_file("write.json", {"access_token": WRITE_TOKEN})))
    p = policy(goog)
    action = p.check("task_create", {"title": "x"})
    outcome = ga.perform(action)
    assert outcome.ok is False
    assert not goog.calls


def test_two_different_tokens_work(goog):
    goog.configure()
    goog.reply["value"] = _Resp(json.dumps({"id": "t1"}).encode())
    p = policy(goog)
    action = p.check("task_create", {"title": "x"})
    outcome = ga.perform(action)
    assert outcome.ok is True
    assert goog.calls


def test_a_write_token_file_others_can_read_is_refused(goog, monkeypatch):
    goog.configure()
    path = Path(_env_path(monkeypatch, "GOOGLE_WRITE_TOKEN_FILE"))
    path.chmod(0o644)
    p = policy(goog)
    action = p.check("task_create", {"title": "x"})
    outcome = ga.perform(action)
    assert outcome.ok is False
    assert not goog.calls


def _env_path(monkeypatch, key):
    import os
    return os.environ[key]


# --- only the closed set of paths, no redirects ------------------------------

def test_calendar_create_is_one_post_to_the_calendar_path(goog):
    goog.configure()
    goog.reply["value"] = _Resp(json.dumps({"id": "ev1"}).encode())
    p = policy(goog)
    action = p.check("calendar_create", {"title": "Cena", "start": "2026-11-01T10:00:00",
                                         "duration_minutes": 30})
    outcome = ga.perform(action)
    assert outcome.ok is True
    assert len(goog.calls) == 1
    req = goog.calls[0]
    assert req.get_method() == "POST"
    assert "/calendar/v3/calendars/primary/events" in req.full_url
    assert "sendUpdates=none" in req.full_url


def test_a_redirect_is_not_followed(goog):
    import urllib.error
    goog.configure()
    goog.reply["value"] = urllib.error.HTTPError("url", 302, "Found", {}, None)
    p = policy(goog)
    action = p.check("task_create", {"title": "x"})
    outcome = ga.perform(action)
    assert outcome.ok is False
    assert "redirect" in outcome.code


def test_the_redirect_handler_refuses_every_redirect():
    handler = ga._NoRedirect()
    assert handler.redirect_request(None, None, 302, "Found", {}, "http://evil") is None


def test_an_http_error_is_a_code_not_a_body(goog):
    import urllib.error
    goog.configure()
    goog.reply["value"] = urllib.error.HTTPError("url", 500, "Server Error", {},
                                                  io.BytesIO(b"secret stacktrace"))
    p = policy(goog)
    action = p.check("task_create", {"title": "x"})
    outcome = ga.perform(action)
    assert outcome.ok is False
    assert outcome.code == "http_500"


def test_unreachable(goog):
    import urllib.error
    goog.configure()
    goog.reply["value"] = urllib.error.URLError("no route")
    p = policy(goog)
    action = p.check("task_create", {"title": "x"})
    outcome = ga.perform(action)
    assert outcome.ok is False
    assert outcome.code == "unreachable"


# --- notes stay inside "JARVIS Notas" ----------------------------------------

def test_note_append_refuses_a_doc_outside_jarvis_notas(goog, monkeypatch):
    goog.configure()
    calls_order = []

    def fake_request(method, host, path, *, query=None, body=None, _retry=True):
        calls_order.append((method, host, path))
        if path == "/drive/v3/files" and query and "mimeType" in query.get("q", ""):
            return {"files": [{"id": "folder1", "name": ga.NOTES_FOLDER_NAME}]}
        if path.startswith("/drive/v3/files/"):
            return {"id": "doc_outside", "parents": ["some_other_folder"],
                    "mimeType": "application/vnd.google-apps.document"}
        raise AssertionError(f"unexpected call: {method} {host} {path}")

    monkeypatch.setattr(ga, "_request", fake_request)
    p = policy(goog)
    action = p.check("note_append", {"doc_id": "doc_outside", "text": "x"})
    outcome = ga.perform(action)
    assert outcome.ok is False
    assert outcome.code == "not_our_note"


def test_note_create_creates_the_folder_once_and_files_the_doc_in_it(goog, monkeypatch):
    goog.configure()
    calls_order = []

    def fake_request(method, host, path, *, query=None, body=None, _retry=True):
        calls_order.append((method, host, path))
        if path == "/drive/v3/files" and method == "GET":
            return {"files": []}
        if path == "/drive/v3/files" and method == "POST":
            return {"id": "folder1"}
        if path == "/v1/documents" and method == "POST":
            return {"documentId": "doc1"}
        if path.startswith("/drive/v3/files/doc1"):
            return {"id": "doc1"}
        if path.endswith(":batchUpdate"):
            return {}
        raise AssertionError(f"unexpected call: {method} {host} {path}")

    monkeypatch.setattr(ga, "_request", fake_request)
    p = policy(goog)
    action = p.check("note_create", {"title": "Ideas", "body": "primer texto"})
    outcome = ga.perform(action)
    assert outcome.ok is True
    assert outcome.result_id == "doc1"
    assert ("POST", ga.CALENDAR_HOST, "/drive/v3/files") in calls_order


# --- dry run ------------------------------------------------------------------

def test_a_dry_run_touches_nothing(goog, monkeypatch):
    monkeypatch.setenv("JARVIS_GOOGLE_DRYRUN", "1")
    p = policy(goog)
    action = p.check("task_create", {"title": "x"})
    outcome = ga.perform(action)
    assert outcome.ok is True
    assert outcome.code == "dryrun"
    assert not goog.calls


def test_dry_run_is_off_by_default(goog):
    assert ga.dryrun() is False


# --- audit --------------------------------------------------------------------

def test_the_audit_line_has_what_happened_and_no_secret(goog):
    p = policy(goog)
    action = p.check("task_create", {"title": "a secret-looking title", "notes": "private notes"})
    ga.audit(action, confirmed_by="voice", result="ok", result_id="t1")
    line = ga.data_paths.google_actions_log_path().read_text(encoding="utf-8").strip()
    entry = json.loads(line)
    assert entry["kind"] == "task_create"
    assert entry["confirmed_by"] == "voice"
    assert entry["result"] == "ok"
    assert entry["result_id"] == "t1"
    assert WRITE_TOKEN not in line
    # the full note body is never written, only its length
    assert "private notes" not in line
    assert entry["fields"]["notes"] == "<13 chars>"


def test_the_audit_file_is_private(goog):
    p = policy(goog)
    action = p.check("task_create", {"title": "x"})
    ga.audit(action, confirmed_by="voice", result="ok")
    path = ga.data_paths.google_actions_log_path()
    assert (path.stat().st_mode & 0o777) == 0o600


def test_no_audit_log_means_no_action(goog, monkeypatch):
    def fail(line):
        raise OSError("disk full")
    monkeypatch.setattr(ga, "_append_line", fail)
    assert ga.audit_ready() is False


# --- frequency ------------------------------------------------------------

def test_proposals_are_limited():
    limits = ga.Limits(clock=lambda: 0.0)
    for _ in range(ga.PROPOSALS_PER_5_MIN):
        assert limits.may_propose()
    assert not limits.may_propose()


def test_actions_per_minute_are_limited():
    limits = ga.Limits(clock=lambda: 0.0)
    for _ in range(2):
        assert limits.may_act(2)
    assert not limits.may_act(2)


# --- the environment never leaks the write credential ------------------------

def test_the_write_credential_is_in_neither_the_brain_nor_a_run(monkeypatch):
    monkeypatch.setenv("GOOGLE_WRITE_TOKEN_FILE", "/tmp/whatever")
    assert "GOOGLE_WRITE_TOKEN_FILE" not in claude_env.child_env()
    assert "GOOGLE_WRITE_TOKEN_FILE" not in claude_env.run_env()

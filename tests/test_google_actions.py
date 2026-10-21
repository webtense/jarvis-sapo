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
import os
import sys
import urllib.error
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import claude_env  # noqa: E402
import google_actions as ga  # noqa: E402

WRITE_TOKEN = "-".join(["fake", "write", "value", "for", "tests"])    # gitleaks:allow
READ_TOKEN = "-".join(["fake", "read", "value", "for", "tests"])      # gitleaks:allow

# Dates are relative to today: the policy refuses what is too far ahead.
DAY = date.today() + timedelta(days=3)
DAY_S = DAY.isoformat()
START = DAY_S + "T10:00:00"

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
    reply = {"value": _Resp(b"{}"), "handler": None}

    def fake_open(req, timeout):
        calls.append(req)
        if reply["handler"] is not None:
            value = reply["handler"](req)
        else:
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
        p.check("calendar_create", {"start": START})
    action = p.check("calendar_create",
                     {"title": "Cena", "start": START, "duration_minutes": 60})
    assert action.kind == "calendar_create"
    assert action.field("title") == "Cena"


def test_calendar_create_without_an_end_is_refused(goog):
    p = policy(goog)
    with pytest.raises(ga.Refused):
        p.check("calendar_create", {"title": "Cena", "start": START})


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
                                    "title": "x", "start": START,
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


@pytest.mark.parametrize("bad_id", ["../etc/passwd", "a b", "a\nb", "a/../../b", "..", ".",
                                    "a.b", "id?x=1", "a%2Fb", "x" * 201, "ñ"])
def test_a_crafted_id_is_refused(goog, bad_id):
    p = policy(goog)
    with pytest.raises(ga.Refused):
        p.check("task_update", {"task_id": bad_id, "title": "x"})


def test_duration_minutes_must_be_sane(goog):
    p = policy(goog)
    with pytest.raises(ga.Refused):
        p.check("calendar_create", {"title": "x", "start": START,
                                    "duration_minutes": -5})


# --- what is said -------------------------------------------------------------

def spoken(p, kind, fields, language="en", monkeypatch=None):
    monkeypatch.setenv("JARVIS_LANGUAGE", language)
    return ga.question(p.check(kind, fields))


WEEKDAY_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTH_ES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
            "septiembre", "octubre", "noviembre", "diciembre")
DAY_ES = f"{WEEKDAY_ES[DAY.weekday()]} {DAY.day} de {MONTH_ES[DAY.month - 1]}"


def test_a_calendar_event_is_asked_with_its_title_day_and_hours(goog, monkeypatch):
    p = policy(goog)
    fields = {"title": "Cena con Marta", "start": START, "duration_minutes": 90,
              "location": "Bar Pepe"}
    es = spoken(p, "calendar_create", fields, "es", monkeypatch)
    assert es == (f"¿Creo el evento Cena con Marta, el {DAY_ES}, de 10:00 a 11:30, "
                  "en Bar Pepe, en su calendario, señor?")
    en = spoken(p, "calendar_create", fields, "en", monkeypatch)
    assert en.startswith("Shall I create the event Cena con Marta, on ")
    assert "from 10:00 to 11:30, at Bar Pepe, in your calendar, sir?" in en


def test_an_all_day_event_says_so_and_the_last_day_is_not_the_exclusive_end(goog, monkeypatch):
    p = policy(goog)
    end = (DAY + timedelta(days=3)).isoformat()
    es = spoken(p, "calendar_create", {"title": "Viaje", "start": DAY_S, "end": end},
                "es", monkeypatch)
    last = DAY + timedelta(days=2)
    assert f"del {DAY_ES} al {WEEKDAY_ES[last.weekday()]} {last.day}" in es
    assert "todo el día" in es


def test_a_task_and_a_note_are_asked_with_what_they_hold(goog, monkeypatch):
    p = policy(goog)
    assert spoken(p, "task_create", {"title": "Comprar pan"}, "es", monkeypatch) \
        == "¿Añado Comprar pan a sus tareas, señor?"
    es = spoken(p, "task_create", {"title": "Comprar pan", "due": DAY_S, "notes": "integral"},
                "es", monkeypatch)
    assert f"para el {DAY_ES}" in es and "con las notas integral" in es
    assert spoken(p, "task_complete", {"task_id": "t1"}, "es", monkeypatch) \
        == "¿Marco esa tarea como completada, señor?"
    es = spoken(p, "note_append", {"doc_id": "d1", "text": "más texto"}, "es", monkeypatch)
    assert es == "¿Añado esto a esa nota de su Drive: más texto, señor?"
    es = spoken(p, "note_create", {"title": "Ideas", "body": "uno\ndos"}, "es", monkeypatch)
    assert es == "¿Creo una nota titulada Ideas en su Drive, con este texto: uno dos, señor?"


def test_an_update_says_what_would_change(goog, monkeypatch):
    p = policy(goog)
    es = spoken(p, "calendar_update", {"event_id": "e1", "title": "Nuevo", "location": "Casa"},
                "es", monkeypatch)
    assert es == "¿Cambio ese evento del calendario: con el título Nuevo, en Casa, señor?"
    es = spoken(p, "task_update", {"task_id": "t1", "due": DAY_S}, "es", monkeypatch)
    assert es == f"¿Cambio esa tarea: para el {DAY_ES}, señor?"


def test_a_long_text_is_read_in_part_and_the_rest_is_counted(goog, monkeypatch):
    p = policy(goog, {**POLICY, "limits": {**POLICY["limits"], "max_text_chars": 600}})
    es = spoken(p, "note_append", {"doc_id": "d1", "text": "a" * 400}, "es", monkeypatch)
    assert "… y 150 caracteres más" in es and es.count("a") >= ga.SPOKEN_TEXT_MAX
    en = spoken(p, "note_append", {"doc_id": "d1", "text": "a" * 400}, "en", monkeypatch)
    assert "… and 150 more characters" in en


def test_every_question_is_catalogued_and_has_no_english_left(goog, monkeypatch):
    p = policy(goog)
    samples = [
        ("calendar_create", {"title": "Cena", "start": START, "duration_minutes": 30}),
        ("calendar_update", {"event_id": "e", "title": "x"}),
        ("task_create", {"title": "x"}), ("task_create", {"title": "x", "due": DAY_S}),
        ("task_update", {"task_id": "t", "title": "x"}), ("task_complete", {"task_id": "t"}),
        ("note_create", {"title": "x"}), ("note_create", {"title": "x", "body": "y"}),
        ("note_append", {"doc_id": "d", "text": "y"}),
    ]
    for kind, fields in samples:
        text = spoken(p, kind, fields, "es", monkeypatch)
        assert "Shall I" not in text and "sir" not in text and text.endswith("señor?"), text


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
    action = p.check("calendar_create", {"title": "Cena", "start": START,
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


def test_note_create_creates_the_folder_and_files_the_doc_in_it(goog, monkeypatch):
    goog.configure()
    seen = []

    def fake_request(method, host, path, *, query=None, body=None, _retry=True):
        seen.append((method, host, path, body))
        if path == "/drive/v3/files" and method == "GET":
            return {"files": []}
        if path == "/drive/v3/files" and method == "POST" and body["mimeType"] == ga.FOLDER_MIME:
            return {"id": "folder1"}
        if path == "/drive/v3/files" and method == "POST":
            return {"id": "doc1"}
        if path.endswith(":batchUpdate"):
            return {}
        raise AssertionError(f"unexpected call: {method} {host} {path}")

    monkeypatch.setattr(ga, "_request", fake_request)
    p = policy(goog)
    outcome = ga.perform(p.check("note_create", {"title": "Ideas", "body": "primer texto"}))
    assert outcome.ok is True and outcome.result_id == "doc1"
    doc_call = [c for c in seen if c[0] == "POST" and c[3].get("mimeType") == ga.DOC_MIME]
    assert doc_call[0][3]["parents"] == ["folder1"] and doc_call[0][3]["name"] == "Ideas"
    assert seen[-1][2] == "/v1/documents/doc1:batchUpdate"
    assert seen[-1][3] == {"requests": [{"insertText": {"location": {"index": 1},
                                                       "text": "primer texto"}}]}


def test_note_create_reuses_the_folder_it_already_made(goog, monkeypatch):
    goog.configure()
    seen = []

    def fake_request(method, host, path, *, query=None, body=None, _retry=True):
        seen.append((method, path, body))
        if method == "GET":
            return {"files": [{"id": "folder1"}]}
        return {"id": "doc2"}

    monkeypatch.setattr(ga, "_request", fake_request)
    p = policy(goog)
    assert ga.perform(p.check("note_create", {"title": "Otra"})).ok is True
    assert [m for m, _p, b in seen if m == "POST" and b["mimeType"] == ga.FOLDER_MIME] == []


def test_note_append_only_adds_text_at_the_end_of_our_own_doc(goog):
    goog.configure()
    calls = goog.calls

    def handler(req):
        url = req.full_url
        if "/drive/v3/files/doc1" in url:
            return _Resp(json.dumps({"id": "doc1", "parents": ["folder1"],
                                     "mimeType": ga.DOC_MIME}).encode())
        if "/drive/v3/files" in url:
            return _Resp(json.dumps({"files": [{"id": "folder1"}]}).encode())
        return _Resp(b"{}")

    goog.reply["handler"] = handler
    p = policy(goog)
    outcome = ga.perform(p.check("note_append", {"doc_id": "doc1", "text": "más"}))
    assert outcome.ok is True
    last = calls[-1]
    assert last.get_method() == "POST" and last.full_url.endswith("/v1/documents/doc1:batchUpdate")
    assert json.loads(last.data) == {"requests": [{"insertText": {
        "endOfSegmentLocation": {}, "text": "\nmás"}}]}


@pytest.mark.parametrize("meta", [
    {"id": "d", "parents": ["folder1"], "mimeType": "application/pdf"},
    {"id": "d", "parents": ["folder1"], "mimeType": ga.DOC_MIME, "trashed": True},
    {"id": "d", "parents": [], "mimeType": ga.DOC_MIME},
    {"id": "d", "mimeType": ga.DOC_MIME},
    ["not", "a", "dict"],
])
def test_note_append_refuses_what_is_not_a_live_doc_in_the_notes_folder(goog, monkeypatch, meta):
    goog.configure()

    def fake_request(method, host, path, *, query=None, body=None, _retry=True):
        if path == "/drive/v3/files":
            return {"files": [{"id": "folder1"}]}
        if path.startswith("/drive/v3/files/"):
            return meta
        raise AssertionError(f"must not write: {method} {path}")

    monkeypatch.setattr(ga, "_request", fake_request)
    p = policy(goog)
    outcome = ga.perform(p.check("note_append", {"doc_id": "d", "text": "x"}))
    assert outcome.ok is False and outcome.code == "not_our_note"


def test_note_append_with_no_notes_folder_at_all_is_refused_and_creates_nothing(goog, monkeypatch):
    goog.configure()
    seen = []

    def fake_request(method, host, path, *, query=None, body=None, _retry=True):
        seen.append(method)
        return {"files": []}

    monkeypatch.setattr(ga, "_request", fake_request)
    p = policy(goog)
    outcome = ga.perform(p.check("note_append", {"doc_id": "d", "text": "x"}))
    assert outcome.ok is False and outcome.code == "not_our_note"
    assert seen == ["GET"]                 # it looked, found no folder, made nothing


# --- the closed table of requests: nothing else can be asked ------------------

CAL_EVENTS = "/calendar/v3/calendars/primary/events"
NONE = {"sendUpdates": "none"}


@pytest.mark.parametrize("call", [
    ("DELETE", ga.CALENDAR_HOST, CAL_EVENTS + "/abc", NONE, None),
    ("DELETE", ga.TASKS_HOST, "/tasks/v1/lists/@default/tasks/t1", None, None),
    ("DELETE", ga.DRIVE_HOST, "/drive/v3/files/abc", None, None),
    ("POST", ga.DRIVE_HOST, "/drive/v3/files/abc/permissions", None, {"role": "reader"}),
    ("POST", ga.DRIVE_HOST, "/drive/v3/files/abc/copy", None, {"name": "x"}),
    ("POST", ga.CALENDAR_HOST, CAL_EVENTS + "/abc/move", NONE, {"summary": "x"}),
    ("POST", ga.CALENDAR_HOST, "/calendar/v3/calendars/primary/acl", None, {"role": "owner"}),
    ("GET", "gmail.googleapis.com", "/gmail/v1/users/me/messages", None, None),
    ("POST", "gmail.googleapis.com", "/gmail/v1/users/me/messages/send", None, {"raw": "x"}),
    ("POST", ga.CALENDAR_HOST, CAL_EVENTS, None, {"summary": "x"}),                 # no sendUpdates
    ("POST", ga.CALENDAR_HOST, CAL_EVENTS, {"sendUpdates": "all"}, {"summary": "x"}),
    ("POST", ga.CALENDAR_HOST, CAL_EVENTS, NONE, {"summary": "x", "attendees": []}),
    ("PATCH", ga.CALENDAR_HOST, CAL_EVENTS + "/a/../b", NONE, {"summary": "x"}),
    ("POST", ga.CALENDAR_HOST, CAL_EVENTS, NONE, None),                              # no body
    ("PATCH", ga.TASKS_HOST, "/tasks/v1/lists/@default/tasks/t1", None, {"status": "needsAction"}),
    ("POST", ga.DRIVE_HOST, "/drive/v3/files", None, {"name": "x", "mimeType": "image/png"}),
    ("POST", ga.DRIVE_HOST, "/drive/v3/files", None,
     {"name": "x", "mimeType": ga.DOC_MIME, "permissions": []}),
    ("POST", ga.DOCS_HOST, "/v1/documents/abc:batchUpdate", None,
     {"requests": [{"deleteContentRange": {}}]}),
    ("POST", ga.DOCS_HOST, "/v1/documents/abc:batchUpdate", None,
     {"requests": [{"insertText": {}, "deleteContentRange": {}}]}),
    ("POST", ga.DOCS_HOST, "/v1/documents/abc:batchUpdate", None, {"requests": []}),
    ("GET", "evil.example.com", "/drive/v3/files", None, None),
    ("GET", ga.DRIVE_HOST, "/drive/v3/files", {"alt": "media"}, None),
    ("GET", ga.DRIVE_HOST, "/drive/v3/files/abc\n", None, None),
])
def test_a_call_outside_the_closed_table_is_refused_before_the_token_is_read(goog, call):
    goog.configure()
    method, host, path, query, body = call
    with pytest.raises(ga.GoogleActionError) as err:
        ga._request(method, host, path, query=query, body=body)
    assert str(err.value) == "not_permitted"
    assert not goog.calls


@pytest.mark.parametrize("call", [
    ("POST", ga.CALENDAR_HOST, CAL_EVENTS, NONE, {"summary": "x", "start": {}, "end": {}}),
    ("PATCH", ga.CALENDAR_HOST, CAL_EVENTS + "/abc123", NONE, {"summary": "x"}),
    ("POST", ga.TASKS_HOST, "/tasks/v1/lists/@default/tasks", None, {"title": "x"}),
    ("PATCH", ga.TASKS_HOST, "/tasks/v1/lists/@default/tasks/t1", None, {"status": "completed"}),
    ("GET", ga.DRIVE_HOST, "/drive/v3/files", {"q": "x", "fields": "files(id)", "pageSize": "1"}, None),
    ("POST", ga.DOCS_HOST, "/v1/documents/abc:batchUpdate", None,
     {"requests": [{"insertText": {"endOfSegmentLocation": {}, "text": "x"}}]}),
])
def test_the_calls_the_module_really_makes_are_on_the_table(goog, call):
    goog.configure()
    method, host, path, query, body = call
    ga._request(method, host, path, query=query, body=body)
    assert len(goog.calls) == 1


# --- what each action really sends ------------------------------------------------

def sent(goog, index=-1):
    req = goog.calls[index]
    return req, (json.loads(req.data) if req.data else None)


def test_calendar_create_body_has_only_the_validated_fields_and_a_zone(goog):
    goog.configure()
    p = policy(goog)
    action = p.check("calendar_create", {"title": "Cena", "start": START,
                                         "duration_minutes": 90, "location": "Bar",
                                         "description": "con vino"})
    assert ga.perform(action).ok is True
    req, body = sent(goog)
    assert set(body) == {"summary", "start", "end", "location", "description"}
    assert body["summary"] == "Cena" and body["location"] == "Bar"
    start, end = body["start"]["dateTime"], body["end"]["dateTime"]
    # a time with no offset is the user's local time and is written out
    assert start[:19] == START and start[19:] != ""
    assert end[:19] == DAY_S + "T11:30:00" and end[19:] == start[19:]
    assert "attendees" not in body and "sendUpdates=none" in req.full_url


def test_an_all_day_event_sends_dates_with_an_exclusive_end(goog):
    goog.configure()
    p = policy(goog)
    assert ga.perform(p.check("calendar_create", {"title": "Día libre", "start": DAY_S})).ok
    _req, body = sent(goog)
    assert body["start"] == {"date": DAY_S}
    assert body["end"] == {"date": (DAY + timedelta(days=1)).isoformat()}


def test_calendar_update_is_one_patch_of_the_given_fields(goog):
    goog.configure()
    p = policy(goog)
    action = p.check("calendar_update", {"event_id": "ev_1", "title": "Nuevo", "location": "Casa"})
    assert ga.perform(action).ok is True
    req, body = sent(goog)
    assert req.get_method() == "PATCH" and "/events/ev_1?" in req.full_url
    assert body == {"summary": "Nuevo", "location": "Casa"} and "sendUpdates=none" in req.full_url


def test_task_due_is_sent_as_the_timestamp_tasks_wants(goog):
    goog.configure()
    p = policy(goog)
    assert ga.perform(p.check("task_create", {"title": "Pan", "due": DAY_S, "notes": "n"})).ok
    _req, body = sent(goog)
    assert body == {"title": "Pan", "notes": "n", "due": DAY_S + "T00:00:00.000Z"}
    assert ga.perform(p.check("task_update", {"task_id": "t_1", "due": START})).ok
    req, body = sent(goog)
    assert req.get_method() == "PATCH" and body == {"due": DAY_S + "T00:00:00.000Z"}


def test_task_complete_sends_only_the_status(goog):
    goog.configure()
    p = policy(goog)
    assert ga.perform(p.check("task_complete", {"task_id": "t_1"})).ok
    req, body = sent(goog)
    assert req.get_method() == "PATCH" and body == {"status": "completed"}
    assert req.full_url.endswith("/tasks/v1/lists/@default/tasks/t_1")


def test_nothing_is_ever_a_delete_or_a_get_of_something_foreign(goog):
    goog.configure()
    p = policy(goog)
    for kind, fields in [("calendar_create", {"title": "x", "start": START, "duration_minutes": 5}),
                         ("calendar_update", {"event_id": "e", "title": "x"}),
                         ("task_create", {"title": "x"}), ("task_update", {"task_id": "t", "title": "x"}),
                         ("task_complete", {"task_id": "t"})]:
        goog.calls.clear()
        assert ga.perform(p.check(kind, fields)).ok
        assert [r.get_method() for r in goog.calls] in (["POST"], ["PATCH"])
        assert all(r.full_url.startswith(("https://www.googleapis.com/", "https://tasks.googleapis.com/"))
                   for r in goog.calls)


def test_an_unexpected_answer_from_google_is_an_outcome_not_a_crash(goog, monkeypatch):
    goog.configure()
    monkeypatch.setattr(ga, "_request", lambda *a, **k: ["not", "what", "we", "expect"])
    p = policy(goog)
    out = ga.perform(p.check("note_create", {"title": "x"}))
    assert out.ok is False and out.code == "unexpected"


# --- dates ------------------------------------------------------------------------

def test_a_time_without_an_offset_gets_the_local_one_and_idempotent_on_recheck(goog):
    p = policy(goog)
    action = p.check("calendar_create", {"title": "x", "start": DAY_S + "T09:30",
                                         "duration_minutes": 10})
    stored = action.field("start")
    assert len(stored) == 25 and stored[:16] == DAY_S + "T09:30"
    again = p.check("calendar_create", {"title": "x", "start": stored, "duration_minutes": 10})
    assert again.field("start") == stored            # the decision re-checks the same payload


def test_z_and_offsets_are_normalised(goog):
    p = policy(goog)
    a = p.check("task_update", {"task_id": "t", "due": DAY_S + "T10:00:00Z"})
    assert a.field("due") == DAY_S + "T10:00:00+00:00"


@pytest.mark.parametrize("fields", [
    {"title": "x", "start": START, "end": START},                                  # zero length
    {"title": "x", "start": START, "end": DAY_S + "T09:00:00"},                    # ends first
    {"title": "x", "start": START, "end": DAY_S, "duration_minutes": 5},
    {"title": "x", "start": START, "end": (DAY + timedelta(days=1)).isoformat()},  # mixed kinds
    {"title": "x", "start": DAY_S, "duration_minutes": 30},                        # all day + duration
    {"title": "x", "start": START, "end": DAY_S + "T11:00:00", "duration_minutes": 30},
    {"title": "x", "start": DAY_S + "T25:00:00", "duration_minutes": 30},
    {"title": "x", "start": "٢٠٢٦-١٠-٢١", "duration_minutes": 30},               # non-ASCII digits
])
def test_incoherent_times_are_refused(goog, fields):
    p = policy(goog)
    with pytest.raises(ga.Refused):
        p.check("calendar_create", fields)


def test_a_calendar_id_that_is_an_email_is_fine_when_the_user_listed_it(goog):
    p = policy(goog, {**POLICY, "calendars": ["primary", "yo@example.com"]})
    action = p.check("calendar_create", {"calendar_id": "yo@example.com", "title": "x",
                                         "start": START, "duration_minutes": 5})
    assert action.field("calendar_id") == "yo@example.com"
    goog.configure()
    assert ga.perform(action).ok
    assert "/calendars/yo@example.com/events" in goog.calls[-1].full_url


def test_multiline_text_keeps_its_paragraphs_but_nothing_else(goog):
    p = policy(goog)
    a = p.check("note_create", {"title": "x", "body": "uno\r\ndos\x07\n\n\n\ntres"})
    assert a.field("body") == "uno\ndos\n\ntres"
    t = p.check("task_create", {"title": "a\nb\tc"})
    assert t.field("title") == "a b c"


# --- the token: renewal, and never the read token -----------------------------------

CREDS = {"access_token": "old-access", "refresh_token": "the-refresh",       # gitleaks:allow
         "client_id": "cid", "client_secret": "csecret"}                        # gitleaks:allow


def test_an_expired_access_token_is_renewed_once_and_the_request_repeated(goog):
    path = goog.token_file("write_token.json", CREDS)
    goog.monkeypatch.setenv("GOOGLE_WRITE_TOKEN_FILE", str(path))
    steps = []

    def handler(req):
        steps.append(req.full_url.split("?")[0])
        if "oauth2.googleapis.com" in req.full_url:
            return _Resp(json.dumps({"access_token": "new-access"}).encode())
        if req.headers.get("Authorization") == "Bearer old-access":
            return urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))
        return _Resp(json.dumps({"id": "t1"}).encode())

    goog.reply["handler"] = handler
    p = policy(goog)
    assert ga.perform(p.check("task_create", {"title": "x"})).ok is True
    assert [u.split("/")[2] for u in steps] == ["tasks.googleapis.com", "oauth2.googleapis.com",
                                               "tasks.googleapis.com"]
    stored = json.loads(path.read_text())
    assert stored["access_token"] == "new-access" and stored["refresh_token"] == "the-refresh"
    assert (path.stat().st_mode & 0o777) == 0o600


def test_a_second_401_is_a_code_and_a_403_is_not_retried(goog):
    path = goog.token_file("write_token.json", CREDS)
    goog.monkeypatch.setenv("GOOGLE_WRITE_TOKEN_FILE", str(path))

    def handler(req):
        if "oauth2.googleapis.com" in req.full_url:
            return _Resp(json.dumps({"access_token": "new-access"}).encode())
        return urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))

    goog.reply["handler"] = handler
    p = policy(goog)
    out = ga.perform(p.check("task_create", {"title": "x"}))
    assert out.ok is False and out.code == "http_401"
    goog.calls.clear()
    goog.reply["handler"] = lambda req: urllib.error.HTTPError(req.full_url, 403, "No", {},
                                                               io.BytesIO(b"{}"))
    out = ga.perform(p.check("task_create", {"title": "x"}))
    assert out.code == "http_403" and len(goog.calls) == 1


def test_a_revoked_grant_says_reauth(goog):
    path = goog.token_file("write_token.json", CREDS)
    goog.monkeypatch.setenv("GOOGLE_WRITE_TOKEN_FILE", str(path))

    def handler(req):
        if "oauth2.googleapis.com" in req.full_url:
            return urllib.error.HTTPError(req.full_url, 400, "Bad", {},
                                          io.BytesIO(b'{"error": "invalid_grant"}'))
        return urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))

    goog.reply["handler"] = handler
    out = ga.perform(policy(goog).check("task_create", {"title": "x"}))
    assert out.ok is False and out.code == "reauth"


def test_the_same_refresh_token_in_the_read_file_is_refused(goog, monkeypatch):
    read_path = goog.token_file("read.json", {"access_token": "other-access",
                                              "refresh_token": CREDS["refresh_token"]})
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(read_path))
    monkeypatch.setenv("GOOGLE_WRITE_TOKEN_FILE", str(goog.token_file("write.json", CREDS)))
    out = ga.perform(policy(goog).check("task_create", {"title": "x"}))
    assert out.ok is False and not goog.calls


def test_the_same_file_for_both_is_refused(goog, monkeypatch):
    both = goog.token_file("both.json", CREDS)
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(both))
    monkeypatch.setenv("GOOGLE_WRITE_TOKEN_FILE", str(both))
    out = ga.perform(policy(goog).check("task_create", {"title": "x"}))
    assert out.ok is False and not goog.calls


def test_a_proxy_in_the_environment_is_not_used(goog, monkeypatch):
    monkeypatch.setenv("https_proxy", "http://proxy.invalid:3128")
    import urllib.request
    # build_opener skips the default ProxyHandler (the one that reads
    # https_proxy) when it is given its own, and an empty one adds nothing.
    assert not any(isinstance(h, urllib.request.ProxyHandler) for h in ga._OPENER.handlers)
    assert not any(key.endswith("_proxy") for key in ga._OPENER.handle_open)


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

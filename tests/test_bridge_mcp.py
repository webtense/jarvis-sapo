"""jarvis_bridge_mcp.py: the stdio MCP protocol, with simulated stdin/stdout
and a fake at the network boundary (`urllib.request.urlopen`). No server, no
socket, no real token."""
import io
import json
import urllib.error

import pytest

import jarvis_bridge_mcp as mcp

FAKE_TOKEN = "token-de-prueba-aaaa"


class FakeResponse:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._raw


@pytest.fixture
def wire(monkeypatch, tmp_path):
    """Token file + environment + a recording fake for urlopen."""
    token_file = tmp_path / "bridge-token"
    token_file.write_text(FAKE_TOKEN + "\n")
    monkeypatch.setenv("JARVIS_BRIDGE_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("JARVIS_BRIDGE_URL", "http://127.0.0.1:9/internal/bridge")
    monkeypatch.setenv("JARVIS_BRIDGE_SESSION", "mi-proyecto")
    seen = []
    state = {"reply": {"ok": True, "text": "hecho"}, "raise": None}

    def fake_urlopen(req, timeout=None, context=None):
        seen.append({"url": req.full_url, "timeout": timeout,
                     "auth": req.get_header("Authorization"),
                     "body": json.loads(req.data.decode())})
        if state["raise"] is not None:
            raise state["raise"]
        return FakeResponse(state["reply"])

    monkeypatch.setattr(mcp.urllib.request, "urlopen", fake_urlopen)
    return seen, state


def run(monkeypatch, *messages):
    """Feed these messages (dicts, or raw strings) to main() and return the
    replies it wrote, parsed."""
    lines = "".join((m if isinstance(m, str) else json.dumps(m)) + "\n"
                    for m in messages)
    out = io.StringIO()
    monkeypatch.setattr(mcp.sys, "stdin", io.StringIO(lines))
    monkeypatch.setattr(mcp.sys, "stdout", out)
    mcp.main()
    return [json.loads(line) for line in out.getvalue().splitlines()]


def rpc(method, params=None, rid=1):
    msg = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def tool_call(name, arguments, rid=1):
    return rpc("tools/call", {"name": name, "arguments": arguments}, rid)


# --- protocol ------------------------------------------------------------

def test_initialize_names_the_bridge_and_offers_tools(monkeypatch, wire):
    (reply,) = run(monkeypatch, rpc("initialize"))
    result = reply["result"]
    assert reply["id"] == 1
    assert result["protocolVersion"] == mcp.PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "jarvis-bridge"
    assert "tools" in result["capabilities"]


def test_a_notification_gets_no_reply(monkeypatch, wire):
    replies = run(monkeypatch,
                  {"jsonrpc": "2.0", "method": "notifications/initialized"},
                  {"jsonrpc": "2.0", "method": "no/such/thing"},
                  rpc("ping", rid=7))
    assert [r["id"] for r in replies] == [7]


def test_tools_list_is_exactly_the_four_bridge_tools(monkeypatch, wire):
    (reply,) = run(monkeypatch, rpc("tools/list"))
    tools = reply["result"]["tools"]
    assert {t["name"] for t in tools} == {
        "jarvis_speak", "jarvis_ask_user", "jarvis_notify", "jarvis_remember"}
    for t in tools:
        assert t["inputSchema"]["type"] == "object" and t["description"]


def test_an_unknown_method_is_a_jsonrpc_error(monkeypatch, wire):
    (reply,) = run(monkeypatch, rpc("resources/list"))
    assert reply["error"]["code"] == -32601


def test_garbage_and_blank_lines_do_not_kill_the_server(monkeypatch, wire):
    replies = run(monkeypatch, "this is not json", "", "[1, 2]",
                  rpc("ping", rid=3))
    assert [r["id"] for r in replies] == [3]


def test_an_unknown_tool_or_bad_arguments_are_tool_errors(monkeypatch, wire):
    seen, _ = wire
    replies = run(monkeypatch,
                  tool_call("steer_session", {"name": "x", "prompt": "y"}, 1),
                  tool_call("run_command", {}, 2),
                  rpc("tools/call", {"name": "jarvis_speak",
                                     "arguments": ["no"]}, 3))
    assert all(r["result"]["isError"] is True for r in replies)
    assert seen == []                       # nothing was forwarded


# --- forwarding ----------------------------------------------------------

def test_speak_goes_to_its_route_with_the_bridge_token(monkeypatch, wire):
    seen, _ = wire
    (reply,) = run(monkeypatch, tool_call("jarvis_speak", {"text": "hola"}))
    assert reply["result"] == {"content": [{"type": "text", "text": "hecho"}],
                               "isError": False}
    (call,) = seen
    assert call["url"] == "http://127.0.0.1:9/internal/bridge/speak"
    assert call["auth"] == f"Bearer {FAKE_TOKEN}"
    assert call["body"] == {"text": "hola", "session": "mi-proyecto"}


def test_only_the_declared_fields_are_forwarded(monkeypatch, wire):
    seen, _ = wire
    run(monkeypatch, tool_call("jarvis_notify", {
        "title": "t", "message": "m", "tool": "steer_session",
        "Authorization": "Bearer otro", "session": "suplantada"}))
    assert seen[0]["body"] == {"title": "t", "message": "m",
                               "session": "mi-proyecto"}
    assert seen[0]["auth"] == f"Bearer {FAKE_TOKEN}"


@pytest.mark.parametrize("name,args,route", [
    ("jarvis_speak", {"text": "x"}, "speak"),
    ("jarvis_ask_user", {"question": "x"}, "ask"),
    ("jarvis_notify", {"title": "x", "message": "y"}, "notify"),
    ("jarvis_remember", {"fact": "x"}, "remember"),
])
def test_each_tool_has_its_own_route(monkeypatch, wire, name, args, route):
    seen, _ = wire
    run(monkeypatch, tool_call(name, args))
    assert seen[0]["url"].endswith(f"/internal/bridge/{route}")


def test_the_session_defaults_to_the_folder_name(monkeypatch, wire, tmp_path):
    seen, _ = wire
    monkeypatch.delenv("JARVIS_BRIDGE_SESSION")
    folder = tmp_path / "proyecto-x"
    folder.mkdir()
    monkeypatch.chdir(folder)
    run(monkeypatch, tool_call("jarvis_speak", {"text": "hola"}))
    assert seen[0]["body"]["session"] == "proyecto-x"


def test_ask_waits_longer_than_the_server_does(monkeypatch, wire):
    seen, _ = wire
    run(monkeypatch,
        tool_call("jarvis_ask_user", {"question": "q", "timeout_sec": 60}, 1),
        tool_call("jarvis_ask_user", {"question": "q", "timeout_sec": 9999}, 2),
        tool_call("jarvis_ask_user", {"question": "q", "timeout_sec": "ya"}, 3),
        tool_call("jarvis_ask_user", {"question": "q", "timeout_sec": 1}, 4))
    sent = [c["body"]["timeout_sec"] for c in seen]
    assert sent == [60, mcp.ASK_MAX_SEC, mcp.ASK_DEFAULT_SEC, mcp.ASK_MIN_SEC]
    for call, wait in zip(seen, sent):
        assert call["timeout"] >= wait + 30.0 + 15.0      # speak wait + margin
    assert seen[0]["timeout"] > mcp.TIMEOUT_SEC


def test_the_ask_wait_matches_the_servers_limits():
    import bridge
    assert mcp.ASK_MIN_SEC == bridge.ASK_TIMEOUT_MIN
    assert mcp.ASK_MAX_SEC == bridge.ASK_TIMEOUT_MAX
    assert mcp.ASK_DEFAULT_SEC == bridge.ASK_TIMEOUT_DEFAULT
    assert mcp.ASK_HEADROOM_SEC >= bridge.SPEAK_WAIT_MAX


def test_the_servers_refusal_comes_back_as_a_tool_error(monkeypatch, wire):
    _, state = wire
    state["reply"] = {"ok": False, "text": "Demasiado seguido"}
    (reply,) = run(monkeypatch, tool_call("jarvis_speak", {"text": "x"}))
    assert reply["result"]["isError"] is True
    assert reply["result"]["content"][0]["text"] == "Demasiado seguido"


def test_the_answer_is_passed_through(monkeypatch, wire):
    _, state = wire
    state["reply"] = {"ok": True, "text": "sin respuesta"}
    (reply,) = run(monkeypatch, tool_call("jarvis_ask_user", {"question": "q"}))
    assert reply["result"]["content"][0]["text"] == "sin respuesta"
    assert reply["result"]["isError"] is False


# --- failures say something useful and never the token -------------------

@pytest.mark.parametrize("failure", [
    urllib.error.HTTPError("http://x", 401, "no", {}, None),
    urllib.error.HTTPError("http://x", 500, "boom", {}, None),
    urllib.error.URLError("refused"),
    OSError("socket"),
    TimeoutError("slow"),
])
def test_a_failure_is_a_sentence_without_the_token(monkeypatch, wire, failure):
    _, state = wire
    state["raise"] = failure
    (reply,) = run(monkeypatch, tool_call("jarvis_speak", {"text": "x"}))
    text = reply["result"]["content"][0]["text"]
    assert reply["result"]["isError"] is True and text
    assert FAKE_TOKEN not in text and FAKE_TOKEN not in json.dumps(reply)


def test_an_unreadable_server_reply_is_an_error(monkeypatch, wire):
    _, state = wire
    state["reply"] = ["not", "an", "object"]
    (reply,) = run(monkeypatch, tool_call("jarvis_speak", {"text": "x"}))
    assert reply["result"]["isError"] is True


def test_no_token_file_is_not_a_crash(monkeypatch, wire):
    seen, _ = wire
    monkeypatch.setenv("JARVIS_BRIDGE_TOKEN_FILE", "/nonexistent/bridge-token")
    (reply,) = run(monkeypatch, tool_call("jarvis_speak", {"text": "x"}))
    assert reply["id"] == 1
    assert seen[0]["auth"] == "Bearer "          # the server will answer 401


def test_the_token_is_never_in_any_reply_to_the_tool_list(monkeypatch, wire):
    replies = run(monkeypatch, rpc("initialize", rid=1), rpc("tools/list", rid=2))
    assert FAKE_TOKEN not in json.dumps(replies)


# --- it matches the server it talks to ----------------------------------

def test_every_route_it_calls_exists_on_the_server(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    import importlib
    import server
    importlib.reload(server)
    paths = {getattr(r, "path", "") for r in server.app.routes}
    for route, _ in mcp._ROUTES.values():
        assert f"/internal/bridge/{route}" in paths

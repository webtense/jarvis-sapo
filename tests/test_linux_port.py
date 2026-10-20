"""The Linux branches of the platform modules.

Same technique as the macOS suite: every subprocess is faked at the seam, so
these run on any runner. `as_linux` (tests/conftest.py) flips `host.is_linux`.
"""
import asyncio
import json
import shutil
from unittest.mock import AsyncMock, MagicMock

import pytest

import actions
import dialog
import notifier
import preflight
import screen


class _Proc:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode, self._o, self._e = returncode, stdout, stderr

    async def communicate(self, input=None):
        return self._o, self._e


def _spawn(monkeypatch, module, proc=None):
    spawn = AsyncMock(return_value=proc or _Proc())
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    return spawn


def _which(monkeypatch, *present):
    monkeypatch.setattr(shutil, "which",
                        lambda name, *a, **k: f"/usr/bin/{name}" if name in present else None)


# ── notifier ────────────────────────────────────────────────────────────────

def test_notifier_is_available_with_notify_send(as_linux, monkeypatch):
    _which(monkeypatch, "notify-send")
    assert notifier.available() is True
    _which(monkeypatch)
    assert notifier.available() is False


@pytest.mark.asyncio
async def test_notify_uses_notify_send_with_untrusted_text_as_data(as_linux, monkeypatch):
    _which(monkeypatch, "notify-send")
    spawn = _spawn(monkeypatch, notifier)
    assert await notifier.notify("--help", "body", subtitle="sub") is True
    argv = spawn.call_args.args
    assert argv[0] == "notify-send"
    assert argv[argv.index("--") + 1:] == ("--help", "sub\nbody")


@pytest.mark.asyncio
async def test_notify_reports_failure_without_raising(as_linux, monkeypatch):
    _which(monkeypatch, "notify-send")
    _spawn(monkeypatch, notifier, _Proc(returncode=1, stderr=b"no bus"))
    assert await notifier.notify("t", "m") is False


# ── actions ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_open_browser_refuses_anything_that_is_not_a_web_url(as_linux, monkeypatch):
    spawn = _spawn(monkeypatch, actions)
    for bad in ("--gpu-launcher=evil", "javascript:alert(1)", "", "-x https://a"):
        result = await actions.open_browser(bad)
        assert result["success"] is False
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_open_browser_launches_chrome_with_the_url_as_one_argument(as_linux, monkeypatch):
    _which(monkeypatch, "google-chrome")
    spawn = _spawn(monkeypatch, actions)

    async def _hang(self_or_none=None):
        await asyncio.sleep(10)
    proc = MagicMock(returncode=None)
    proc.communicate = _hang
    spawn.return_value = proc
    result = await actions.open_browser("https://example.com/a b?x=1")
    assert result["success"] is True
    assert spawn.call_args.args == ("/usr/bin/google-chrome", "https://example.com/a b?x=1")


@pytest.mark.asyncio
async def test_chrome_tab_info_is_empty_on_linux(as_linux):
    assert await actions.get_chrome_tab_info() == {}


def test_editor_falls_back_to_xdg_open(as_linux, monkeypatch):
    _which(monkeypatch)
    assert actions._vscode_command("/x") is None


# ── screen ──────────────────────────────────────────────────────────────────

GNOME_RAW = ("""('[{"in_current_workspace":true,"wm_class":"gnome-terminal-server",'
             '"title":"~/jarvis","focus":true,"id":1},'
             '{"wm_class":"Google-chrome","title":"JARVIS","focus":false,"id":2},'
             '{"wm_class":"x","title":"","focus":false,"id":3}]',)""")


def test_parse_gnome_windows():
    got = screen.parse_gnome_windows(GNOME_RAW)
    assert [(w.app, w.title, w.frontmost) for w in got] == [
        ("gnome-terminal-server", "~/jarvis", True),
        ("Google-chrome", "JARVIS", False)]


def test_parse_gnome_windows_garbage_is_empty():
    assert screen.parse_gnome_windows("") == []
    assert screen.parse_gnome_windows("(not json)") == []


def test_parse_sway_windows():
    tree = {"nodes": [{"nodes": [
        {"type": "con", "app_id": "foot", "name": "vim", "focused": True, "nodes": []},
        {"type": "con", "app_id": None, "name": "nope", "nodes": []}]}]}
    got = screen.parse_sway_windows(json.dumps(tree))
    assert [(w.app, w.title, w.frontmost) for w in got] == [("foot", "vim", True)]


def test_parse_hypr_windows():
    raw = json.dumps([{"class": "kitty", "title": "a", "focusHistoryID": 0},
                      {"class": "firefox", "title": "b", "focusHistoryID": 1}])
    got = screen.parse_hypr_windows(raw)
    assert [(w.app, w.frontmost) for w in got] == [("kitty", True), ("firefox", False)]


def test_parse_wmctrl_windows():
    raw = ("0x04000007  0 gnome-terminal.Gnome-terminal  host  ~/jarvis\n"
           "0x04200003  0 google-chrome.Google-chrome  host  A page - Chrome\n")
    got = screen.parse_wmctrl_windows(raw, 0x04200003)
    assert [(w.app, w.title, w.frontmost) for w in got] == [
        ("Gnome-terminal", "~/jarvis", False), ("Google-chrome", "A page - Chrome", True)]


@pytest.mark.asyncio
async def test_list_windows_says_so_when_the_desktop_offers_nothing(as_linux, monkeypatch):
    _which(monkeypatch)
    with pytest.raises(screen.ScreenError):
        await screen.list_windows()


@pytest.mark.asyncio
async def test_capture_needs_imagemagick(as_linux, monkeypatch):
    _which(monkeypatch, "gnome-screenshot")
    with pytest.raises(screen.ScreenError, match="ImageMagick"):
        await screen.capture_screen()


@pytest.mark.asyncio
async def test_capture_needs_a_grabber(as_linux, monkeypatch):
    _which(monkeypatch, "magick")
    with pytest.raises(screen.ScreenError, match="no screenshot tool"):
        await screen.capture_screen()


# ── dialog (tmux) ───────────────────────────────────────────────────────────

def test_pts_ttys_are_understood_and_junk_is_not():
    assert dialog.normalize_tty("pts/3") == "/dev/pts/3"
    assert dialog.normalize_tty("/dev/pts/12") == "/dev/pts/12"
    assert dialog.normalize_tty("pts/3; rm -rf /") is None
    assert dialog.normalize_tty("pts/../../etc") is None


@pytest.mark.asyncio
async def test_answer_presses_nothing_when_no_tmux_pane_owns_the_tty(as_linux, monkeypatch):
    _which(monkeypatch, "tmux")
    sent = []

    async def fake_tmux(*args, timeout=5.0):
        if args[0] == "list-panes":
            return 0, "/dev/pts/9 %1\n", ""
        sent.append(args)
        return 0, "", ""
    monkeypatch.setattr(dialog, "_tmux", fake_tmux)
    monkeypatch.setattr(dialog, "tty_for_pid", lambda pid: "/dev/pts/3")
    assert await dialog.answer(4242, "return") == dialog.NOT_FOUND
    assert sent == []


@pytest.mark.asyncio
async def test_answer_sends_the_key_to_the_exact_pane(as_linux, monkeypatch):
    _which(monkeypatch, "tmux")
    sent = []

    async def fake_tmux(*args, timeout=5.0):
        if args[0] == "list-panes":
            return 0, "/dev/pts/3 %7\n/dev/pts/31 %8\n", ""
        sent.append(args)
        return 0, "", ""
    monkeypatch.setattr(dialog, "_tmux", fake_tmux)
    monkeypatch.setattr(dialog, "tty_for_pid", lambda pid: "/dev/pts/3")
    assert await dialog.answer(4242, "yes") == dialog.SENT
    assert await dialog.answer(4242, "2") == dialog.SENT
    assert await dialog.answer(4242, "esc") == dialog.SENT
    assert sent == [("send-keys", "-t", "%7", "Enter"),
                    ("send-keys", "-t", "%7", "2"),
                    ("send-keys", "-t", "%7", "Escape")]


@pytest.mark.asyncio
async def test_answer_still_refuses_free_text_on_linux(as_linux, monkeypatch):
    called = AsyncMock()
    monkeypatch.setattr(dialog, "_tmux", called)
    assert await dialog.answer(1, "rm -rf ~") == dialog.BAD_KEY
    called.assert_not_called()


@pytest.mark.asyncio
async def test_a_dead_tmux_server_is_not_found_not_a_crash(as_linux, monkeypatch):
    _which(monkeypatch, "tmux")

    async def fake_tmux(*args, timeout=5.0):
        return 1, "", "no server running"
    monkeypatch.setattr(dialog, "_tmux", fake_tmux)
    monkeypatch.setattr(dialog, "tty_for_pid", lambda pid: "/dev/pts/3")
    assert await dialog.answer(4242, "return") == dialog.NOT_FOUND


# ── preflight ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_macos_permission_checks_do_not_fail_on_linux(as_linux):
    assert (await preflight._check_accessibility()).status == preflight.STATUS_OK
    assert preflight._check_screen_recording_sync().status == preflight.STATUS_OK


def test_missing_linux_tools_warn_and_name_the_package(as_linux, monkeypatch):
    _which(monkeypatch)
    check = preflight._check_linux_tools_sync()
    assert check.status == preflight.STATUS_WARN
    assert "tmux" in check.message and "apt install" in check.remedy


def test_linux_tools_check_is_quiet_off_linux():
    assert preflight._check_linux_tools_sync().status == preflight.STATUS_OK


@pytest.mark.asyncio
async def test_oauth_expiry_is_read_from_the_credentials_file(as_linux, tmp_path):
    (tmp_path / ".credentials.json").write_text(json.dumps(
        {"claudeAiOauth": {"accessToken": "SECRET", "refreshTokenExpiresAt": 2_000_000_000_000}}))
    got = await preflight._read_oauth_refresh_expiry(tmp_path, 1.0)
    assert got == 2_000_000_000.0
    assert await preflight._read_oauth_refresh_expiry(tmp_path / "nope", 1.0) is None

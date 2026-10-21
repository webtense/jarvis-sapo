"""Drive the REAL dashboard bundle in a real browser, against a stub API.

WHY THIS EXISTS
---------------
There are no frontend tests and there cannot be new ones of the usual kind:
`package.json` has no test runner and "no new npm dependencies" is a hard
rule, so Jest and Vitest are both out. Every honesty rule in
`frontend/src/dashboard/` was therefore enforced by a comment — including
three that the comment described correctly and the code did the opposite of:

  * a recovered usage fetch left "Cannot read the usage limits." on screen
    above two live gauges, for ever;
  * a failed transcript fetch rendered as an empty transcript, which is
    pixel-for-pixel a run that recorded nothing;
  * the Specs document scrolled back to the top on every checkbox tick,
    directly under a comment saying that would make the page unusable.

Those are behaviours of the assembled page, not of a function — a unit test
would have had to mock the DOM, the fetch layer and the module graph, and
would have passed against all three bugs. So this drives the built page.

WHAT IT COSTS AND WHAT IT NEEDS
-------------------------------
`node` (to run `vite build`, already a dev dependency) and Playwright's
Chromium, which is already in `.venv` and already used by `browser.py`. No
new dependency of either kind. The bundle is built ONCE per session and only
when a source file is newer than it or it was not built by this module for
that language (`dist-en` / `dist-es`, never the shipped `dist`). If either is missing the tests skip
rather than fail: they are a real check on this machine, not a new
requirement for anyone cloning the repo.

THE ASYNC API, NEVER `sync_playwright`. The sync API drives its event loop
with greenlets on the calling thread, and it leaves that loop *running*
between calls: every pytest-asyncio test that ran after one of these in the
same session then died with "Runner.run() cannot be called from a running
event loop". Measured — 463 failures across the suite, none of them in the
code under test. `browser.py` uses the async API for the same reason.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

VIEWPORT = {"width": 1400, "height": 900}

REPO = Path(__file__).resolve().parent.parent
FRONTEND = REPO / "frontend"
DIST = FRONTEND / "dist"       # the shipped build; this module never writes it
# Language the bundle is compiled for -> VITE_JARVIS_LANG, which is fixed at
# BUILD time (it is how scripts/start-ubuntu.sh tells the page its language).
# "en" is the default build: the variable is REMOVED from the environment, so
# the English bundle is the one that ships when nothing is set.
LANG_ENV = {"es": "es-ES"}
BUILD_TIMEOUT_SEC = 300
# Written next to a bundle this module built, naming the language it was
# compiled for (see `_stamp_matches`).
BUNDLE_STAMP = ".jarvis-bundle-lang"


def why_unavailable() -> str | None:
    """The reason these tests cannot run here, or None."""
    try:
        import playwright.async_api           # noqa: F401
    except ImportError:
        return "playwright is not installed in this environment"
    if shutil.which("npm") is None:
        return "npm is not on PATH, so the bundle cannot be built"
    if not (FRONTEND / "node_modules").is_dir():
        return "frontend/node_modules is missing (run `npm install`)"
    return None


def _newest_source() -> float:
    newest = 0.0
    for path in list((FRONTEND / "src").rglob("*")) + [
            FRONTEND / "dashboard.html", FRONTEND / "index.html",
            FRONTEND / "vite.config.ts", FRONTEND / "package.json"]:
        if path.is_file():
            newest = max(newest, path.stat().st_mtime)
    return newest


def _wanted_lang_env(lang: str) -> str:
    """What `VITE_JARVIS_LANG` must be for this bundle: nothing for English (the
    default build), `es-ES` for Spanish."""
    return LANG_ENV.get(lang, "")


def _stamp_matches(out: Path, lang: str) -> bool:
    """Whether `out` was built by THIS module for `lang`. The mtime of the
    sources says a bundle is fresh, not what language it was compiled for: a
    `dist` that `scripts/start-ubuntu.sh` built with `VITE_JARVIS_LANG=es-ES`
    is newer than every source and would have been reused as "English",
    turning every English assertion into a failure that looks like a regression
    in the page. The stamp is written only by `build_bundle`."""
    try:
        return (out / BUNDLE_STAMP).read_text(encoding="utf-8").strip() == \
            f"lang={_wanted_lang_env(lang) or 'default'}"
    except OSError:
        return False


def build_bundle(lang: str = "en") -> Path:
    """The bundle for `lang`, rebuilt unless a bundle THIS module built for
    that language is newer than every source file.

    The same `npm run build` a developer runs — never a bespoke bundling
    path, or the thing under test stops being the thing that ships. Every
    language goes to its own folder (`dist-en`, `dist-es`) and the shipped
    `frontend/dist` is never touched: the one the service serves may have been
    compiled for a language this test run knows nothing about, and the
    English one the rest of the suite reads (`tests/test_clickjacking.py`) is
    the plain `npm run build` of CI.
    """
    out = FRONTEND / f"dist-{lang}"
    target = out / "dashboard.html"
    if (not target.is_file() or not _stamp_matches(out, lang)
            or target.stat().st_mtime < _newest_source()):
        # Whatever is in the environment, the language of the bundle is the
        # one asked for: removed for English, set for the others.
        env = {k: v for k, v in os.environ.items() if k != "VITE_JARVIS_LANG"}
        if _wanted_lang_env(lang):
            env["VITE_JARVIS_LANG"] = _wanted_lang_env(lang)
        subprocess.run(["npm", "run", "build", "--", "--outDir", out.name,
                        "--emptyOutDir"],
                       cwd=FRONTEND, check=True, env=env,
                       capture_output=True, text=True,
                       timeout=BUILD_TIMEOUT_SEC)
        (out / BUNDLE_STAMP).write_text(
            f"lang={_wanted_lang_env(lang) or 'default'}\n", encoding="utf-8")
    return out


class Api:
    """The answers the stub gives, keyed by path. Mutable mid-test.

    A route is a callable taking the parsed path and query string and
    returning `(status, body)`. `json_route` covers the common case; a
    failing route is how "the endpoint is down" is expressed.
    """

    def __init__(self) -> None:
        self.routes: dict[str, object] = {}
        self.calls: list[str] = []

    def json(self, path: str, body) -> None:
        self.routes[path] = (200, body)

    def fails(self, path: str, status: int = 500) -> None:
        self.routes[path] = (status, {"error": "stub failure"})

    def answer(self, path: str, query: str):
        self.calls.append(path if not query else f"{path}?{query}")
        route = self.routes.get(path)
        if route is None:
            return 404, {"error": "no stub for " + path}
        if callable(route):
            return route(query)
        return route


class StubServer:
    """`frontend/dist` on / and `Api` on /api, on one loopback port.

    One origin for both, which is how the built page is really served, so
    the fetches under test are the same-origin relative ones the code makes.
    """

    def __init__(self, api: Api, root: Path) -> None:
        self.api = api
        self.root = root
        handler = _handler_for(api, root)
        self._server = _QuietServer(("127.0.0.1", _free_port()), handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)

    @property
    def base(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> "StubServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class _QuietServer(ThreadingHTTPServer):
    """A browser drops keep-alive connections whenever it likes, and the
    stdlib prints a traceback for every one. They are not failures and they
    bury the actual test output."""
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
}


def _handler_for(api: Api, root: Path):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):        # keep pytest output readable
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):                    # noqa: N802 (stdlib contract)
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                status, payload = api.answer(parsed.path, parsed.query)
                self._send(status, json.dumps(payload).encode(),
                           "application/json")
                return
            name = parsed.path.lstrip("/") or "dashboard.html"
            target = (root / name).resolve()
            if not target.is_file() or root.resolve() not in target.parents:
                self._send(404, b"not found", "text/plain")
                return
            self._send(200, target.read_bytes(),
                       _CONTENT_TYPES.get(target.suffix, "text/plain"))

        def do_POST(self):                   # noqa: N802
            self.do_GET()

        def do_DELETE(self):                 # noqa: N802
            self.do_GET()

    return Handler


# ── the shapes the page needs to boot ──────────────────────────────────────
#
# Every one of these is fetched on DOMContentLoaded. A missing route is a
# 404, which the client handles, but an empty page tells a test nothing — so
# the defaults are a plausible quiet machine and each test overrides only
# what it is about.

def quiet_machine() -> Api:
    api = Api()
    api.json("/api/runs", {"runs": []})
    api.json("/api/runs/stats", {
        "period": "day", "by_status": {}, "total_runs": 0,
        "total_cost_usd": 0.0, "total_input_tokens": 0,
        "total_output_tokens": 0})
    api.json("/api/usage/limits", usage_limits())
    api.json("/api/sessions", {"sessions": [], "projects": {},
                               "taken_at": 0.0})
    api.json("/api/memory", {"documents": [], "journal": []})
    api.json("/api/projects/view", {"projects": [], "taken_at": 0.0})
    api.json("/api/specs", {"projects": []})
    api.json("/api/usage/sessions", {
        "measured": False, "scanned_at": 0.0, "active_within_sec": 90.0,
        "roots": [], "files": 0, "bytes_read": 0,
        "totals": tokens(), "own_totals": tokens(), "today": tokens(),
        "session_count": 0, "own_session_count": 0, "project_count": 0,
        "active_agents": 0, "daily": [], "models": [],
        "largest_listed": 0, "sessions": [], "own_sessions": []})
    return api


def tokens(total: int = 0) -> dict:
    return {"input": total, "output": 0, "cache_read": 0,
            "cache_creation": 0, "total": total}


def usage_limits(*keys: str) -> dict:
    """A measured reading over the named windows (default: both)."""
    names = keys or ("five_hour", "seven_day")
    return {
        "measured": True, "observed_at": 1788404571.0, "age_sec": 12.0,
        "stale": False, "stale_after_sec": 900.0, "status": "allowed",
        "windows": [{
            "key": key, "label": key.replace("_", " "),
            "utilization": 40.0, "resets_at": 1788500000.0,
            "status": "allowed", "observed_at": 1788404571.0,
            "age_sec": 12.0, "stale": False, "expired": False,
        } for key in names],
    }


@asynccontextmanager
async def dashboard(api: Api, lang: str = "en"):
    """The built page, loaded against `api`, as a `page` to drive.

    The browser is launched per test rather than shared: a session-scoped
    async fixture would need a session-scoped event loop, and the whole
    point of the async API here is not to fight pytest-asyncio. A launch is
    about half a second and the bundle is already built.

    The clock is faked BEFORE navigation, so a test can fast-forward to the
    next poll instead of sleeping through it — several of the paths under
    test are only reachable on a second fetch.
    """
    from playwright.async_api import async_playwright

    dist = build_bundle(lang)
    with StubServer(api, dist) as server:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                # The browser's own locale follows the page's language, as it
                # would on the machine that set JARVIS_LANGUAGE=es.
                options = {"locale": LANG_ENV[lang]} if lang != "en" else {}
                context = await browser.new_context(viewport=VIEWPORT,
                                                    **options)
                page = await context.new_page()
                await page.clock.install()
                await page.goto(f"{server.base}/dashboard.html",
                                wait_until="load")
                yield page
            finally:
                await browser.close()


def run_row(run_id: str = "r1", status: str = "succeeded") -> dict:
    return {
        "id": run_id, "project_name": "tony-starks-website",
        "project_path": "/tmp/tsw", "prompt": "build the landing page",
        "origin": "voice", "status": status, "resume_from": None,
        "result_text": "", "summary": "", "error": "", "exit_code": 0,
        "pid": None, "cost_usd": 0.0, "input_tokens": 10,
        "output_tokens": 20, "cache_read_tokens": 0,
        "cache_creation_tokens": 0, "num_turns": 1, "model": "sonnet",
        "created_at": 1788404000.0, "started_at": 1788404001.0,
        "ended_at": 1788404100.0,
    }

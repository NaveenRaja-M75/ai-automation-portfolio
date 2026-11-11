"""`python -m frost.server` — FROST as a local web app.

Why a browser and not tkinter: the interface you actually want — a
force-directed graph of your notes, glow, a live reactor HUD — is a canvas
rendering problem. tkinter is a widget toolkit from another era and cannot get
there. A browser can, and Python's stdlib already contains an HTTP server, so
this costs nothing to install. That is the same architecture the polished
JARVIS builds use; they are web apps on localhost, which is exactly why they
look like that.

Nothing is exposed off-machine: it binds to 127.0.0.1 only.
"""

from __future__ import annotations

import asyncio
import json
import queue
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import MODEL, VAULT, WORKSPACE
from .vault_index import build_graph, read_note

UI_DIR = Path(__file__).parent / "ui"
HOST, PORT = "127.0.0.1", 4720

_clients: list[queue.Queue] = []
_clients_lock = threading.Lock()
_session = None
_loop_ready = threading.Event()


def broadcast(kind: str, text: str) -> None:
    """Fan an event out to every open browser tab."""
    with _clients_lock:
        dead = []
        for q in _clients:
            try:
                q.put_nowait({"kind": kind, "text": text})
            except Exception:
                dead.append(q)
        for q in dead:
            _clients.remove(q)


class Server(ThreadingHTTPServer):
    """ThreadingHTTPServer that does not scream when a browser walks away.

    The stdlib prints a full traceback whenever a client disconnects mid-request
    — which for us is *every* page refresh, because the SSE stream at
    /api/events is a request that never ends until the tab closes. The result
    was thirty lines of `ConnectionAbortedError: [WinError 10053]` in the middle
    of the voice log, indistinguishable from something actually being wrong.

    These disconnects are normal and there is nothing to do about them, so they
    are swallowed. Anything else still prints: a log that hides real faults is
    worse than a noisy one.
    """

    daemon_threads = True
    BENIGN = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)

    def handle_error(self, request, client_address) -> None:  # noqa: D102
        if isinstance(sys.exc_info()[1], self.BENIGN):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:  # silence per-request noise
        pass

    # --- helpers -----------------------------------------------------------

    def _send(self, body: bytes, ctype: str = "application/json", code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload) -> None:
        self._send(json.dumps(payload).encode("utf-8"))

    # --- routes ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path)
        path = route.path

        if path in ("/", "/index.html"):
            try:
                self._send((UI_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            except OSError:
                self._send(b"UI missing", "text/plain", 500)
            return

        if path == "/api/graph":
            graph = build_graph()
            graph["meta"] = {"model": MODEL, "vault": VAULT or str(WORKSPACE)}
            self._json(graph)
            return

        if path == "/api/note":
            note_id = parse_qs(route.query).get("id", [""])[0]
            self._json(read_note(note_id))
            return

        if path == "/api/events":
            self._stream()
            return

        self._send(b"not found", "text/plain", 404)

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}") if length else {}

        if route.path == "/api/talk":
            if _session is not None:
                _session.request_talk()
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": "voice session not running"})
            return

        if route.path == "/api/ask":
            text = (payload.get("text") or "").strip()
            if text and _session is not None:
                _session.submit_text(text)
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": "no voice session"})
            return

        self._send(b"not found", "text/plain", 404)

    def _stream(self) -> None:
        """Server-sent events: one long-lived response per browser tab."""
        q: queue.Queue = queue.Queue(maxsize=200)
        with _clients_lock:
            _clients.append(q)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                try:
                    event = q.get(timeout=15)
                    data = json.dumps(event)
                except queue.Empty:
                    data = json.dumps({"kind": "ping", "text": ""})
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _clients_lock:
                if q in _clients:
                    _clients.remove(q)


def _start_voice() -> None:
    global _session
    from .voice import VoiceSession

    def worker() -> None:
        global _session
        session = VoiceSession(on_event=broadcast)
        _session = session
        _loop_ready.set()
        try:
            asyncio.run(session.run())
        except Exception as exc:  # noqa: BLE001
            broadcast("status", f"voice stopped: {exc}")
        finally:
            session.close()
            broadcast("state", "idle")

    threading.Thread(target=worker, daemon=True).start()


def main() -> int:
    _start_voice()
    server = Server((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"FROST UI on {url}   (Ctrl-C to stop)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

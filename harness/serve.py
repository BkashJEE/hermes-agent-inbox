#!/usr/bin/env python3
"""Serve the page harness for a demo: http://127.0.0.1:4733/ opens the Inbox with sample data.

    python3 harness/serve.py [PORT]

/ -> the demo (harness/demo.json, made-up Bots and repos; safe to show or record)
/live -> your own data (harness/live.json from `hermes inbox --json`; keep it off screen recordings)
"""
import functools
import http.server
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTES = {"/": "/harness/index.html?data=demo", "/live": "/harness/index.html"}


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        target = ROUTES.get(self.path.rstrip("/") or "/")
        if target:
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 4733
    http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(Handler, directory=str(ROOT))).serve_forever()

"""``hermes inbox`` and ``/inbox``: thin wrappers over hermes_inbox.py.

The script is the single source of truth (it also runs standalone), so it is loaded from its
file rather than copied here.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent / "hermes_inbox.py"


def _inbox():
    spec = importlib.util.spec_from_file_location("hermes_inbox_script", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def slash_command(raw_args: str) -> str:
    h = _inbox()
    sources = (raw_args or "").split() or None
    return h.render(h.collect(sources))


def setup_argparse(subparser) -> None:
    subparser.add_argument("action", nargs="?", default="list", choices=["list", "done", "snooze", "reset"])
    subparser.add_argument("target", nargs="*", help="done ID | snooze ID HOURS")
    subparser.add_argument("--json", action="store_true", help="one-line JSON for the desktop page")
    subparser.add_argument("--source", action="append", help="only this source (keys, vitals, sessions, crons, prs, review)")
    subparser.set_defaults(func=handle_cli)


def handle_cli(args) -> None:
    as_json = getattr(args, "json", False)
    try:
        h = _inbox()
        action, target = getattr(args, "action", "list"), getattr(args, "target", []) or []
        if action in ("done", "snooze", "reset"):
            st = h.load_state()
            if action == "done" and target:
                st[target[0]] = {"done": True, "at": time.time()}
            elif action == "snooze" and len(target) >= 2:
                st[target[0]] = {"snooze_until": time.time() + float(target[1]) * 3600}
            elif action == "reset":
                st = {}
            else:
                raise ValueError("usage: hermes inbox done ID | snooze ID HOURS | reset")
            h.save_state(st)
            print(json.dumps({"ok": True}))
            return
        data = h.collect(getattr(args, "source", None))
        print(json.dumps(data, separators=(",", ":")) if as_json else h.render(data))
    except Exception as e:  # the page shows this instead of crashing
        if as_json or getattr(args, "action", "list") != "list":
            sys.stdout.write(json.dumps({"error": f"inbox failed: {type(e).__name__}: {e}"}) + "\n")
        else:
            print(f"inbox failed: {e}")

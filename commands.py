"""``hermes inbox`` and ``/inbox``: thin wrappers over hermes_inbox.py.

The script is the single source of truth (it also runs standalone), so it is loaded from its
file rather than copied here.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent / "hermes_inbox.py"


def _inbox():
    spec = importlib.util.spec_from_file_location("hermes_agent_inbox_script", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def slash_command(raw_args: str) -> str:
    h = _inbox()
    sources = (raw_args or "").split() or None
    return h.render(h.collect(sources))


def setup_argparse(subparser) -> None:
    _inbox().add_arguments(subparser)
    subparser.set_defaults(func=handle_cli)


def handle_cli(args) -> None:
    try:
        print(_inbox().dispatch(args))
    except Exception as e:  # the page shows this instead of crashing
        if getattr(args, "json", False) or getattr(args, "action", "list") != "list":
            sys.stdout.write(json.dumps({"error": f"inbox failed: {type(e).__name__}: {e}"}) + "\n")
        else:
            print(f"inbox failed: {e}")

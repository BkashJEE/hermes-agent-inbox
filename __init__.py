"""hermes-inbox: everything your Bots and tools need from you, in one list.

Registers:
  * ``hermes inbox`` CLI command (``--json`` for the desktop page, ``done`` / ``snooze`` to clear items);
  * ``/inbox`` slash command with the text list.

The desktop page in ``desktop/plugin.js`` runs ``hermes inbox --json`` through the gateway's
``cli.exec`` RPC. Reading is read-only; ``done`` and ``snooze`` only write the inbox's own state file.
"""

from __future__ import annotations

from . import commands


def register(ctx) -> None:
    ctx.register_command(
        "inbox",
        handler=commands.slash_command,
        description="What your Bots and tools are waiting on you for (hermes-inbox)",
        args_hint="[source]",
    )
    ctx.register_cli_command(
        name="inbox",
        help="Everything your Bots and tools need from you: stuck sessions, failed crons, benched keys, PRs, reviews",
        setup_fn=commands.setup_argparse,
        handler_fn=commands.handle_cli,
    )

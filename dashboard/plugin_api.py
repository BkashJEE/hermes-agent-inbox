"""Hermes Agent Inbox backend: the Desktop page's own REST namespace, ``/api/plugins/hermes-agent-inbox/``.

Hermes mounts this router when the plugin is in ``plugins.enabled`` and the page reaches it with
``ctx.rest('/list')``. It is the documented way for a Desktop plugin to run server-side work. Unlike
the ``hermes inbox --json`` fallback it has no 48,000-character output cap, so nothing is trimmed.
Same reader as the CLI: titles, how a chat ended, counts and times; never message bodies.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

VERSION = "0.6.0"
router = APIRouter()


def _inbox():
    """hermes_inbox.py, loaded by path: the host imports this file on its own, so a package-relative
    import would not resolve."""
    name = "hermes_agent_inbox_script"
    mod = sys.modules.get(name)
    if mod is None:
        path = Path(__file__).resolve().parent.parent / "hermes_inbox.py"
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


def _str(body, key: str) -> str:
    v = body.get(key) if isinstance(body, dict) else None
    if not isinstance(v, str) or not v:
        raise HTTPException(status_code=400, detail=f"body needs a string {key!r}")
    return v


@router.get("/health")
async def health():
    return {"ok": True, "version": VERSION}


@router.get("/list")
async def list_items(app: list[str] | None = Query(default=None), source: list[str] | None = Query(default=None)):
    return await asyncio.to_thread(_inbox().collect, source or None, app or None)


@router.get("/stamp")
async def stamp():
    return await asyncio.to_thread(_inbox().change_stamp)


@router.post("/done")
async def done(body: dict):
    return await asyncio.to_thread(_inbox().mark_done, _str(body, "id"))


@router.post("/snooze")
async def snooze(body: dict):
    hours = body.get("hours") if isinstance(body, dict) else None
    if not isinstance(hours, (int, float)) or not 0 < hours <= 24 * 90:
        raise HTTPException(status_code=400, detail="body needs 'hours' between 0 and 2160")
    return await asyncio.to_thread(_inbox().snooze, _str(body, "id"), float(hours))


@router.post("/set")
async def set_source(body: dict):
    h = _inbox()
    name = _str(body, "source")
    on = body.get("on")
    if name not in h.SOURCES or not isinstance(on, bool):
        raise HTTPException(status_code=400, detail="body needs 'source' (one of SOURCES) and boolean 'on'")
    return await asyncio.to_thread(h.set_source, name, on)


@router.post("/run-cron")
async def run_cron(body: dict):
    return await asyncio.to_thread(_inbox().run_cron, _str(body, "profile"), _str(body, "id"))

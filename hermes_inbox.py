#!/usr/bin/env python3
"""Hermes Inbox: everything your Bots and tools need from you, in one list.

Each source is read-only and turns its own state into inbox items:

    keys      Key Doctor: a benched, dead or blocked login, a Bot that can't reach its model
    vitals    Vitals: red alarms
    sessions  Bot sessions in the last 48 h that ended waiting on you (approval, sign-in, a limit)
              or asked you a question you haven't opened. Judged from the last reply with the
              Session Health rules; only the session title and the kind of ending are shown.
    crons     Cron jobs whose last run failed
    prs       Your open PRs that are ready to merge, or need a fix
    review    Hermes X Studio drafts waiting for your decision

An item is {id, source, priority (now|today|later), title, detail, at, action}. Ids are stable,
so "done" and "snooze" (kept in ~/.local/state/hermes-inbox/state.json) survive refreshes.

    hermes_inbox.py [--json] [--source NAME ...]
    hermes_inbox.py done ID | snooze ID HOURS | reset
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HOME = Path.home()
HERMES = Path(os.environ.get("HERMES_ROOT", HOME / ".hermes"))
STATE = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local" / "state")) / "hermes-inbox" / "state.json"
HERMES_PY = HERMES / "hermes-agent" / "venv" / "bin" / "python"
X_STUDIO = os.environ.get("HERMES_INBOX_XSTUDIO", "http://127.0.0.1:3417")
PRIORITY = {"now": 0, "today": 1, "later": 2}

# Session endings, from ~/hermes-session-health (first match wins). Approximate by design.
ENDINGS = [
    ("needs sign-in", r"\b(sign[ -]?in|log[ -]?in|logged out|2fa|two[- ]factor|captcha|verification code)\b"),
    ("waiting on your approval", r"\b(requires approval|needs? (your )?approval|awaiting (your )?approval|single-query mode)\b"),
    ("missing a tool", r"\b(not available in this session|tool is unavailable|unknown tool|no such tool)\b"),
    ("hit a limit", r"\b(tool-call limit|max(imum)? turns|iteration limit|timed out|budget)\b"),
    ("a site refused it", r"\b(can.t provide a secure connection|connection (refused|reset)|rate limit(ed)?|http 4(03|29))\b"),
    ("screen problem", r"\b(no active window|display (is )?not reachable|capture failed)\b"),
    ("asked you a question", r"\?\s*$"),
]
STUCK = {"needs sign-in", "waiting on your approval", "missing a tool", "hit a limit", "a site refused it", "screen problem"}


# ---- pure logic (tested) ---------------------------------------------------------------------

def iso_ts(value: str | None) -> float:
    """'2026-10-11T05:52:19.229Z' -> epoch seconds (UTC); 0 when missing."""
    try:
        return float(calendar.timegm(time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")))
    except (TypeError, ValueError):
        return 0.0


def stamp(value) -> float:
    """A time that may be epoch seconds or an ISO string."""
    return float(value) if isinstance(value, (int, float)) else iso_ts(value)


def ending(text: str) -> str:
    for name, pattern in ENDINGS:
        if re.search(pattern, text or "", re.I | re.M):
            return name
    return "done"


def session_item(profile: str, bot: str, s: dict, last_reply: str, now: float) -> dict | None:
    """A session needs you if it ended stuck, or asked a question you haven't opened since."""
    kind = ending(last_reply)
    unread = (s.get("last_read_at") or 0) < (s.get("last_activity_at") or 0)
    if kind in STUCK:
        priority = "now" if kind in ("waiting on your approval", "needs sign-in") else "today"
    elif kind == "asked you a question" and unread:
        priority = "today"
    else:
        return None
    title = (s.get("title") or "Untitled session").strip()[:90]
    return {"id": f"session:{profile}:{s['id']}", "source": "sessions", "priority": priority,
            "title": f"{bot} {kind}", "detail": f"“{title}”", "at": s.get("last_activity_at") or now,
            "action": {"kind": "session", "label": "Open chat", "value": f"hermes -p {profile} chat --resume {s['id']}",
                       "session": s["id"], "profile": profile}}


def pr_item(pr: dict) -> dict | None:
    repo, n = pr["repo"], pr["n"]
    if repo.startswith("NousResearch/"):
        return None  # upstream: waiting on maintainers, nothing for you to do here
    cat = pr.get("cat")
    if cat == "ready":
        stale = (pr.get("now") or time.time()) - (pr.get("at") or 0) > 30 * 86400  # untouched a month: a paused project, not a to-do
        title, priority = f"Ready to merge: {repo.split('/')[1]} #{n}", "later" if stale else "today"
    elif cat in ("conflict", "failing-checks"):
        title, priority = f"{'Conflicts' if cat == 'conflict' else 'Checks failing'}: {repo.split('/')[1]} #{n}", "later"
    else:
        return None
    return {"id": f"pr:{repo}#{n}", "source": "prs", "priority": priority, "title": title, "detail": pr.get("title", "")[:90],
            "at": pr.get("at") or 0, "action": {"kind": "url", "label": "Open PR", "value": pr["url"]}}


def classify_pr(d: dict) -> str:
    if d.get("isDraft"):
        return "draft"
    if d.get("baseRefName") not in ("main", "master"):
        return "stacked"
    if d.get("mergeable") == "CONFLICTING" or d.get("mergeStateStatus") == "DIRTY":
        return "conflict"
    concl = [(c.get("conclusion") or c.get("state") or "").upper() for c in (d.get("statusCheckRollup") or [])]
    if any(c in ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED") for c in concl):
        return "failing-checks"
    if d.get("mergeable") == "MERGEABLE" and d.get("mergeStateStatus") in ("CLEAN", "HAS_HOOKS", "UNSTABLE"):
        return "ready"
    return "unknown"


DATE_STAMP = re.compile(r"\s*[·•|-]\s*\w{3} \d{1,2}( \d{1,2}:\d{2})?\s*(?=”?$)")


def collapse_sessions(items: list[dict]) -> list[dict]:
    """One line per recurring job: the newest session of the same Bot and title (date stamp ignored), with a count."""
    groups: dict[tuple, list[dict]] = {}
    out = []
    for it in items:
        if it["source"] != "sessions":
            out.append(it); continue
        key = (it["id"].split(":")[1], it["title"], DATE_STAMP.sub("", it["detail"]))
        groups.setdefault(key, []).append(it)
    for group in groups.values():
        group.sort(key=lambda i: -(i.get("at") or 0))
        top = dict(group[0])
        if len(group) > 1:
            top["detail"] += f"  (also {len(group) - 1} earlier time{'s' if len(group) > 2 else ''})"
        out.append(top)
    return out


def visible(items: list[dict], state: dict, now: float) -> list[dict]:
    """Drop items marked done (until they change) or snoozed (until the snooze ends); sort by urgency, newest first."""
    out = []
    for it in items:
        mark = state.get(it["id"]) or {}
        if mark.get("done") and mark.get("at", 0) >= (it.get("at") or 0):
            continue
        if mark.get("snooze_until", 0) > now:
            continue
        out.append(it)
    return sorted(out, key=lambda it: (PRIORITY[it["priority"]], -(it.get("at") or 0)))


# ---- sources (side effects, each fails soft) ----------------------------------------------------

def profiles() -> list[tuple[str, Path]]:
    found = [("default", HERMES)]
    base = HERMES / "profiles"
    if base.is_dir():
        found += [(p.name, p) for p in sorted(base.iterdir()) if (p / "config.yaml").exists()]
    return found


def bot_name(profile: str, home: Path) -> str:
    try:
        m = re.search(r"^display_name:\s*['\"]?([^'\"\n]+)", (home / "profile.yaml").read_text(), re.M)
        if m:
            name = re.split(r"\s+[—-]\s+", m.group(1).strip())[0]  # "Sanvith — Chief of Staff" -> "Sanvith"
            return name if len(name) <= 16 else name.split(" ")[0]   # "PR Babysitter" stays whole
    except OSError:
        pass
    return profile


def run_json(cmd: list[str], timeout: int = 120):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip().splitlines()
        return json.loads(out[-1]) if out else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def src_keys(now: float) -> list[dict]:
    script = HERMES / "scripts" / "key-doctor.py"
    if not script.exists():
        return []
    py = str(HERMES_PY) if HERMES_PY.exists() else sys.executable
    try:
        results = json.loads(subprocess.run([py, str(script), "--json"], capture_output=True, text=True, timeout=120).stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    items, seen = [], set()
    for r in results:
        dm = r.get("default_model") or {}
        if (dm.get("reach") or {}).get("verdict") == "blocked" and not dm.get("covered"):
            items.append({"id": f"keys:model:{r['profile']}", "source": "keys", "priority": "now",
                          "title": f"{r['profile']} can't reach its model", "detail": f"{dm.get('model')} via {dm.get('provider')}: no usable login",
                          "at": 0, "action": {"kind": "command", "label": "Key Doctor", "value": f"hermes -p {r['profile']} keydoctor"}})
        for c in r.get("credentials", []):
            state = c.get("state") or c.get("status")
            if state in ("ok", None) or (c["provider"], c["id"]) in seen:
                continue
            seen.add((c["provider"], c["id"]))
            items.append({"id": f"keys:{c['provider']}:{c['id']}:{state}", "source": "keys", "priority": "now" if state in ("benched", "dead", "expired", "wrong-auth-mode") else "today",
                          "title": f"{c['provider']} login {state.replace('-', ' ')}", "detail": f"{r['profile']}" + (f" + {len(c.get('shared_with', []))} more Bots" if c.get("shared_with") else ""),
                          "at": 0, "action": {"kind": "command", "label": "Copy fix", "value": (c.get("fix") or "").split("   #")[0] or f"hermes -p {r['profile']} keydoctor"}})
    return items


def src_vitals(now: float) -> list[dict]:
    data = run_json(["hermes", "-p", "ceo", "vitals", "--json"])
    if not isinstance(data, dict):
        return []
    items = []
    for bot in data.get("bots", []):
        for a in bot.get("alarms", []):
            if a.get("level") != "alert":
                continue
            items.append({"id": f"vitals:{bot['profile']}:{a.get('rule') or a['finding']}", "source": "vitals", "priority": "now",
                          "title": f"{bot.get('name') or bot['profile']}: {a['finding']}", "detail": a.get("treatment", ""),
                          "at": stamp((a.get("evidence") or {}).get("last")), "action": {"kind": "open", "label": "Vitals", "value": "/vitals"}})
    return items


def src_sessions(now: float, hours: float = 48) -> list[dict]:
    items = []
    since = now - hours * 3600
    for profile, home in profiles():
        db = home / "state.db"
        if not db.exists():
            continue
        try:
            c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            c.row_factory = sqlite3.Row
            rows = c.execute("select id, title, last_activity_at, last_read_at from sessions "
                             "where coalesce(archived,0)=0 and coalesce(hidden,0)=0 and last_activity_at >= ? "
                             "order by last_activity_at desc limit 40", (since,)).fetchall()
            for s in rows:
                last = c.execute("select content from messages where session_id=? and role='assistant' and coalesce(content,'')<>'' "
                                 "order by id desc limit 1", (s["id"],)).fetchone()
                item = session_item(profile, bot_name(profile, home), dict(s), last[0] if last else "", now)
                if item:
                    items.append(item)
            c.close()
        except sqlite3.Error:
            continue
    return items


def src_crons(now: float) -> list[dict]:
    items = []
    for profile, home in profiles():
        try:
            raw = json.loads((home / "cron" / "jobs.json").read_text())
        except (OSError, ValueError):
            continue
        jobs = raw.get("jobs", []) if isinstance(raw, dict) else raw
        for j in jobs if isinstance(jobs, list) else []:
            if not isinstance(j, dict) or j.get("enabled") is False or j.get("paused_at"):
                continue
            failed = str(j.get("last_status") or "").lower() in ("error", "failed", "failure") or (j.get("failure_streak") or 0) > 0
            if failed:
                items.append({"id": f"cron:{profile}:{j.get('id')}:{j.get('last_run_at')}", "source": "crons", "priority": "now",
                              "title": f"Cron failed: {j.get('name') or j.get('id')}", "detail": f"{profile}" + (f" · {str(j.get('last_error'))[:80]}" if j.get("last_error") else ""),
                              "at": stamp(j.get("last_run_at")), "action": {"kind": "command", "label": "Runs", "value": f"hermes -p {profile} cron runs {j.get('id')}"}})
    return items


PR_CACHE = STATE.parent / "prs-cache.json"


def src_prs(now: float, max_age: float = 600) -> list[dict]:
    """Checking every PR's mergeability is slow (one call each), so the result is reused for 10 minutes."""
    try:
        cached = json.loads(PR_CACHE.read_text())
        if now - cached["at"] < max_age:
            return cached["items"]
    except (OSError, ValueError, KeyError):
        pass
    items = _src_prs(now)
    PR_CACHE.parent.mkdir(parents=True, exist_ok=True)
    PR_CACHE.write_text(json.dumps({"at": now, "items": items}))
    return items


def _src_prs(now: float) -> list[dict]:
    listing = run_json(["gh", "search", "prs", "--author", "@me", "--state", "open", "--json", "repository,number,title,url,updatedAt", "--limit", "100"], 60)
    if not isinstance(listing, list):
        return []
    mine = [p for p in listing if not p["repository"]["nameWithOwner"].startswith("NousResearch/")]

    def one(p: dict) -> dict | None:
        repo = p["repository"]["nameWithOwner"]
        d = run_json(["gh", "pr", "view", str(p["number"]), "-R", repo, "--json", "isDraft,baseRefName,mergeable,mergeStateStatus,statusCheckRollup"], 30) or {}
        return pr_item({"repo": repo, "n": p["number"], "title": p["title"], "url": p["url"], "cat": classify_pr(d), "now": now,
                        "at": iso_ts(p.get("updatedAt"))})

    with ThreadPoolExecutor(max_workers=8) as pool:  # one gh call per PR; in parallel it's seconds, not a minute
        items = [it for it in pool.map(one, mine) if it]
    return items


def src_review(now: float) -> list[dict]:
    try:
        with urllib.request.urlopen(f"{X_STUDIO}/api/state", timeout=5) as r:
            data = json.loads(r.read())["data"]
    except Exception:  # X Studio not running: nothing to report
        return []
    items = []
    for it in data.get("items", []):
        if it.get("status") != "REVIEW" or it.get("dataOrigin") == "STARTER" or str(it.get("id", "")).startswith("demo-"):
            continue
        items.append({"id": f"review:{it['id']}", "source": "review", "priority": "today", "title": f"Review: {it.get('title', '')[:70]}",
                      "detail": f"{it.get('contentKey', '')} in Hermes X Studio", "at": iso_ts(it.get("updatedAt")),
                      "action": {"kind": "url", "label": "Open X Studio", "value": X_STUDIO}})
    return items


SOURCES = {"keys": src_keys, "vitals": src_vitals, "sessions": src_sessions, "crons": src_crons, "prs": src_prs, "review": src_review}


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1))


def collect(names: list[str] | None = None) -> dict:
    now = time.time()
    items, errors, counts = [], {}, {}
    for name, fn in SOURCES.items():
        if names and name not in names:
            continue
        try:
            got = fn(now)
            counts[name] = len(got)
            items += got
        except Exception as e:  # one broken source must not hide the others
            errors[name] = f"{type(e).__name__}: {e}"[:160]
    merged = collapse_sessions(items)
    shown = visible(merged, load_state(), now)
    return {"checked_at": now, "items": shown, "counts": counts, "hidden": len(merged) - len(shown), "errors": errors,
            "summary": {p: sum(1 for i in shown if i["priority"] == p) for p in PRIORITY}}


def render(data: dict) -> str:
    s = data["summary"]
    lines = [f"Hermes Inbox: {s['now']} now · {s['today']} today · {s['later']} later" + (f"  ({data['hidden']} done/snoozed)" if data["hidden"] else "")]
    for it in data["items"]:
        lines.append(f"  [{it['priority']:>5}] {it['title']} — {it['detail']}")
        lines.append(f"          {it['action']['label']}: {it['action']['value']}")
    for k, v in data["errors"].items():
        lines.append(f"  (couldn't read {k}: {v})")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Everything your Hermes Bots and tools need from you")
    ap.add_argument("cmd", nargs="?", default="list", choices=["list", "done", "snooze", "reset"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--source", action="append")
    a = ap.parse_args()
    if a.cmd == "done" and a.args:
        st = load_state(); st[a.args[0]] = {"done": True, "at": time.time()}; save_state(st); print(json.dumps({"ok": True})); return 0
    if a.cmd == "snooze" and len(a.args) >= 2:
        st = load_state(); st[a.args[0]] = {"snooze_until": time.time() + float(a.args[1]) * 3600}; save_state(st); print(json.dumps({"ok": True})); return 0
    if a.cmd == "reset":
        save_state({}); print(json.dumps({"ok": True})); return 0
    data = collect(a.source)
    print(json.dumps(data, separators=(",", ":")) if a.json else render(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())

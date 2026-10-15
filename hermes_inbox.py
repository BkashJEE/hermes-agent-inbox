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
def hermes_root() -> Path:
    """The main Hermes folder: HERMES_ROOT, else HERMES_HOME (a profile home maps back to its root), else ~/.hermes."""
    if os.environ.get("HERMES_ROOT"):
        return Path(os.environ["HERMES_ROOT"]).expanduser()
    home = os.environ.get("HERMES_HOME")
    if home:
        p = Path(home).expanduser()
        return p.parent.parent if p.parent.name == "profiles" else p
    return HOME / ".hermes"


HERMES = hermes_root()
STATE = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local" / "state")) / "hermes-inbox" / "state.json"
HERMES_PY = HERMES / "hermes-agent" / "venv" / "bin" / "python"
FILED = HERMES / "inbox"          # cards that Bots, crons and scripts post with `hermes inbox add`
CONFIG = HERMES / "inbox.json"    # optional settings, see README


def config() -> dict:
    """{"xstudio_url": "...", "ignore_repos": ["owner/repo", ...], "stale_pr_days": 30, "sources": {"prs": false}}"""
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}
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
    ("asked you to check", r"\b(please (check|confirm|review|approve|test|try)|after you (approve|check|confirm)|once you (approve|confirm|check)|let me know|your call|waiting for your (ok|go-ahead|answer))\b"),
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


def when(ts: float) -> str:
    return time.strftime("%b %d %H:%M", time.localtime(ts)) if ts else "unknown"


WHY = {
    "needs sign-in": "Its last reply asked you to sign in somewhere. It can't go on until you do.",
    "waiting on your approval": "It stopped at a step that needs your OK.",
    "missing a tool": "It needed a tool this session doesn't have.",
    "hit a limit": "It ran out of turns, time or budget before finishing.",
    "a site refused it": "A site refused or rate-limited it.",
    "screen problem": "It couldn't see or reach the screen.",
    "asked you a question": "It ended on a question and you haven't opened the chat since.",
    "asked you to check": "It ended by asking you to check, confirm or approve something.",
}


def ending(text: str) -> str:
    """How a reply ends. Only its closing part counts: a long answer that merely mentions a login or a
    rate limit somewhere in the middle is not stuck."""
    tail = (text or "").strip()[-500:]
    for name, pattern in ENDINGS:
        if re.search(pattern, tail, re.I | re.M):
            return name
    return "done"


def session_item(profile: str, bot: str, s: dict, last_reply: str, now: float) -> dict | None:
    """A session needs you if it ended stuck, or asked a question you haven't opened since."""
    kind = ending(last_reply)
    unread = (s.get("last_read_at") or 0) < (s.get("last_activity_at") or 0)
    if kind in STUCK:
        priority = "now" if kind in ("waiting on your approval", "needs sign-in") else "today"
    elif kind in ("asked you a question", "asked you to check") and unread:
        priority = "today"
    else:
        return None
    title = (s.get("title") or "Untitled session").strip()[:90]
    return {"id": f"session:{profile}:{s['id']}", "source": "sessions", "priority": priority,
            "title": f"{bot} {kind}", "detail": f"“{title}”", "at": s.get("last_activity_at") or now,
            "why": WHY.get(kind, ""), "agent": bot, "app": "hermes",
            "facts": [["Bot", f"{bot} ({profile})"], ["Session", title], ["Last active", when(s.get("last_activity_at") or 0)],
                      ["Opened by you", when(s["last_read_at"]) if s.get("last_read_at") else "never"]],
            "more": [{"kind": "command", "label": "Copy resume command", "value": f"hermes -p {profile} chat --resume {s['id']}"}],
            "action": {"kind": "session", "label": "Open chat", "value": f"hermes -p {profile} chat --resume {s['id']}",
                       "session": s["id"], "profile": profile}}


def pr_item(pr: dict) -> dict | None:
    repo, n = pr["repo"], pr["n"]
    if repo.startswith("NousResearch/"):
        return None  # upstream: waiting on maintainers, nothing for you to do here
    cat = pr.get("cat")
    if cat == "ready":
        stale = (pr.get("now") or time.time()) - (pr.get("at") or 0) > pr.get("stale_days", 30) * 86400  # untouched a month: a paused project, not a to-do
        title, priority = f"Ready to merge: {repo.split('/')[1]} #{n}", "later" if stale else "today"
    elif cat in ("conflict", "failing-checks"):
        title, priority = f"{'Conflicts' if cat == 'conflict' else 'Checks failing'}: {repo.split('/')[1]} #{n}", "later"
    else:
        return None
    why = {"ready": "Checks pass and it merges cleanly.", "conflict": "main moved on; it needs a rebase before it can merge.",
           "failing-checks": "One or more checks failed."}[cat]
    return {"id": f"pr:{repo}#{n}", "source": "prs", "priority": priority, "title": title, "detail": pr.get("title", "")[:90],
            "at": pr.get("at") or 0, "why": why + (" Untouched for a month, so it's parked under Later." if cat == "ready" and priority == "later" else ""),
            "facts": [["Repo", repo], ["Title", pr.get("title", "")], ["Last update", when(pr.get("at") or 0)]],
            "more": [{"kind": "command", "label": "Copy checkout command", "value": f"gh pr checkout {n} -R {repo}"}],
            "action": {"kind": "url", "label": "Open PR", "value": pr["url"]}}


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
            top["facts"] = list(top.get("facts") or []) + [["Also happened", ", ".join(when(g.get("at") or 0) for g in group[1:])]]
            top["why"] = (top.get("why") or "") + " It's a recurring job, so this will keep happening until it's fixed."
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
            items.append({"id": f"keys:model:{r['profile']}", "source": "keys", "priority": "now", "agent": r["profile"],
                          "title": f"{r['profile']} can't reach its model", "detail": f"{dm.get('model')} via {dm.get('provider')}: no usable login",
                          "at": 0, "action": {"kind": "command", "label": "Key Doctor", "value": f"hermes -p {r['profile']} keydoctor"}})
        for c in r.get("credentials", []):
            state = c.get("state") or c.get("status")
            if state in ("ok", None) or (c["provider"], c["id"]) in seen:
                continue
            seen.add((c["provider"], c["id"]))
            items.append({"agent": r["profile"] + (f" +{len(c.get('shared_with', []))}" if c.get("shared_with") else ""),
                          "id": f"keys:{c['provider']}:{c['id']}:{state}", "source": "keys", "priority": "now" if state in ("benched", "dead", "expired", "wrong-auth-mode") else "today",
                          "title": f"{c['provider']} login {state.replace('-', ' ')}", "detail": f"{r['profile']}" + (f" + {len(c.get('shared_with', []))} more Bots" if c.get("shared_with") else ""),
                          "at": 0, "action": {"kind": "command", "label": "Copy fix", "value": (c.get("fix") or "").split("   #")[0] or f"hermes -p {r['profile']} keydoctor"}})
    return items


def src_vitals(now: float) -> list[dict]:
    data = run_json(["hermes", "vitals", "--json"])  # the Vitals plugin reads every Bot
    if not isinstance(data, dict):
        return []
    items = []
    for bot in data.get("bots", []):
        for a in bot.get("alarms", []):
            if a.get("level") != "alert":
                continue
            items.append({"id": f"vitals:{bot['profile']}:{a.get('rule') or a['finding']}", "source": "vitals", "priority": "now",
                          "agent": bot.get("name") or bot["profile"],
                          "title": f"{bot.get('name') or bot['profile']}: {a['finding']}", "detail": a.get("treatment", ""),
                          "why": f"Reading {a.get('reading', '?')}; normal is {a.get('normal', '?')}.",
                          "facts": [["Seen in", f"{(a.get('evidence') or {}).get('file', '?')} · {(a.get('evidence') or {}).get('source', '?')}"],
                                    ["Count", str((a.get("evidence") or {}).get("count", "?"))],
                                    ["Last seen", when(stamp((a.get("evidence") or {}).get("last")))]],
                          "more": [{"kind": "command", "label": "Copy log search", "value": f"grep -n {(a.get('evidence') or {}).get('source', '')} {'~/.hermes' if bot['profile'] == 'default' else '~/.hermes/profiles/' + bot['profile']}/logs/{(a.get('evidence') or {}).get('file', 'errors.log')} | tail -20"}],
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
                              "agent": bot_name(profile, home),
                              "why": f"Failed {j.get('failure_streak') or 1} time(s) in a row." + (f" Last error: {str(j.get('last_error'))[:160]}" if j.get("last_error") else ""),
                              "facts": [["Schedule", str(j.get("schedule") or j.get("cron") or "?")], ["Last run", when(stamp(j.get("last_run_at")))],
                                        ["Next run", when(stamp(j.get("next_run_at")))]],
                              "title": f"Cron failed: {j.get('name') or j.get('id')}", "detail": f"{profile}" + (f" · {str(j.get('last_error'))[:80]}" if j.get("last_error") else ""),
                              "at": stamp(j.get("last_run_at")), "action": {"kind": "command", "label": "Runs", "value": f"hermes -p {profile} cron runs {j.get('id')}"}})
    return items


# ---- other agent apps: Claude Code and Codex keep their sessions as JSONL on disk ---------------

APPS = {"claude": ("Claude Code", "Claude"), "codex": ("Codex", "Codex")}
TAG = re.compile(r"<[^>]{1,40}>")


def transcript_item(app: str, sid: str, title: str, last_role: str, last_text: str, at: float, cwd: str, now: float) -> dict | None:
    """A Claude Code / Codex session is waiting on you when the assistant spoke last and ended stuck or on a question."""
    if last_role != "assistant":
        return None
    kind = ending(TAG.sub(" ", last_text or "").strip())
    if kind == "done":
        return None
    label, short = APPS[app]
    title = (title or "Untitled session").strip()[:90]
    resume = f"claude --resume {sid}" if app == "claude" else f"codex resume {sid}"
    if cwd:
        resume = f"cd {json.dumps(cwd)} && {resume}"
    return {"id": f"{app}:{sid}", "source": app, "app": app, "priority": "now" if kind in ("needs sign-in", "waiting on your approval") else "today",
            "title": f"{short} {kind}", "detail": f"“{title}”", "at": at, "agent": label,
            "why": "It ended on a question and nobody has replied since." if kind == "asked you a question" else WHY.get(kind, ""),
            "facts": [["Session", title], ["Folder", cwd or "?"], ["Last active", when(at)]],
            "action": {"kind": "command", "label": "Resume", "value": resume}}


def _tail_lines(path: Path, size: int = 2_000_000) -> list[str]:
    """The last ~2 MB of a JSONL file: enough for the final messages of a long session."""
    with path.open("rb") as f:
        f.seek(0, 2)
        end = f.tell()
        f.seek(max(0, end - size))
        data = f.read().decode("utf-8", "replace")
    lines = data.splitlines()
    return lines[1:] if end > size else lines


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") in ("text", "output_text", "input_text"))


def src_claude(now: float, hours: float = 48) -> list[dict]:
    """Claude Code sessions in ~/.claude/projects (opt in: "sources": {"claude": true})."""
    if not (config().get("sources") or {}).get("claude"):
        return []
    items = []
    for f in (HOME / ".claude" / "projects").glob("*/*.jsonl"):
        try:
            mtime = f.stat().st_mtime
            if mtime < now - hours * 3600:
                continue
            title, role, text, cwd = "", "", "", ""
            for line in _tail_lines(f):
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                t = d.get("type")
                if t in ("custom-title", "ai-title", "summary"):
                    title = d.get("customTitle") or d.get("aiTitle") or d.get("summary") or title
                elif t in ("user", "assistant") and not d.get("isSidechain") and not d.get("isMeta"):
                    msg = d.get("message") or {}
                    body = _text(msg.get("content"))
                    if t == "user" and not body.strip():
                        continue  # a tool result, not you replying
                    role, text, cwd = t, body, d.get("cwd") or cwd
            item = transcript_item("claude", f.stem, title or f.parent.name.replace("-", "/"), role, text, mtime, cwd, now)
            if item:
                items.append(item)
        except OSError:
            continue
    return items


def src_codex(now: float, hours: float = 48) -> list[dict]:
    """Codex threads in ~/.codex/sessions (opt in: "sources": {"codex": true})."""
    if not (config().get("sources") or {}).get("codex"):
        return []
    base = HOME / ".codex"
    names = {}
    try:
        for line in (base / "session_index.jsonl").read_text().splitlines():
            try:
                d = json.loads(line)
                names[d["id"]] = d.get("thread_name") or ""
            except (ValueError, KeyError):
                continue
    except OSError:
        pass
    items = []
    for f in (base / "sessions").glob("*/*/*/rollout-*.jsonl"):
        try:
            mtime = f.stat().st_mtime
            if mtime < now - hours * 3600:
                continue
            sid, role, text, cwd = "", "", "", ""
            for line in _tail_lines(f):
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                p = d.get("payload") or {}
                if d.get("type") == "session_meta":
                    sid, cwd = p.get("id") or sid, p.get("cwd") or cwd
                elif d.get("type") == "response_item" and p.get("type") == "message" and p.get("role") in ("user", "assistant"):
                    body = _text(p.get("content"))
                    if p["role"] == "user" and body.lstrip().startswith("<"):
                        continue  # context Codex injects, not you replying
                    role, text = p["role"], body
            sid = sid or f.stem[-36:]
            item = transcript_item("codex", sid, names.get(sid, ""), role, text, mtime, cwd, now)
            if item:
                items.append(item)
        except OSError:
            continue
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
    ignore = set(config().get("ignore_repos") or [])
    mine = [p for p in listing if not p["repository"]["nameWithOwner"].startswith("NousResearch/") and p["repository"]["nameWithOwner"] not in ignore]

    def one(p: dict) -> dict | None:
        repo = p["repository"]["nameWithOwner"]
        d = run_json(["gh", "pr", "view", str(p["number"]), "-R", repo, "--json", "isDraft,baseRefName,mergeable,mergeStateStatus,statusCheckRollup"], 30) or {}
        return pr_item({"repo": repo, "n": p["number"], "title": p["title"], "url": p["url"], "cat": classify_pr(d), "now": now, "stale_days": config().get("stale_pr_days", 30),
                        "at": iso_ts(p.get("updatedAt"))})

    with ThreadPoolExecutor(max_workers=8) as pool:  # one gh call per PR; in parallel it's seconds, not a minute
        items = [it for it in pool.map(one, mine) if it]
    return items


def src_review(now: float) -> list[dict]:
    """Optional: Hermes X Studio drafts in REVIEW. Off unless "xstudio_url" is set in inbox.json."""
    base = config().get("xstudio_url") or os.environ.get("HERMES_INBOX_XSTUDIO")
    if not base:
        return []
    try:
        with urllib.request.urlopen(f"{base}/api/state", timeout=5) as r:
            data = json.loads(r.read())["data"]
    except Exception:  # X Studio not running: nothing to report
        return []
    items = []
    for it in data.get("items", []):
        if it.get("status") != "REVIEW" or it.get("dataOrigin") == "STARTER" or str(it.get("id", "")).startswith("demo-"):
            continue
        items.append({"id": f"review:{it['id']}", "source": "review", "priority": "today", "title": f"Review: {it.get('title', '')[:70]}",
                      "detail": f"{it.get('contentKey', '')} in Hermes X Studio", "at": iso_ts(it.get("updatedAt")),
                      "agent": it.get("assignedAgent") if it.get("assignedAgent") not in (None, "", "Owner") else None,
                      "why": "A draft is waiting for you to approve, change or drop it." + (" It still has no visual." if it.get("visualStatus") == "MISSING" else ""),
                      "facts": [["Hook", str(it.get("hook") or "")[:140]], ["Type", str(it.get("contentType") or "")],
                                ["Checked", str(it.get("verificationStatus") or "").lower()], ["Visual", str(it.get("visualStatus") or "").lower()],
                                ["Created", when(iso_ts(it.get("createdAt")))]],
                      "action": {"kind": "url", "label": "Open X Studio", "value": base}})
    return items


PRIORITIES = tuple(PRIORITY)


def filed_card(raw: dict, now: float) -> dict | None:
    """A card a Bot or script posted. Expired or malformed cards are skipped."""
    if not isinstance(raw, dict) or not raw.get("id") or not raw.get("title"):
        return None
    if raw.get("expires_at") and raw["expires_at"] < now:
        return None
    action = {"kind": "command", "label": "Copy command", "value": raw["command"]} if raw.get("command") else {"kind": "none", "label": "", "value": ""}
    if raw.get("url"):
        action = {"kind": "url", "label": raw.get("label") or "Open", "value": raw["url"]}
    elif raw.get("session"):
        action = {"kind": "session", "label": "Open chat", "session": raw["session"], "profile": raw.get("profile") or "default",
                  "value": f"hermes -p {raw.get('profile') or 'default'} chat --resume {raw['session']}"}
    return {"id": f"filed:{raw['id']}", "source": "filed", "priority": raw.get("priority") if raw.get("priority") in PRIORITIES else "today",
            "title": str(raw["title"])[:120], "detail": str(raw.get("detail") or "")[:240], "at": raw.get("at") or now,
            "why": str(raw.get("why") or (f"Posted by {raw['from']}." if raw.get("from") else "")),
            "facts": [[str(k), str(v)] for k, v in (raw.get("facts") or {}).items()][:8], "from": raw.get("from"), "agent": raw.get("from"), "action": action}


def src_filed(now: float) -> list[dict]:
    items = []
    for f in sorted(FILED.glob("*.json")) if FILED.is_dir() else []:
        try:
            card = filed_card(json.loads(f.read_text()), now)
        except (OSError, ValueError):
            continue
        if card:
            items.append(card)
    return items


def file_card(title: str, *, detail: str = "", priority: str = "today", url: str = "", command: str = "", session: str = "",
              profile: str = "", why: str = "", sender: str = "", key: str = "", hours: float = 0) -> dict:
    """Write one card to ~/.hermes/inbox/. Same key = same card (updated in place), so a cron can re-post without duplicates."""
    now = time.time()
    key = re.sub(r"[^a-zA-Z0-9_.-]+", "-", key or f"{sender or 'card'}-{title}")[:80].strip("-")
    raw = {"id": key, "title": title, "detail": detail, "priority": priority if priority in PRIORITIES else "today", "at": now,
           "url": url, "command": command, "session": session, "profile": profile, "why": why, "from": sender,
           "expires_at": now + hours * 3600 if hours else None}
    FILED.mkdir(parents=True, exist_ok=True)
    (FILED / f"{key}.json").write_text(json.dumps({k: v for k, v in raw.items() if v not in ("", None)}, indent=1))
    return {"ok": True, "id": f"filed:{key}"}


def remove_card(item_id: str) -> bool:
    """Done on a filed card deletes its file, so the sender can post it again later."""
    if not item_id.startswith("filed:"):
        return False
    f = FILED / f"{item_id[6:]}.json"
    if f.exists():
        f.unlink()
        return True
    return False


APP = {"prs": "github", "review": "xstudio", "claude": "claude", "codex": "codex"}

SOURCES = {"filed": src_filed, "claude": src_claude, "codex": src_codex, "keys": src_keys, "vitals": src_vitals, "sessions": src_sessions, "crons": src_crons, "prs": src_prs, "review": src_review}


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
    enabled = config().get("sources") or {}
    for name, fn in SOURCES.items():
        if (names and name not in names) or enabled.get(name) is False:
            continue
        try:
            got = fn(now)
            counts[name] = len(got)
            items += got
        except Exception as e:  # one broken source must not hide the others
            errors[name] = f"{type(e).__name__}: {e}"[:160]
    for it in items:
        it.setdefault("app", APP.get(it["source"], "hermes"))
    merged = collapse_sessions(items)
    shown = visible(merged, load_state(), now)
    return {"checked_at": now, "items": shown, "counts": counts, "hidden": len(merged) - len(shown), "errors": errors,
            "summary": {p: sum(1 for i in shown if i["priority"] == p) for p in PRIORITY}}


def render(data: dict) -> str:
    s = data["summary"]
    lines = [f"Hermes Inbox: {s['now']} now · {s['today']} today · {s['later']} later" + (f"  ({data['hidden']} done/snoozed)" if data["hidden"] else "")]
    for it in data["items"]:
        lines.append(f"  [{it['priority']:>5}] {it['title']} — {it['detail']}")
        if it["action"].get("value"):
            lines.append(f"          {it['action']['label']}: {it['action']['value']}")
    for k, v in data["errors"].items():
        lines.append(f"  (couldn't read {k}: {v})")
    return "\n".join(lines)


def add_arguments(ap) -> None:
    """Shared by the standalone script and `hermes inbox`."""
    ap.add_argument("action", nargs="?", default="list", choices=["list", "add", "done", "snooze", "reset"])
    ap.add_argument("target", nargs="*", help="add TITLE | done ID | snooze ID HOURS")
    ap.add_argument("--json", action="store_true", help="one-line JSON (the Desktop page reads this)")
    ap.add_argument("--source", action="append", help="only this source: " + ", ".join(SOURCES))
    add = ap.add_argument_group("add (post a card, e.g. from a Bot or a cron job)")
    add.add_argument("--detail", default="")
    add.add_argument("--priority", default="today", choices=list(PRIORITY))
    add.add_argument("--url", default="")
    add.add_argument("--command", dest="card_command", default="")
    add.add_argument("--session", default="", help="session id to open; with --profile")
    add.add_argument("--profile", dest="card_profile", default="")
    add.add_argument("--why", default="")
    add.add_argument("--from", dest="sender", default="", help="who is asking, e.g. the Bot's name")
    add.add_argument("--key", default="", help="stable id: posting the same key again updates the card")
    add.add_argument("--hours", type=float, default=0, help="drop the card after this many hours")


def dispatch(a) -> str:
    """Run one action; returns what to print."""
    action, target = getattr(a, "action", "list"), getattr(a, "target", None) or []
    if action == "add":
        if not target:
            raise ValueError('usage: hermes inbox add "Title" [--detail ...] [--priority now|today|later] [--url ...]')
        return json.dumps(file_card(" ".join(target), detail=a.detail, priority=a.priority, url=a.url, command=a.card_command,
                                    session=a.session, profile=a.card_profile, why=a.why, sender=a.sender, key=a.key, hours=a.hours))
    if action in ("done", "snooze", "reset"):
        st = load_state()
        if action == "done" and target:
            if not remove_card(target[0]):
                st[target[0]] = {"done": True, "at": time.time()}
        elif action == "snooze" and len(target) >= 2:
            st[target[0]] = {"snooze_until": time.time() + float(target[1]) * 3600}
        elif action == "reset":
            st = {}
        else:
            raise ValueError("usage: hermes inbox done ID | snooze ID HOURS | reset")
        save_state(st)
        return json.dumps({"ok": True})
    data = collect(getattr(a, "source", None))
    return json.dumps(data, separators=(",", ":")) if getattr(a, "json", False) else render(data)


def main() -> int:
    ap = argparse.ArgumentParser(description="Everything your Hermes Bots and tools need from you")
    add_arguments(ap)
    a = ap.parse_args()
    try:
        print(dispatch(a))
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

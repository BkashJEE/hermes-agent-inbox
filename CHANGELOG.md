# Changelog

## 0.3.2 — 2026-10-15

- **Links work in Hermes Desktop.** "Open PR" and "Open X Studio" now open through the Desktop's OS link handler (`ctx.os.openExternal`); the Desktop blocks ordinary new-window links from plugins, so they did nothing before.
- **The All tab shows one block per agent** (Hermes inbox, Claude inbox, Codex inbox, X Studio), each with its own Now / Today / Later, instead of one mixed list. Click a block's title to open that agent's tab.

## 0.3.1 — 2026-10-15

- **Always talk to the local gateway.** The page used to send `hermes inbox` to whichever gateway owned the focused chat; with a remote connection focused (a laptop), that machine answered "'inbox' is not a hermes command". The Inbox is about this machine, so it now always uses the local route.
- A request that never settles (gateway restarted mid-call) no longer blocks every later refresh.

## 0.3.0 — 2026-10-15

- **One inbox per agent.** The Hermes tab is Hermes work only: PRs are included only from Hermes repos (`hermes_repos`, default `["hermes"]`). Other PRs are left out.
- **Claude's own inbox:** with the Claude Code switch on, its tab shows Claude's waiting sessions and the PRs its sessions opened (from Claude Code's `pr-link` records).
- **Codex (ChatGPT)** tab: its waiting threads.
- Flipping a switch on jumps to that tab. The GitHub tab is gone.

## 0.2.1 — 2026-10-15

- **Hermes is the default tab.**
- **Claude Code and Codex are off until switched on** with the page's **Also show** switches or `hermes inbox set claude|codex on|off`. Each one's tab appears only while it's on.

## 0.2.0 — 2026-10-15

- **Claude Code and Codex sources** (opt-in with `"sources": {"claude": true, "codex": true}`): a session whose last message is the assistant's and ends stuck, on a question, or asking you to check something shows up, with a copyable resume command.
- **Tabs** split the page by app: All · Hermes · Claude · Codex · GitHub · X Studio.
- **Every card names who needs you:** the Bot on the card line, and a Bot / Agent row when it's expanded.
- **Fewer false alarms.** Only the closing part of a reply decides how it ended, so a long report that merely mentions a login is no longer "needs sign-in".
- **New ending, "asked you to check":** replies like "please check…" or "after you approve…".

## 0.1.0 — 2026-10-15

First release.

**Sources:**
- stuck or questioning Bot sessions (last 48 h);
- failed cron jobs;
- Key Doctor logins;
- Vitals red alarms;
- your open PRs (`gh`);
- cards posted with `hermes inbox add`;
- optional Hermes X Studio reviews.

**Hermes Desktop page `/inbox`:**
- Now / Today / Later flash cards;
- sections that fold;
- cards that expand to show why they're there, the facts, and copy buttons;
- Open chat into the right Bot;
- Done and Snooze;
- a `▣` status-bar chip.

**Commands:**
- `hermes inbox` (`--json`, `--source`, `done`, `snooze`, `add`);
- `/inbox`;
- an `inbox` skill for Bots.

**Settings:** `~/.hermes/inbox.json` turns sources on or off, sets `ignore_repos` and `stale_pr_days`, and holds `xstudio_url`.

# Changelog

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

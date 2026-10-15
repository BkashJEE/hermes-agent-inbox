# Hermes Inbox

Everything your Hermes Bots and tools are waiting on you for, in one list.

| Source | Shows up when | Priority |
|---|---|---|
| Chats | a Bot's session in the last 48 h ended on a sign-in or approval request | now |
| | it hit a limit, a missing tool or a refusing site | today |
| | it asked you a question you haven't opened | today |
| Vitals | a red alarm | now |
| Keys (Key Doctor) | a Bot can't reach its model; a login is benched, dead or expiring | now / today |
| Crons | the last run failed | now |
| PRs | yours are ready to merge (today; later if untouched for a month), or have conflicts or failing checks (later) | |
| Review | a Hermes X Studio draft is waiting for your decision | today |

Chats show only the session title and how it ended, never the messages. A daily cron that is stuck every day shows as one line ("also 1 earlier time").

## Use it

```bash
hermes inbox                 # the list
hermes inbox --json          # for the Desktop page
hermes inbox done ID         # hide until the item changes
hermes inbox snooze ID 24    # hide for 24 hours
/inbox                       # in any chat
```

In Hermes Desktop it is the **Inbox** page (`/inbox`, sidebar and ⌘K) with a `▣` status-bar chip.
- **Open chat** jumps to the stuck session in its own Bot.
- **Done** and **Snooze 1d** clear a row.

The script also runs on its own: `python3 hermes_inbox.py`.

## Install (this machine)

```bash
ln -s ~/hermes-inbox ~/.hermes/profiles/ceo/plugins/hermes-inbox
ln -s ~/hermes-inbox/desktop ~/.hermes/desktop-plugins/hermes-inbox
hermes -p ceo plugins enable hermes-inbox
```

Then switch it on under **Desktop Settings → Plugins**. The Desktop marks it as a unified package (it writes `desktop/.hermes-package.json`), and those start off.

## Notes

- **Read-only.** The only write is `~/.local/state/hermes-inbox/state.json` (done/snooze). PR checks are cached for 10 minutes in the same folder.
- **Upstream PRs are skipped.** Anything under `NousResearch/` is waiting on maintainers.
- **Each source fails soft.** If X Studio is not running, its items are missing; the rest still show.
- **Tests:** `python3 -m unittest discover -s tests`. The page harness is `harness/index.html`; save `hermes inbox --json > harness/live.json` first.

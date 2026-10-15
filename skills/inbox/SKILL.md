---
name: inbox
description: Ask the user for something without blocking or spamming them — a decision, an approval, a sign-in, a file, a review — by posting a card to their Hermes Agent Inbox with `hermes inbox add`. Also reads the inbox ("what's waiting on me?", "what's stuck?") with `hermes inbox`.
---

# Hermes Agent Inbox

The user keeps one list of everything their Bots and tools are waiting on them for. Their Desktop shows it as cards at `/inbox`, with a count in the status bar. Most cards are found automatically: stuck sessions, failed crons, benched keys, red Vitals alarms and PRs.

Post your own card when you need the user and they are not in the chat. For example:

- a cron job or background task needs a decision, an approval or a sign-in;
- you finished something they should review;
- you are blocked and would otherwise end the session on a question nobody reads.

## Post a card

```bash
hermes inbox add "Approve the newsletter draft" \
  --from "Gary" --priority today \
  --detail "Draft 3 is ready; two links need checking" \
  --url "https://example.com/draft/3" \
  --key newsletter-draft
```

| Flag | Use |
|---|---|
| `--priority now` | Something is stopped until they act: a sign-in, an approval, a failing job. Use sparingly. |
| `--priority today` | A decision or a review. This is the default. |
| `--priority later` | Nice to have. |
| `--url` / `--command` | What clicking the card does: open a link, or copy a command. |
| `--session ID --profile NAME` | Opens that chat in Hermes Desktop. |
| `--key` | A stable id. Posting the same key again updates the card instead of adding another, which is right for crons. |
| `--hours N` | The card disappears after N hours if it no longer matters. |
| `--why` | One sentence on why you need them. |

## Rules

1. **One card per need.** Use `--key` so repeats update the card instead of piling up.
2. **No secrets.** Titles and details show on screen: never put tokens, passwords or private message text in them.
3. **Say what you need, not what you did.** Write "Pick one of 3 thumbnails", not "I generated thumbnails".
4. **Don't post what is already detected.** Stuck sessions, failed crons, benched keys and Vitals alarms show up on their own.
5. **Read before you post.** `hermes inbox --source filed` shows the cards already posted.

## Read the inbox

```bash
hermes inbox                 # everything, grouped Now / Today / Later
hermes inbox --json          # for tools
hermes inbox done ID         # the user handled it (only when they ask)
```

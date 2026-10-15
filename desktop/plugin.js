// hermes-inbox: desktop half. Plain ESM, loaded uncompiled by Hermes Desktop (no JSX, no build).
//
// Surfaces: route /inbox (sidebar row "Inbox" + ⌘K "Open Inbox") and a status-bar chip.
// Data: host.requestProfile(route, "cli.exec", { argv: ["--profile", p, "inbox", "--json"] })
//       -> a fresh `hermes inbox` process; refreshed every 5 min while visible (PRs are cached 10 min).
// Done / Snooze run `hermes inbox done|snooze ID` and only touch the inbox's own state file.
// Look: theme variables only (--ui-*), so it follows Nous dark and every other skin.

import { host, PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, STATUSBAR_AREAS } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'hermes-inbox'
const ROUTE = '/inbox'
const REFRESH_MS = 5 * 60000
const CLI_TIMEOUT_MS = 150000

// ── data ────────────────────────────────────────────────────────────────────
async function resolveRoute() {
  const owner = host.state?.focusedSessionOwner?.get?.() || null
  const routes = await host.profileRoutes()
  const exact = owner ? routes.filter((r) => r.connectionId === owner.connectionId && r.profile === owner.profile) : []
  if (exact.length === 1) return exact[0]
  const fallback = routes.find((r) => r.primary) || routes.find((r) => r.mode === 'local') || routes[0]
  if (!fallback) throw new Error('No Hermes profile route available')
  return fallback
}

function parseCliJson(output) {
  const lines = String(output || '').split('\n').map((l) => l.trim()).filter(Boolean)
  for (let i = lines.length - 1; i >= 0; i--) {
    if (!lines[i].startsWith('{')) continue
    try { return JSON.parse(lines[i]) } catch { /* keep looking */ }
  }
  throw new Error(lines.slice(-2).join(' ') || 'empty output from hermes inbox')
}

async function runInbox(args) {
  const route = await resolveRoute()
  const res = await host.requestProfile(route, 'cli.exec',
    { argv: ['--profile', route.targetProfile, 'inbox', ...args], timeout: CLI_TIMEOUT_MS / 1000 }, CLI_TIMEOUT_MS + 5000)
  if (res && res.blocked) throw new Error(res.hint || 'cli.exec blocked')
  const payload = parseCliJson(res && res.output)
  if (payload.error) throw new Error(payload.error)
  return payload
}

// One shared poller so the page and the status chip never double-fetch.
const store = { data: null, error: null, at: 0, listeners: new Set(), inflight: null, cleared: new Set() }
function notify() { store.listeners.forEach((fn) => fn()) }
function refresh() {
  if (store.inflight) return store.inflight
  store.inflight = runInbox(['--json'])
    .then((d) => { store.data = d; store.error = null; store.at = Date.now(); store.cleared.clear() })
    .catch((e) => { store.error = String((e && e.message) || e) })
    .finally(() => { store.inflight = null; notify() })
  notify()
  return store.inflight
}
// Done / snooze hide the row at once; the next refresh confirms it from the state file.
async function clear(item, verb, hours) {
  store.cleared.add(item.id); notify()
  try {
    await runInbox(verb === 'done' ? ['done', item.id] : ['snooze', item.id, String(hours)])
  } catch (e) {
    store.cleared.delete(item.id); notify()
    host.notify?.({ kind: 'error', message: `Inbox: ${String((e && e.message) || e)}` })
  }
}
function useInbox() {
  const [, bump] = useState(0)
  useEffect(() => {
    const fn = () => bump((n) => n + 1)
    store.listeners.add(fn)
    if (!store.data && !store.inflight) refresh()
    return () => store.listeners.delete(fn)
  }, [])
  return store
}
function startPolling(ctx) {
  const tick = () => { if (!document.hidden) refresh() }
  ctx.setInterval(tick, REFRESH_MS)
  ctx.addEventListener(document, 'visibilitychange', tick)
  refresh()
}
const shown = (s) => ((s.data && s.data.items) || []).filter((i) => !s.cleared.has(i.id))

// ── theme ───────────────────────────────────────────────────────────────────
const V = (name, fallback) => `var(${name}, ${fallback})`
const C = {
  accent: V('--ui-accent', '#4a84fe'), red: V('--ui-red', '#ff7b72'), yellow: V('--ui-yellow', '#d29922'),
  green: V('--ui-green', '#3fb950'), orange: V('--ui-orange', '#ffa657'),
  t1: V('--ui-text-primary', '#e6edf3'), t2: V('--ui-text-secondary', '#b1bac4'), t3: V('--ui-text-tertiary', '#7d8590'), t4: V('--ui-text-quaternary', '#6e7681'),
  stroke: V('--ui-stroke-tertiary', '#30363d'), panel: V('--ui-bg-elevated', '#010409'), sunk: V('--ui-bg-tertiary', '#0d1117'),
}
const MONO = 'ui-monospace, SFMono-Regular, "JetBrains Mono", Menlo, monospace'
const LANES = [
  { key: 'now', label: 'Now', hint: 'a Bot is stopped until you act', color: C.red },
  { key: 'today', label: 'Today', hint: 'waiting on a decision', color: C.orange },
  { key: 'later', label: 'Later', hint: 'when you have a minute', color: C.t3 },
]
const SOURCE = { keys: 'KEYS', vitals: 'VITALS', sessions: 'CHAT', crons: 'CRON', prs: 'PR', review: 'REVIEW' }

function ago(ts) {
  if (!ts) return ''
  const s = Date.now() / 1000 - ts
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m`
  if (s < 86400) return `${Math.round(s / 3600)}h`
  return `${Math.round(s / 86400)}d`
}

// ── pieces ──────────────────────────────────────────────────────────────────
// Card look follows the Skills Library page: fixed header, its own scroll area, a grid of flash cards.
const CSS = `
.hin{height:100%;display:flex;flex-direction:column;overflow:hidden;min-height:0;container-type:inline-size;color:var(--ui-text-primary,#eeedf2);background:var(--background,#141419);font:14px/1.55 system-ui,sans-serif;box-sizing:border-box}
.hin *{box-sizing:border-box}
.hin .head{flex-shrink:0;display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:24px clamp(20px,3vw,40px) 14px;border-bottom:1px solid var(--ui-stroke-tertiary,#34343b)}
.hin h1{font-size:22px;letter-spacing:-.5px;font-weight:600;margin:0}
.hin .sub{font-size:12px;color:var(--ui-text-tertiary,#a6a4b0)}
.hin .meta{margin-left:auto;font:11px ${MONO};color:var(--ui-text-tertiary,#a6a4b0)}
.hin .tally{display:flex;gap:8px;width:100%}
.hin .tally span{font:600 11px ${MONO};letter-spacing:.06em;padding:3px 9px;border-radius:999px;border:1px solid var(--ui-stroke-tertiary,#34343b);color:var(--ui-text-quaternary,#6e7681)}
.hin .tally .on{color:var(--t);border-color:color-mix(in srgb,var(--t) 55%,transparent);background:color-mix(in srgb,var(--t) 10%,transparent)}
.hin .scroll{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;overscroll-behavior-y:contain;scrollbar-gutter:stable;padding:18px clamp(20px,3vw,40px) 32px}
.hin .scroll::-webkit-scrollbar{width:8px}.hin .scroll::-webkit-scrollbar-track{background:transparent}
.hin .scroll::-webkit-scrollbar-thumb{background:var(--ui-stroke-tertiary,#34343b);border-radius:8px}
.hin .lane{margin:4px 0 16px}
.hin .lane-title{display:flex;align-items:baseline;gap:10px;margin:0 0 10px}
.hin .lane-title b{font:700 12px ${MONO};letter-spacing:.08em}
.hin .lane-title span{font-size:11.5px;color:var(--ui-text-quaternary,#6e7681)}
.hin .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,210px),1fr));gap:10px}
@container (min-width:1500px){.hin .grid{grid-template-columns:repeat(6,minmax(0,1fr))}}
.hin .card{position:relative;display:flex;flex-direction:column;min-width:0;height:116px;padding:8px 10px 6px;border-radius:8px;border:1px solid var(--ui-stroke-quaternary,#2c2b34);border-top:2px solid color-mix(in srgb,var(--a) 65%,var(--card,#1c1c23));background:linear-gradient(155deg,color-mix(in srgb,var(--a) 8%,var(--card,#1c1c23)),var(--card,#1c1c23) 75%);box-shadow:0 3px 10px #0000000a;transition:border-color .15s,transform .15s}
.hin .card:hover{border-color:var(--ui-stroke-primary,#64616f);transform:translateY(-1px)}
.hin .card-head{display:flex;gap:8px;align-items:flex-start;min-width:0}
.hin .icon{display:grid;place-items:center;flex-shrink:0;width:22px;height:22px;border-radius:5px;color:var(--a);background:color-mix(in srgb,var(--a) 13%,transparent);font-size:13px}.hin .icon .codicon{font-size:13px}
.hin .name{font-size:12.5px;font-weight:600;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.hin .src{font-size:10px;color:var(--ui-text-tertiary,#a6a4b0);margin-top:1px}
.hin .label{display:none}
.hin .brief{margin:5px 0 0;padding:0;list-style:none;font-size:11px;line-height:1.45;color:var(--ui-text-secondary,#b8b6c2)}
.hin .brief li{position:relative;padding-left:11px}
.hin .brief li:before{content:"•";position:absolute;left:0;color:var(--a)}
.hin .brief li span{display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.hin .foot{display:flex;align-items:center;gap:4px;margin-top:auto}
.hin .foot button,.hin .foot a{font:11px system-ui,sans-serif;border:0;background:transparent;color:var(--ui-text-tertiary,#a6a4b0);padding:2px 5px;border-radius:6px;cursor:pointer;text-decoration:none;white-space:nowrap}
.hin .foot button:hover,.hin .foot a:hover{background:var(--chrome-action-hover,#303038);color:var(--ui-text-primary,#eeedf2)}
.hin .foot .go{margin-left:auto;color:var(--ui-text-primary,#eeedf2);font-weight:500}
.hin .head button{font-size:12px;padding:5px 12px;border-radius:7px;border:1px solid var(--ui-stroke-tertiary,#34343b);background:var(--ui-bg-quaternary,#24242b);color:inherit;cursor:pointer}
.hin .head button:disabled{opacity:.5;cursor:default}
.hin .empty{grid-column:1/-1;font-size:12px;color:var(--ui-text-quaternary,#6e7681);padding:4px 0 8px}
.hin .zero{margin-bottom:18px;padding:12px 16px;border-radius:10px;font-weight:600;color:var(--ui-green,#3fb950);border:1px solid color-mix(in srgb,var(--ui-green,#3fb950) 45%,transparent)}
.hin .note{font-size:11px;color:var(--ui-text-quaternary,#6e7681);line-height:1.6}
.hin .warn{font-size:11.5px;color:var(--ui-orange,#ffa657);margin-bottom:10px}
`
const ICON = { keys: 'key', vitals: 'pulse', sessions: 'comment-discussion', crons: 'clock', prs: 'git-pull-request', review: 'eye' }
const KIND = { keys: 'Key Doctor', vitals: 'Vitals', sessions: 'Bot chat', crons: 'Cron job', prs: 'Pull request', review: 'X Studio' }
const NEEDS = { now: 'What it needs', today: 'What it needs', later: 'When you can' }

function Go({ a }) {
  const [copied, setCopied] = useState(false)
  const label = `${a.label} →`
  if (a.kind === 'session') {
    return jsx('button', { type: 'button', className: 'go',
      onClick: () => host.openSession(a.session, { profile: a.profile, intent: 'tab' })
        .catch((e) => host.notify?.({ kind: 'error', message: String((e && e.message) || e) })),
      children: label })
  }
  if (a.kind === 'url') return jsx('a', { className: 'go', href: a.value, target: '_blank', rel: 'noreferrer', children: label })
  if (a.kind === 'open' || /^\/[a-z-]+$/.test(a.value)) return jsx('button', { type: 'button', className: 'go', onClick: () => host.navigate(a.value), children: label })
  return jsx('button', { type: 'button', className: 'go', title: a.value,
    onClick: () => navigator.clipboard?.writeText(a.value).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) }),
    children: copied ? 'Copied ✓' : `Copy ${a.label.toLowerCase()} →` })
}

function Card({ it, color }) {
  return jsxs('div', {
    className: 'card', style: { '--a': color },
    children: [
      jsxs('div', { className: 'card-head', children: [
        jsx('span', { className: 'icon', children: jsx('i', { className: `codicon codicon-${ICON[it.source] || 'circle'}` }) }),
        jsxs('div', { style: { minWidth: 0, flex: 1 }, children: [
          jsx('div', { className: 'name', title: it.title, children: it.title }),
          jsx('div', { className: 'src', children: `${KIND[it.source] || it.source}${it.at ? ` · ${ago(it.at)} ago` : ''}` }),
        ] }),
      ] }),
      jsx('div', { className: 'label', children: NEEDS[it.priority] }),
      jsx('ul', { className: 'brief', children: jsx('li', { children: jsx('span', { title: it.detail, children: it.detail || it.action.label }) }) }),
      jsxs('div', { className: 'foot', children: [
        jsx('button', { type: 'button', title: 'Hide until it changes', onClick: () => clear(it, 'done'), children: 'Done' }),
        jsx('button', { type: 'button', title: 'Hide for a day', onClick: () => clear(it, 'snooze', 24), children: 'Snooze' }),
        jsx(Go, { a: it.action }),
      ] }),
    ],
  })
}

function Lane({ lane, items }) {
  return jsxs('section', { className: 'lane', children: [
    jsxs('div', { className: 'lane-title', children: [
      jsx('b', { style: { color: items.length ? lane.color : C.t4 }, children: `${lane.label.toUpperCase()} · ${items.length}` }),
      jsx('span', { children: lane.hint }),
    ] }),
    jsx('div', { className: 'grid', children: items.length ? items.map((it) => jsx(Card, { it, color: lane.color }, it.id))
      : jsx('div', { className: 'empty', children: 'Nothing here.' }) }),
  ] })
}

function InboxPage() {
  const s = useInbox()
  const d = s.data
  const items = shown(s)
  const head = jsxs('div', { className: 'head', children: [
    jsx('h1', { children: 'Inbox' }),
    jsx('span', { className: 'sub', children: 'everything your Bots and tools are waiting on you for' }),
    jsx('span', { className: 'meta', children: d ? `${d.hidden ? `${d.hidden} done/snoozed · ` : ''}checked ${new Date(s.at).toLocaleTimeString()}` : s.error ? '' : 'checking Bots, crons, keys, PRs…' }),
    jsx('button', { type: 'button', onClick: () => refresh(), disabled: Boolean(s.inflight), children: s.inflight ? 'Checking…' : 'Check now' }),
    d ? jsx('div', { className: 'tally', children: LANES.map((l) => {
      const n = items.filter((i) => i.priority === l.key).length
      return jsx('span', { className: n ? 'on' : '', style: { '--t': l.color }, children: `${n} ${l.label.toLowerCase()}` }, l.key)
    }) }) : null,
  ] })
  const errs = d ? Object.entries(d.errors || {}) : []
  return jsxs('div', { className: 'hin', children: [
    jsx('style', { children: CSS }),
    head,
    jsx('div', { className: 'scroll', children: !d
      ? (s.error ? jsx('div', { style: { color: C.red, fontSize: 12.5 }, children: `Could not read the inbox: ${s.error}. Is the hermes-inbox plugin switched on (Settings → Plugins)?` }) : null)
      : [
        items.length ? null : jsx('div', { className: 'zero', children: 'Inbox zero. No Bot is waiting on you.' }, 'zero'),
        ...LANES.map((lane) => jsx(Lane, { lane, items: items.filter((i) => i.priority === lane.key) }, lane.key)),
        errs.length ? jsx('div', { className: 'warn', children: `Couldn't read: ${errs.map(([k, v]) => `${k} (${v})`).join(' · ')}` }, 'errs') : null,
        jsx('div', { className: 'note', children: 'Chats show only the title and how they ended, never the messages. Done hides a card until it changes; Snooze hides it for a day.' }, 'note'),
      ] }),
  ] })
}

function StatusChip() {
  const s = useInbox()
  if (!s.data) return null
  const items = shown(s)
  const now = items.filter((i) => i.priority === 'now').length
  const today = items.filter((i) => i.priority === 'today').length
  return jsx('button', {
    type: 'button', title: 'Open Inbox', onClick: () => host.navigate(ROUTE),
    style: { background: 'transparent', border: 0, cursor: 'pointer', padding: '0 6px', fontSize: 11, fontFamily: MONO, color: now ? C.red : today ? C.orange : C.green },
    children: now ? `▣ ${now} waiting on you` : today ? `▣ ${today} today` : '▣ inbox zero',
  })
}

export default {
  id: ID,
  name: 'Inbox',
  register(ctx) {
    ctx.register({ id: 'page', area: ROUTES_AREA, data: { path: ROUTE }, render: () => jsx(InboxPage, {}) })
    ctx.register({ id: 'nav', area: SIDEBAR_NAV_AREA, order: 86, data: { path: ROUTE, label: 'Inbox', codicon: 'inbox' } })
    ctx.register({ id: 'open', area: PALETTE_AREA, data: { id: `${ID}.open`, label: 'Open Inbox', keywords: ['inbox', 'waiting', 'approval', 'stuck', 'todo', 'review'], run: () => host.navigate(ROUTE) } })
    ctx.register({ id: 'statusbar', area: STATUSBAR_AREAS.right, order: 131, render: () => jsx(StatusChip, {}) })
    startPolling(ctx)
  },
}

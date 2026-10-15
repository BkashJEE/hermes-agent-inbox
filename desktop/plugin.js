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
const btn = (extra) => ({ fontSize: 11, padding: '4px 10px', borderRadius: 6, border: `1px solid ${C.stroke}`, background: 'transparent', color: C.t2, cursor: 'pointer', whiteSpace: 'nowrap', ...extra })

function Primary({ a }) {
  const [copied, setCopied] = useState(false)
  if (a.kind === 'session') {
    return jsx('button', { type: 'button', style: btn({ color: C.accent, borderColor: C.accent }),
      onClick: () => host.openSession(a.session, { profile: a.profile, intent: 'tab' })
        .catch((e) => host.notify?.({ kind: 'error', message: String((e && e.message) || e) })),
      children: a.label })
  }
  if (a.kind === 'url') {
    return jsx('a', { href: a.value, target: '_blank', rel: 'noreferrer', style: btn({ color: C.accent, borderColor: C.accent, textDecoration: 'none' }), children: a.label })
  }
  if (a.kind === 'open' || /^\/[a-z-]+$/.test(a.value)) {
    return jsx('button', { type: 'button', style: btn({ color: C.accent, borderColor: C.accent }), onClick: () => host.navigate(a.value), children: a.label })
  }
  return jsx('button', { type: 'button', style: btn({ color: copied ? C.green : C.accent, borderColor: C.accent }), title: a.value,
    onClick: () => navigator.clipboard?.writeText(a.value).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) }),
    children: copied ? 'Copied' : `Copy: ${a.label}` })
}

function Row({ it, color }) {
  return jsxs('div', {
    style: { display: 'grid', gridTemplateColumns: '64px 1fr auto', gap: 12, alignItems: 'center', padding: '9px 0', borderTop: `1px solid ${C.stroke}` },
    children: [
      jsx('span', { style: { fontFamily: MONO, fontSize: 10, fontWeight: 700, letterSpacing: '.08em', color, border: `1px solid ${color}`, borderRadius: 4, padding: '2px 0', textAlign: 'center' }, children: SOURCE[it.source] || it.source.toUpperCase() }),
      jsxs('div', { style: { minWidth: 0 }, children: [
        jsxs('div', { style: { fontSize: 12.5, color: C.t1, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: [
          it.title, jsx('span', { style: { fontFamily: MONO, fontSize: 10.5, fontWeight: 400, color: C.t4, marginLeft: 8 }, children: ago(it.at) })] }),
        jsx('div', { style: { fontSize: 11.5, color: C.t3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: it.detail }),
      ] }),
      jsxs('div', { style: { display: 'flex', gap: 6 }, children: [
        jsx(Primary, { a: it.action }),
        jsx('button', { type: 'button', style: btn(), title: 'Hide for a day', onClick: () => clear(it, 'snooze', 24), children: 'Snooze 1d' }),
        jsx('button', { type: 'button', style: btn(), title: 'Hide until it changes', onClick: () => clear(it, 'done'), children: 'Done' }),
      ] }),
    ],
  })
}

function Lane({ lane, items }) {
  return jsxs('div', {
    style: { background: C.panel, border: `1px solid ${items.length && lane.key === 'now' ? C.red : C.stroke}`, borderRadius: 10, padding: '10px 14px', marginBottom: 12 },
    children: [
      jsxs('div', { style: { display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 2 }, children: [
        jsx('span', { style: { fontFamily: MONO, fontSize: 13, fontWeight: 700, color: items.length ? lane.color : C.t4, letterSpacing: '.05em' }, children: `${lane.label.toUpperCase()} · ${items.length}` }),
        jsx('span', { style: { fontSize: 11, color: C.t4 }, children: lane.hint }),
      ] }),
      items.length ? items.map((it) => jsx(Row, { it, color: lane.color }, it.id))
        : jsx('div', { style: { fontSize: 11.5, color: C.t4, padding: '6px 0' }, children: 'Nothing here.' }),
    ],
  })
}

function InboxPage() {
  const s = useInbox()
  const d = s.data
  const items = shown(s)
  const head = jsxs('div', {
    style: { display: 'flex', alignItems: 'baseline', gap: 14, marginBottom: 12 },
    children: [
      jsx('div', { style: { fontSize: 13, color: C.t1, fontWeight: 600 }, children: 'Inbox' }),
      jsx('div', { style: { fontSize: 11.5, color: C.t3 }, children: 'everything your Bots and tools are waiting on you for' }),
      jsx('div', { style: { marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: C.t3 },
        children: d ? `${d.hidden ? `${d.hidden} done/snoozed · ` : ''}checked ${new Date(s.at).toLocaleTimeString()}` : s.error ? '' : 'checking Bots, crons, keys, PRs…' }),
      jsx('button', { type: 'button', onClick: () => refresh(), disabled: Boolean(s.inflight), style: btn(), children: s.inflight ? 'Checking…' : 'Check now' }),
    ],
  })
  if (!d) {
    return jsxs('div', { style: { padding: '20px 28px', height: '100%', overflow: 'auto' }, children: [head,
      s.error ? jsx('div', { style: { color: C.red, fontSize: 12.5 }, children: `Could not read the inbox: ${s.error}. Is the hermes-inbox Python plugin enabled (hermes plugins enable hermes-inbox)?` }) : null] })
  }
  const errs = Object.entries(d.errors || {})
  return jsxs('div', {
    style: { padding: '20px 28px 28px', height: '100%', overflow: 'auto', maxWidth: 1100 },
    children: [
      head,
      items.length ? null : jsx('div', { style: { marginBottom: 12, padding: '10px 14px', borderRadius: 10, fontSize: 13, fontWeight: 600, color: C.green, border: `1px solid ${C.green}` }, children: 'Inbox zero. No Bot is waiting on you.' }),
      ...LANES.map((lane) => jsx(Lane, { lane, items: items.filter((i) => i.priority === lane.key) }, lane.key)),
      errs.length ? jsx('div', { style: { fontSize: 11, color: C.orange, marginTop: 4 }, children: `Couldn't read: ${errs.map(([k, v]) => `${k} (${v})`).join(' · ')}` }) : null,
      jsx('div', { style: { marginTop: 10, fontSize: 11, color: C.t4, lineHeight: 1.6 },
        children: 'Chats show only the title and how they ended, never the messages. Done hides an item until it changes; Snooze hides it for a day.' }),
    ],
  })
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

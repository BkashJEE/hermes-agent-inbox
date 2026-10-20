// hermes-agent-inbox: desktop half. Plain ESM, loaded uncompiled by Hermes Desktop (no JSX, no build).
//
// Surfaces: route /inbox (sidebar row "Inbox" + ⌘K "Open Inbox") and a status-bar chip.
// Data: the plugin's own backend first, ctx.rest('/list') -> dashboard/plugin_api.py (mounted by Hermes
//       when the plugin is in plugins.enabled; no output cap). If it isn't mounted, the CLI:
//       host.requestProfile(route, "cli.exec", { argv: ["--profile", p, "inbox", "--json"] }).
// Fresh: with the backend, a cheap /stamp every 30 s re-reads only when something changed; the 5-min
//       full refresh stays as a safety net. New "Now" cards raise one desktop notification each.
// Done / Snooze only touch the inbox's own state file.
// Look: theme variables only (--ui-*), so it follows Nous dark and every other skin.

import { host, PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, STATUSBAR_AREAS } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'hermes-agent-inbox'
const ROUTE = '/inbox'
const REFRESH_MS = 5 * 60000
const CLI_TIMEOUT_MS = 150000

// ── data ────────────────────────────────────────────────────────────────────
// The Inbox reads THIS machine's Bots, so it always talks to the local gateway — never the one that
// owns the focused chat (a remote connection may not have this plugin installed at all).
async function resolveRoute() {
  const routes = await host.profileRoutes()
  const route = routes.find((r) => r.mode === 'local' && r.primary) || routes.find((r) => r.mode === 'local') || routes.find((r) => r.primary) || routes[0]
  if (!route) throw new Error('No Hermes profile route available')
  return route
}

function parseCliJson(output) {
  const lines = String(output || '').split('\n').map((l) => l.trim()).filter(Boolean)
  for (let i = lines.length - 1; i >= 0; i--) {
    if (!lines[i].startsWith('{')) continue
    try { return JSON.parse(lines[i]) } catch { /* keep looking */ }
  }
  throw new Error(lines.slice(-2).join(' ') || 'empty output from hermes inbox')
}

// A call that never settles (gateway restarted mid-request) must not leave `inflight` set forever.
const withDeadline = (promise, ms) => Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new Error('timed out waiting for hermes inbox')), ms))])

// One `hermes …` run through the local gateway; returns {route, output}.
async function runHermes(argv) {
  const route = await resolveRoute()
  const res = await withDeadline(host.requestProfile(route, 'cli.exec',
    { argv: ['--profile', route.targetProfile, ...argv], timeout: CLI_TIMEOUT_MS / 1000 }, CLI_TIMEOUT_MS + 5000), CLI_TIMEOUT_MS + 10000)
  if (res && res.blocked) throw new Error(res.hint || 'cli.exec blocked')
  return { route, output: String((res && res.output) || '') }
}

async function viaCli(args) {
  const { output } = await runHermes(['inbox', ...args])
  const payload = parseCliJson(output)
  if (payload.error) throw new Error(payload.error)
  return payload
}

// ── backend: REST (dashboard/plugin_api.py) first, the CLI as fallback ──────
let pluginCtx = null           // set in register(); ctx.rest is this plugin's own REST namespace
let backend = null             // 'rest' | 'cli', once one has answered
let cliFallbackSince = 0       // when backend === 'cli': the call that latched it, so REST gets another chance later
const CLI_FALLBACK_RETRY_MS = 5 * 60000   // latched on the CLI: give the REST backend one more shot this often
const REST_TIMEOUT_MS = 150000
const restAvailable = () => !!(pluginCtx && typeof pluginCtx.rest === 'function')
function viaRest(args) {
  const rest = (path, opts) => pluginCtx.rest(path, { timeoutMs: REST_TIMEOUT_MS, ...(opts || {}) })
  const post = (path, body) => rest(path, { method: 'POST', body })
  const [cmd, ...tail] = args
  if (cmd === '--json') {  // ['--json', '--app', a, '--app', b]
    const q = []
    for (let i = 0; i < tail.length; i += 2) if (tail[i] === '--app' || tail[i] === '--source') q.push(`${tail[i].slice(2)}=${encodeURIComponent(tail[i + 1])}`)
    return rest(`/list${q.length ? `?${q.join('&')}` : ''}`)
  }
  if (cmd === 'done') return post('/done', { id: tail[0] })
  if (cmd === 'snooze') return post('/snooze', { id: tail[0], hours: Number(tail[1]) })
  if (cmd === 'set') return post('/set', { source: tail[0], on: tail[1] === 'on' })
  if (cmd === 'stamp') return rest('/stamp', { timeoutMs: 10000 })
  if (cmd === 'run-cron') return post('/run-cron', { profile: tail[0], id: tail[1] })
  throw new Error(`no REST route for ${cmd}`)
}
// A fallback that never latches forever: on headless boxes `hermes serve` may mount this plugin's
// REST routes *after* the pane first runs (e.g. the serve started with the plugin not yet installed)
// — a single early REST failure then used to strand the pane on the CLI for the whole Desktop
// session, silently dropping the 30-second freshness stamp and re-applying the CLI's 48 KB output
// cap. While latched on the CLI we give REST one more chance every CLI_FALLBACK_RETRY_MS.
async function runInbox(args) {
  if (restAvailable() && (backend !== 'cli' || Date.now() - cliFallbackSince >= CLI_FALLBACK_RETRY_MS)) {
    try {
      const r = await viaRest(args)
      backend = 'rest'
      if (r && r.error) throw new Error(r.error)
      return r
    } catch (e) {
      if (backend === 'rest') throw e   // the backend answered before, so this is a real failure
      console.error(`[hermes-agent-inbox] backend REST unavailable (${String((e && e.message) || e)}); using the CLI${backend === 'cli' ? '; will retry REST in five minutes' : ''}`)
      if (backend === 'cli') cliFallbackSince = Date.now()   // the retry missed too — wait another full window
    }
  }
  if (backend !== 'cli') cliFallbackSince = Date.now()
  const r = await viaCli(args)
  backend = 'cli'
  return r
}

// Continue a Claude Code / Codex session as a Hermes chat: `hermes sessions import` copies it into the
// Hermes store (prints "Imported … as <id>"), then the Desktop opens that session.
async function continueInHermes(m) {
  const { route, output } = await runHermes(['sessions', 'import', '--from', m.app, m.path])
  const id = (output.match(/\bas\s+([A-Za-z0-9_.-]+)/) || [])[1]
  if (!id) throw new Error(output.trim().split('\n').slice(-2).join(' ') || 'import printed no session id')
  await host.openSession(id, { profile: route.targetProfile, intent: 'tab' })
  return id
}

// One shared poller so the page and the status chip never double-fetch.
// Links: the Desktop denies every new-window open from a plugin, so URLs go through the OS
// handler the SDK provides (ctx.os.openExternal); outside the Desktop (the harness) a plain open.
let os = null
function openLink(url) {
  if (os && typeof os.openExternal === 'function') {
    os.openExternal(url).then((ok) => { if (!ok) host.notify?.({ kind: 'error', message: `Couldn't open ${url}` }) })
  } else {
    window.open(url, '_blank', 'noopener')
  }
}

// Each tab is fetched on its own (`--app`), so no single response can outgrow the gateway's 48 KB
// output cap. 'base' = Hermes + X Studio, loaded at start; Claude and Codex load when their tab is opened.
const GROUPS = { base: ['hermes', 'xstudio'], claude: ['claude'], codex: ['codex'] }
const store = { apps: {}, switches: {}, error: null, at: 0, listeners: new Set(), inflight: {}, cleared: new Set(), loaded: new Set() }
function notify() { store.listeners.forEach((fn) => fn()) }
function refreshGroup(g) {
  if (store.inflight[g]) return store.inflight[g]
  store.inflight[g] = runInbox(['--json', ...GROUPS[g].flatMap((a) => ['--app', a])])
    .then((d) => { store.apps[g] = d; store.loaded.add(g); if (d.switches) store.switches = d.switches; store.error = null; store.at = Date.now() })
    .catch((e) => { store.error = String((e && e.message) || e) })
    .finally(() => { delete store.inflight[g]; notify() })
  notify()
  return store.inflight[g]
}
function refresh() {
  return Promise.all(['base', ...[...store.loaded].filter((g) => g !== 'base')].map(refreshGroup)).then(announce)
}

// One desktop notification per NEW "Now" card in the Hermes tab. The first read only records what is
// already there, so opening Desktop never fires a burst. Seen ids are remembered per viewer.
const SEEN_KEY = 'hermes-inbox.seen-now.v1'
let seenNow = null
function loadSeen() { try { return new Set(JSON.parse(localStorage.getItem(SEEN_KEY) || '[]')) } catch { return new Set() } }
function saveSeen(set) { try { localStorage.setItem(SEEN_KEY, JSON.stringify([...set].slice(-300))) } catch { /* fine */ } }
function announce() {
  if (!store.apps.base) return
  const nowItems = shown(store).filter((i) => appOf(i) === 'hermes' && i.priority === 'now')
  const first = seenNow === null
  if (first) seenNow = loadSeen()
  const fresh = nowItems.filter((i) => !seenNow.has(i.id))
  fresh.forEach((i) => seenNow.add(i.id))
  saveSeen(seenNow)
  if (first && seenNow.size === fresh.length) return   // a brand-new viewer: record, don't notify
  if (!os || typeof os.notify !== 'function') return
  fresh.slice(0, 3).forEach((i) => os.notify({ title: `Inbox · ${i.title}`, body: i.detail || i.why || '', activate: ROUTE }))
}

// Cheap change check: the backend reports the newest mtime of what the inbox reads; re-read on change.
let lastStamp = null
async function checkStamp() {
  if (backend !== 'rest' || document.hidden || busy(store)) return
  try {
    const { stamp } = await runInbox(['stamp'])
    if (lastStamp !== null && stamp !== lastStamp) refresh()
    lastStamp = stamp
  } catch { /* the 5-min refresh still runs */ }
}
const busy = (s) => Object.keys(s.inflight).length > 0
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
    if (!store.apps.base && !store.inflight.base) refresh()
    return () => store.listeners.delete(fn)
  }, [])
  return store
}
function startPolling(ctx) {
  const tick = () => { if (!document.hidden) refresh() }
  ctx.setInterval(tick, REFRESH_MS)
  ctx.setInterval(checkStamp, 30000)
  ctx.addEventListener(document, 'visibilitychange', tick)
  refresh()
}
const shown = (s) => Object.keys(GROUPS).flatMap((g) => (s.apps[g] && s.apps[g].items) || []).filter((i) => !s.cleared.has(i.id))

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
const SOURCE = { filed: 'POSTED', keys: 'KEYS', vitals: 'VITALS', sessions: 'CHAT', crons: 'CRON', prs: 'PR', review: 'REVIEW' }

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
.hin .tabs{display:flex;gap:4px;width:100%;margin:2px 0 -2px;border-bottom:1px solid var(--ui-stroke-tertiary,#34343b)}.hin .head .tab{font:13px system-ui,sans-serif;padding:7px 12px;border:0;border-radius:0;background:transparent;color:var(--ui-text-tertiary,#a6a4b0);cursor:pointer;display:flex;gap:7px;align-items:center}.hin .tab span.dim{opacity:.5}.hin .tab span{font:11px ${MONO};padding:0 6px;border-radius:999px;background:var(--ui-bg-quaternary,#24242b);color:var(--ui-text-secondary,#b8b6c2)}.hin .head .tab:hover{color:var(--ui-text-primary,#eeedf2)}.hin .head .tab.on{color:var(--ui-text-primary,#eeedf2);box-shadow:0 2px 0 var(--ui-accent,#4a84fe)}.hin .tab.on span{background:color-mix(in srgb,var(--ui-accent,#4a84fe) 25%,transparent);color:var(--ui-text-primary,#eeedf2)}.hin .tally{display:flex;gap:8px;width:100%}
.hin .tally span{font:600 11px ${MONO};letter-spacing:.06em;padding:3px 9px;border-radius:999px;border:1px solid var(--ui-stroke-tertiary,#34343b);color:var(--ui-text-quaternary,#6e7681)}
.hin .tally .on{color:var(--t);border-color:color-mix(in srgb,var(--t) 55%,transparent);background:color-mix(in srgb,var(--t) 10%,transparent)}
.hin .scroll{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;overscroll-behavior-y:contain;scrollbar-gutter:stable;padding:18px clamp(20px,3vw,40px) 32px}
.hin .scroll::-webkit-scrollbar{width:8px}.hin .scroll::-webkit-scrollbar-track{background:transparent}
.hin .scroll::-webkit-scrollbar-thumb{background:var(--ui-stroke-tertiary,#34343b);border-radius:8px}
.hin .lane{margin:4px 0 16px}
.hin .lane-title{display:flex;align-items:center;gap:8px;margin:0 0 10px;padding:2px 6px 2px 0;border:0;background:transparent;color:inherit;cursor:pointer;border-radius:6px;font:inherit}.hin .lane-title:hover b{text-decoration:underline}.hin .shine{background:linear-gradient(100deg,var(--l) 0%,var(--l) 38%,#fff 50%,var(--l) 62%,var(--l) 100%);background-size:250% 100%;-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;filter:drop-shadow(0 0 6px color-mix(in srgb,var(--l) 55%,transparent));animation:hin-sweep 4.5s ease-in-out infinite}.hin .shine.soft{background-image:linear-gradient(100deg,color-mix(in srgb,var(--l) 45%,#8b8b95) 0%,color-mix(in srgb,var(--l) 45%,#8b8b95) 40%,color-mix(in srgb,var(--l) 30%,#fff) 50%,color-mix(in srgb,var(--l) 45%,#8b8b95) 60%,color-mix(in srgb,var(--l) 45%,#8b8b95) 100%);filter:drop-shadow(0 0 4px color-mix(in srgb,var(--l) 30%,transparent));animation-delay:.25s}@keyframes hin-sweep{0%{background-position:100% 0}55%,100%{background-position:0 0}}.hin .tally .on{position:relative;overflow:hidden;box-shadow:0 0 10px color-mix(in srgb,var(--t) 35%,transparent),inset 0 0 6px color-mix(in srgb,var(--t) 18%,transparent)}.hin .tally .on:after{content:'';position:absolute;inset:0;background:linear-gradient(100deg,transparent 35%,color-mix(in srgb,#fff 35%,transparent) 50%,transparent 65%);background-size:250% 100%;animation:hin-sweep 4.5s ease-in-out infinite;pointer-events:none}.hin h1{background:linear-gradient(100deg,var(--ui-text-primary,#eeedf2) 40%,#fff 50%,var(--ui-text-primary,#eeedf2) 60%);background-size:250% 100%;-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;animation:hin-sweep 6s ease-in-out infinite;filter:drop-shadow(0 0 8px color-mix(in srgb,var(--ui-accent,#4a84fe) 35%,transparent))}@media (prefers-reduced-motion:reduce){.hin .shine,.hin h1,.hin .tally .on:after{animation:none}}.hin .lane-title .codicon{font-size:12px;color:var(--ui-text-tertiary,#a6a4b0)}.hin .lane.folded{margin-bottom:8px}.hin .lane.folded .lane-title{margin-bottom:0}.hin .tally span{cursor:pointer;user-select:none}.hin .tally span.off{opacity:.45;text-decoration:line-through}.hin .card{cursor:pointer;outline:none}.hin .card:focus-visible{box-shadow:0 0 0 2px var(--a)}.hin .chev{font-size:12px;color:var(--ui-text-quaternary,#6e7681);opacity:0;transition:opacity .15s}.hin .card:hover .chev,.hin .card.open .chev{opacity:1}.hin .card.open{height:auto;min-height:116px;grid-column:span 2;cursor:default;transform:none}.hin .card.open .name{-webkit-line-clamp:unset;font-size:13.5px}.hin .card.open .label{display:block;font-size:10.5px;font-weight:600;color:var(--ui-text-secondary,#b8b6c2);margin:8px 0 0}.hin .card.open .brief li span{-webkit-line-clamp:unset}.hin .card.open .foot{margin-top:10px}.hin .why{margin-top:8px;font-size:11.5px;line-height:1.5;color:var(--ui-text-primary,#eeedf2)}.hin .facts{display:grid;grid-template-columns:auto 1fr;gap:3px 12px;margin:8px 0 0;font-size:11px}.hin .facts dt{color:var(--ui-text-quaternary,#6e7681)}.hin .facts dd{margin:0;color:var(--ui-text-secondary,#b8b6c2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.hin .more{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.hin .more button{font:11px system-ui,sans-serif;padding:3px 9px;border-radius:999px;border:1px solid color-mix(in srgb,var(--a) 40%,transparent);background:color-mix(in srgb,var(--a) 8%,transparent);color:var(--ui-text-secondary,#b8b6c2);cursor:pointer}.hin .more button:hover{color:var(--ui-text-primary,#eeedf2);background:color-mix(in srgb,var(--a) 18%,transparent)}.hin .cmd{display:block;margin-top:8px;font:11px ${MONO};color:var(--ui-text-secondary,#b8b6c2);background:var(--ui-bg-tertiary,#0d1117);border:1px solid var(--ui-stroke-tertiary,#34343b);border-radius:6px;padding:5px 8px;overflow-wrap:anywhere;user-select:all;cursor:text}@container (max-width:520px){.hin .card.open{grid-column:auto}}
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
.hin .foot .import{margin-left:auto;color:var(--ui-accent,#4a84fe)}.hin .foot .import + .go{margin-left:0}.hin .foot .go{margin-left:auto;color:color-mix(in srgb,var(--a) 75%,var(--ui-text-primary,#eeedf2));font-weight:600}.hin .foot .go:hover{color:var(--ui-text-primary,#eeedf2);background:color-mix(in srgb,var(--a) 22%,transparent)}.hin .foot .done{color:color-mix(in srgb,var(--ui-green,#3fb950) 80%,#888)}.hin .foot .done:hover{color:var(--ui-green,#3fb950);background:color-mix(in srgb,var(--ui-green,#3fb950) 14%,transparent)}.hin .foot .snooze{color:color-mix(in srgb,var(--ui-yellow,#d29922) 80%,#888)}.hin .foot .snooze:hover{color:var(--ui-yellow,#d29922);background:color-mix(in srgb,var(--ui-yellow,#d29922) 14%,transparent)}
.hin .head button{font-size:12px;padding:5px 12px;border-radius:7px;border:1px solid var(--ui-stroke-tertiary,#34343b);background:var(--ui-bg-quaternary,#24242b);color:inherit;cursor:pointer}
.hin .head button:disabled{opacity:.5;cursor:default}
.hin .empty{grid-column:1/-1;font-size:12px;color:var(--ui-text-quaternary,#6e7681);padding:4px 0 8px}
.hin .zero{margin-bottom:18px;padding:12px 16px;border-radius:10px;font-weight:600;color:var(--ui-green,#3fb950);border:1px solid color-mix(in srgb,var(--ui-green,#3fb950) 45%,transparent)}
.hin .note{font-size:11px;color:var(--ui-text-quaternary,#6e7681);line-height:1.6}
.hin .warn{font-size:11.5px;color:var(--ui-orange,#ffa657);margin-bottom:10px}
`
const ICON = { claude: 'sparkle', codex: 'terminal', filed: 'mail', keys: 'key', vitals: 'pulse', sessions: 'comment-discussion', crons: 'clock', prs: 'git-pull-request', review: 'eye' }
const KIND = { claude: 'Claude Code', codex: 'Codex (ChatGPT)', filed: 'Posted', keys: 'Key Doctor', vitals: 'Vitals', sessions: 'Bot chat', crons: 'Cron job', prs: 'Pull request', review: 'X Studio' }
// Tabs: one per app that has something waiting. The status-bar chip always counts every tab.
// Hermes is the default. Claude and Codex are tabs you click: nothing of theirs shows anywhere else.
const TABS = [['hermes', 'Hermes'], ['claude', 'Claude'], ['codex', 'Codex (ChatGPT)'], ['xstudio', 'X Studio']]
const ON_CLICK = ['claude', 'codex']  // their source is switched on the first time you open the tab
const TAB_KEY = 'hermes-inbox.tab.v2'
const loadTab = () => { try { return localStorage.getItem(TAB_KEY) || 'hermes' } catch { return 'hermes' } }
const saveTab = (t) => { try { localStorage.setItem(TAB_KEY, t) } catch { /* fine */ } }
const appOf = (it) => it.app || 'hermes'
// Other agent apps are off until switched on here; the choice is saved in ~/.hermes/inbox.json.
async function flipSource(name, on) {
  store.switches = { ...store.switches, [name]: on }
  notify()
  try { await runInbox(['set', name, on ? 'on' : 'off']) } catch (e) { host.notify?.({ kind: 'error', message: `Inbox: ${String((e && e.message) || e)}` }) }
  refresh()
}
// Who needs you: the Bot (or the app, for Claude Code and Codex).
function agentFacts(it) {
  const facts = it.facts || []
  if (!it.agent || facts.some(([k]) => k === 'Bot' || k === 'Agent')) return facts
  return [[it.app === 'claude' || it.app === 'codex' ? 'Agent' : 'Bot', it.agent], ...facts]
}
const NEEDS = { now: 'What it needs', today: 'What it needs', later: 'When you can' }

function Go({ a }) {
  const [copied, setCopied] = useState(false)
  if (!a || a.kind === 'none' || !a.value) return jsx('span', { style: { marginLeft: 'auto' } })
  const label = `${a.label} →`
  if (a.kind === 'session') {
    return jsx('button', { type: 'button', className: 'go',
      onClick: () => host.openSession(a.session, { profile: a.profile, intent: 'tab' })
        .catch((e) => host.notify?.({ kind: 'error', message: String((e && e.message) || e) })),
      children: label })
  }
  if (a.kind === 'url') return jsx('a', { className: 'go', href: a.value, rel: 'noreferrer', onClick: (e) => { e.preventDefault(); openLink(a.value) }, children: label })
  if (a.kind === 'open' || /^\/[a-z-]+$/.test(a.value)) return jsx('button', { type: 'button', className: 'go', onClick: () => host.navigate(a.value), children: label })
  return jsx('button', { type: 'button', className: 'go', title: a.value,
    onClick: () => navigator.clipboard?.writeText(a.value).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) }),
    children: copied ? 'Copied ✓' : `${/^copy\b/i.test(a.label) ? a.label : `Copy ${a.label.toLowerCase()}`} →` })
}

// Folded sections are remembered per viewer (a convenience; the page works without storage).
const FOLD_KEY = 'hermes-inbox.folded.v1'
function loadFolded() { try { return JSON.parse(localStorage.getItem(FOLD_KEY) || '{}') } catch { return {} } }
function saveFolded(v) { try { localStorage.setItem(FOLD_KEY, JSON.stringify(v)) } catch { /* fine */ } }

function ImportBtn({ m }) {
  const [state, setState] = useState('idle')
  return jsx('button', { type: 'button', className: 'go import', disabled: state === 'busy', title: `hermes sessions import --from ${m.app} ${m.path}`,
    onClick: () => {
      setState('busy')
      continueInHermes(m).then(() => setState('done')).catch((e) => { setState('idle'); host.notify?.({ kind: 'error', message: `Continue in Hermes: ${String((e && e.message) || e)}` }) })
    },
    children: state === 'busy' ? 'Importing…' : state === 'done' ? 'Opened in Hermes ✓' : 'Continue in Hermes →' })
}

function CopyBtn({ m }) {
  const [done, setDone] = useState(false)
  if (m.kind === 'hermes-import') return null  // shown in the card foot instead
  if (m.kind === 'open') return jsx('button', { type: 'button', onClick: () => host.navigate(m.value), children: `${m.label} →` })
  return jsx('button', { type: 'button', title: m.value,
    onClick: () => navigator.clipboard?.writeText(m.value).then(() => { setDone(true); setTimeout(() => setDone(false), 1500) }),
    children: done ? 'Copied ✓' : m.label })
}

// ✦ Hand to a Bot: drafts the card as a job in Hermes chat (never sent), with what the inbox knows.
const COMPOSER_INSERT = 'hermes:composer-insert'
const COMPOSER_FOCUS = 'hermes:composer-focus'
function handToBot(it) {
  const facts = agentFacts(it).filter(([, v]) => v).map(([k, v]) => `- ${k}: ${v}`)
  const act = it.action && it.action.value ? `\nSuggested next step: ${it.action.label}: ${it.action.value}` : ''
  const text = `My Inbox says: ${it.title}${it.detail ? ` (${it.detail})` : ''}.` + (it.why ? `\nWhy: ${it.why}` : '') +
    (facts.length ? `\n${facts.join('\n')}` : '') + act +
    '\n\nLook into this and tell me what is wrong and what you would do. Do not change, run or send anything until I say go.'
  host.navigate('/')
  window.setTimeout(() => {
    window.dispatchEvent(new CustomEvent(COMPOSER_INSERT, { detail: { mode: 'block', target: 'main', text } }))
    window.dispatchEvent(new CustomEvent(COMPOSER_FOCUS, { detail: { target: 'main' } }))
  }, 250)
}

function RunAgain({ it }) {
  const [state, setState] = useState('idle')
  if (!it.cron) return null
  return jsx('button', { type: 'button', disabled: state === 'busy' || state === 'done', title: `hermes -p ${it.cron.profile} cron run ${it.cron.id}`,
    onClick: () => {
      setState('busy')
      runInbox(['run-cron', it.cron.profile, it.cron.id])
        .then((r) => { if (r && r.ok) setState('done'); else { setState('idle'); host.notify?.({ kind: 'error', message: `Run again: ${(r && r.why) || 'failed'}` }) } })
        .catch((e) => { setState('idle'); host.notify?.({ kind: 'error', message: `Run again: ${String((e && e.message) || e)}` }) })
    },
    children: state === 'busy' ? 'Starting…' : state === 'done' ? 'Runs on next tick ✓' : '↻ Run again' })
}

function Card({ it, color, open, onToggle }) {
  const stop = (e) => e.stopPropagation()
  return jsxs('div', {
    className: `card${open ? ' open' : ''}`, style: { '--a': color }, role: 'button', tabIndex: 0, 'aria-expanded': open,
    onClick: onToggle, onKeyDown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle() } },
    children: [
      jsxs('div', { className: 'card-head', children: [
        jsx('span', { className: 'icon', children: jsx('i', { className: `codicon codicon-${ICON[it.source] || 'circle'}` }) }),
        jsxs('div', { style: { minWidth: 0, flex: 1 }, children: [
          jsx('div', { className: 'name', children: it.title }),
          jsx('div', { className: 'src', children: [KIND[it.source] || it.source, it.agent && it.agent !== KIND[it.source] ? it.agent : null, it.at ? `${ago(it.at)} ago` : null].filter(Boolean).join(' · ') }),
        ] }),
        jsx('i', { className: `chev codicon codicon-chevron-${open ? 'up' : 'down'}` }),
      ] }),
      jsx('div', { className: 'label', children: NEEDS[it.priority] }),
      jsx('ul', { className: 'brief', children: jsx('li', { children: jsx('span', { children: it.detail || it.why || it.action.label }) }) }),
      open && it.why ? jsx('div', { className: 'why', children: it.why }) : null,
      open && agentFacts(it).length ? jsx('dl', { className: 'facts', children: agentFacts(it).filter(([, v]) => v).flatMap(([k, v]) => [jsx('dt', { children: k }, `${k}-k`), jsx('dd', { title: v, children: v }, `${k}-v`)]) }) : null,
      open && it.action.kind === 'command' ? jsx('code', { className: 'cmd', onClick: stop, children: it.action.value }) : null,
      open ? jsx('div', { className: 'more', onClick: stop, children: [
        ...(it.more || []).map((m) => jsx(CopyBtn, { m }, m.label)),
        jsx(RunAgain, { it }, 'run'),
        jsx('button', { type: 'button', title: 'Draft this as a job for a Bot in chat (not sent)', onClick: () => handToBot(it), children: '✦ Hand to a Bot' }, 'bot'),
      ] }) : null,
      jsxs('div', { className: 'foot', onClick: stop, children: [
        jsx('button', { type: 'button', className: 'done', title: 'Hide until it changes', onClick: () => clear(it, 'done'), children: '✓ Done' }),
        jsx('button', { type: 'button', className: 'snooze', title: 'Hide for a day', onClick: () => clear(it, 'snooze', 24), children: '◷ Snooze' }),
        open ? jsx('button', { type: 'button', className: 'snooze', title: 'Hide for a week', onClick: () => clear(it, 'snooze', 168), children: '1 week' }) : null,
        ...((it.more || []).filter((m) => m.kind === 'hermes-import').map((m) => jsx(ImportBtn, { m }, 'import'))),
        // With "Continue in Hermes" on the card, the copy-resume button only shows once the card is open.
        (it.more || []).some((m) => m.kind === 'hermes-import') && !open ? null : jsx(Go, { a: it.action }),
      ] }),
    ],
  })
}

function Lane({ lane, items, folded, onFold, openId, setOpenId }) {
  return jsxs('section', { className: `lane${folded ? ' folded' : ''}`, children: [
    jsxs('button', { type: 'button', className: 'lane-title', 'aria-expanded': !folded, onClick: onFold, children: [
      jsx('i', { className: `codicon codicon-chevron-${folded ? 'right' : 'down'}` }),
      jsx('b', { className: items.length ? 'shine' : '', style: { '--l': items.length ? lane.color : C.t4, color: items.length ? lane.color : C.t4 }, children: `${lane.label.toUpperCase()} · ${items.length}` }),
      jsx('span', { className: items.length ? 'shine soft' : '', style: { '--l': items.length ? lane.color : C.t4 }, children: lane.hint }),
    ] }),
    folded ? null : jsx('div', { className: 'grid', children: items.length
      ? items.map((it) => jsx(Card, { it, color: lane.color, open: openId === it.id, onToggle: () => setOpenId(openId === it.id ? null : it.id) }, it.id))
      : jsx('div', { className: 'empty', children: 'Nothing here.' }) }),
  ] })
}

function InboxPage() {
  const s = useInbox()
  const [folded, setFolded] = useState(loadFolded)
  const [openId, setOpenId] = useState(null)
  const [tab, setTabState] = useState(loadTab)
  const setTab = (t) => { setTabState(t); saveTab(t) }
  const fold = (key) => { const next = { ...folded, [key]: !folded[key] }; setFolded(next); saveFolded(next) }
  const allFolded = LANES.every((l) => folded[l.key])
  const foldAll = () => { const next = Object.fromEntries(LANES.map((l) => [l.key, !allFolded])); setFolded(next); saveFolded(next) }
  const d = s.apps.base
  const all = shown(s)
  const tabCount = (t) => (t === 'all' ? all.length : all.filter((i) => appOf(i) === t).length)
  const tabs = TABS.filter(([t]) => t === 'hermes' || ON_CLICK.includes(t) || tabCount(t) > 0)
  const current = tabs.some(([t]) => t === tab) ? tab : 'hermes'
  const items = all.filter((i) => appOf(i) === current)
  const sourceOn = (t) => !ON_CLICK.includes(t) || Boolean(s.switches[t])
  const loaded = (t) => !ON_CLICK.includes(t) || s.loaded.has(t)
  const loadTabData = (t) => { if (ON_CLICK.includes(t) && !s.loaded.has(t) && !s.inflight[t]) (sourceOn(t) ? Promise.resolve() : flipSource(t, true)).then(() => refreshGroup(t)) }
  const openTab = (t) => { setTab(t); loadTabData(t) }
  // The page may open on a remembered Claude/Codex tab: fetch it then too, not only on a click.
  useEffect(() => { if (d) loadTabData(current) }, [current, Boolean(d)])
  const head = jsxs('div', { className: 'head', children: [
    jsx('h1', { children: 'Inbox' }),
    jsx('span', { className: 'sub', children: 'everything your Bots and tools are waiting on you for' }),
    jsx('span', { className: 'meta', children: d ? `${d.hidden ? `${d.hidden} done/snoozed · ` : ''}checked ${new Date(s.at).toLocaleTimeString()}` : s.error ? '' : 'checking Bots, crons, keys, PRs…' }),
    d ? jsx('button', { type: 'button', onClick: foldAll, children: allFolded ? 'Expand all' : 'Collapse all' }) : null,
    jsx('button', { type: 'button', onClick: () => refresh(), disabled: busy(s), children: busy(s) ? 'Checking…' : 'Check now' }),
    d ? jsx('div', { className: 'tabs', role: 'tablist', children: tabs.map(([t, label]) => jsxs('button', {
      type: 'button', role: 'tab', 'aria-selected': current === t, className: current === t ? 'tab on' : 'tab', onClick: () => openTab(t),
      title: sourceOn(t) ? `${label} inbox` : `Click to read ${label} sessions that are waiting on you`,
      children: [label, loaded(t) ? jsx('span', { children: tabCount(t) }) : jsx('span', { className: 'dim', children: s.inflight[t] ? '…' : '·' })] }, t)) }) : null,
    d ? jsx('div', { className: 'tally', children: LANES.map((l) => {
      const n = items.filter((i) => i.priority === l.key).length
      return jsx('span', { className: `${n ? 'on' : ''}${folded[l.key] ? ' off' : ''}`, style: { '--t': l.color }, title: folded[l.key] ? 'Show this section' : 'Fold this section', onClick: () => fold(l.key), children: `${n} ${l.label.toLowerCase()}` }, l.key)
    }) }) : null,
  ] })
  const cur = s.apps[current === 'xstudio' || current === 'hermes' ? 'base' : current]
  const errs = cur ? Object.entries(cur.errors || {}) : []
  const truncated = cur && cur.truncated
  return jsxs('div', { className: 'hin', children: [
    jsx('style', { children: CSS }),
    head,
    jsx('div', { className: 'scroll', children: !d
      ? (s.error ? jsxs('div', { style: { color: C.red, fontSize: 12.5 }, children: [`Could not read the inbox: ${String(s.error).slice(0, 300)}`,
        jsx('div', { style: { color: C.t3, fontSize: 12, marginTop: 8, fontFamily: MONO }, children: 'Fix: hermes plugins enable hermes-agent-inbox, then restart Hermes Desktop (Capabilities → Plugins → Rescan).' })] }) : null)
      : [
        items.length ? null : jsx('div', { className: 'zero', children: !loaded(current) || s.inflight[current] ? 'Reading…' : current === 'hermes' ? 'Inbox zero. No Bot is waiting on you.' : `Nothing from ${(TABS.find(([t]) => t === current) || [])[1]} is waiting on you.` }, 'zero'),
        truncated ? jsx('div', { className: 'warn', children: `${truncated} more card${truncated === 1 ? '' : 's'} didn't fit on this page; \`hermes inbox\` in a terminal shows them all.` }, 'trunc') : null,
        ...LANES.map((lane) => jsx(Lane, { lane, items: items.filter((i) => i.priority === lane.key), folded: Boolean(folded[lane.key]), onFold: () => fold(lane.key), openId, setOpenId }, lane.key)),
        errs.length ? jsx('div', { className: 'warn', children: `Couldn't read: ${errs.map(([k, v]) => `${k} (${v})`).join(' · ')}` }, 'errs') : null,
        jsx('div', { className: 'note', children: 'Chats show only the title and how they ended, never the messages. Done hides a card until it changes; Snooze hides it for a day.' }, 'note'),
      ] }),
  ] })
}

function StatusChip() {
  const s = useInbox()
  if (!s.apps.base) return null
  const items = shown(s).filter((i) => appOf(i) === 'hermes')  // the chip is Hermes only; other agents show only on their tab
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
    os = ctx.os || null
    pluginCtx = ctx
    ctx.register({ id: 'page', area: ROUTES_AREA, data: { path: ROUTE }, render: () => jsx(InboxPage, {}) })
    ctx.register({ id: 'nav', area: SIDEBAR_NAV_AREA, order: 86, data: { path: ROUTE, label: 'Inbox', codicon: 'inbox' } })
    ctx.register({ id: 'open', area: PALETTE_AREA, data: { id: `${ID}.open`, label: 'Open Inbox', keywords: ['inbox', 'waiting', 'approval', 'stuck', 'todo', 'review'], run: () => host.navigate(ROUTE) } })
    ctx.register({ id: 'statusbar', area: STATUSBAR_AREAS.right, order: 131, render: () => jsx(StatusChip, {}) })
    startPolling(ctx)
  },
}

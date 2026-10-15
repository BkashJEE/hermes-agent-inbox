// Test harness only: mimics the parts of @hermes/plugin-sdk that hermes-agent-inbox uses.
// live.json (default) is `hermes inbox --json` saved on this machine (gitignored: it holds real titles).
// ?data=demo loads demo.json: made-up Bots and repos, used for the README screenshots.
const params = new URLSearchParams(location.search)
const payload = await fetch(params.get('data') === 'demo' ? './demo.json' : './live.json').then((r) => r.json())
const nowS = Date.now() / 1000
for (const it of payload.items || []) if ('ago' in it) it.at = it.ago ? nowS - it.ago : 0
export const ROUTES_AREA = 'routes', SIDEBAR_NAV_AREA = 'sidebar.nav', PALETTE_AREA = 'palette', STATUSBAR_AREAS = { left: 'statusBar.left', right: 'statusBar.right' }
export const host = {
  state: { focusedSessionOwner: { get: () => null } },
  navigate: (p) => { window.__nav = p },
  openSession: async (id, opts) => { window.__opened = { id, ...opts } },
  notify: (n) => console.log('notify', n),
  profileRoutes: async () => [{ connectionId: 'local', mode: 'local', primary: true, profile: 'ceo', targetProfile: 'ceo' }],
  requestProfile: async (route, method, params) => {
    window.__calls = (window.__calls || []).concat([{ method, argv: params.argv }])
    const verb = params.argv[3]
    return { code: 0, output: 'some log line\n' + JSON.stringify(verb === 'done' || verb === 'snooze' ? { ok: true } : payload) }
  },
}

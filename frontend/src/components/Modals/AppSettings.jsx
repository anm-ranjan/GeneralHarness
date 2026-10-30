import { useEffect, useState } from 'react'
import { useApp } from '../../context/AppContext'
import useApi from '../../hooks/useApi'
import {
  bridgeAction,
  bridgeStateLabel,
  normalizeBridgeHosts,
  stopConfirmation,
} from '../../bridge'

const BRIDGE_POLL_MS = 6000
const STATE_TONE = { online: 'text-ok', off: 'text-muted', unreachable: 'text-danger' }

// Bring online / take offline go to the machine serving this page, never to the
// host currently being viewed: it owns the SSH forwards. Hence bare fetch
// rather than api(), which follows the active host.
async function bridgeRequest(path, body) {
  const res = await fetch(path, body === undefined
    ? undefined
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  let data = null
  try { data = await res.json() } catch { /* empty body */ }
  if (!res.ok) {
    const error = new Error(typeof data?.detail === 'string' ? data.detail : (data?.detail?.message || `Request failed (${res.status})`))
    error.status = res.status
    error.detail = data?.detail
    throw error
  }
  return data
}

function HostsSection() {
  const { dispatch } = useApp()
  const [hosts, setHosts] = useState([])
  const [pending, setPending] = useState({})
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const data = await bridgeRequest('/api/fleet/bridge')
        if (!cancelled) setHosts(normalizeBridgeHosts(data))
      } catch {
        // An older backend has no bridge route: show nothing rather than an error.
        if (!cancelled) setHosts([])
      }
    }
    load()
    const timer = setInterval(load, BRIDGE_POLL_MS)
    return () => { cancelled = true; clearInterval(timer) }
  }, [])

  if (!hosts.length) return null

  async function run(host, action, force) {
    setError('')
    setPending(prev => ({ ...prev, [host.id]: action.kind }))
    try {
      const body = action.kind === 'stop' ? { force } : undefined
      const next = await bridgeRequest(`/api/fleet/bridge/${encodeURIComponent(host.id)}/${action.kind}`, body ?? {})
      setHosts(prev => prev.map(entry => (entry.id === host.id ? normalizeBridgeHosts({ hosts: [next] })[0] : entry)))
    } catch (err) {
      if (err.status === 409 && action.kind === 'stop') {
        // It became busy between the last poll and the click: ask again.
        const d = err.detail || {}
        confirmStop({ ...host, running: d.running || 0, waitingApproval: d.waiting_approval || 0 })
      } else {
        setError(err.message)
      }
    } finally {
      setPending(prev => { const { [host.id]: _done, ...rest } = prev; return rest })
    }
  }

  function confirmStop(host) {
    const confirmation = stopConfirmation(host)
    dispatch({
      type: 'OPEN_CONFIRM',
      payload: {
        title: confirmation.title,
        message: confirmation.message,
        confirmLabel: confirmation.confirmLabel,
        tone: confirmation.tone,
        onConfirm: () => run(host, { kind: 'stop' }, confirmation.force),
      },
    })
  }

  function act(host) {
    const action = bridgeAction(host)
    if (!action) return
    if (action.kind === 'stop') confirmStop(host)
    else run(host, action, false)
  }

  return (
    <div>
      <h4 className="text-[13px] font-bold text-text-bright">Hosts</h4>
      <p className="text-[13px] text-muted mb-2">
        Bring another machine online to switch to it, or take it offline when you are done.
      </p>
      <ul className="space-y-1.5">
        {hosts.map(host => {
          const action = bridgeAction(host)
          const busy = pending[host.id]
          return (
            <li key={host.id} className="flex items-center gap-3 text-[13px]">
              <span className="text-text-bright min-w-[7rem]">{host.label}</span>
              <span className={`min-w-[5.5rem] ${STATE_TONE[host.state]}`}>
                {busy === 'start' ? 'Starting…' : busy === 'stop' ? 'Stopping…' : bridgeStateLabel(host.state)}
              </span>
              {action && (
                <button
                  onClick={() => act(host)}
                  disabled={!!busy}
                  className="px-3 py-1 text-[12px] font-medium text-muted border border-line rounded hover:text-text-bright hover:border-line-hover transition-colors disabled:opacity-50"
                >
                  {action.label}
                </button>
              )}
            </li>
          )
        })}
      </ul>
      {error && <p className="mt-2 text-[12px] text-danger" role="alert">{error}</p>}
    </div>
  )
}

// Connection details and Quit live here rather than in the header: they are
// needed rarely, and the header only raises its voice when the server is down.
export default function AppSettings() {
  const { state, dispatch } = useApp()
  const { shutdown } = useApi()
  const desktopApi = typeof window !== 'undefined' ? window.myharnessDesktop : null
  const [desktopStatus, setDesktopStatus] = useState(null)

  useEffect(() => {
    if (!desktopApi?.getStatus) return
    let cancelled = false
    desktopApi.getStatus()
      .then(status => { if (!cancelled) setDesktopStatus(status || null) })
      .catch(() => { if (!cancelled) setDesktopStatus(null) })
    return () => { cancelled = true }
  }, [desktopApi])

  const backendLabel = state.desktopEnabled
    ? `Desktop app, server at ${state.desktopBackendUrl || window.location.origin}${state.electronOnly ? ' (Electron only)' : ''}`
    : (state.serverOnline ? 'Server is running' : 'Server is stopped')
  const desktopBackendMode = desktopStatus?.backendMode || ''
  const usesConfiguredDesktopBackend = !!desktopApi && desktopBackendMode === 'configured'
  const usesLocalDesktopBackend = !!desktopApi && desktopBackendMode === 'local'
  const quitLabel = usesConfiguredDesktopBackend
    ? 'Close desktop app'
    : (usesLocalDesktopBackend ? 'Stop local backend and close' : 'Quit')

  function quit() {
    dispatch({
      type: 'OPEN_CONFIRM',
      payload: {
        title: `Quit ${state.appName}?`,
        message: desktopApi
          ? (usesConfiguredDesktopBackend
            ? 'This closes the desktop app and leaves the configured backend running.'
            : (usesLocalDesktopBackend
              ? 'This stops the local backend sidecar and closes the desktop app.'
              : 'This closes the desktop app. If this app started a local backend sidecar, that sidecar will be stopped.'))
          : 'This stops the local backend and frontend servers for the current app session.',
        confirmLabel: 'Quit',
        tone: 'danger',
        onConfirm: desktopApi?.quit ? () => desktopApi.quit() : shutdown,
      },
    })
  }

  return (
    <div className="space-y-4 max-w-xl">
      <div>
        <h4 className="text-[13px] font-bold text-text-bright">Connection</h4>
        <p className={`text-[13px] ${state.serverOnline ? 'text-muted' : 'text-danger'}`}>{backendLabel}</p>
      </div>
      <HostsSection />
      <div>
        <h4 className="text-[13px] font-bold text-text-bright">Quit</h4>
        <p className="text-[13px] text-muted mb-2">Close {state.appName} when you are done for the day.</p>
        <button
          onClick={quit}
          className="px-3 py-1 text-[12px] font-medium text-danger border border-danger/30 rounded hover:bg-danger-soft transition-colors"
        >
          {quitLabel}
        </button>
      </div>
    </div>
  )
}

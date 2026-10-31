import { useEffect, useState } from 'react'
import { useApp } from '../../context/AppContext'
import useApi from '../../hooks/useApi'
import useHostRoster from '../../hooks/useHostRoster'
import HostTag from '../Fleet/HostTag'

function HostsSection() {
  const { rows, error, act } = useHostRoster()

  // No fleet configured: there is no other machine to show.
  if (rows.length < 2) return null

  return (
    <div>
      <h4 className="text-[13px] font-bold text-text-bright">Hosts</h4>
      <p className="text-[13px] text-muted mb-2">
        Bring another machine online to switch to it, or take it offline when you are done.
      </p>
      <ul className="space-y-1.5">
        {rows.map(row => (
          <li key={row.id} className="flex items-center gap-3 text-[13px]">
            <span className="text-text-bright min-w-[7rem]">{row.label}</span>
            <span className="min-w-[7rem]"><HostTag tag={row.tag} busy={row.busy} /></span>
            {row.action && (
              <button
                onClick={() => act(row)}
                className="px-3 py-1 text-[12px] font-medium text-muted border border-line rounded hover:text-text-bright hover:border-line-hover transition-colors"
              >
                {row.action.label}
              </button>
            )}
          </li>
        ))}
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

import { useEffect, useState } from 'react'
import { useApp } from '../context/AppContext'
import useApi from '../hooks/useApi'
import { copyText } from '../clipboard'
import { sessionReference } from '../sessionLabels'
import { shortcutLabel } from '../constants'
import SessionBreadcrumb from './SessionBreadcrumb'
import { SidebarIcon } from './Sidebar/Sidebar'

export default function TopBar() {
  const { state, dispatch } = useApp()
  const { cancelRun, shutdown } = useApi()
  const desktopApi = typeof window !== 'undefined' ? window.myharnessDesktop : null
  const [desktopStatus, setDesktopStatus] = useState(null)
  const [copyState, setCopyState] = useState('')
  useEffect(() => { setCopyState('') }, [state.currentSessionId])
  useEffect(() => { if (!copyState) return; const timer = setTimeout(() => setCopyState(''), 2500); return () => clearTimeout(timer) }, [copyState])

  useEffect(() => {
    if (!desktopApi?.getStatus) return
    let cancelled = false
    desktopApi.getStatus()
      .then(status => {
        if (!cancelled) setDesktopStatus(status || null)
      })
      .catch(() => {
        if (!cancelled) setDesktopStatus(null)
      })
    return () => {
      cancelled = true
    }
  }, [desktopApi])

  const meta = state.currentMeta
  const backendLabel = state.desktopEnabled
    ? `Server · ${state.desktopBackendUrl || window.location.origin}${state.electronOnly ? ' · Electron only' : ''}`
    : (state.serverOnline ? 'Server is running' : 'Server is stopped')
  // "Offline" on its own is ambiguous once a fleet is configured: the machine
  // that cannot be reached is usually not the one serving the page, so the
  // status chip has to name it or it points at the wrong problem.
  const activeHost = (state.fleetHosts || []).find(host => host.id === state.activeHostId)
  const remoteActive = !!activeHost && !activeHost.self
  const offlineLabel = remoteActive ? `${activeHost.label} offline` : 'Offline'
  const offlineTitle = remoteActive
    ? `${activeHost.label} (${activeHost.url}) is not answering. Switch hosts in the sidebar, or bring its tunnel up.`
    : backendLabel
  const desktopBackendMode = desktopStatus?.backendMode || ''
  const usesConfiguredDesktopBackend = !!desktopApi && desktopBackendMode === 'configured'
  const usesLocalDesktopBackend = !!desktopApi && desktopBackendMode === 'local'
  const quitTitle = usesConfiguredDesktopBackend
    ? 'Close desktop app'
    : (usesLocalDesktopBackend ? 'Stop local backend sidecar' : 'Close app')

  return (
    <div className="flex items-center gap-3 px-7 py-2.5 border-b border-line min-h-[48px]">
      {!state.sidebarOpen && (
        <button
          onClick={() => dispatch({ type: 'TOGGLE_SIDEBAR' })}
          className="-ml-3 p-1.5 rounded text-muted hover:text-accent hover:bg-surface-hover transition-colors"
          title={`Show sidebar (${shortcutLabel('B')})`}
          aria-label="Show sidebar"
        >
          <SidebarIcon />
        </button>
      )}
      {meta ? (
        <>
          <SessionBreadcrumb meta={meta} />
          <button type="button" className={`shrink-0 inline-flex items-center justify-center rounded p-1.5 focus-visible:ring-1 focus-visible:ring-accent ${copyState === 'Copy failed' ? 'text-danger' : copyState === 'Copied' ? 'text-ok' : 'text-muted hover:text-accent'}`}
            aria-label="Copy session reference" title={copyState || 'Copy session reference'}
            onClick={async () => {
              try { await copyText(sessionReference(meta, state.projectNames)); setCopyState('Copied') }
              catch { setCopyState('Copy failed') }
            }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              {copyState === 'Copied' ? <path d="m5 12 4 4L19 6" /> : copyState === 'Copy failed' ? <><circle cx="12" cy="12" r="9" /><path d="M12 8v5m0 3h.01" /></> : <><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" /></>}
            </svg>
          </button>
          <span className="sr-only" role="status">{copyState}</span>
        </>
      ) : (
        <h2 className="text-[15px] font-semibold text-text-bright">{state.appName}</h2>
      )}

      <div className="ml-auto flex items-center gap-2">
        <button
          onClick={() => dispatch({ type: 'OPEN_SETTINGS' })}
          className="px-2.5 py-1 text-[13px] text-muted border border-line rounded hover:text-accent hover:border-accent/30 hover:bg-accent-soft transition-colors"
          title="Appearance settings"
        >
          &#9788;
        </button>
        <button
          onClick={() => dispatch({ type: 'TOGGLE_WORKSPACE_PANEL' })}
          className={`px-3 py-1 text-[12px] font-medium border rounded transition-colors ${
            state.workspacePanelOpen
              ? 'text-accent border-accent/30 bg-accent/10'
              : 'text-muted border-line hover:text-text-bright hover:bg-surface-hover'
          }`}
          title="Toggle workspace panel"
        >
          Workspace{state.touchedFiles.length > 0 ? ` (${state.touchedFiles.length})` : ''}
        </button>
        {state.isRunning && (
          <button
            onClick={() => cancelRun(state.currentSessionId)}
            disabled={state.isCancelling}
            className="px-3 py-1 text-[12px] font-medium text-danger border border-danger/30 rounded hover:bg-danger-soft transition-colors disabled:opacity-50"
          >
            {state.isCancelling ? 'Interrupting…' : 'Cancel'}
          </button>
        )}
        {state.hostFallbackFrom && (
          <div
            className="flex items-center gap-1.5 px-2.5 py-1 text-[12px] border rounded text-warn border-warn/30 bg-warn-soft"
            title={`${state.hostFallbackFrom} did not respond on load, so this machine was opened instead.`}
          >
            <span>{state.hostFallbackFrom} unreachable</span>
            <button
              onClick={() => dispatch({ type: 'CLEAR_HOST_FALLBACK' })}
              className="text-faint hover:text-text-bright"
              title="Dismiss"
            >
              ×
            </button>
          </div>
        )}
        <div
          className={`flex items-center gap-1.5 px-2.5 py-1 text-[12px] border rounded ${
            state.serverOnline
              ? 'text-ok border-ok/30 bg-ok-soft'
              : 'text-danger border-danger/30 bg-danger-soft'
          }`}
          title={state.serverOnline ? backendLabel : offlineTitle}
        >
          <span className={`h-1.5 w-1.5 rounded-full ${state.serverOnline ? 'bg-ok' : 'bg-danger'}`} />
          <span>{state.serverOnline ? (state.desktopEnabled ? 'Desktop' : 'Server') : offlineLabel}</span>
        </div>
        <button
          onClick={() => dispatch({
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
          })}
          className="px-3 py-1 text-[12px] font-medium text-muted border border-line rounded hover:text-danger hover:border-danger/30 hover:bg-danger-soft transition-colors"
          title={quitTitle}
        >
          Quit
        </button>
      </div>
    </div>
  )
}

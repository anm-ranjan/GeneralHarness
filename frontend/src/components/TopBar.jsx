import { useEffect, useState } from 'react'
import { useApp } from '../context/AppContext'
import useApi from '../hooks/useApi'
import { labelColorStyle } from '../labelColors'

export default function TopBar() {
  const { state, dispatch } = useApp()
  const { cancelRun, shutdown } = useApi()
  const desktopApi = typeof window !== 'undefined' ? window.myharnessDesktop : null
  const [desktopStatus, setDesktopStatus] = useState(null)

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
  const isChat = meta?.kind === 'chat'
  const projectName = meta ? (state.projectNames[meta.project_id] || meta.project_id) : ''
  const labelName = meta ? (state.taskNames[meta.task_id] || meta.task_id) : ''
  const sessionTitle = meta ? (meta.title || meta.id?.slice(0, 8)) : ''
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
      {meta ? (
        <>
          <div className="flex items-center gap-1.5 text-[12px] text-muted">
            {isChat ? (
              <span>Chat</span>
            ) : (
              <>
                <span>{projectName}</span>
                <span className="text-faint">/</span>
                <span
                  className="label-chip rounded-full px-1.5 py-px text-[10px] font-semibold uppercase tracking-wider"
                  style={labelColorStyle((state.projects || []).flatMap(project => project.tasks || []).find(task => task.id === meta.task_id)?.color || meta.task_id)}
                  title="Primary label"
                >
                  {labelName}
                </span>
              </>
            )}
            <span className="text-faint">/</span>
          </div>
          <h2 className="text-[15px] font-semibold text-text-bright truncate">{sessionTitle}</h2>
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

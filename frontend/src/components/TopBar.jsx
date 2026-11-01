import { useEffect, useState } from 'react'
import { useApp } from '../context/AppContext'
import { copyText } from '../clipboard'
import { sessionReference } from '../sessionLabels'
import { shortcutLabel } from '../constants'
import SessionBreadcrumb from './SessionBreadcrumb'
import BrandMark from './BrandMark'
import { SidebarIcon } from './Sidebar/Sidebar'

// Quiet header: where you are on the left, two plain text links on the right.
// It only raises its voice when something is wrong (server down, host fallback).
export default function TopBar() {
  const { state, dispatch } = useApp()
  const [copyState, setCopyState] = useState('')
  useEffect(() => { setCopyState('') }, [state.currentSessionId])
  useEffect(() => { if (!copyState) return; const timer = setTimeout(() => setCopyState(''), 2500); return () => clearTimeout(timer) }, [copyState])

  const meta = state.currentMeta
  // "Offline" on its own is ambiguous once a fleet is configured: the machine
  // that cannot be reached is usually not the one serving the page, so the
  // notice has to name it or it points at the wrong problem.
  const activeHost = (state.fleetHosts || []).find(host => host.id === state.activeHostId)
  const remoteActive = !!activeHost && !activeHost.self
  const offlineLabel = remoteActive ? `${activeHost.label} offline` : 'Offline'
  const offlineTitle = remoteActive
    ? `${activeHost.label} (${activeHost.url}) is not answering. Switch hosts in the sidebar, or bring its tunnel up.`
    : (state.desktopEnabled
      ? `Server · ${state.desktopBackendUrl || window.location.origin}`
      : 'Server is stopped')
  const linkClass = 'text-[13px] text-muted underline decoration-line underline-offset-4 hover:text-accent transition-colors'

  return (
    <div className="flex items-center gap-4 pl-9 pr-8 pt-6 pb-3 min-h-[64px]">
      {!state.sidebarOpen && (
        <button
          onClick={() => dispatch({ type: 'TOGGLE_SIDEBAR' })}
          className="-ml-6 p-1 rounded text-muted hover:text-accent hover:bg-surface-hover transition-colors"
          title={`Show sidebar (${shortcutLabel('B')})`}
          aria-label="Show sidebar"
        >
          <SidebarIcon />
        </button>
      )}
      {/* The sidebar carries the logo; it moves up here only while the sidebar is hidden. */}
      {!state.sidebarOpen && <BrandMark name={state.appName} className="-ml-2 h-7 w-7" />}
      {meta ? (
        <>
          <SessionBreadcrumb meta={meta} />
          <button
            type="button"
            className={`shrink-0 inline-flex items-center justify-center rounded p-1 focus-visible:ring-1 focus-visible:ring-accent ${copyState === 'Copy failed' ? 'text-danger' : copyState === 'Copied' ? 'text-ok' : 'text-faint hover:text-accent'}`}
            aria-label="Copy session reference"
            title={copyState || 'Copy session reference'}
            onClick={async () => {
              try { await copyText(sessionReference(meta, state.projectNames)); setCopyState('Copied') }
              catch { setCopyState('Copy failed') }
            }}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              {copyState === 'Copied' ? <path d="m5 12 4 4L19 6" /> : copyState === 'Copy failed' ? <><circle cx="12" cy="12" r="9" /><path d="M12 8v5m0 3h.01" /></> : <><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" /></>}
            </svg>
          </button>
          <span className="sr-only" role="status">{copyState}</span>
        </>
      ) : (
        <h2 className="sr-only">{state.appName}</h2>
      )}

      <div className="ml-auto flex shrink-0 items-center gap-4 whitespace-nowrap">
        {state.hostFallbackFrom && (
          <span
            className="text-[13px] text-warn"
            title={`${state.hostFallbackFrom} did not respond on load, so this machine was opened instead.`}
          >
            {state.hostFallbackFrom} unreachable.{' '}
            <button
              onClick={() => dispatch({ type: 'CLEAR_HOST_FALLBACK' })}
              className="underline decoration-line underline-offset-4 hover:text-text-bright"
            >
              Dismiss
            </button>
          </span>
        )}
        {!state.serverOnline && (
          <span className="text-[13px] text-danger" title={offlineTitle} role="status">{offlineLabel}</span>
        )}
        <button
          onClick={() => dispatch({ type: 'TOGGLE_WORKSPACE_PANEL' })}
          aria-pressed={state.workspacePanelOpen}
          className={`${linkClass} ${state.workspacePanelOpen ? '!text-text-bright decoration-accent' : ''}`}
          title="Toggle workspace panel"
        >
          Workspace{state.touchedFiles.length > 0 ? ` ${state.touchedFiles.length}` : ''}
        </button>
        <button
          onClick={() => dispatch({ type: 'OPEN_SETTINGS' })}
          className={linkClass}
          title="Appearance, credentials, connection and quit"
        >
          Settings
        </button>
      </div>
    </div>
  )
}

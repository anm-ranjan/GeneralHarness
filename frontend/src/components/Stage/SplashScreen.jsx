import { CHATS_PROJECT_ID } from '../../constants'
import { useApp } from '../../context/AppContext'
import useStartChat from '../../hooks/useStartChat'
import RecentSessions from './RecentSessions'

export default function SplashScreen() {
  const { state, dispatch } = useApp()
  const startChat = useStartChat()
  const approvalLabel = state.approvalMode || '—'
  const verboseLabel = state.approvalMode ? (state.verbose ? 'on' : 'off') : '—'
  const logo = state.splashAscii
  const title = state.appName
  const projectCount = state.projects.filter(project => project.id !== CHATS_PROJECT_ID).length
  const sessionCount = Object.keys(state.sessionsById || {}).length

  return (
    <div className="flex h-full items-center justify-center px-6">
      <div className="w-full max-w-[760px] animate-scale-in">
        <div className="mb-5 flex items-center justify-center gap-2 text-[11px] uppercase tracking-[0.16em] text-faint">
          <span className="h-1.5 w-1.5 rounded-full bg-ok shadow-[0_0_12px_rgba(34,197,94,0.65)] animate-pulse-slow" />
          <span>Workspace ready</span>
        </div>

        <div className="splash-console rounded-lg border border-line bg-surface/70 px-6 py-7 text-center shadow-[0_18px_80px_rgba(0,0,0,0.24)]">
          {logo ? (
            <pre className="mx-auto mb-5 max-w-full overflow-x-auto whitespace-pre text-[7px] leading-[1.15] text-accent sm:text-[8px] md:text-[9px] font-mono">{logo}</pre>
          ) : (
            <div className="mx-auto mb-5 text-[40px] font-bold tracking-[0.08em] text-accent font-mono">{title}</div>
          )}
          <h3 className="mb-2 text-[22px] font-bold text-text-bright">{title}</h3>
          <p className="mx-auto mb-5 max-w-[460px] text-[13px] text-faint">
            {sessionCount > 0
              ? 'Pick up where you left off, or start a new workspace.'
              : 'Select a session from the sidebar or start a new workspace.'}
          </p>

          <RecentSessions />

          <div className="mb-6 grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div className="rounded-md border border-line bg-bg/35 px-3 py-2">
              <div className="text-[10px] uppercase tracking-[0.12em] text-faint">Approval</div>
              <div className="mt-1 truncate text-[12px] font-medium text-text-default">{approvalLabel}</div>
            </div>
            <div className="rounded-md border border-line bg-bg/35 px-3 py-2">
              <div className="text-[10px] uppercase tracking-[0.12em] text-faint">Verbose</div>
              <div className="mt-1 text-[12px] font-medium text-text-default">{verboseLabel}</div>
            </div>
            <div className="rounded-md border border-line bg-bg/35 px-3 py-2">
              <div className="text-[10px] uppercase tracking-[0.12em] text-faint">Projects</div>
              <div className="mt-1 text-[12px] font-medium text-text-default tabular-nums">{projectCount}</div>
            </div>
            <div className="rounded-md border border-line bg-bg/35 px-3 py-2">
              <div className="text-[10px] uppercase tracking-[0.12em] text-faint">Sessions</div>
              <div className="mt-1 text-[12px] font-medium text-text-default tabular-nums">{sessionCount}</div>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-center gap-3">
          <button
            onClick={() => startChat()}
            className="px-5 py-2 text-[13px] font-medium text-bg bg-accent border border-accent rounded-md hover:brightness-110 transition-[filter,transform] hover:-translate-y-0.5"
          >
            + Chat
          </button>
          <button
            onClick={() => dispatch({ type: 'OPEN_DIR_PICKER' })}
            className="px-5 py-2 text-[13px] font-medium text-accent border border-accent/30 rounded-md hover:bg-accent-soft transition-colors"
          >
            + Project
          </button>
          <button
            onClick={() => dispatch({ type: 'OPEN_SEARCH' })}
            className="px-5 py-2 text-[13px] font-medium text-muted border border-line rounded-md hover:text-accent hover:border-accent/30 hover:bg-accent-soft transition-colors"
          >
            Search
          </button>
          </div>
        </div>
      </div>
    </div>
  )
}

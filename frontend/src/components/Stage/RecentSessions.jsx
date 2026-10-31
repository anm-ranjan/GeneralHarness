import { useMemo } from 'react'
import { useApp, useAppSelector } from '../../context/AppContext'
import useSelectSession from '../../hooks/useSelectSession'
import { recentSessions } from '../../recentSessions'
import { relativeTime } from '../../utils'
import { runStateBadge } from '../../runStates'

// One recent-session row. Mirrors SessionItem's run-state dot so a background run
// reads the same way here as it does in the sidebar.
function RecentSessionRow({ session, onSelect }) {
  const ownRunState = useAppSelector(state => state.runStates[session.id])
  const runState = ownRunState || (session.status === 'running' ? 'running' : null)

  return (
    <button
      onClick={() => onSelect(session.id)}
      title={session.title}
      className="group flex w-full items-baseline gap-2 rounded px-2 py-1.5 text-left transition-colors hover:bg-accent-soft focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/70"
    >
      {runState && (
        <span
          className={`shrink-0 self-center h-1.5 w-1.5 rounded-full ${runStateBadge(runState).dotClass}`}
          title={runStateBadge(runState).label}
        />
      )}
      <span className="max-w-[55%] truncate text-[14px] text-text-default group-hover:text-accent">
        {session.title}
      </span>
      {/* Always rendered, empty label included, so it also spaces the timestamp right. */}
      <span className="min-w-0 flex-1 truncate text-[11px] text-faint">
        {session.contextLabel}
      </span>
      <span className="shrink-0 text-[11px] tabular-nums text-faint">
        {relativeTime(session.updatedAt)}
      </span>
    </button>
  )
}

export default function RecentSessions() {
  const { state } = useApp()
  const selectSession = useSelectSession()
  const recent = useMemo(
    () => recentSessions(state.sessionsById, state.projects),
    [state.sessionsById, state.projects],
  )

  function handleSelect(sessionId) {
    selectSession(sessionId).catch(err => console.error('Failed to select session:', err))
  }

  if (!recent.length) return null

  return (
    <div className="w-full text-left">
      <h3 className="mb-1 font-display text-[15px] font-bold text-text-bright">Pick up where you left off</h3>
      <div className="-mx-2">
        {recent.map(session => (
          <RecentSessionRow key={session.id} session={session} onSelect={handleSelect} />
        ))}
      </div>
    </div>
  )
}

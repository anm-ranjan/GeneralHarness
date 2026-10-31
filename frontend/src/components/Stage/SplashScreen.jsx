import { useMemo } from 'react'
import { CHATS_PROJECT_ID } from '../../constants'
import { useApp, useSwitchHost } from '../../context/AppContext'
import useHostRoster from '../../hooks/useHostRoster'
import useSelectSession from '../../hooks/useSelectSession'
import useStartChat from '../../hooks/useStartChat'
import { runStateBadge } from '../../runStates'
import HostTag from '../Fleet/HostTag'
import RecentSessions from './RecentSessions'

const quietButton = 'underline decoration-line underline-offset-4 hover:text-accent transition-colors'

// Every machine in the fleet with its one tag and what you can do with it.
function Machines() {
  const { rows, error, act } = useHostRoster()
  const switchHost = useSwitchHost()
  if (rows.length < 2) return null

  return (
    <section>
      <h3 className="mb-2 font-display text-[14px] font-bold text-text-bright">Machines</h3>
      <ul className="space-y-2">
        {rows.map(row => (
          <li key={row.id}>
            <div className="flex items-baseline gap-2">
              <span className="text-text-default">{row.label}</span>
              <span className="text-[12px]"><HostTag tag={row.tag} busy={row.busy} /></span>
            </div>
            <div className="mt-0.5 flex gap-3 text-[12px] text-faint">
              {row.active
                ? <span>Working here</span>
                : row.tag === 'serving' && <button type="button" className={quietButton} onClick={() => switchHost(row.id)}>Switch</button>}
              {row.action && <button type="button" className={quietButton} onClick={() => act(row)}>{row.action.label}</button>}
            </div>
          </li>
        ))}
      </ul>
      {error && <p className="mt-2 text-[12px] text-danger" role="alert">{error}</p>}
    </section>
  )
}

// Threads with a run in progress or waiting on you, so nothing stalls unseen.
function RunningNow() {
  const { state } = useApp()
  const selectSession = useSelectSession()
  const running = useMemo(
    () => Object.entries(state.runStates || {})
      .map(([id, runState]) => ({ id, runState, session: state.sessionsById[id] }))
      .filter(entry => entry.session),
    [state.runStates, state.sessionsById],
  )
  if (!running.length) return null

  return (
    <section>
      <h3 className="mb-2 font-display text-[14px] font-bold text-text-bright">Running now</h3>
      <ul className="space-y-1.5">
        {running.map(({ id, runState, session }) => (
          <li key={id}>
            <button
              type="button"
              onClick={() => selectSession(id).catch(err => console.error('Failed to select session:', err))}
              className="group flex w-full items-baseline gap-2 text-left"
              title={runStateBadge(runState).label}
            >
              <span className={`h-1.5 w-1.5 shrink-0 self-center rounded-full ${runStateBadge(runState).dotClass}`} />
              <span className="min-w-0 truncate text-text-default group-hover:text-accent">{session.title || id.slice(0, 8)}</span>
            </button>
            <p className="pl-3.5 text-[12px] text-faint">{runStateBadge(runState).label}</p>
          </li>
        ))}
      </ul>
    </section>
  )
}

export default function SplashScreen() {
  const { state, dispatch } = useApp()
  const startChat = useStartChat()
  const projectCount = state.projects.filter(project => project.id !== CHATS_PROJECT_ID).length
  const sessionCount = Object.keys(state.sessionsById || {}).length
  const host = state.fleetHosts.find(entry => entry.id === state.activeHostId)

  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`
  const inventory = sessionCount
    ? `${plural(projectCount, 'project')} and ${plural(sessionCount, 'thread')}${host ? ` on ${host.label}` : ''}.`
    : 'Nothing here yet. Start a chat, or open a project folder.'

  return (
    <div className="@container h-full overflow-y-auto px-9 py-12">
      <div className="mx-auto grid max-w-[1080px] gap-x-10 gap-y-10 @3xl:grid-cols-[minmax(0,1fr)_17rem]">
        <div className="min-w-0 max-w-[62ch]">
          <h2 className="font-display text-[34px] font-bold leading-[1.1] tracking-[-0.015em] text-text-bright">
            What are we working on?
          </h2>
          <p className="mt-2 mb-7 text-[14px] text-muted">{inventory}</p>

          <div className="mb-10 flex flex-wrap items-center gap-x-5 gap-y-3 text-[13px]">
            <button
              type="button"
              onClick={() => startChat()}
              className="rounded-md border border-accent bg-accent px-4 py-1.5 font-medium text-bg transition-[filter] hover:brightness-110"
            >
              New chat
            </button>
            <button
              type="button"
              onClick={() => dispatch({ type: 'OPEN_DIR_PICKER' })}
              className="text-muted transition-colors hover:text-accent"
            >
              Open a project
            </button>
            <button
              type="button"
              onClick={() => dispatch({ type: 'OPEN_SEARCH' })}
              className="text-muted transition-colors hover:text-accent"
            >
              Search transcripts
            </button>
          </div>

          <RecentSessions />
        </div>

        <aside className="min-w-0 space-y-7 text-[13px] text-muted @3xl:border-l @3xl:border-line @3xl:pl-5">
          <Machines />
          <RunningNow />
          {state.approvalMode && (
            <section>
              <h3 className="mb-2 font-display text-[14px] font-bold text-text-bright">Approvals</h3>
              <p>
                Mode is <span className="font-mono text-[12px] text-text-default">{state.approvalMode}</span>.
                Verbose output is {state.verbose ? 'on' : 'off'}.
              </p>
            </section>
          )}
        </aside>
      </div>
    </div>
  )
}

import { useApp } from '../../context/AppContext'
import useSelectSession from '../../hooks/useSelectSession'
import { runDisplayStatus } from '../../agentUi'
import AgentRunStatus from './AgentRunStatus'
import StatusPill from './StatusPill'

// Workspace "Run" tab: facts about the run, then each script invocation's
// items, latest first. The transcript keeps the full tables.
export default function AgentRunPanel() {
  const { state, dispatch } = useApp()
  const selectSession = useSelectSession()
  const meta = state.currentMeta
  if (!meta || meta.kind !== 'agent_run') return null
  const parent = meta.parent_session_id ? state.sessionsById[meta.parent_session_id] : null
  const invocations = state.stageItems.filter(i => i.type === 'agent_run_status').slice().reverse()
  const status = runDisplayStatus(state.runStates[meta.id] || (state.isRunning ? 'running' : ''), meta)
  const row = 'grid grid-cols-[96px_minmax(0,1fr)] gap-2 py-1'
  return (
    <div className="p-4 text-[13px] text-text-default">
      <div className="mb-3 flex items-center justify-between gap-2">
        <button type="button" className="truncate font-medium text-text-bright hover:text-accent" onClick={() => dispatch({ type: 'OPEN_AGENT_HOME', payload: meta.agent })}>{meta.agent} · Run {meta.agent_run_number}</button>
        <StatusPill status={status} />
      </div>
      <dl className="mb-4 border-y border-line py-2 text-[12.5px]">
        <div className={row}><dt className="text-faint">Version</dt><dd>{meta.agent_version}</dd></div>
        <div className={row}><dt className="text-faint">Started</dt><dd>{new Date(meta.created_at).toLocaleString()}</dd></div>
        <div className={row}>
          <dt className="text-faint">Started from</dt>
          <dd className="truncate">
            {!meta.parent_session_id ? 'Agent home'
              : parent ? <button type="button" className="truncate text-accent hover:underline" onClick={() => selectSession(parent.id)}>{parent.title}</button>
              : 'a deleted session'}
          </dd>
        </div>
        <div className={row}><dt className="text-faint">Working folder</dt><dd className="break-all font-mono text-[12px]">{state.currentWorkspaceRoot || '—'}</dd></div>
      </dl>
      {invocations.length === 0 && <p className="text-faint">The script has not run yet in this session.</p>}
      {invocations.map((i, n) => (
        <div key={i.toolCallId}>
          {invocations.length > 1 && <p className="mt-3 text-[11.5px] uppercase tracking-wide text-faint">{n === 0 ? 'Latest invocation' : `Invocation ${invocations.length - n}`}</p>}
          <AgentRunStatus item={i} compact live={n === 0 && state.isRunning} />
        </div>
      ))}
    </div>
  )
}

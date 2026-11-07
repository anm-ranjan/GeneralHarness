import { useApp } from '../../context/AppContext'
import { relativeTime } from '../../utils'
import { itemCounts, runDisplayStatus } from '../../agentUi'
import StatusPill from './StatusPill'

// Reads each run's stored last result (SessionMeta.agent_last_result), so the
// list costs no per-run event requests.
export default function RecentAgentRuns({ runs, sessions, onSelect, limit = 12 }) {
  const { state } = useApp()
  if (!runs.length) return <p className="text-[13px] text-faint">No runs yet. Your runs will appear here.</p>
  return (
    <ul className="divide-y divide-line">
      {runs.slice(0, limit).map(run => {
        const result = run.agent_last_result || {}
        const parent = run.parent_session_id ? (sessions[run.parent_session_id]?.title || 'a deleted session') : ''
        return (
          <li key={run.id}>
            <button type="button" onClick={() => onSelect(run.id)} className="group block w-full py-2.5 text-left">
              <div className="flex items-center justify-between gap-2">
                <span className="min-w-0 truncate text-[13px] text-text-default group-hover:text-accent">{run.title}</span>
                <StatusPill status={runDisplayStatus(state.runStates[run.id], run)} />
              </div>
              <p className="mt-0.5 truncate text-[11.5px] text-faint">
                {[relativeTime(run.created_at), itemCounts(result), parent && `from ${parent}`].filter(Boolean).join(' · ')}
              </p>
            </button>
          </li>
        )
      })}
      {runs.length > limit && <li className="pt-2 text-[12px] text-faint">{runs.length - limit} older runs in the sidebar</li>}
    </ul>
  )
}

import { useEffect, useState } from 'react'
import { useApp } from '../../context/AppContext'
import useAgentProfiles from '../../hooks/useAgentProfiles'
import SessionItem from '../Sidebar/SessionItem'
import { runDisplayStatus, statusInfo } from '../../agentUi'

const COLLAPSE_KEY = 'myharness.agents.collapsed'
const RUN_LIMIT = 5

function loadCollapsed() {
  try { return JSON.parse(localStorage.getItem(COLLAPSE_KEY) || '{}') || {} } catch { return {} }
}

function saveCollapsed(value) {
  try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify(value)) } catch { /* per-browser convenience only */ }
}

function runSubtitle(run, liveState, sessionsById) {
  const status = statusInfo(runDisplayStatus(liveState, run)).label
  if (!run.parent_session_id) return status
  const parent = sessionsById[run.parent_session_id]?.title || 'a deleted session'
  return `${status} · from ${parent}`
}

function AgentNode({ profile, runs, collapsed, onToggle, filtering, rowProps }) {
  const { state, dispatch } = useApp()
  const [showAll, setShowAll] = useState(false)
  const blocked = profile.errors.length > 0 || profile.ready === false
  const isHome = state.agentHome === profile.name
  const containsCurrent = runs.some(r => r.id === state.currentSessionId)
  const open = filtering || !collapsed
  // Opening a run (for example from a parent's card) reveals it once.
  useEffect(() => { if (containsCurrent && collapsed) onToggle() }, [containsCurrent, state.currentSessionId])  // eslint-disable-line react-hooks/exhaustive-deps
  const visible = showAll || filtering ? runs : runs.slice(0, RUN_LIMIT)
  const reasons = (profile.disabled_reasons || profile.errors || []).join('\n')
  return (
    <div className="mb-3">
      <div
        className={`group flex items-center gap-1.5 rounded-md px-1.5 py-1 ${isHome ? 'bg-panel shadow-[0_0_0_1px_var(--color-line)]' : ''}`}
        onClick={e => { if (e.target === e.currentTarget) onToggle() }}
      >
        <button
          type="button"
          onClick={onToggle}
          className={`h-4 w-4 shrink-0 text-[10px] text-faint transition-transform ${open ? '' : '-rotate-90'}`}
          aria-label={open ? `Collapse ${profile.name}` : `Expand ${profile.name}`}
          aria-expanded={open}
        >▼</button>
        <button
          type="button"
          title={blocked ? `Not ready:\n${reasons}` : profile.description}
          onClick={() => dispatch({ type: 'OPEN_AGENT_HOME', payload: profile.name })}
          className={`min-w-0 truncate text-left font-display text-[14px] font-medium transition-colors hover:text-accent ${isHome ? 'text-text-bright' : blocked ? 'text-faint' : 'text-muted'}`}
          aria-current={isHome ? 'page' : undefined}
        >
          {profile.name}
        </button>
        {blocked && <span className="shrink-0 text-[11px] text-warn" title={reasons} aria-label="Not ready">⚠</span>}
        <span className="ml-1 text-[10px] tabular-nums text-faint">{runs.length || ''}</span>
        <button
          type="button"
          onClick={() => dispatch({ type: 'OPEN_AGENT_HOME', payload: profile.name })}
          className="ml-auto shrink-0 px-1 text-[15px] leading-none text-faint hover:text-accent"
          title={`New ${profile.name} run`}
          aria-label={`New ${profile.name} run`}
        >+</button>
      </div>
      {open && (
        <div className="mt-0.5">
          {visible.map(s => (
            <SessionItem
              key={s.id}
              session={s}
              subtitle={runSubtitle(s, state.runStates[s.id], state.sessionsById)}
              isActive={s.id === state.currentSessionId && !state.agentHome}
              onSelect={rowProps.onSelectSession}
              onRename={rowProps.onRenameSession}
              onContextMenu={rowProps.onSessionContextMenu}
              renameRequested={rowProps.renameSessionId === s.id}
              onRenameDone={rowProps.onRenameHandled}
            />
          ))}
          {!filtering && runs.length > RUN_LIMIT && (
            <button type="button" onClick={() => setShowAll(v => !v)} className="ml-2.5 mt-0.5 text-[12px] text-faint hover:text-accent">
              {showAll ? 'Show fewer' : `Show all ${runs.length} runs`}
            </button>
          )}
          {!runs.length && !filtering && <p className="ml-2.5 text-[12px] text-faint">No runs yet</p>}
        </div>
      )}
    </div>
  )
}

export default function AgentsView({ search, ...rowProps }) {
  const { state } = useApp()
  const profiles = useAgentProfiles()
  const [collapsed, setCollapsed] = useState(loadCollapsed)
  useEffect(() => { saveCollapsed(collapsed) }, [collapsed])
  const query = search.trim().toLowerCase()
  const filtering = !!query
  const nodes = profiles.map(p => {
    const runs = Object.values(state.sessionsById)
      .filter(s => s.kind === 'agent_run' && s.agent === p.name && !s.archived)
      .sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
    const matches = `${p.name} ${p.description}`.toLowerCase().includes(query)
    const visible = matches ? runs : runs.filter(s => `${s.title} ${(s.labels || []).join(' ')}`.toLowerCase().includes(query))
    return (!matches && !visible.length) ? null : { profile: p, runs: visible }
  }).filter(Boolean)
  if (!profiles.length) return <p className="mt-8 text-center text-[13px] italic text-faint">No agents installed. Add one under agents/ or use /create_agent.</p>
  if (!nodes.length) return <p className="mt-8 text-center text-[13px] italic text-faint">No matching agents or runs.</p>
  return (
    <div>
      {nodes.map(({ profile, runs }) => (
        <AgentNode
          key={profile.name}
          profile={profile}
          runs={runs}
          filtering={filtering}
          collapsed={collapsed[profile.name] ?? runs.length === 0}
          onToggle={() => setCollapsed(c => ({ ...c, [profile.name]: !(c[profile.name] ?? runs.length === 0) }))}
          rowProps={rowProps}
        />
      ))}
    </div>
  )
}

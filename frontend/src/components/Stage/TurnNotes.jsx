import { memo, useEffect, useMemo, useState } from 'react'
import { useApp } from '../../context/AppContext'
import useApi from '../../hooks/useApi'
import { baseName, formatElapsed, notesForTurn, nowLine, turnDurationMs } from '../../turnModel'
import { waitingPhrase } from '../../waitingPhrase'
import { agentIsLive, agentLabel, agentStatusLabel, canInterruptAgent, sortedAgents } from '../../agents'
import { api } from '../../api'
import ThinkingBlock from './ThinkingBlock'
import ToolCardCompact from './ToolCardCompact'

const PLAN_MARK = {
  completed: 'text-ok',
  in_progress: 'text-accent',
  pending: 'text-faint',
}

function clock(ms) {
  return new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

// Ticks once a second; kept in its own component so only the live line
// re-renders, not the whole transcript.
function LiveLine({ turn }) {
  const { state } = useApp()
  const { cancelRun } = useApi()
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  const current = nowLine(turn.tools)
  const started = turn.startTime || state.lastActivityTime
  // Time since the last sign of life: the run starting or the latest tool
  // finishing. Each tool return restarts the waiting phrases at "Thinking…".
  const lastToolEnd = turn.tools.reduce(
    (latest, tool) => (tool.startedAt ? Math.max(latest, tool.startedAt + (tool.durationMs || 0)) : latest),
    0,
  )
  const idleMs = now - Math.max(lastToolEnd, state.lastActivityTime || 0)
  const label = state.showCodexRunning && !current
    ? 'Codex is working…'
    : current
      ? null
      : waitingPhrase(idleMs)

  return (
    <div aria-live="polite">
      <p className="font-medium text-text-bright">
        {label ? <span className="text-shimmer">{label}</span> : <>{current.verb}{current.rest && <> <span className="font-mono text-[12px]">{current.rest}</span></>}…</>}
      </p>
      <p className="mt-1">
        Running for {formatElapsed(now - started)}
        {state.iterationN > 0 ? `, step ${state.iterationN}` : ''}.{' '}
        <button
          type="button"
          onClick={() => cancelRun(state.currentSessionId)}
          disabled={state.isCancelling}
          className="underline decoration-line underline-offset-4 hover:text-danger disabled:opacity-50"
        >
          {state.isCancelling ? 'Interrupting…' : 'Stop'}
        </button>
      </p>
    </div>
  )
}

function TurnNotes({ turn, live, isLast }) {
  const { state, dispatch } = useApp()
  const [showSteps, setShowSteps] = useState(false)
  const notes = useMemo(() => notesForTurn(turn), [turn.tools, turn.modelNotes])
  const plan = state.plan || []
  const planDone = plan.filter(item => item.status === 'completed').length
  const showPlan = isLast && plan.length > 0 && (live || planDone < plan.length)
  // Child agents belong to the latest turn; they stay listed after it ends so
  // their outcome can be read, until the next prompt starts a fresh set.
  const agents = useMemo(() => (isLast ? sortedAgents(state.agentsById) : []), [isLast, state.agentsById])

  async function stopAgent(agent) {
    const path = String(agent.agent_path || '').replace(/^\//, '').split('/').map(encodeURIComponent).join('/')
    try {
      await api('POST', `/api/sessions/${encodeURIComponent(state.currentSessionId)}/agents/${path}/interrupt`)
    } catch (err) {
      dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: err.detail || err.message } })
    }
  }
  const first = turn.tools.find(tool => tool.startedAt)
  const took = turnDurationMs(turn.tools)

  function diffPaths(files = []) {
    return files
      .map(name => state.touchedFiles.find(file => baseName(file.path) === name)?.path)
      .filter(Boolean)
  }

  if (!live && !notes.length && !turn.thinking.length && !showPlan && !turn.tools.length && !agents.length) return null

  return (
    <aside className="min-w-0 space-y-2 text-[13px] leading-snug text-muted" aria-label="What the agent did">
      {[...turn.anchors, ...turn.thinking].map((item, i) => (
        item.eventIndex != null && <span key={item._id || i} data-evt-index={item.eventIndex} />
      ))}

      {first && !live && (
        <time className="block text-[12px] text-faint">
          {clock(first.startedAt)}{took > 0 ? `, took ${formatElapsed(took)}` : ''}
        </time>
      )}

      {turn.thinking.map((item, i) => (
        <ThinkingBlock key={item._id || i} markdown={item.markdown} streaming={item.type === 'thinking_stream'} />
      ))}

      {notes.map((note, i) => {
        const paths = note.kind === 'edit' ? diffPaths(note.files) : []
        return (
          <p key={i} className={note.tone === 'bad' ? 'text-danger' : ''}>
            {note.text}
            {paths.map(path => (
              <button
                key={path}
                type="button"
                onClick={() => dispatch({ type: 'OPEN_DIFF_VIEWER', payload: path })}
                className="ml-1.5 underline decoration-line underline-offset-4 text-accent hover:text-text-bright"
              >
                {paths.length > 1 ? `Diff of ${baseName(path)}` : 'Open diff'}
              </button>
            ))}
          </p>
        )
      })}

      {showPlan && (
        <ol className="space-y-0.5" aria-label="Plan">
          {plan.map((item, i) => (
            <li key={i} className="flex gap-2">
              <span className={`mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full ${item.status === 'completed' ? 'bg-ok' : item.status === 'in_progress' ? 'bg-accent animate-pulse-slow' : 'border border-faint'} ${PLAN_MARK[item.status] || ''}`} />
              <span className={item.status === 'completed' ? 'text-faint line-through' : item.status === 'in_progress' ? 'text-text-bright' : ''}>{item.content}</span>
            </li>
          ))}
        </ol>
      )}

      {agents.length > 0 && (
        <div aria-label="Agents">
          <p className="text-[12px] text-faint">Agents</p>
          <ul className="space-y-0.5">
            {agents.map(agent => (
              <li key={agent.agent_id || agent.agent_path} title={agent.error || agent.task || ''}>
                <div className="flex items-baseline gap-2">
                  <span className={`min-w-0 flex-1 truncate ${agentIsLive(agent) ? 'text-text-bright' : ''}`}>
                    {agentLabel(agent)}{agent.role ? ` · ${agent.role}` : ''}
                  </span>
                  <span className={`shrink-0 text-[12px] ${agent.status === 'failed' ? 'text-danger' : 'text-faint'}`}>{agentStatusLabel(agent.status)}</span>
                  {canInterruptAgent(agent) && (
                    <button
                      type="button"
                      onClick={() => stopAgent(agent)}
                      className="shrink-0 text-[12px] text-faint underline decoration-line underline-offset-4 hover:text-danger"
                    >
                      stop
                    </button>
                  )}
                </div>
                {agent.status === 'failed' && agent.error && (
                  <p className="truncate text-[12px] text-danger">{agent.error}</p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {live && <LiveLine turn={turn} />}

      {turn.tools.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setShowSteps(v => !v)}
            aria-expanded={showSteps}
            className="text-[12px] text-faint underline decoration-line underline-offset-4 hover:text-accent"
          >
            {showSteps ? 'Hide steps' : `Show steps (${turn.tools.length})`}
          </button>
          {showSteps && (
            <div className="mt-1">
              {turn.tools.map((tool, i) => <ToolCardCompact key={tool.callId || i} {...tool} />)}
            </div>
          )}
        </div>
      )}
    </aside>
  )
}

export default memo(TurnNotes)

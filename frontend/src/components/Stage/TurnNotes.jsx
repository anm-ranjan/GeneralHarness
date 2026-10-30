import { memo, useEffect, useMemo, useState } from 'react'
import { useApp } from '../../context/AppContext'
import useApi from '../../hooks/useApi'
import { baseName, formatElapsed, notesForTurn, nowLine, turnDurationMs } from '../../turnModel'
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
  const label = state.showCodexRunning && !current
    ? 'Codex is working'
    : current
      ? null
      : 'Thinking'

  return (
    <div aria-live="polite">
      <p className="font-medium text-text-bright">
        {label ? <span className="text-shimmer">{label}…</span> : <>{current.verb}{current.rest && <> <span className="font-mono text-[12px]">{current.rest}</span></>}…</>}
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
  const first = turn.tools.find(tool => tool.startedAt)
  const took = turnDurationMs(turn.tools)

  function diffPaths(files = []) {
    return files
      .map(name => state.touchedFiles.find(file => baseName(file.path) === name)?.path)
      .filter(Boolean)
  }

  if (!live && !notes.length && !turn.thinking.length && !showPlan && !turn.tools.length) return null

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

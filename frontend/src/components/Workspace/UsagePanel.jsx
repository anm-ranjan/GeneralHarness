import { useState, useEffect, useCallback } from 'react'
import { api } from '../../api'
import { useApp } from '../../context/AppContext'

function fmtPct(value) {
  return typeof value === 'number' ? `${value.toFixed(1)}%` : '—'
}

const REASON_STYLES = {
  completed: 'bg-ok-soft text-ok',
  interrupted: 'bg-warn-soft text-warn',
  error: 'bg-danger-soft text-danger',
}

export default function UsagePanel() {
  const { state } = useApp()
  const sessionId = state.currentSessionId
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    if (!sessionId) return
    try {
      const res = await api('GET', `/api/sessions/${encodeURIComponent(sessionId)}/metrics`)
      setData(res)
      setError('')
    } catch (err) {
      setError(err.detail || err.message)
    }
  }, [sessionId])

  // Reload when the session changes or a run finishes (isRunning falls to false).
  useEffect(() => { load() }, [load, state.isRunning])

  if (!sessionId) {
    return <div className="p-4 text-[13px] text-muted italic">Select a session to see usage.</div>
  }
  if (error) {
    return <div className="p-4 text-[13px] text-danger">{error}</div>
  }
  if (!data || data.total_runs === 0) {
    return <div className="p-4 text-[13px] text-muted italic">No runs recorded yet in this session.</div>
  }

  const runs = data.runs || []
  const maxPercent = Math.max(1, ...runs.map(r => r.context_percent || 0))

  return (
    <div className="flex flex-col">
      <div className="grid grid-cols-2 gap-2 p-3 border-b border-line">
        <div className="rounded border border-line px-3 py-2">
          <div className="text-[11px] uppercase tracking-wide text-muted">Runs</div>
          <div className="text-[15px] font-semibold text-text-bright">{data.total_runs}</div>
        </div>
        <div className="rounded border border-line px-3 py-2">
          <div className="text-[11px] uppercase tracking-wide text-muted">Total time</div>
          <div className="text-[15px] font-semibold text-text-bright">{data.total_elapsed_s}s</div>
        </div>
        {data.latest && (
          <div className="col-span-2 rounded border border-line px-3 py-2">
            <div className="text-[11px] uppercase tracking-wide text-muted">
              Latest context {data.latest.estimated ? '(estimated)' : ''}
            </div>
            <div className="text-[15px] font-semibold text-text-bright">
              {fmtPct(data.latest.context_percent)}
              {data.latest.context_used != null && (
                <span className="text-[12px] font-normal text-muted ml-1">
                  ({data.latest.context_used}
                  {data.latest.context_limit ? ` / ${data.latest.context_limit}` : ''} tokens)
                </span>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="p-3">
        <div className="text-[11px] uppercase tracking-wide text-muted mb-2">Context per run</div>
        <div className="flex flex-col gap-1.5">
          {runs.map((run, i) => (
            <div key={i} className="flex items-center gap-2" title={run.ts || ''}>
              <span className="shrink-0 w-7 text-[11px] text-faint tabular-nums">#{i + 1}</span>
              <div className="flex-1 h-3 rounded bg-surface overflow-hidden">
                <div
                  className="h-full bg-accent/60"
                  style={{ width: `${Math.min(100, ((run.context_percent || 0) / maxPercent) * 100)}%` }}
                />
              </div>
              <span className="shrink-0 w-12 text-right text-[11px] text-muted tabular-nums">{fmtPct(run.context_percent)}</span>
              <span className={`shrink-0 px-1 py-0.5 text-[9px] font-semibold uppercase rounded ${REASON_STYLES[run.reason] || 'text-faint'}`}>
                {(run.reason || '').slice(0, 4)}
              </span>
            </div>
          ))}
        </div>
        <p className="mt-3 text-[10px] text-faint italic">
          Token counts are estimates.
        </p>
      </div>
    </div>
  )
}

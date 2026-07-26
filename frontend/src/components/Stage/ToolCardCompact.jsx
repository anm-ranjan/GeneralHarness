import { useState } from 'react'
import { toolIcon } from '../../constants'
import { truncate } from '../../utils'

function formatDuration(durationMs) {
  if (durationMs == null) return ''
  if (durationMs < 1000) return `${durationMs} ms`
  return `${(durationMs / 1000).toFixed(durationMs < 10_000 ? 1 : 0)} s`
}

function formatValue(value) {
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2)
}

export default function ToolCardCompact({ name, args = {}, detail, statusLine, status, resultPreview, durationMs }) {
  const [expanded, setExpanded] = useState(false)
  const failed = status === 'failed'
  const running = status === 'running'
  const statusLabel = running ? 'running' : status === 'stopped' ? 'stopped' : failed ? 'failed' : 'done'
  const statusClass = running
    ? 'text-accent animate-pulse-slow'
    : failed
      ? 'text-danger'
      : status === 'stopped'
        ? 'text-faint'
        : 'text-ok'

  return (
    <section className={`mb-1 overflow-hidden rounded-md border ${failed ? 'border-danger/35' : 'border-line'}`}>
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] transition-colors hover:bg-surface"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        <span className="text-[14px]" aria-hidden="true">{toolIcon(name)}</span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-text-bright">{statusLine || detail || name}</div>
          <div className="mt-0.5 flex min-w-0 items-center gap-2 text-[10px] text-faint">
            <code className="rounded bg-white/5 px-1 py-px">{name}</code>
            {detail && detail !== statusLine && <span className="truncate">{detail}</span>}
          </div>
        </div>
        {durationMs != null && <span className="text-[10px] tabular-nums text-faint">{formatDuration(durationMs)}</span>}
        <span className={`text-[10px] uppercase tracking-[0.12em] ${statusClass}`}>{statusLabel}</span>
        <span className={`text-[9px] text-faint transition-transform ${expanded ? 'rotate-90' : ''}`}>▶</span>
      </button>
      {expanded && (
        <div className="border-t border-line bg-black/20 px-3 py-2.5">
          <div className="mb-1.5 text-[10px] uppercase tracking-[0.14em] text-faint">Input</div>
          {Object.keys(args).length ? (
            <dl className="space-y-2">
              {Object.entries(args).map(([key, value]) => (
                <div key={key} className="grid gap-1 sm:grid-cols-[8rem_minmax(0,1fr)]">
                  <dt className="font-mono text-[11px] text-muted">{key}</dt>
                  <dd className="min-w-0">
                    <pre className="max-h-52 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-4 text-text-default">
                      {truncate(formatValue(value), 4000)}
                    </pre>
                  </dd>
                </div>
              ))}
            </dl>
          ) : (
            <div className="text-[11px] text-faint">No arguments</div>
          )}
          {resultPreview != null && (
            <div className="mt-3 border-t border-line pt-2.5">
              <div className={`mb-1.5 text-[10px] uppercase tracking-[0.14em] ${failed ? 'text-danger' : 'text-faint'}`}>
                {failed ? 'Error' : 'Output'}
              </div>
              <pre className={`max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-4 ${failed ? 'text-danger' : 'text-muted'}`}>
                {truncate(resultPreview, 4000)}
              </pre>
            </div>
          )}
        </div>
      )}
    </section>
  )
}

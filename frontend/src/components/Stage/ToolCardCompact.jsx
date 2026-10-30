import { useState } from 'react'
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

export default function ToolCardCompact({ name, args = {}, detail, statusLine, status, resultPreview, durationMs, detailsHidden = false }) {
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
    <section className="border-b border-line last:border-b-0">
      <button
        type="button"
        className="flex w-full items-baseline gap-2 py-1.5 text-left text-[12.5px] transition-colors hover:text-accent"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        <span className={`min-w-0 flex-1 truncate ${failed ? 'text-danger' : 'text-text-default'}`}>{statusLine || detail || name}</span>
        {durationMs != null && <span className="text-[11px] tabular-nums text-faint">{formatDuration(durationMs)}</span>}
        {(running || failed || status === 'stopped') && <span className={`text-[11px] ${statusClass}`}>{statusLabel}</span>}
      </button>
      {expanded && (
        <div className="pb-2.5">
          {detailsHidden && (
            <p className="text-[12px] text-faint">Input and output are hidden. Type /verbose to keep them for new tool calls.</p>
          )}
          {!detailsHidden && (Object.keys(args).length ? (
            <dl className="space-y-2">
              {Object.entries(args).map(([key, value]) => (
                <div key={key} className="grid gap-1 sm:grid-cols-[6rem_minmax(0,1fr)]">
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
          ))}
          {resultPreview != null && resultPreview !== '' && (
            <pre className={`mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-4 ${failed ? 'text-danger' : 'text-muted'}`}>
              {truncate(resultPreview, 4000)}
            </pre>
          )}
        </div>
      )}
    </section>
  )
}

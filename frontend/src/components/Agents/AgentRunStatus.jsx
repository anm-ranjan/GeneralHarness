import { useState } from 'react'
import MessageBubble from '../Stage/MessageBubble'
import StatusPill from './StatusPill'
import { itemCounts } from '../../agentUi'

const GLYPHS = {
  ok: ['✓', 'text-ok'],
  failed: ['✗', 'text-danger'],
  skipped: ['–', 'text-faint'],
  running: ['●', 'text-info animate-pulse'],
  pending: ['○', 'text-faint'],
  info: ['i', 'text-info'],
}

export function ProgressBar({ done = 0, total }) {
  const pct = total > 0 ? Math.min(100, Math.max(0, (done / total) * 100)) : null
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface" role="progressbar" aria-valuemin={0} aria-valuemax={total || undefined} aria-valuenow={done}>
      <div className={`h-full rounded-full bg-accent transition-[width] duration-300 ${pct === null ? 'w-1/3 animate-pulse' : ''}`} style={pct === null ? undefined : { width: `${pct}%` }} />
    </div>
  )
}

function ItemRow({ item, settled }) {
  const [glyph, tone] = GLYPHS[item.status] || ['•', 'text-faint']
  // After the result, a leftover running/pending row never finished.
  const stale = settled && ['running', 'pending'].includes(item.status)
  return (
    <li className={`flex gap-2.5 py-1 ${stale ? 'opacity-55' : ''}`}>
      <span aria-label={item.status} className={`w-3.5 shrink-0 text-center font-medium ${stale ? 'text-faint' : tone}`}>{glyph}</span>
      <div className="min-w-0 break-words">
        <span className="text-text-default">{item.label || item.key}</span>
        {item.detail && <p className={`whitespace-pre-wrap text-[12px] ${item.status === 'failed' ? 'text-danger' : 'text-faint'}`}>{item.detail}</p>}
      </div>
    </li>
  )
}

function ResultTable({ table }) {
  return (
    <div className="max-h-[420px] overflow-auto rounded-lg border border-line">
      <table className="w-full text-left text-[12.5px]">
        <thead className="sticky top-0 bg-panel text-faint">
          <tr>{table.columns.map((c, n) => <th key={n} className="whitespace-nowrap px-3 py-2 font-medium">{String(c)}</th>)}</tr>
        </thead>
        <tbody>
          {table.rows.map((r, n) => (
            <tr key={n} className="border-t border-line">
              {r.map((v, j) => <td key={j} className="px-3 py-1.5 align-top">{typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v ?? '')}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// One script invocation: progress, items, then the final result.
// compact: no table or Markdown, and long item lists are folded.
export default function AgentRunStatus({ item, compact = false, live = false }) {
  const { progress, result, items = {} } = item
  const rows = Object.values(items)
  const [showAll, setShowAll] = useState(false)
  const limit = compact ? 6 : 50
  const visible = showAll ? rows : rows.slice(0, limit)
  const status = result ? result.status : (live ? 'running' : null)
  return (
    <section className={`my-3 rounded-xl border border-line text-[13px] text-text-default ${compact ? 'p-3' : 'p-4'} space-y-3`}>
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-medium text-text-bright">{result?.summary || progress?.label || 'Running script…'}</p>
          {(result ? itemCounts(result) : progress) && (
            <p className="text-[12px] text-faint">{result ? itemCounts(result) : `${progress.done} of ${progress.total ?? '?'}`}</p>
          )}
        </div>
        {status && <StatusPill status={status} />}
      </div>
      {!result && progress && <ProgressBar done={progress.done} total={progress.total} />}
      {rows.length > 0 && (
        <ul className="divide-y divide-line/60">
          {visible.map(i => <ItemRow key={i.key} item={i} settled={!!result} />)}
        </ul>
      )}
      {rows.length > limit && (
        <button type="button" className="text-[12px] text-accent hover:underline" onClick={() => setShowAll(v => !v)}>
          {showAll ? 'Show fewer' : `Show all ${rows.length} items`}
        </button>
      )}
      {!compact && result?.table && <ResultTable table={result.table} />}
      {!compact && result?.markdown && <MessageBubble role="assistant" markdown={result.markdown} />}
    </section>
  )
}

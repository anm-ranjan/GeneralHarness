import { memo, useState } from 'react'
import { compactionView } from '../../compaction'

const fmt = n => (Number.isFinite(Number(n)) ? Number(n).toLocaleString() : String(n ?? '?'))

function Section({ title, children }) {
  return (
    <div className="mt-2">
      <p className="text-[12px] font-medium text-muted">{title}</p>
      {children}
    </div>
  )
}

// A finished compaction. Collapsed it reads like the old status line; opened
// it shows the memory block the model sees from now on instead of the older
// history, so a compaction can be checked without reading context.json.
function CompactionNotice({ before, after, memory }) {
  const [open, setOpen] = useState(false)
  const headline = `Context compacted: ${fmt(before)} → ${fmt(after)} tokens`
  if (!memory) {
    return <div className="text-[13px] text-faint italic py-1 px-2">{headline}</div>
  }
  const view = compactionView(memory)

  return (
    <div className="max-w-full py-1 px-2 text-[13px] text-faint">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="group flex items-center gap-1.5 italic hover:text-muted transition-colors"
        aria-expanded={open}
      >
        <span className={`inline-block not-italic transition-transform duration-200 ${open ? 'rotate-90' : ''}`}>&#9654;</span>
        <span>{headline}</span>
        <span className="not-italic text-[12px] group-hover:text-muted">· what the model kept</span>
      </button>
      <div className={`collapse-grid ${open ? 'open' : ''}`}>
        <div>
          {open && (
            <div className="border-l border-line pl-3 mt-1 text-[12.5px] leading-relaxed">
              {view.taskState ? (
                <>
                  <Section title={`Your requests and answers (${view.requests.length}), kept word for word`}>
                    <ol className="list-decimal pl-5 space-y-1">
                      {view.requests.map((text, i) => (
                        <li key={i} className="whitespace-pre-wrap break-words">{text}</li>
                      ))}
                    </ol>
                  </Section>

                  <Section title="Model summary">
                    {view.summary == null ? (
                      <p>None: the summary was missing or rejected, so the summary-free version was used.</p>
                    ) : view.summary.sections ? (
                      view.summary.sections.map(({ key, items }) => (
                        <div key={key} className="mt-1">
                          <p className="text-muted">{key.replace(/_/g, ' ')}</p>
                          {items.length ? (
                            <ul className="list-disc pl-5">
                              {items.map((item, i) => <li key={i} className="break-words">{item}</li>)}
                            </ul>
                          ) : <p className="pl-5">(none)</p>}
                        </div>
                      ))
                    ) : (
                      <p className="whitespace-pre-wrap break-words">{view.summary.text}</p>
                    )}
                  </Section>

                  <Section title={`Facts kept (${view.facts.length})${view.omitted ? `, ${view.omitted} older facts dropped` : ''}`}>
                    {view.omitted > 0 && (
                      <p className="mb-1">Dropped facts are still retrievable by the agent as archived evidence.</p>
                    )}
                    <ul className="space-y-0.5">
                      {view.facts.map((fact, i) => (
                        <li key={i} className={`break-words ${fact.bad ? 'text-danger' : ''}`}>
                          <span className="text-muted">{fact.label}</span>
                          {fact.text ? <>: <span className={fact.mono ? 'font-mono text-[12px]' : ''}>{fact.text}</span></> : null}
                        </li>
                      ))}
                    </ul>
                  </Section>

                  {view.priorMemory.length > 0 && (
                    <Section title="Older free-form memory">
                      {view.priorMemory.map((text, i) => (
                        <p key={i} className="whitespace-pre-wrap break-words">{text}</p>
                      ))}
                    </Section>
                  )}
                </>
              ) : (
                <p className="mt-1">This compaction used a different memory format; see the raw block below.</p>
              )}

              <details className="mt-2">
                <summary className="cursor-pointer text-[12px] hover:text-muted">Raw memory block (JSON)</summary>
                <pre className="mt-1 max-h-96 overflow-auto whitespace-pre-wrap break-words font-mono text-[11.5px]">
                  {JSON.stringify(memory, null, 2)}
                </pre>
              </details>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// Transcript items are immutable once appended.
export default memo(CompactionNotice)

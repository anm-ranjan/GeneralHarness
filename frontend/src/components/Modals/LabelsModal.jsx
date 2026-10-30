import { useEffect, useRef, useState } from 'react'

// Edits a thread's labels. The first label is the primary one the thread is
// filed under; removing them all returns it to the unlabeled bucket. A fixed
// `taskLabel` is shown but not editable (chats keep their bucket).
export default function LabelsModal({ session, taskLabel = '', suggestions = [], onClose, onSave }) {
  const [labels, setLabels] = useState(() => [...(session.labels || [])])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef(null)

  useEffect(() => { inputRef.current?.focus() }, [])

  function add(value) {
    const label = value.replace(/\s+/g, ' ').trim().slice(0, 48)
    if (!label) return
    const taken = [...labels, taskLabel].filter(Boolean).some(l => l.toLowerCase() === label.toLowerCase())
    if (!taken) setLabels(current => [...current, label])
    setDraft('')
  }

  async function submit(e) {
    e.preventDefault()
    if (busy) return
    const pending = draft.trim() ? [...labels, draft.trim()] : labels
    setBusy(true)
    setError('')
    try {
      await onSave(pending)
      onClose()
    } catch (err) {
      setError(err.detail || err.message || 'Saving labels failed')
      setBusy(false)
    }
  }

  const available = suggestions.filter(s => s !== taskLabel && !labels.some(l => l.toLowerCase() === s.toLowerCase())).slice(0, 12)

  return (
    <div
      className="fixed inset-0 glass-overlay z-50 flex items-center justify-center p-5"
      onClick={e => { if (e.target === e.currentTarget && !busy) onClose() }}
      onKeyDown={e => { if (e.key === 'Escape' && !busy) onClose() }}
    >
      <form onSubmit={submit} className="glass-surface border border-line rounded-lg w-full max-w-[400px] overflow-hidden">
        <div className="px-5 py-4 border-b border-line">
          <h3 className="text-[15px] font-semibold text-text-bright">Labels</h3>
          <p className="mt-1.5 text-[13px] text-muted truncate" title={session.title || session.id}>{session.title || session.id}</p>
        </div>
        <div className="px-5 py-4 space-y-3">
          <div className="flex flex-wrap gap-1.5 min-h-[26px]">
            {taskLabel && (
              <span className="rounded px-2 py-0.5 text-[12px] border border-line text-faint" title="Fixed label">{taskLabel}</span>
            )}
            {labels.map(label => (
              <span key={label} className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-[12px] border border-accent/30 text-accent">
                {label}
                <button type="button" className="text-faint hover:text-danger" aria-label={`Remove ${label}`}
                  onClick={() => setLabels(current => current.filter(l => l !== label))}>×</button>
              </span>
            ))}
            {!taskLabel && labels.length === 0 && <span className="text-[12px] text-faint italic">No labels yet.</span>}
          </div>
          <input
            ref={inputRef}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => {
              if (e.nativeEvent.isComposing) return
              if (e.key === 'Enter' && draft.trim()) { e.preventDefault(); add(draft) }
              if (e.key === ',') { e.preventDefault(); add(draft) }
              if (e.key === 'Backspace' && !draft && labels.length) setLabels(current => current.slice(0, -1))
            }}
            placeholder="Add a label and press Enter"
            className="w-full bg-surface border border-line rounded-md px-3 py-2 text-[13px] text-text-bright outline-none focus:border-accent/60"
          />
          {available.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {available.map(s => (
                <button key={s} type="button" onClick={() => add(s)}
                  className="rounded px-1.5 py-0.5 text-[11px] border border-line text-faint hover:text-accent hover:border-accent/30">+ {s}</button>
              ))}
            </div>
          )}
          {error && <p className="text-[12px] text-danger">{error}</p>}
        </div>
        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-line">
          <button type="button" onClick={onClose} disabled={busy}
            className="px-3 py-1.5 text-[13px] font-medium text-muted border border-line rounded-md hover:text-text-bright hover:border-line-hover transition-colors disabled:opacity-50">
            Cancel
          </button>
          <button type="submit" disabled={busy}
            className="px-3 py-1.5 text-[13px] font-semibold rounded-md bg-accent text-bg hover:brightness-110 transition-colors disabled:opacity-50">
            {busy ? 'Saving…' : 'Save'}
          </button>
        </div>
      </form>
    </div>
  )
}

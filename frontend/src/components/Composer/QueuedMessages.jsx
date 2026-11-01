import { useEffect, useRef, useState } from 'react'
import { useApp } from '../../context/AppContext'
import { api } from '../../api'

function previewText(item) {
  const text = (item.text || '').trim()
  if (text) return text
  return item.attachmentCount === 1 ? 'Attachment' : `${item.attachmentCount} attachments`
}

export default function QueuedMessages() {
  const { state, dispatch } = useApp()
  const [removingId, setRemovingId] = useState(null)
  const [dragId, setDragId] = useState(null)
  const [dropIndex, setDropIndex] = useState(null)
  const reorderingRef = useRef(false)
  const [editing, setEditing] = useState(null)
  const [draft, setDraft] = useState('')
  const [editError, setEditError] = useState('')
  const [saving, setSaving] = useState(false)
  useEffect(() => { setEditing(null); setEditError('') }, [state.currentSessionId])
  const queued = state.queuedMessages || []
  const steering = (state.pendingSteering || []).map(item => ({ ...item, steering: true }))
  const items = [...queued, ...steering]
  if (!state.currentSessionId || (items.length === 0 && !editing)) return null

  const canDrag = queued.length > 1 && !steering.length && queued.every((item) => item.id) && !removingId && !editing

  async function saveEdit() {
    if (!editing || saving) return
    setSaving(true); setEditError('')
    try {
      await api('PATCH', `/api/sessions/${encodeURIComponent(state.currentSessionId)}/${editing.steering ? 'steering' : 'queue'}/${encodeURIComponent(editing.id)}`, { text: draft, expected_text: editing.text })
      setEditing(null)
    } catch (err) { setEditError(err.detail || err.message || 'Could not save the message') }
    finally { setSaving(false) }
  }

  async function removeItem(item) {
    if (!item.id || removingId) return
    setRemovingId(item.id)
    try {
      await api('DELETE', `/api/sessions/${encodeURIComponent(state.currentSessionId)}/queue/${encodeURIComponent(item.id)}`)
      // The queue_updated broadcast refreshes state for every client.
    } catch (err) {
      dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: err.detail || err.message || 'Failed to remove queued message.' } })
    } finally {
      setRemovingId(null)
    }
  }

  async function steerItem(item) {
    if (removingId) return
    setRemovingId(item.id)
    try {
      await api('POST', `/api/sessions/${encodeURIComponent(state.currentSessionId)}/queue/${encodeURIComponent(item.id)}/steer`)
    } catch (err) {
      dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: err.detail || err.message || 'Failed to steer queued message.' } })
    } finally { setRemovingId(null) }
  }

  function insertionIndex(event, index) {
    const rect = event.currentTarget.getBoundingClientRect()
    const before = event.clientX < rect.left + rect.width / 2
    return before ? index : index + 1
  }

  function handleDragOver(event, index) {
    if (!dragId) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
    setDropIndex(insertionIndex(event, index))
  }

  async function handleDrop(event) {
    event.preventDefault()
    const fromIndex = items.findIndex((item) => item.id === dragId)
    const target = dropIndex
    setDragId(null)
    setDropIndex(null)
    if (fromIndex < 0 || target == null || reorderingRef.current) return
    // Dropping onto the dragged chip's own slot is a no-op.
    if (target === fromIndex || target === fromIndex + 1) return

    const reordered = [...items]
    const [moved] = reordered.splice(fromIndex, 1)
    reordered.splice(target > fromIndex ? target - 1 : target, 0, moved)

    reorderingRef.current = true
    dispatch({ type: 'SET_QUEUE', payload: reordered })
    try {
      await api('POST', `/api/sessions/${encodeURIComponent(state.currentSessionId)}/queue/reorder`, {
        order: reordered.map((item) => item.id),
      })
      // queue_updated broadcast confirms the order for every client.
    } catch (err) {
      dispatch({ type: 'SET_QUEUE', payload: items })
      dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: err.detail || err.message || 'Failed to reorder the queue.' } })
    } finally {
      reorderingRef.current = false
    }
  }

  return (
    <div className="border-t border-line px-7 py-2 bg-bg/95">
      {editing && <form onSubmit={e => { e.preventDefault(); saveEdit() }} className="mb-2 space-y-2">
        <label className="block text-[12px] text-muted">Edit {editing.steering ? 'pending steering' : 'queued message'}
          <textarea autoFocus value={draft} onChange={e => setDraft(e.target.value)} disabled={saving} rows={4} className="mt-1 w-full rounded border border-line bg-surface text-text-default p-2 focus:border-accent outline-none" />
        </label>
        {editError && <p role="alert" className="text-[12px] text-danger">{editError}</p>}
        <div className="flex gap-2"><button type="submit" disabled={saving || (!draft.trim() && !editing.attachmentCount)} className="rounded px-3 py-1 bg-accent text-bg disabled:opacity-50">{saving ? 'Saving…' : 'Save'}</button>
          <button type="button" disabled={saving} onClick={() => setEditing(null)} className="rounded px-3 py-1 text-muted border border-line">Cancel</button></div>
      </form>}
      <div className="max-w-[min(clamp(1100px,72cqw,1460px),calc(100cqw-2rem))] mx-auto flex items-center gap-3 text-[12px]">
        <span className="shrink-0 text-accent font-medium">
          Queued {queued.length}{steering.length > 0 ? ` · Steering ${steering.length}` : ''}
        </span>
        <div
          className="min-w-0 flex-1 flex gap-2 overflow-x-auto pb-0.5"
          onDragLeave={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) setDropIndex(null)
          }}
        >
          {items.map((item, i) => (
            <div
              key={item.id || i}
              draggable={canDrag && !!item.id}
              onDragStart={(event) => {
                event.dataTransfer.effectAllowed = 'move'
                setDragId(item.id)
              }}
              onDragEnd={() => { setDragId(null); setDropIndex(null) }}
              onDragOver={(event) => handleDragOver(event, i)}
              onDrop={handleDrop}
              className={`group relative shrink-0 max-w-[260px] rounded-md border bg-surface pl-2.5 pr-7 py-1.5 flex items-center gap-1 text-muted animate-scale-in transition-[opacity,box-shadow] ${removingId === item.id ? 'opacity-40' : ''} ${dragId === item.id ? 'opacity-40 border-accent/60' : 'border-line'} ${dropIndex === i && dragId !== item.id ? 'shadow-[inset_2px_0_0_0_var(--color-accent)]' : ''} ${dropIndex === i + 1 && dragId !== item.id ? 'shadow-[inset_-2px_0_0_0_var(--color-accent)]' : ''} ${canDrag ? 'cursor-grab active:cursor-grabbing' : ''}`}
              title={previewText(item)}
            >
              <span className="inline-flex items-center justify-center h-4 min-w-4 px-1 mr-1.5 rounded-full bg-accent-soft text-accent text-[10px] font-semibold align-middle">{i + 1}</span>
              <button type="button" className="min-w-0 truncate text-text-default hover:text-accent text-left" aria-label={`Edit ${item.steering ? 'pending steering' : 'queued message'} ${i + 1}`}
                onClick={() => { setEditing(item); setDraft(item.text); setEditError('') }}>{item.steering ? 'Steering: ' : ''}{previewText(item)}</button>
              {item.attachmentCount > 0 && (
                <span className="ml-2 text-faint">
                  +{item.attachmentCount} file{item.attachmentCount === 1 ? '' : 's'}
                </span>
              )}
              {item.id && !item.steering && <button type="button"
                onClick={() => steerItem(item)}
                disabled={!state.isRunning || removingId !== null || item.attachmentCount > 0 || !item.text?.trim() || item.text.trimStart().startsWith('/')}
                title={item.attachmentCount > 0 ? 'Messages with attachments must stay queued' : 'Steer the active run with this message'}
                aria-label={`Steer queued message ${i + 1}`}
                className="ml-2 shrink-0 rounded border border-line px-1.5 py-0.5 text-accent hover:bg-accent-soft disabled:opacity-30">↵</button>}
              {item.id && !item.steering && (
                <button
                  type="button"
                  onClick={() => removeItem(item)}
                  disabled={removingId !== null}
                  className="absolute top-1/2 -translate-y-1/2 right-1.5 h-4 w-4 rounded-full text-faint hover:text-danger hover:bg-danger-soft leading-none text-[11px] opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity disabled:opacity-30"
                  title="Remove from queue"
                  aria-label={`Remove queued message ${i + 1}`}
                >
                  ✕
                </button>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

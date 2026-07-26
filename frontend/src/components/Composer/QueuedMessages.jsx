import { useRef, useState } from 'react'
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
  const items = state.queuedMessages || []
  if (!state.currentSessionId || items.length === 0) return null

  const canDrag = items.length > 1 && items.every((item) => item.id) && !removingId

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
      <div className="max-w-[min(1100px,calc(100vw-2rem))] mx-auto flex items-center gap-3 text-[12px]">
        <span className="shrink-0 text-accent font-medium">
          Queued {items.length}
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
              className={`group relative shrink-0 max-w-[260px] rounded-md border bg-surface pl-2.5 pr-7 py-1.5 text-muted animate-scale-in transition-[opacity,box-shadow] ${removingId === item.id ? 'opacity-40' : ''} ${dragId === item.id ? 'opacity-40 border-accent/60' : 'border-line'} ${dropIndex === i && dragId !== item.id ? 'shadow-[inset_2px_0_0_0_var(--color-accent)]' : ''} ${dropIndex === i + 1 && dragId !== item.id ? 'shadow-[inset_-2px_0_0_0_var(--color-accent)]' : ''} ${canDrag ? 'cursor-grab active:cursor-grabbing' : ''}`}
              title={previewText(item)}
            >
              <span className="inline-flex items-center justify-center h-4 min-w-4 px-1 mr-1.5 rounded-full bg-accent-soft text-accent text-[10px] font-semibold align-middle">{i + 1}</span>
              <span className="text-text-default">{previewText(item)}</span>
              {item.attachmentCount > 0 && (
                <span className="ml-2 text-faint">
                  +{item.attachmentCount} file{item.attachmentCount === 1 ? '' : 's'}
                </span>
              )}
              {item.id && (
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

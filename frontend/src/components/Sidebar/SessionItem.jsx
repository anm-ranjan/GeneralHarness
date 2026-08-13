import { useState } from 'react'
import { useAppDispatch, useAppSelector } from '../../context/AppContext'
import InlineEdit from './InlineEdit'
import { relativeTime } from '../../utils'
import { runStateBadge } from '../../runStates'
import { downloadSessionBackup, downloadSessionExport } from '../../api'

export default function SessionItem({ session, isActive, onSelect, onRename, onMove, onDelete }) {
  const dispatch = useAppDispatch()
  const [editing, setEditing] = useState(false)
  const ownRunState = useAppSelector(state => state.runStates[session.id])
  const runState = ownRunState || (session.status === 'running' ? 'running' : null)

  function handleKeyDown(e) {
    if (editing) return
    if (e.key === 'F2') {
      e.preventDefault()
      setEditing(true)
      return
    }
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onSelect(session.id)
    }
  }

  return (
    <div
      className={`group relative grid grid-cols-[1fr_auto_auto] items-center gap-1 px-2 py-1.5 rounded-md cursor-pointer text-[13px] transition-[background-color,color,box-shadow,transform] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/70 ${
        isActive
          ? 'bg-accent-soft text-accent shadow-[inset_2px_0_0_var(--color-accent),0_0_0_1px_rgba(245,158,11,0.16)]'
          : 'text-muted hover:bg-surface hover:text-text-default hover:translate-x-0.5'
      }`}
      tabIndex={0}
      aria-current={isActive ? 'page' : undefined}
      onClick={() => !editing && onSelect(session.id)}
      onKeyDown={handleKeyDown}
    >
      {editing ? (
        <InlineEdit
          value={session.title || session.id}
          onSave={(name) => { onRename(session.id, name); setEditing(false) }}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <span
          className="truncate flex items-center gap-1.5 min-w-0"
          onDoubleClick={(e) => { e.stopPropagation(); setEditing(true) }}
          title={session.title || session.id}
        >
          {runState && (
            <span
              className={`shrink-0 w-1.5 h-1.5 rounded-full ${runStateBadge(runState).dotClass}`}
              title={runStateBadge(runState).label}
            />
          )}
          <span className="truncate">{session.title || session.id.slice(0, 8)}</span>
        </span>
      )}
      <span className={`text-[11px] ${isActive ? 'text-accent/80' : 'text-faint'}`}>{relativeTime(session.updated_at)}</span>
      <div className="flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          onClick={(e) => {
            e.stopPropagation()
            downloadSessionExport(session.id, 'md').catch((err) => {
              dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: err.detail || err.message } })
            })
          }}
          className="text-faint hover:text-accent text-[11px] px-0.5"
          title="Export as Markdown"
        >↓</button>
        <button
          onClick={(e) => {
            e.stopPropagation()
            downloadSessionBackup(session.id).catch((err) => {
              dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: err.detail || err.message } })
            })
          }}
          className="text-faint hover:text-accent text-[11px] px-0.5"
          title="Download portable backup (.zip)"
        >⇩</button>
        <button
          onClick={(e) => { e.stopPropagation(); setEditing(true) }}
          className="text-faint hover:text-accent text-[11px] px-0.5"
          title="Rename"
        >✎</button>
        {onMove && (
          <button
            onClick={(e) => { e.stopPropagation(); onMove(session) }}
            className="text-faint hover:text-accent text-[11px] px-0.5"
            title="Assign another label"
          >↷</button>
        )}
        <button
          onClick={(e) => {
            e.stopPropagation()
            dispatch({
              type: 'OPEN_CONFIRM',
              payload: {
                title: 'Delete thread?',
                message: 'This deletes the thread event stream and attachments.',
                detail: session.title || session.id,
                confirmLabel: 'Delete',
                tone: 'danger',
                onConfirm: () => onDelete(session.id),
              },
            })
          }}
          className="text-faint hover:text-danger text-[11px] px-0.5"
          title="Delete"
        >✕</button>
      </div>
    </div>
  )
}

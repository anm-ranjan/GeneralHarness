import { memo, useEffect, useState } from 'react'
import { endSessionDrag, startSessionDrag } from '../../sidebarDrag'
import { useAppSelector } from '../../context/AppContext'
import InlineEdit from './InlineEdit'
import { relativeTime } from '../../utils'
import { runStateBadge } from '../../runStates'
import { copyText } from '../../clipboard'
import { sessionReference } from '../../sessionLabels'

const selectProjectNames = state => state.projectNames

// One sidebar row. Hover shows rename and copy; everything else lives in the
// right-click menu. `subtitle` names the project in the flat Activity view.
function SessionItem({ session, labels = [], subtitle = '', isActive, onSelect, onRename, onContextMenu, renameRequested = false, onRenameDone }) {
  const [editing, setEditing] = useState(false)
  const [copied, setCopied] = useState('')
  const ownRunState = useAppSelector(state => state.runStates[session.id])
  const projectNames = useAppSelector(selectProjectNames)
  const runState = ownRunState || (session.status === 'running' ? 'running' : null)

  useEffect(() => {
    if (renameRequested) { setEditing(true); onRenameDone?.() }
  }, [renameRequested, onRenameDone])
  useEffect(() => {
    if (!copied) return
    const timer = setTimeout(() => setCopied(''), 1500)
    return () => clearTimeout(timer)
  }, [copied])

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
    if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) {
      e.preventDefault()
      const rect = e.currentTarget.getBoundingClientRect()
      onContextMenu?.(session, rect.left + 24, rect.bottom)
    }
  }

  async function copyReference(e) {
    e.stopPropagation()
    try { await copyText(sessionReference(session, projectNames)); setCopied('Copied') }
    catch { setCopied('Copy failed') }
  }

  return (
    <div
      className={`group relative grid grid-cols-[1fr_auto] items-center gap-x-1 px-2.5 py-1.5 rounded-lg cursor-pointer text-[13.5px] leading-snug transition-[background-color,color,box-shadow] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/70 ${
        isActive
          ? 'bg-panel text-text-bright shadow-[0_0_0_1px_var(--color-line)]'
          : 'text-text-default hover:bg-surface-hover'
      }`}
      tabIndex={0}
      draggable={!editing && session.kind !== 'chat' && !runState}
      onDragStart={e => {
        if (editing || runState) { e.preventDefault(); return }
        e.dataTransfer.setData('application/x-myharness-session', session.id)
        e.dataTransfer.effectAllowed = 'move'
        startSessionDrag(session.id, session.project_id)
      }}
      onDragEnd={endSessionDrag}
      aria-current={isActive ? 'page' : undefined}
      onClick={() => !editing && onSelect(session.id)}
      onContextMenu={e => {
        if (editing || !onContextMenu) return
        e.preventDefault()
        onContextMenu(session, e.clientX, e.clientY)
      }}
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
          <span className="truncate">
            {session.title || session.id.slice(0, 8)}
            {labels.length > 0 && <span className="ml-1.5 text-[12px] text-faint" title={labels.join(', ')}>{labels.join(', ')}</span>}
          </span>
        </span>
      )}
      <span className="relative flex items-center justify-end">
        <span className={`text-[11px] transition-opacity group-hover:opacity-0 group-focus-within:opacity-0 ${copied ? 'opacity-0' : ''} text-faint`}>
          {relativeTime(session.updated_at)}
        </span>
        <span className={`absolute right-0 flex gap-0.5 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 ${copied ? 'opacity-100' : 'opacity-0'}`}>
          {copied ? (
            <span className={`text-[11px] ${copied === 'Copied' ? 'text-ok' : 'text-danger'}`} role="status">{copied}</span>
          ) : (
            <>
              <button onClick={(e) => { e.stopPropagation(); setEditing(true) }} className="text-faint hover:text-accent text-[11px] px-0.5" title="Rename (F2)">✎</button>
              <button onClick={copyReference} className="text-faint hover:text-accent text-[11px] px-0.5" title="Copy thread reference">⧉</button>
            </>
          )}
        </span>
      </span>
      {subtitle && !editing && (
        <span className="col-span-2 truncate text-[11.5px] leading-4 text-faint">{subtitle}</span>
      )}
    </div>
  )
}

export default memo(SessionItem)

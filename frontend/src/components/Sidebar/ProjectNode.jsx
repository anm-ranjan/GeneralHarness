import { memo, useEffect, useState } from 'react'
import { useAppDispatch } from '../../context/AppContext'
import InlineEdit from './InlineEdit'
import SessionItem from './SessionItem'
import { lastSessionId, isRowAction } from '../../sidebarState'
import { draggedSession, endSessionDrag } from '../../sidebarDrag'
import { reorderIds } from '../../sessionLabels'

const SESSION_TYPE = 'application/x-myharness-session'

// A project and its threads: newest first, or in the order the user dragged
// them into. Labels are not a level here: they show as chips on each thread row.
// Dragging a thread within its project reorders it (onto a row: above or below
// it; onto empty space: to the bottom); dragging one from another project moves it here.
function ProjectNode({
  project, sessions, totalCount, labelsById, currentSessionId, filtering = false,
  onSelectSession, onRenameSession, onSessionContextMenu, renameSessionId, onRenameHandled,
  onRenameProject, onDeleteProject, onCreateSession, onImportSession, onDropSession, onReorderSessions,
}) {
  const dispatch = useAppDispatch()
  const [editing, setEditing] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  // Where a thread of this project would land: { id, place } on a row, or 'end'.
  const [dropMark, setDropMark] = useState(null)
  const manualOrder = (project.session_order || []).length > 0

  function ownDrag() {
    const drag = draggedSession()
    return drag && drag.projectId === project.id && !filtering && onReorderSessions ? drag : null
  }

  function reorder(movedId, targetId, place) {
    const ids = reorderIds(sessions.map(s => s.id), movedId, targetId, place)
    if (ids.join() !== sessions.map(s => s.id).join()) onReorderSessions(project.id, ids)
  }

  function clearDrag() {
    setDragOver(false)
    setDropMark(null)
  }
  const containsActiveSession = sessions.some(s => s.id === currentSessionId)
  const [collapsed, setCollapsed] = useState(() => !sessions.some(s => s.id === (currentSessionId || lastSessionId())))

  useEffect(() => {
    if (containsActiveSession) setCollapsed(false)
  }, [containsActiveSession, currentSessionId])

  const open = filtering || !collapsed

  function handleHeaderKeyDown(e) {
    if (editing || isRowAction(e.target)) return
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setCollapsed(v => !v); return }
    if (e.key !== 'F2') return
    e.preventDefault()
    setEditing(true)
  }

  return (
    <div
      className={`mb-4 rounded-md transition-colors ${dragOver ? 'bg-accent-soft ring-1 ring-accent/40' : ''}`}
      onDragOver={e => {
        if (!e.dataTransfer.types.includes(SESSION_TYPE)) return
        const own = draggedSession()?.projectId === project.id
        if (own && !ownDrag()) return
        e.preventDefault()
        e.dataTransfer.dropEffect = 'move'
        if (own) setDropMark('end')
        else setDragOver(true)
      }}
      onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget)) clearDrag() }}
      onDrop={e => {
        const id = e.dataTransfer.getData(SESSION_TYPE)
        const own = ownDrag()
        clearDrag()
        endSessionDrag()
        if (!id) return
        e.preventDefault()
        if (own) reorder(id, null)
        else onDropSession?.(id, project.id)
      }}
    >
      <div
        className={`flex items-center gap-1.5 group rounded-md px-1.5 py-0.5 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/70`}
        tabIndex={0}
        role="group"
        onKeyDown={handleHeaderKeyDown}
        onClick={e => { if (!editing && !isRowAction(e.target)) setCollapsed(v => !v) }}
        aria-expanded={open}
      >
        <button
          onClick={() => setCollapsed(!collapsed)}
          className={`text-faint text-[10px] w-4 h-4 shrink-0 transition-transform ${open ? '' : '-rotate-90'}`}
          aria-label={open ? 'Collapse project' : 'Expand project'}
          aria-expanded={open}
        >
          ▼
        </button>
        {editing ? (
          <InlineEdit
            value={project.name}
            onSave={(name) => { onRenameProject(project.id, name); setEditing(false) }}
            onCancel={() => setEditing(false)}
          />
        ) : (
          <span
            className="font-display text-[14px] font-medium text-muted truncate cursor-pointer"
            onDoubleClick={() => setEditing(true)}
            title={project.root ? `${project.name}\n${project.root}` : project.name}
          >
            {project.name}
          </span>
        )}
        <span className="ml-1 text-[10px] text-faint tabular-nums">
          {filtering && sessions.length !== totalCount ? `${sessions.length}/${totalCount}` : totalCount}
        </span>
        <button
          onClick={() => { setCollapsed(false); onCreateSession(project.id) }}
          className="ml-auto shrink-0 text-faint hover:text-accent text-[15px] leading-none px-1"
          title="New thread in this project"
          aria-label={`New thread in ${project.name}`}
        >+</button>
        <div className="flex gap-0.5 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity shrink-0">
          <button
            onClick={() => setEditing(true)}
            className="text-faint hover:text-accent text-[11px] px-0.5"
            title="Rename project"
          >✎</button>
          <button
            onClick={() => dispatch({
              type: 'OPEN_CONFIRM',
              payload: {
                title: 'Delete project?',
                message: 'This removes the project and deletes its threads, events, and attachments.',
                detail: project.name,
                confirmLabel: 'Delete',
                tone: 'danger',
                onConfirm: () => onDeleteProject(project.id),
              },
            })}
            className="text-faint hover:text-danger text-[11px] px-0.5"
            title="Delete project"
          >✕</button>
          <button
            onClick={() => onImportSession(project.id)}
            className="text-faint hover:text-ok text-[11px] px-0.5"
            title="Import thread backup (.zip)"
          >⇪</button>
          {manualOrder && onReorderSessions && (
            <button
              onClick={() => onReorderSessions(project.id, [])}
              className="text-faint hover:text-accent text-[11px] px-0.5"
              title="Sort threads by latest activity again"
              aria-label={`Sort ${project.name} by latest activity`}
            >⇅</button>
          )}
        </div>
      </div>

      <div className={`collapse-grid ${open ? 'open' : ''}`}>
        <div hidden={!open} className="mt-1 space-y-px pl-3">
          {sessions.length === 0 ? (
            <p className="text-[12px] text-faint italic py-1 px-2">{filtering ? 'No matching threads.' : 'No threads yet.'}</p>
          ) : sessions.map(s => (
            <div
              key={s.id}
              className="relative"
              onDragOver={e => {
                const drag = ownDrag()
                if (!drag) return
                e.preventDefault()
                e.stopPropagation()
                e.dataTransfer.dropEffect = 'move'
                const rect = e.currentTarget.getBoundingClientRect()
                const place = e.clientY < rect.top + rect.height / 2 ? 'before' : 'after'
                setDropMark(drag.sessionId === s.id ? null : { id: s.id, place })
              }}
              onDrop={e => {
                const drag = ownDrag()
                if (!drag) return
                e.preventDefault()
                e.stopPropagation()
                const mark = dropMark
                clearDrag()
                endSessionDrag()
                if (mark && mark.id === s.id) reorder(drag.sessionId, s.id, mark.place)
              }}
            >
              {dropMark?.id === s.id && (
                <span
                  aria-hidden="true"
                  className={`pointer-events-none absolute inset-x-2 h-0.5 rounded-full bg-accent ${dropMark.place === 'before' ? '-top-px' : '-bottom-px'}`}
                />
              )}
              <SessionItem
                session={s}
                labels={labelsById[s.id]}
                isActive={s.id === currentSessionId}
                onSelect={onSelectSession}
                onRename={onRenameSession}
                onContextMenu={onSessionContextMenu}
                renameRequested={renameSessionId === s.id}
                onRenameDone={onRenameHandled}
              />
            </div>
          ))}
          {dropMark === 'end' && sessions.length > 0 && (
            <span aria-hidden="true" className="pointer-events-none mx-2 block h-0.5 rounded-full bg-accent" />
          )}
        </div>
      </div>
    </div>
  )
}

export default memo(ProjectNode)

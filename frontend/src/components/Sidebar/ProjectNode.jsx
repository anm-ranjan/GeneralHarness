import { memo, useEffect, useState } from 'react'
import { useAppDispatch } from '../../context/AppContext'
import InlineEdit from './InlineEdit'
import SessionItem from './SessionItem'
import { lastSessionId, isRowAction } from '../../sidebarState'

// A project and its threads, newest first. Labels are not a level here: they
// show as chips on each thread row.
function ProjectNode({
  project, sessions, totalCount, labelsById, currentSessionId, filtering = false,
  onSelectSession, onRenameSession, onSessionContextMenu, renameSessionId, onRenameHandled,
  onRenameProject, onDeleteProject, onCreateSession, onImportSession, onDropSession,
}) {
  const dispatch = useAppDispatch()
  const [editing, setEditing] = useState(false)
  const [dragOver, setDragOver] = useState(false)
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
        if (e.dataTransfer.types.includes('application/x-myharness-session')) {
          e.preventDefault(); setDragOver(true); e.dataTransfer.dropEffect = 'move'
        }
      }}
      onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget)) setDragOver(false) }}
      onDrop={e => {
        const id = e.dataTransfer.getData('application/x-myharness-session')
        setDragOver(false)
        if (!id) return
        e.preventDefault()
        onDropSession?.(id, project.id)
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
        </div>
      </div>

      <div className={`collapse-grid ${open ? 'open' : ''}`}>
        <div hidden={!open} className="mt-1 space-y-px">
          {sessions.length === 0 ? (
            <p className="text-[12px] text-faint italic py-1 px-2">{filtering ? 'No matching threads.' : 'No threads yet.'}</p>
          ) : sessions.map(s => (
            <SessionItem
              key={s.id}
              session={s}
              labels={labelsById[s.id]}
              isActive={s.id === currentSessionId}
              onSelect={onSelectSession}
              onRename={onRenameSession}
              onContextMenu={onSessionContextMenu}
              renameRequested={renameSessionId === s.id}
              onRenameDone={onRenameHandled}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

export default memo(ProjectNode)

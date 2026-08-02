import { useState } from 'react'
import { useAppDispatch } from '../../context/AppContext'
import InlineEdit from './InlineEdit'
import SessionItem from './SessionItem'
import { LABEL_COLORS, labelColorStyle } from '../../labelColors'

export default function TaskRow({
  task, projectId, sessions, sessionsById, currentSessionId,
  onSelectSession, onRenameTask, onDeleteTask,
  onCreateSession, onImportSession, onRenameSession, onDeleteSession,
  onMoveSession,
}) {
  const dispatch = useAppDispatch()
  const [editing, setEditing] = useState(false)
  const isActiveTask = sessions.includes(currentSessionId)

  function handleHeaderKeyDown(e) {
    if (e.key !== 'F2' || editing) return
    e.preventDefault()
    setEditing(true)
  }

  return (
    <div
      className={`label-group ml-3 mb-1 border-l pl-2 transition-colors ${isActiveTask ? 'label-group-active' : ''}`}
      style={labelColorStyle(task.color || task.id)}
    >
      <div
        className="flex items-center gap-1 group rounded-md px-1 py-0.5 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/70"
        tabIndex={0}
        onKeyDown={handleHeaderKeyDown}
      >
        {editing ? (
          <InlineEdit
            value={task.name}
            onSave={(name) => { onRenameTask(projectId, task.id, name); setEditing(false) }}
            onCancel={() => setEditing(false)}
          />
        ) : (
          <span
            className="label-chip max-w-full rounded-full px-1.5 py-px text-[10px] font-semibold uppercase tracking-wider truncate cursor-default"
            onDoubleClick={() => setEditing(true)}
            title={task.name}
          >
            {task.name}
          </span>
        )}
        <div className="flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity ml-auto shrink-0">
          <button
            onClick={() => {
              const current = LABEL_COLORS.indexOf(task.color)
              const color = LABEL_COLORS[(current + 1 + LABEL_COLORS.length) % LABEL_COLORS.length]
              onRenameTask(projectId, task.id, undefined, color)
            }}
            className="label-chip text-[11px] px-1 rounded-full"
            title="Change label colour"
            aria-label={`Change colour for ${task.name}`}
          >●</button>
          <button
            onClick={() => setEditing(true)}
            className="text-faint hover:text-accent text-[11px] px-0.5"
            title="Rename label"
          >✎</button>
          <button
            onClick={() => dispatch({
              type: 'OPEN_CONFIRM',
              payload: {
                title: 'Delete label?',
                message: 'This removes the label. Its threads will move to the General label.',
                detail: task.name,
                confirmLabel: 'Delete',
                tone: 'danger',
                onConfirm: () => onDeleteTask(projectId, task.id),
              },
            })}
            className="text-faint hover:text-danger text-[11px] px-0.5"
            title="Delete label"
          >✕</button>
          {onImportSession && (
            <button
              onClick={() => onImportSession(projectId, task.id)}
              className="text-faint hover:text-ok text-[11px] px-0.5"
              title="Import thread backup (.zip)"
            >⇪</button>
          )}
          <button
            onClick={() => onCreateSession(projectId, task.id)}
            className="text-faint hover:text-ok text-[11px] px-0.5"
            title="New thread"
          >+</button>
        </div>
      </div>
      <div className="mt-1 space-y-px">
        {sessions.map(sid => {
          const s = sessionsById[sid]
          if (!s) return null
          return (
            <SessionItem
              key={sid}
              session={s}
              isActive={sid === currentSessionId}
              onSelect={onSelectSession}
              onRename={onRenameSession}
              onMove={onMoveSession ? () => onMoveSession(s, projectId, task.id) : null}
              onDelete={(id) => onDeleteSession(id, projectId, task.id)}
            />
          )
        })}
      </div>
    </div>
  )
}

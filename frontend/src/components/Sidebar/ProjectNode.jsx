import { useEffect, useState } from 'react'
import { useAppDispatch } from '../../context/AppContext'
import InlineEdit from './InlineEdit'
import TaskRow from './TaskRow'

export default function ProjectNode({
  project, sessionsById, currentSessionId,
  onSelectSession, onRenameProject, onDeleteProject,
  onCreateTask, onRenameTask, onDeleteTask,
  onCreateSession, onImportSession, onRenameSession, onMoveSession, onDeleteSession,
}) {
  const dispatch = useAppDispatch()
  const [editing, setEditing] = useState(false)
  const [collapsed, setCollapsed] = useState(false)
  const tasks = project.tasks || []
  const sessionCount = tasks.reduce((total, task) => total + (task.sessions || []).length, 0)
  const containsActiveSession = tasks.some(task => (task.sessions || []).includes(currentSessionId))

  useEffect(() => {
    if (containsActiveSession) setCollapsed(false)
  }, [containsActiveSession])

  function handleHeaderKeyDown(e) {
    if (e.key !== 'F2' || editing) return
    e.preventDefault()
    setEditing(true)
  }

  return (
    <div className={`mb-3 rounded-md transition-colors ${containsActiveSession ? 'bg-accent-glow/40' : ''}`}>
      <div
        className={`flex items-center gap-1.5 group rounded-md px-1.5 py-1 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/70 ${containsActiveSession ? 'text-accent' : ''}`}
        tabIndex={0}
        onKeyDown={handleHeaderKeyDown}
      >
        <button
          onClick={() => setCollapsed(!collapsed)}
          className={`text-faint text-[10px] w-4 h-4 shrink-0 transition-transform ${collapsed ? '-rotate-90' : ''}`}
          aria-label={collapsed ? 'Expand project' : 'Collapse project'}
          aria-expanded={!collapsed}
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
            className="text-[13px] font-semibold text-text-bright truncate cursor-default"
            onDoubleClick={() => setEditing(true)}
            title={project.name}
          >
            {project.name}
          </span>
        )}
        <span className="ml-1 text-[10px] text-faint tabular-nums">{sessionCount}</span>
        <div className="flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity ml-auto shrink-0">
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
                message: 'This removes the project from MyHarness and deletes its sessions, events, and attachments.',
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
            onClick={() => onCreateTask(project.id)}
            className="text-faint hover:text-ok text-[11px] px-0.5"
            title="New task"
          >+ task</button>
        </div>
      </div>

      <div className={`collapse-grid ${collapsed ? '' : 'open'}`}>
        <div className="mt-1">
          {tasks.map(task => (
            <TaskRow
              key={task.id}
              task={task}
              projectId={project.id}
              sessions={task.sessions || []}
              sessionsById={sessionsById}
              currentSessionId={currentSessionId}
              onSelectSession={onSelectSession}
              onRenameTask={onRenameTask}
              onDeleteTask={onDeleteTask}
              onCreateSession={onCreateSession}
              onImportSession={onImportSession}
              onRenameSession={onRenameSession}
              onMoveSession={onMoveSession}
              onDeleteSession={onDeleteSession}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

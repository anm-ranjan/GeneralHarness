import { useEffect, useState } from 'react'
import { useApp } from '../../context/AppContext'
import InlineEdit from './InlineEdit'
import TaskRow from './TaskRow'
import { lastSessionId, isRowAction } from '../../sidebarState'

export default function ProjectNode({
  project, sessionsById, currentSessionId,
  onSelectSession, onRenameProject, onDeleteProject,
  onCreateTask, onRenameTask, onDeleteTask,
  onCreateSession, onImportSession, onRenameSession, onMoveSession, onDeleteSession,
  onDropSession,
}) {
  const { state, dispatch } = useApp()
  const [editing, setEditing] = useState(false)
  const [collapsed, setCollapsed] = useState(() => !(project.tasks || []).some(t => (t.sessions || []).includes(currentSessionId || lastSessionId())))
  const tasks = project.tasks || []
  const sessionCount = tasks.reduce((total, task) => total + (task.sessions || []).length, 0)
  const containsActiveSession = tasks.some(task => (task.sessions || []).includes(currentSessionId))

  useEffect(() => {
    if (containsActiveSession) setCollapsed(false)
  }, [containsActiveSession, currentSessionId])

  function handleHeaderKeyDown(e) {
    if (editing || isRowAction(e.target)) return
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setCollapsed(v => !v); return }
    if (e.key !== 'F2') return
    e.preventDefault()
    setEditing(true)
  }

  return (
    <div className={`mb-3 rounded-md transition-colors ${containsActiveSession ? 'bg-accent-glow/40' : ''}`}>
      <div
        className={`flex items-center gap-1.5 group rounded-md px-1.5 py-1 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/70 ${containsActiveSession ? 'text-accent' : ''}`}
        tabIndex={0}
        role="group"
        onKeyDown={handleHeaderKeyDown}
        onClick={e => { if (!editing && !isRowAction(e.target)) setCollapsed(v => !v) }}
        aria-expanded={!collapsed}
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
            className="text-[13px] font-semibold text-text-bright truncate cursor-pointer"
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
                message: `This removes the project from ${state.appName} and deletes its threads, events, and attachments.`,
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
            title="New label"
          >+ label</button>
          <button
            onClick={() => onCreateSession(project.id, '')}
            className="text-faint hover:text-ok text-[11px] px-0.5"
            title="New thread with the General label"
          >+ thread</button>
        </div>
      </div>

      <div className={`collapse-grid ${collapsed ? '' : 'open'}`}>
        <div hidden={collapsed} className="mt-1">
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
              onDropSession={onDropSession}
              onDeleteSession={onDeleteSession}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

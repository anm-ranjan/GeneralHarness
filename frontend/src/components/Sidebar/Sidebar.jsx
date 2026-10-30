import { useState, useEffect, useCallback, useMemo } from 'react'
import { useApp } from '../../context/AppContext'
import useSessionTree from '../../hooks/useSessionTree'
import useSelectSession from '../../hooks/useSelectSession'
import { importSessionBackup, isAbandonedRequest, downloadSessionBackup, downloadSessionExport } from '../../api'
import SearchInput from './SearchInput'
import HostSwitcher from './HostSwitcher'
import ProjectNode from './ProjectNode'
import ChatsNode from './ChatsNode'
import useStartChat from '../../hooks/useStartChat'
import { effectiveWorkspaceRoot } from '../../sessionWorkspace'
import { shortcutLabel, CHATS_PROJECT_ID } from '../../constants'
import MoveSessionModal from '../Modals/MoveSessionModal'
import SessionItem from './SessionItem'
import SessionContextMenu from './SessionContextMenu'
import { SidebarActionsContext } from './SidebarActions'
import { copyText } from '../../clipboard'
import {
  activitySessions, archivedSessions, isChatSession, sessionPlace, sessionReference, withoutArchived,
} from '../../sessionList'

export default function Sidebar() {
  const { state, dispatch } = useApp()
  const tree = useSessionTree()
  const startChat = useStartChat()
  const [search, setSearch] = useState('')
  const [moveRequest, setMoveRequest] = useState(null)
  const [menu, setMenu] = useState(null)
  const [renameSessionId, setRenameSessionId] = useState(null)
  const [archivedOpen, setArchivedOpen] = useState(false)
  const selectSessionMeta = useSelectSession()
  const view = state.sidebarView

  // Reloads on a host switch: the tree belongs to one machine, and
  // SWITCH_HOST has already emptied the previous host's copy.
  useEffect(() => {
    tree.loadTree().catch(err => {
      if (isAbandonedRequest(err)) return
      dispatch({ type: 'HOST_SWITCH_FAILED' })
      console.error('Failed to load tree:', err)
    })
  }, [state.activeHostId])

  const selectSession = useCallback(async (sid) => {
    try {
      await selectSessionMeta(sid)
    } catch (err) {
      console.error('Failed to select session:', err)
    }
  }, [selectSessionMeta])

  const reportError = useCallback((err) => {
    dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: err.detail || err.message || String(err) } })
  }, [dispatch])

  const createSession = useCallback(async (projectId, taskId, provider) => {
    const providers = [
      state.nativeEnabled && 'native',
      state.codexAppServerEnabled && 'codex-app-server',
      state.claudeAgentEnabled && 'claude-agent',
    ].filter(Boolean)
    if (!provider && providers.length !== 1) {
      dispatch({ type: 'OPEN_PROVIDER_PICKER', payload: { projectId, taskId } })
      return
    }
    try {
      const meta = await tree.createSession(projectId, taskId, '', provider || providers[0])
      await selectSession(meta.id)
    } catch (err) {
      console.error('Failed to create session:', err)
    }
  }, [tree, selectSession, state.nativeEnabled, state.codexAppServerEnabled, state.claudeAgentEnabled, dispatch])

  const deleteSession = useCallback(async (sessionId, projectId, taskId) => {
    try {
      await tree.deleteSession(sessionId, projectId, taskId)
      if (state.currentSessionId === sessionId) {
        dispatch({ type: 'CLEAR_SESSION' })
      }
    } catch (err) {
      reportError(err)
    }
  }, [tree, state.currentSessionId, dispatch, reportError])

  const importSession = useCallback((projectId, taskId) => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.zip,application/zip'
    input.onchange = async () => {
      const file = input.files && input.files[0]
      if (!file) return
      try {
        const res = await importSessionBackup(file, projectId, taskId)
        await tree.loadTree()
        await selectSession(res.session_id)
      } catch (err) {
        dispatch({
          type: 'APPEND_STAGE_ITEM',
          payload: { type: 'error', text: err.detail || err.message || 'Import failed' },
        })
      }
    }
    input.click()
  }, [tree, selectSession, dispatch])

  const moveSession = useCallback(async (sessionId, projectId, taskId) => {
    const meta = await tree.moveSession(sessionId, projectId, taskId)
    if (state.currentSessionId === sessionId) {
      dispatch({ type: 'UPDATE_SESSION_META', payload: meta })
      const project = state.projects.find(p => p.id === projectId)
      dispatch({ type: 'SET_WORKSPACE_ROOT', payload: { root: effectiveWorkspaceRoot(meta, project?.root || ''), workingDirectory: meta.working_directory } })
    }
    return meta
  }, [tree, state.currentSessionId, state.projects, dispatch])

  const openContextMenu = useCallback((session, x, y) => setMenu({ session, x, y }), [])
  const closeContextMenu = useCallback(() => setMenu(null), [])
  const clearRenameRequest = useCallback(() => setRenameSessionId(null), [])
  const sidebarActions = useMemo(
    () => ({ openContextMenu, renameSessionId, clearRenameRequest }),
    [openContextMenu, renameSessionId, clearRenameRequest],
  )

  const menuItems = useMemo(() => {
    if (!menu) return []
    const s = state.sessionsById[menu.session.id] || menu.session
    const busy = !!(state.runStates[s.id] || s.status === 'running')
    const chat = isChatSession(s)
    return [
      { label: 'Copy session reference', onSelect: () => copyText(sessionReference(s, state.projectNames, state.taskNames)).catch(reportError) },
      { label: 'Rename', onSelect: () => { setRenameSessionId(s.id); if (s.archived) setArchivedOpen(true) } },
      ...(!chat ? [{ label: 'Move to label or project…', disabled: busy, onSelect: () => setMoveRequest({ session: s, projectId: s.project_id, currentTaskId: s.task_id }) }] : []),
      'separator',
      { label: 'Export as Markdown', onSelect: () => downloadSessionExport(s.id, 'md').catch(reportError) },
      { label: 'Export backup (.zip)', onSelect: () => downloadSessionBackup(s.id).catch(reportError) },
      'separator',
      s.archived
        ? { label: 'Unarchive', onSelect: () => tree.updateSession(s.id, { archived: false }).catch(reportError) }
        : { label: 'Archive', disabled: busy, title: busy ? 'Wait for the run to finish' : '', onSelect: () => tree.updateSession(s.id, { archived: true }).catch(reportError) },
      {
        label: 'Delete…',
        tone: 'danger',
        disabled: busy,
        onSelect: () => dispatch({
          type: 'OPEN_CONFIRM',
          payload: {
            title: 'Delete thread?',
            message: 'This deletes the thread event stream and attachments. Archive it instead to keep it.',
            detail: s.title || s.id,
            confirmLabel: 'Delete',
            tone: 'danger',
            onConfirm: () => deleteSession(s.id, s.project_id, s.task_id),
          },
        }),
      },
    ]
  }, [menu, state.sessionsById, state.runStates, state.projectNames, state.taskNames, tree, dispatch, deleteSession, reportError])

  const q = search.toLowerCase()
  const liveProjects = useMemo(() => withoutArchived(state.projects, state.sessionsById), [state.projects, state.sessionsById])
  const chatsProject = liveProjects.find(p => p.id === CHATS_PROJECT_ID)
  const realProjects = liveProjects.filter(p => p.id !== CHATS_PROJECT_ID)
  const placeOf = s => sessionPlace(s, state.projectNames, state.taskNames)
  const matchesSearch = s => !search ||
    (s.title || '').toLowerCase().includes(q) || placeOf(s).toLowerCase().includes(q)
  const activity = useMemo(() => activitySessions(state.sessionsById), [state.sessionsById])
    .filter(matchesSearch)
  const archived = useMemo(() => archivedSessions(state.sessionsById), [state.sessionsById])
    .filter(matchesSearch)
  const flatRow = s => (
    <SessionItem
      key={s.id}
      session={s}
      subtitle={placeOf(s)}
      isActive={s.id === state.currentSessionId}
      onSelect={selectSession}
      onRename={tree.renameSession}
      onMove={isChatSession(s) ? undefined : (session) => setMoveRequest({ session, projectId: session.project_id, currentTaskId: session.task_id })}
      onDelete={sid => deleteSession(sid, s.project_id, s.task_id)}
    />
  )

  const filteredProjects = realProjects.filter(p => {
    if (!search) return true
    return p.name.toLowerCase().includes(q) ||
      (p.tasks || []).some(t =>
        t.name.toLowerCase().includes(q) ||
        (t.sessions || []).some(sid => {
          const s = state.sessionsById[sid]
          return s && (s.title || '').toLowerCase().includes(q)
        })
      )
  })

  const chatSessions = (chatsProject?.tasks || [])
    .flatMap(t => t.sessions || [])
    .map(sid => state.sessionsById[sid])
    .filter(Boolean)
    .filter(s => !search || (s.title || '').toLowerCase().includes(q))
    .sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at))

  return (
    <div className="flex flex-col h-full px-5 pt-8 pb-5">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h1 className="text-[18px] font-bold text-text-bright mb-0.5">{state.appName}</h1>
          <p className="text-[12px] text-faint mb-4">Projects & Threads</p>
        </div>
        <div className="mt-0.5 flex items-center gap-0.5" role="tablist" aria-label="Sidebar view">
          <ViewButton active={view === 'projects'} label="Projects" onClick={() => dispatch({ type: 'SET_SIDEBAR_VIEW', payload: 'projects' })}>
            <BookIcon />
          </ViewButton>
          <ViewButton active={view === 'activity'} label="Activity — all threads by recent activity" onClick={() => dispatch({ type: 'SET_SIDEBAR_VIEW', payload: 'activity' })}>
            <BellIcon />
          </ViewButton>
          <button
            onClick={() => dispatch({ type: 'TOGGLE_SIDEBAR' })}
            className="p-1.5 rounded text-faint hover:text-accent hover:bg-surface-hover transition-colors"
            title={`Hide sidebar (${shortcutLabel('B')})`}
            aria-label="Hide sidebar"
          >
            <SidebarIcon />
          </button>
        </div>
      </div>

      <HostSwitcher />

      <SearchInput value={search} onChange={setSearch} />

      <button
        onClick={() => dispatch({ type: 'OPEN_SEARCH' })}
        className="mt-2 w-full py-1.5 text-[12px] font-medium text-muted border border-line rounded-md hover:text-accent hover:border-accent/30 hover:bg-accent-soft transition-colors flex items-center justify-center gap-1.5"
        title={`Search all transcripts (${shortcutLabel('K')})`}
      >
        <span>Search transcripts</span>
        <span className="text-[10px] text-faint">{shortcutLabel('K')}</span>
      </button>

      <div className="mt-2 mb-4 grid grid-cols-2 gap-2">
        <button
          onClick={() => startChat()}
          className="py-2 text-[13px] font-medium text-accent border border-accent/30 rounded-md hover:bg-accent-soft transition-colors"
        >
          + Chat
        </button>
        <button
          onClick={() => dispatch({ type: 'OPEN_DIR_PICKER' })}
          className="py-2 text-[13px] font-medium text-accent border border-accent/30 rounded-md hover:bg-accent-soft transition-colors"
        >
          + Project
        </button>
      </div>

      <SidebarActionsContext.Provider value={sidebarActions}>
      <div className="flex-1 overflow-y-auto">
        {view === 'activity' ? (
          activity.length === 0 ? (
            <p className="text-[13px] text-faint italic text-center mt-8 mb-4">{search ? 'No matching threads.' : 'No threads yet.'}</p>
          ) : (
            <div className="space-y-px mb-3">{activity.map(flatRow)}</div>
          )
        ) : <>
        {filteredProjects.length === 0 && realProjects.length > 0 ? (
          <p className="text-[13px] text-faint italic text-center mt-8 mb-4">No matching projects.</p>
        ) : realProjects.length === 0 ? (
          <p className="text-[13px] text-faint italic text-center mt-8 mb-4">No projects yet. Click "+ Project" to start.</p>
        ) : (
          filteredProjects.map(project => (
            <ProjectNode
              key={project.id}
              project={project}
              sessionsById={state.sessionsById}
              currentSessionId={state.currentSessionId}
              onSelectSession={selectSession}
              onRenameProject={tree.renameProject}
              onDeleteProject={pid => tree.deleteProject(pid).catch(reportError)}
              onCreateTask={(pid) => tree.createTask(pid, 'New Label')}
              onRenameTask={tree.renameTask}
              onDeleteTask={(pid, tid) => tree.deleteTask(pid, tid).catch(reportError)}
              onCreateSession={createSession}
              onImportSession={importSession}
              onRenameSession={tree.renameSession}
              onMoveSession={(session, projectId, currentTaskId) => setMoveRequest({ session, projectId, currentTaskId })}
              onDropSession={(sid, projectId, taskId) => moveSession(sid, projectId, taskId).catch(err => dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: err.detail || err.message } }))}
              onDeleteSession={deleteSession}
            />
          ))
        )}

        <ChatsNode
          chats={chatSessions}
          currentSessionId={state.currentSessionId}
          onStartChat={startChat}
          onSelectSession={selectSession}
          onRenameSession={tree.renameSession}
          onDeleteSession={(sid) => deleteSession(sid, CHATS_PROJECT_ID, CHATS_PROJECT_ID)}
        />
        </>}

        {archived.length > 0 && (
          <div className="mt-3 pt-2 border-t border-line/60">
            <button
              type="button"
              onClick={() => setArchivedOpen(v => !v)}
              aria-expanded={archivedOpen || !!search}
              className="flex w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-[13px] font-semibold text-muted hover:text-text-bright"
            >
              <span className={`text-faint text-[10px] w-4 transition-transform ${archivedOpen || search ? '' : '-rotate-90'}`}>▼</span>
              Archived
              <span className="ml-1 text-[10px] text-faint tabular-nums">{archived.length}</span>
            </button>
            {(archivedOpen || !!search) && <div className="mt-0.5 pl-4 space-y-px">{archived.map(flatRow)}</div>}
          </div>
        )}
      </div>
      </SidebarActionsContext.Provider>

      {menu && <SessionContextMenu x={menu.x} y={menu.y} items={menuItems} onClose={closeContextMenu} />}

      {moveRequest && (
        <MoveSessionModal
          moveRequest={moveRequest}
          projects={state.projects}
          onClose={() => setMoveRequest(null)}
          onMove={moveSession}
        />
      )}
    </div>
  )
}

function ViewButton({ active, label, onClick, children }) {
  return (
    <button
      role="tab"
      aria-selected={active}
      aria-label={label}
      title={label}
      onClick={onClick}
      className={`p-1.5 rounded transition-colors ${active ? 'text-accent bg-accent-soft' : 'text-faint hover:text-accent hover:bg-surface-hover'}`}
    >
      {children}
    </button>
  )
}

function BookIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5v14Z" />
      <path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5" />
    </svg>
  )
}

function BellIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
      <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
    </svg>
  )
}

export function SidebarIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9 4v16" />
    </svg>
  )
}

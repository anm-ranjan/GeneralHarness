import { useState, useEffect, useCallback } from 'react'
import { useApp } from '../../context/AppContext'
import useSessionTree from '../../hooks/useSessionTree'
import useApi from '../../hooks/useApi'
import { api, importSessionBackup, isAbandonedRequest } from '../../api'
import SearchInput from './SearchInput'
import HostSwitcher from './HostSwitcher'
import ProjectNode from './ProjectNode'
import ChatsNode from './ChatsNode'
import useStartChat from '../../hooks/useStartChat'
import { shortcutLabel, CHATS_PROJECT_ID } from '../../constants'
import { effectiveWorkspaceRoot } from '../../sessionWorkspace'
import MoveSessionModal from '../Modals/MoveSessionModal'

export default function Sidebar() {
  const { state, dispatch } = useApp()
  const tree = useSessionTree()
  const startChat = useStartChat()
  const [search, setSearch] = useState('')
  const [moveRequest, setMoveRequest] = useState(null)

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
      const data = await api('GET', `/api/sessions/${encodeURIComponent(sid)}`)
      const meta = data.meta
      const project = state.projects.find(p => p.id === meta.project_id)
      const workspaceRoot = effectiveWorkspaceRoot(meta, project?.root || '')
      dispatch({ type: 'SELECT_SESSION', payload: { meta, workspaceRoot } })
    } catch (err) {
      console.error('Failed to select session:', err)
    }
  }, [dispatch, state.projects])

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
      console.error('Failed to delete session:', err)
    }
  }, [tree, state.currentSessionId, dispatch])

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
    }
    return meta
  }, [tree, state.currentSessionId, dispatch])

  const q = search.toLowerCase()
  const chatsProject = state.projects.find(p => p.id === CHATS_PROJECT_ID)
  const realProjects = state.projects.filter(p => p.id !== CHATS_PROJECT_ID)

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
      <h1 className="text-[18px] font-bold text-text-bright mb-0.5">{state.appName}</h1>
      <p className="text-[12px] text-faint mb-4">Projects & Threads</p>

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

      <div className="flex-1 overflow-y-auto">
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
              onDeleteProject={tree.deleteProject}
              onCreateTask={(pid) => tree.createTask(pid, 'New Label')}
              onRenameTask={tree.renameTask}
              onDeleteTask={tree.deleteTask}
              onCreateSession={createSession}
              onImportSession={importSession}
              onRenameSession={tree.renameSession}
              onMoveSession={(session, projectId, currentTaskId) => setMoveRequest({ session, projectId, currentTaskId })}
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
      </div>

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

import { useCallback } from 'react'
import { api } from '../api'
import { reportAfterPaint } from '../performanceLog'
import { useAppDispatch } from '../context/AppContext'

export default function useSessionTree() {
  const dispatch = useAppDispatch()

  const loadTree = useCallback(async () => {
    const started = performance.now()
    const data = await api('GET', '/api/sessions')
    dispatch({ type: 'SET_TREE', payload: data })
    reportAfterPaint('sidebar_loaded', started, Object.keys(data.sessions || {}).length)
    return data
  }, [dispatch])

  const createProject = useCallback(async (name, root) => {
    const project = await api('POST', '/api/projects', { name, root })
    await loadTree()
    return project
  }, [loadTree])

  const renameProject = useCallback(async (projectId, name) => {
    await api('PATCH', `/api/projects/${encodeURIComponent(projectId)}`, { name })
    await loadTree()
  }, [loadTree])

  const deleteProject = useCallback(async (projectId) => {
    await api('DELETE', `/api/projects/${encodeURIComponent(projectId)}`)
    await loadTree()
  }, [loadTree])

  const createTask = useCallback(async (projectId, name) => {
    const task = await api('POST', '/api/tasks', { project_id: projectId, name })
    await loadTree()
    return task
  }, [loadTree])

  const renameTask = useCallback(async (projectId, taskId, name, color) => {
    const payload = {}
    if (name !== undefined) payload.name = name
    if (color !== undefined) payload.color = color
    await api('PATCH', `/api/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}`, payload)
    await loadTree()
  }, [loadTree])

  const deleteTask = useCallback(async (projectId, taskId) => {
    await api('DELETE', `/api/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}`)
    await loadTree()
  }, [loadTree])

  const createSession = useCallback(async (projectId, taskId, title, provider) => {
    const session = await api('POST', '/api/sessions', {
      project_id: projectId,
      task_id: taskId,
      title: title || '',
      provider: provider || 'native',
    })
    await loadTree()
    return session
  }, [loadTree])

  const createChat = useCallback(async (provider) => {
    const chat = await api('POST', '/api/chats', {
      title: '',
      provider: provider || 'native',
    })
    await loadTree()
    return chat
  }, [loadTree])

  const renameSession = useCallback(async (sessionId, title) => {
    await api('PATCH', `/api/sessions/${encodeURIComponent(sessionId)}`, { title })
    await loadTree()
  }, [loadTree])

  // Archive state lives on the session alone, so apply the returned metadata
  // locally instead of reloading the whole tree.
  const updateSession = useCallback(async (sessionId, changes) => {
    const meta = await api('PATCH', `/api/sessions/${encodeURIComponent(sessionId)}`, changes)
    dispatch({ type: 'UPDATE_SESSION_META_BY_ID', payload: { sessionId, changes: meta } })
    return meta
  }, [dispatch])

  const moveSession = useCallback(async (sessionId, projectId, taskId) => {
    const session = await api('POST', `/api/sessions/${encodeURIComponent(sessionId)}/move`, {
      project_id: projectId,
      task_id: taskId,
    })
    await loadTree()
    return session
  }, [loadTree])

  const deleteSession = useCallback(async (sessionId, projectId, taskId) => {
    await api('DELETE', `/api/sessions/${encodeURIComponent(sessionId)}`)
    await loadTree()
  }, [loadTree])

  return {
    loadTree,
    createProject, renameProject, deleteProject,
    createTask, renameTask, deleteTask,
    createSession, renameSession, updateSession, moveSession, deleteSession,
    createChat,
  }
}

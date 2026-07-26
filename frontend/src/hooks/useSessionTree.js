import { useCallback } from 'react'
import { api } from '../api'
import { useAppDispatch } from '../context/AppContext'

export default function useSessionTree() {
  const dispatch = useAppDispatch()

  const loadTree = useCallback(async () => {
    const data = await api('GET', '/api/sessions')
    dispatch({ type: 'SET_TREE', payload: data })
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

  const renameTask = useCallback(async (projectId, taskId, name) => {
    await api('PATCH', `/api/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}`, { name })
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
    createSession, renameSession, moveSession, deleteSession,
    createChat,
  }
}

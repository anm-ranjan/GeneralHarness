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

  // An empty label id files the thread under the project's unlabeled bucket.
  const createSession = useCallback(async (projectId, taskId = '', title, provider) => {
    const session = await api('POST', '/api/sessions', {
      project_id: projectId,
      task_id: taskId || '',
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

  // Title and archive state live on the session alone, so apply the returned
  // metadata locally. Labels can create labels and refile the thread under a
  // new primary label, so they reload the tree.
  const updateSession = useCallback(async (sessionId, changes) => {
    const meta = await api('PATCH', `/api/sessions/${encodeURIComponent(sessionId)}`, changes)
    if (changes.labels !== undefined) await loadTree()
    else dispatch({ type: 'UPDATE_SESSION_META_BY_ID', payload: { sessionId, changes: meta } })
    return meta
  }, [dispatch, loadTree])

  const renameSession = useCallback(
    (sessionId, title) => updateSession(sessionId, { title }),
    [updateSession],
  )

  const moveSession = useCallback(async (sessionId, projectId, taskId = '') => {
    const session = await api('POST', `/api/sessions/${encodeURIComponent(sessionId)}/move`, {
      project_id: projectId,
      task_id: taskId || '',
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
    createSession, renameSession, updateSession, moveSession, deleteSession,
    createChat,
  }
}

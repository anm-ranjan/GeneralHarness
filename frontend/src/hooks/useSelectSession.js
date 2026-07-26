import { useCallback } from 'react'
import { api } from '../api'
import { useApp } from '../context/AppContext'
import { effectiveWorkspaceRoot } from '../sessionWorkspace'

// Loads a session's metadata and makes it the active session. Shared by the sidebar
// session list and the global search modal so selection behaves identically.
export default function useSelectSession() {
  const { state, dispatch } = useApp()

  return useCallback(async (sessionId) => {
    const data = await api('GET', `/api/sessions/${encodeURIComponent(sessionId)}`)
    const meta = data.meta
    const project = state.projects.find(p => p.id === meta.project_id)
    dispatch({ type: 'SELECT_SESSION', payload: { meta, workspaceRoot: effectiveWorkspaceRoot(meta, project?.root || '') } })
    return meta
  }, [dispatch, state.projects])
}

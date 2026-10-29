import { useCallback } from 'react'
import { api } from '../api'
import { useAppDispatch, useAppStateRef } from '../context/AppContext'
import { effectiveWorkspaceRoot } from '../sessionWorkspace'

// Loads a session's metadata and makes it the active session. Shared by the sidebar,
// breadcrumb, and global search modal so selection behaves identically.
export default function useSelectSession() {
  const dispatch = useAppDispatch()
  const stateRef = useAppStateRef()

  return useCallback(async (sessionId) => {
    // The session WebSocket replays the transcript, so skip it here.
    const data = await api('GET', `/api/sessions/${encodeURIComponent(sessionId)}?events=0`)
    const meta = data.meta
    const project = stateRef.current.projects.find(p => p.id === meta.project_id)
    dispatch({ type: 'SELECT_SESSION', payload: { meta, workspaceRoot: effectiveWorkspaceRoot(meta, project?.root || '') } })
    return meta
  }, [dispatch, stateRef])
}

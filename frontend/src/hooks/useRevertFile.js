import { useCallback } from 'react'
import { api } from '../api'
import { useApp } from '../context/AppContext'

// Confirm-then-revert flow for a file the agent changed in the current
// session. On a 409 (file changed outside recorded runs) a second, explicit
// force-revert confirmation is offered. Shared by the Changes panel and the
// diff viewer.
export default function useRevertFile() {
  const { state, dispatch } = useApp()

  return useCallback((filePath, onDone) => {
    const sessionId = state.currentSessionId
    if (!sessionId || !filePath) return

    async function doRevert(force) {
      try {
        const res = await api('POST', '/api/workspace/revert', {
          session_id: sessionId,
          file_path: filePath,
          force,
        })
        onDone?.(res)
      } catch (err) {
        if (!force && err.status === 409) {
          dispatch({
            type: 'OPEN_CONFIRM',
            payload: {
              title: 'Revert blocked',
              message: err.detail || 'The file changed outside recorded agent runs.',
              detail: filePath,
              tone: 'danger',
              confirmLabel: 'Force revert',
              onConfirm: () => doRevert(true),
            },
          })
        } else {
          dispatch({
            type: 'APPEND_STAGE_ITEM',
            payload: { type: 'error', text: err.detail || err.message || 'Revert failed' },
          })
        }
      }
    }

    dispatch({
      type: 'OPEN_CONFIRM',
      payload: {
        title: 'Revert file',
        message: 'Restore this file to its state before the agent first changed it in this thread?',
        detail: filePath,
        tone: 'danger',
        confirmLabel: 'Revert',
        onConfirm: () => doRevert(false),
      },
    })
  }, [state.currentSessionId, dispatch])
}

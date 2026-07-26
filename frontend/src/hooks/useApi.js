import { useCallback } from 'react'
import { api } from '../api'
import { useAppDispatch, useAppStateRef } from '../context/AppContext'

// Deliberately non-reactive: these callbacks read state on invocation rather
// than subscribing, so every component using them keeps stable handlers and
// does not re-render on unrelated state changes.
export default function useApi() {
  const dispatch = useAppDispatch()
  const stateRef = useAppStateRef()

  const sendMessage = useCallback(async (sessionId, text, attachments = []) => {
    const wasRunning = stateRef.current.isRunning
    if (!wasRunning) dispatch({ type: 'SET_RUNNING' })
    try {
      return await api('POST', `/api/sessions/${encodeURIComponent(sessionId)}/message`, { text, attachments })
    } catch (err) {
      if (!wasRunning) dispatch({ type: 'SET_IDLE' })
      dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: err.detail || err.message } })
    }
  }, [dispatch, stateRef])

  const sendSilentCommand = useCallback(async (sessionId, cmd) => {
    dispatch({ type: 'SET_SILENT_COMMAND', payload: true })
    dispatch({ type: 'SET_RUNNING' })
    try {
      await api('POST', `/api/sessions/${encodeURIComponent(sessionId)}/message`, { text: cmd })
    } catch (err) {
      dispatch({ type: 'SET_IDLE' })
      dispatch({ type: 'SET_SILENT_COMMAND', payload: false })
      dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: err.detail || err.message } })
    }
  }, [dispatch])

  const respondApproval = useCallback(async (sessionId, approvalId, approved) => {
    dispatch({ type: 'RESOLVE_APPROVAL', payload: { approvalId, approved } })
    try {
      await api('POST', `/api/sessions/${encodeURIComponent(sessionId)}/approval`, { approval_id: approvalId, approved })
    } catch (err) {
      dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: err.detail || err.message } })
    }
  }, [dispatch])

  const cancelRun = useCallback(async (sessionId) => {
    dispatch({ type: 'SET_CANCELLING' })
    try {
      await api('POST', `/api/sessions/${encodeURIComponent(sessionId)}/cancel`)
    } catch (err) {
      dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: err.detail || err.message } })
    }
  }, [dispatch])

  const shutdown = useCallback(async () => {
    dispatch({ type: 'SET_SERVER_ONLINE', payload: false })
    try {
      await api('POST', '/api/shutdown')
    } catch (err) {
      if (err.status) {
        dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: err.detail || err.message } })
      }
    }
  }, [dispatch])

  return { sendMessage, sendSilentCommand, respondApproval, cancelRun, shutdown }
}

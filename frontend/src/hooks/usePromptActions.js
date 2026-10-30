import { useCallback } from 'react'
import { api } from '../api'
import { useAppDispatch, useAppStateRef } from '../context/AppContext'
import { attachmentPayloads, promptAttachments } from '../promptActions'

// Resend, Retry and Delete for a prompt. None of them sends anything itself:
// they hand the prompt to the composer (RESTORE_DRAFT), whose own send path
// applies every existing guard (FhGenie readiness, queueing, provider checks).
export default function usePromptActions() {
  const dispatch = useAppDispatch()
  const stateRef = useAppStateRef()

  const fail = useCallback(err => {
    dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: err.detail || err.message || String(err) } })
  }, [dispatch])

  // Send the same prompt again, keeping the original in the transcript.
  const resend = useCallback(async (item) => {
    const sid = stateRef.current.currentSessionId
    if (!sid || stateRef.current.isRunning) return
    try {
      const attachments = await attachmentPayloads(promptAttachments(item))
      dispatch({ type: 'RESTORE_DRAFT', payload: { sessionId: sid, text: item.text || '', attachments, send: true } })
    } catch (err) {
      fail(err)
    }
  }, [dispatch, stateRef, fail])

  // Delete the latest unanswered prompt on the server. With `thenSend` it goes
  // straight back out (Retry); otherwise it lands in the composer so the model
  // can be changed first (Delete).
  const removeLast = useCallback(async (item, thenSend) => {
    const sid = stateRef.current.currentSessionId
    if (!sid || stateRef.current.isRunning) return
    try {
      // Fetched before deleting, so a failed download cannot lose the prompt.
      const attachments = await attachmentPayloads(promptAttachments(item))
      const result = await api('DELETE', `/api/sessions/${encodeURIComponent(sid)}/last-prompt`)
      dispatch({ type: 'DROP_LAST_TURN', payload: { removedEvents: result?.removed_events } })
      dispatch({ type: 'RESTORE_DRAFT', payload: { sessionId: sid, text: result?.text ?? item.text ?? '', attachments, send: thenSend } })
    } catch (err) {
      fail(err)
    }
  }, [dispatch, stateRef, fail])

  const retryLast = useCallback(item => removeLast(item, true), [removeLast])
  const deleteLast = useCallback(item => removeLast(item, false), [removeLast])

  return { resend, retryLast, deleteLast }
}

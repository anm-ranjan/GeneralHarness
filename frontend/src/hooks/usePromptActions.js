import { useCallback } from 'react'
import { api } from '../api'
import { useAppDispatch, useAppStateRef } from '../context/AppContext'
import { attachmentPayloads, canRemovePrompt, promptAttachments } from '../promptActions'

// Copy, Resend and Edit for a prompt. None of them sends anything itself:
// they hand the prompt to the composer (RESTORE_DRAFT), whose own send path
// applies every existing guard (FhGenie readiness, queueing, provider checks).
export default function usePromptActions() {
  const dispatch = useAppDispatch()
  const stateRef = useAppStateRef()

  const fail = useCallback(err => {
    dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: err.detail || err.message || String(err) } })
  }, [dispatch])

  // Put a finished prompt back in the message box. The transcript is untouched.
  const copyToComposer = useCallback(async (item) => {
    const sid = stateRef.current.currentSessionId
    if (!sid) return
    try {
      const attachments = await attachmentPayloads(promptAttachments(item))
      dispatch({ type: 'RESTORE_DRAFT', payload: { sessionId: sid, text: item.text || '', attachments, send: false } })
    } catch (err) {
      fail(err)
    }
  }, [dispatch, stateRef, fail])

  // Replace the latest unfinished prompt: it leaves the transcript and its text
  // goes back out (Resend) or into the message box to be changed first (Edit).
  // Where the server can roll the turn back it removes it for good. Otherwise the
  // provider keeps its own history, so the attempt is only hidden from this view.
  const replaceLast = useCallback(async (item, thenSend) => {
    const state = stateRef.current
    const sid = state.currentSessionId
    if (!sid || state.isRunning) return
    try {
      // Fetched before removing, so a failed download cannot lose the prompt.
      const attachments = await attachmentPayloads(promptAttachments(item))
      let text = item.text || ''
      let removedEvents = 0
      if (canRemovePrompt(state.currentProvider)) {
        const result = await api('DELETE', `/api/sessions/${encodeURIComponent(sid)}/last-prompt`)
        removedEvents = result?.removed_events
        text = result?.text ?? text
      }
      dispatch({ type: 'DROP_LAST_TURN', payload: { removedEvents } })
      dispatch({ type: 'RESTORE_DRAFT', payload: { sessionId: sid, text, attachments, send: thenSend } })
    } catch (err) {
      fail(err)
    }
  }, [dispatch, stateRef, fail])

  const resendLast = useCallback(item => replaceLast(item, true), [replaceLast])
  const editLast = useCallback(item => replaceLast(item, false), [replaceLast])

  return { copyToComposer, resendLast, editLast }
}

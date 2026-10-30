import { memo } from 'react'
import { useApp } from '../../context/AppContext'
import usePromptActions from '../../hooks/usePromptActions'
import { canRemovePrompt } from '../../promptActions'

const linkClass = 'underline decoration-line underline-offset-4 hover:text-accent transition-colors'

// Quiet text actions under one of your prompts. Nothing is offered while a run
// is going (sending again then would only queue another copy).
//  - Resend: any prompt; sends the same text again and keeps the original.
//  - Retry / Delete: only the latest prompt when it got no answer, and only on
//    native sessions, whose context the server rolls back for a failed turn.
//    Codex and Claude keep their own history, so a failed turn there stays
//    remembered and only Resend is offered. Retry removes the failed attempt and
//    sends it again; Delete removes it and puts the text back in the composer,
//    e.g. to switch the model first.
function PromptActions({ item, unanswered }) {
  const { state, dispatch } = useApp()
  const { resend, retryLast, deleteLast } = usePromptActions()
  if (state.isRunning) return null

  const canRemove = unanswered && canRemovePrompt(state.currentProvider)

  function confirmDelete() {
    dispatch({
      type: 'OPEN_CONFIRM',
      payload: {
        title: 'Delete this prompt?',
        message: 'It never got a reply, so it is not part of the conversation the model sees. The text goes back into the message box so you can send it again.',
        detail: 'Files it already changed are not reverted. The original is kept in the database.',
        confirmLabel: 'Delete',
        tone: 'danger',
        onConfirm: () => deleteLast(item),
      },
    })
  }

  return (
    <div className={`mb-3 -mt-2 flex gap-4 text-[12px] text-faint transition-opacity ${unanswered ? '' : 'opacity-0 group-hover/prompt:opacity-100 focus-within:opacity-100'}`}>
      {canRemove ? (
        <>
          <button type="button" className={linkClass} onClick={() => retryLast(item)} title="Delete this attempt and send the same prompt again">Retry</button>
          <button type="button" className={linkClass} onClick={confirmDelete} title="Delete this attempt and put the text back in the message box">Delete</button>
        </>
      ) : (
        <button type="button" className={linkClass} onClick={() => resend(item)} title="Send this prompt again">Resend</button>
      )}
    </div>
  )
}

export default memo(PromptActions)

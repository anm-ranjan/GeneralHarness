import { memo } from 'react'
import { useApp } from '../../context/AppContext'
import usePromptActions from '../../hooks/usePromptActions'

const linkClass = 'underline decoration-line underline-offset-4 hover:text-accent transition-colors'

// Quiet actions under one of your prompts. Nothing is offered while a run is
// going (sending again then would only queue another copy).
//  - Finished prompt: a copy icon, shown on hover, that puts the text (and
//    attachments) back in the message box.
//  - Unfinished prompt (the latest one, stopped or failed before a final
//    answer): Resend and Edit, always visible. Both replace the attempt in the
//    transcript; Resend sends the same text at once, Edit loads it into the
//    message box first, e.g. after a mistaken Enter.
function PromptActions({ item, unanswered }) {
  const { state } = useApp()
  const { copyToComposer, resendLast, editLast } = usePromptActions()
  if (state.isRunning) return null

  if (unanswered) {
    return (
      <div className="mb-3 -mt-2 flex gap-4 text-[12px] text-faint">
        <button type="button" className={linkClass} onClick={() => resendLast(item)} title="Replace this attempt and send the same prompt again">Resend</button>
        <button type="button" className={linkClass} onClick={() => editLast(item)} title="Replace this attempt and edit the prompt in the message box">Edit</button>
      </div>
    )
  }

  return (
    <div className="mb-3 -mt-2 flex text-faint opacity-0 transition-opacity group-hover/prompt:opacity-100 focus-within:opacity-100">
      <button
        type="button"
        onClick={() => copyToComposer(item)}
        title="Copy this prompt into the message box"
        aria-label="Copy prompt into the message box"
        className="p-1 -ml-1 hover:text-accent transition-colors"
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" />
          <path d="M10.5 5.5V3.5a1.5 1.5 0 0 0-1.5-1.5H3.5A1.5 1.5 0 0 0 2 3.5V9a1.5 1.5 0 0 0 1.5 1.5h2" />
        </svg>
      </button>
    </div>
  )
}

export default memo(PromptActions)

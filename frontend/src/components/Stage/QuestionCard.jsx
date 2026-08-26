import { memo, useState } from 'react'

// One clarifying question from the agent. Offered options are shortcuts, not a
// closed set: free text stays available so the user is never forced to pick a
// wrong answer to get the run moving again.
function QuestionCard({
  questionId,
  question,
  options,
  allowFreeText,
  answer,
  answered,
  submitting,
  submissionError,
  onRespond,
}) {
  const [draft, setDraft] = useState('')
  const [locallySubmitting, setLocallySubmitting] = useState(false)
  const resolved = answered !== undefined && answered !== null
  const choices = options || []
  const answerInFlight = submitting || locallySubmitting

  async function submit(text) {
    if (answerInFlight) return
    const value = (text ?? draft).trim()
    if (!value) return
    setLocallySubmitting(true)
    let accepted = false
    try {
      accepted = await onRespond(questionId, value)
    } finally {
      if (!accepted) setLocallySubmitting(false)
    }
  }

  return (
    <div className={`border rounded-md my-2 px-4 py-3 ${
      resolved ? 'border-line/40 bg-surface/40' : 'border-accent/30 bg-accent-soft'
    }`}>
      <div className="flex items-center gap-2 mb-2">
        <span className={`w-2 h-2 rounded-full ${resolved ? 'bg-muted' : 'bg-accent animate-pulse-fast'}`} />
        <span className="text-[11px] uppercase tracking-wide text-muted">
          {resolved ? 'Question' : 'Question — waiting for you'}
        </span>
      </div>

      <div className="text-[13px] text-text-bright mb-2">{question}</div>

      {resolved ? (
        <div className="text-[13px] text-muted">
          {answered ? <span className="text-text">{answer}</span> : <em>No answer — the agent continued on its own.</em>}
        </div>
      ) : (
        <>
          {choices.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-2">
              {choices.map(option => (
                <button
                  key={option}
                  onClick={() => submit(option)}
                  disabled={answerInFlight}
                  className="px-3 py-1 text-[12px] font-medium text-text border border-line/50 rounded hover:border-accent/50 hover:bg-accent-soft transition-colors disabled:opacity-40 disabled:hover:bg-transparent"
                >
                  {option}
                </button>
              ))}
            </div>
          )}
          {allowFreeText !== false && (
            <div className="flex gap-2">
              <input
                autoFocus
                value={draft}
                disabled={answerInFlight}
                onChange={e => setDraft(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    submit()
                  }
                }}
                placeholder={choices.length > 0 ? 'Or answer in your own words…' : 'Your answer…'}
                className="flex-1 px-2 py-1 text-[13px] bg-surface border border-line/50 rounded text-text placeholder:text-faint focus:outline-none focus:border-accent/50 disabled:opacity-60"
              />
              <button
                onClick={() => submit()}
                disabled={answerInFlight || !draft.trim()}
                className="px-3 py-1 text-[12px] font-medium text-accent border border-accent/30 rounded hover:bg-accent-soft transition-colors disabled:opacity-40 disabled:hover:bg-transparent"
              >
                {answerInFlight ? 'Submitting…' : 'Answer'}
              </button>
            </div>
          )}
          {submissionError && (
            <div role="alert" className="mt-2 text-[12px] text-danger">
              {submissionError} Your answer was not submitted; please try again.
            </div>
          )}
        </>
      )}
    </div>
  )
}

export default memo(QuestionCard)

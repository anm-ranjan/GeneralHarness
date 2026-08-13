import { memo, useState, useMemo, useRef, useEffect } from 'react'
import { renderMarkdown } from '../../utils'
import { useAppSelector } from '../../context/AppContext'

const selectWorkspaceRoot = state => state.currentWorkspaceRoot

// Tail of the live trace kept on screen. Reasoning runs long, and the point of
// the streaming view is "what is it doing right now", not the full history —
// the completed block below carries that.
const LIVE_TAIL_CHARS = 1200

function ThinkingBlock({ markdown, streaming = false }) {
  const [open, setOpen] = useState(streaming)
  const [rendered, setRendered] = useState(false)
  const workspaceRoot = useAppSelector(selectWorkspaceRoot)
  const tailRef = useRef(null)
  const html = useMemo(
    () => (rendered && !streaming ? renderMarkdown(markdown, workspaceRoot) : ''),
    [rendered, streaming, markdown, workspaceRoot],
  )

  // Streamed text is shown raw rather than rendered: markdown arrives in
  // fragments that do not parse mid-token, and re-rendering per delta is waste.
  const tail = streaming && open ? markdown.slice(-LIVE_TAIL_CHARS) : ''

  useEffect(() => {
    if (tailRef.current) tailRef.current.scrollTop = tailRef.current.scrollHeight
  }, [tail])

  function toggle() {
    setRendered(true)
    setOpen(o => !o)
  }

  return (
    <div className="max-w-[min(720px,85%)] self-end mb-2 animate-fade-up">
      <button
        onClick={toggle}
        className="group flex items-center gap-1.5 text-[11px] text-faint hover:text-muted transition-colors px-1 py-0.5"
        aria-expanded={open}
      >
        <span className={`inline-block transition-transform duration-200 ${open ? 'rotate-90' : ''}`}>&#9654;</span>
        <span className={open || streaming ? 'text-shimmer' : 'group-hover:text-muted'}>
          {streaming ? 'Thinking' : 'Thinking trace'}
        </span>
      </button>
      <div className={`collapse-grid ${open ? 'open' : ''}`}>
        <div>
          {streaming ? (
            <div
              ref={tailRef}
              className="bg-surface/50 border border-line/30 rounded-lg px-4 py-3 mt-1 text-[13px] leading-relaxed text-faint whitespace-pre-wrap max-h-40 overflow-y-auto"
            >
              {tail}
            </div>
          ) : (
            rendered && (
              <div
                className="markdown-body bg-surface/50 border border-line/30 rounded-lg px-4 py-3 mt-1 text-[13px] leading-relaxed text-faint"
                dangerouslySetInnerHTML={{ __html: html }}
              />
            )
          )}
        </div>
      </div>
    </div>
  )
}

export default memo(ThinkingBlock)

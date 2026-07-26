import { memo, useState, useMemo } from 'react'
import { renderMarkdown } from '../../utils'
import { useAppSelector } from '../../context/AppContext'

const selectWorkspaceRoot = state => state.currentWorkspaceRoot

function ThinkingBlock({ markdown }) {
  const [open, setOpen] = useState(false)
  const [rendered, setRendered] = useState(false)
  const workspaceRoot = useAppSelector(selectWorkspaceRoot)
  const html = useMemo(
    () => (rendered ? renderMarkdown(markdown, workspaceRoot) : ''),
    [rendered, markdown, workspaceRoot],
  )

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
        <span className={open ? 'text-shimmer' : 'group-hover:text-muted'}>Thinking trace</span>
      </button>
      <div className={`collapse-grid ${open ? 'open' : ''}`}>
        <div>
          {rendered && (
            <div
              className="markdown-body bg-surface/50 border border-line/30 rounded-lg px-4 py-3 mt-1 text-[13px] leading-relaxed text-faint"
              dangerouslySetInnerHTML={{ __html: html }}
            />
          )}
        </div>
      </div>
    </div>
  )
}

export default memo(ThinkingBlock)

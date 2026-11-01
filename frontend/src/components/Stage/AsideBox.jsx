import { memo, useMemo } from 'react'
import { renderMarkdown } from '../../utils'
import { useAppSelector } from '../../context/AppContext'

const selectWorkspaceRoot = state => state.currentWorkspaceRoot

// An /aside: a side question answered from a copy of the context so far.
// Indented from the left and boxed, so it reads as outside the main thread,
// which never sees it.
function AsideBox({ question, answer, error, pending }) {
  const workspaceRoot = useAppSelector(selectWorkspaceRoot)
  const html = useMemo(() => (answer ? renderMarkdown(answer, workspaceRoot) : ''), [answer, workspaceRoot])
  return (
    <aside
      className="mb-5 ml-auto w-full max-w-[85%] rounded-lg border border-line bg-surface/60 px-4 py-3 animate-fade-up"
      aria-label="Aside"
    >
      <p className="mb-1 text-[11.5px] uppercase tracking-wide text-faint">Aside · not part of the thread</p>
      <p className="whitespace-pre-wrap [overflow-wrap:anywhere] text-[14.5px] font-medium text-text-bright">{question}</p>
      <div className="mt-2 border-t border-line/70 pt-2">
        {pending ? (
          <p className="text-[13.5px] text-muted"><span className="text-shimmer">Answering from the conversation so far…</span></p>
        ) : error ? (
          <p className="text-[13.5px] text-danger" role="alert">{error}</p>
        ) : (
          <div className="markdown-body max-w-full overflow-x-auto text-[14.5px] leading-relaxed" dangerouslySetInnerHTML={{ __html: html }} />
        )}
      </div>
    </aside>
  )
}

export default memo(AsideBox)

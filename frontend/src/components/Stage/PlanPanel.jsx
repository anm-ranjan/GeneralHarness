import { useApp } from '../../context/AppContext'

const STATUS_STYLE = {
  completed: { dot: 'bg-accent', text: 'text-faint line-through' },
  in_progress: { dot: 'bg-accent animate-pulse-slow', text: 'text-shimmer font-medium' },
  pending: { dot: 'border border-muted', text: 'text-muted' },
}

export default function PlanPanel() {
  const { state } = useApp()
  const items = state.plan || []
  if (!state.currentSessionId || items.length === 0) return null

  const done = items.filter((item) => item.status === 'completed').length
  // A finished plan has nothing left to track — drop the panel instead of
  // leaving a fully struck-through list on screen.
  if (done === items.length) return null

  return (
    <div className="border-t border-line px-7 py-2 bg-bg/95">
      <div className="max-w-[820px] mx-auto rounded-md bg-surface border border-line px-3 py-2 text-[13px] animate-scale-in">
        <div className="flex items-center gap-2 mb-1.5 text-[12px]">
          <span className="font-medium text-muted">Plan</span>
          <span className="text-faint">{done}/{items.length}</span>
        </div>
        <ul className="flex flex-col gap-1">
          {items.map((item, i) => {
            const style = STATUS_STYLE[item.status] || STATUS_STYLE.pending
            return (
              <li key={i} className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full shrink-0 ${style.dot}`} />
                <span className={`truncate ${style.text}`}>{item.content}</span>
              </li>
            )
          })}
        </ul>
      </div>
    </div>
  )
}

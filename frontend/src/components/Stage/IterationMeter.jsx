import useIterationMeter from '../../hooks/useIterationMeter'

export default function IterationMeter({ n, max }) {
  const { formatted } = useIterationMeter(true)
  const pct = max ? Math.min(100, (n / max) * 100) : 0

  return (
    <div className="flex items-center gap-3 px-3 py-2 my-1 rounded-md bg-surface border border-line text-[13px] animate-scale-in">
      <span className="w-2 h-2 rounded-full bg-accent animate-pulse-slow" />
      <span className="text-shimmer font-medium">Thinking…</span>
      {max > 0 && (
        <div className="flex-1 h-1.5 bg-black/30 rounded-full overflow-hidden">
          <div
            className="h-full bg-accent rounded-full transition-all"
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
      <span className="text-faint text-[12px]">
        {n}{max ? `/${max}` : ''} · {formatted}
      </span>
    </div>
  )
}

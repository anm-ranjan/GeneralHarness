import { useState, useEffect } from 'react'

export default function CodexRunningBar() {
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    const start = Date.now()
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 1000)
    return () => clearInterval(id)
  }, [])

  const mins = Math.floor(elapsed / 60)
  const secs = elapsed % 60
  const fmt = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`

  return (
    <div className="flex items-center gap-3 px-3 py-2 my-1 rounded-md border border-accent animate-pulse-border text-[13px]">
      <span className="font-semibold text-accent">Processing with Codex</span>
      <div className="flex gap-0.5 ml-0.5">
        <span className="w-1 h-1 rounded-full bg-accent animate-dot-bounce" />
        <span className="w-1 h-1 rounded-full bg-accent animate-dot-bounce" style={{ animationDelay: '0.2s' }} />
        <span className="w-1 h-1 rounded-full bg-accent animate-dot-bounce" style={{ animationDelay: '0.4s' }} />
      </div>
      <span className="text-muted ml-auto">{fmt}</span>
    </div>
  )
}

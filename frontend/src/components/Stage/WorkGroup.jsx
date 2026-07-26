import { memo, useState, useEffect, useRef } from 'react'
import ToolCardCompact from './ToolCardCompact'

function WorkGroup({ tools, startTime, finalized }) {
  const [expanded, setExpanded] = useState(!finalized)
  const [elapsed, setElapsed] = useState('')
  const [completedAt, setCompletedAt] = useState(null)
  const timerRef = useRef(null)
  const hasRunningTools = tools.some(tool => tool.status === 'running')
  const timingActive = !finalized && hasRunningTools

  useEffect(() => {
    if (timingActive) {
      setCompletedAt(null)
    }
  }, [timingActive])

  useEffect(() => {
    function update() {
      if (!startTime) {
        setElapsed('')
        return
      }
      const end = timingActive ? Date.now() : (completedAt || Date.now())
      const secs = Math.max(0, Math.floor((end - startTime) / 1000))
      const mins = Math.floor(secs / 60)
      setElapsed(mins > 0 ? `${mins}m ${secs % 60}s` : `${secs}s`)
    }

    if (!timingActive && !completedAt) {
      setCompletedAt(Date.now())
      return
    }

    update()
    if (!timingActive) {
      if (timerRef.current) clearInterval(timerRef.current)
      if (finalized) setExpanded(false)
      return
    }

    timerRef.current = setInterval(update, 1000)
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [timingActive, finalized, startTime, completedAt])

  useEffect(() => {
    if (finalized) setExpanded(false)
  }, [finalized])

  const groupComplete = finalized || !hasRunningTools

  return (
    <div className="border border-line rounded-md my-2 overflow-hidden">
      <div
        className="flex items-center gap-2 px-3 py-1.5 cursor-pointer hover:bg-surface transition-colors text-[13px] text-muted"
        onClick={() => setExpanded(!expanded)}
      >
        <span className={`text-[10px] transition-transform ${expanded ? 'rotate-90' : ''}`}>▶</span>
        <span>
          {groupComplete
            ? `Worked for ${elapsed} · ${tools.length} tool${tools.length !== 1 ? 's' : ''}${tools.some(tool => tool.status === 'failed') ? ' · attention needed' : ''}`
            : `Working ${elapsed} · ${tools.length} tool${tools.length !== 1 ? 's' : ''}…`
          }
        </span>
        {!groupComplete && <span className="w-1.5 h-1.5 rounded-full bg-ok animate-pulse-slow ml-1" />}
      </div>
      {expanded && (
        <div className="border-t border-line p-1.5">
          {tools.map((tool, i) => (
            <ToolCardCompact key={i} {...tool} />
          ))}
        </div>
      )}
    </div>
  )
}

// Transcript items are immutable once appended; memo keeps a long stage
// from re-rendering on every streamed delta.
export default memo(WorkGroup)

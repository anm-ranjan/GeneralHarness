import { useState, useEffect, useRef } from 'react'

export default function useIterationMeter(active) {
  const [elapsed, setElapsed] = useState(0)
  const startRef = useRef(null)

  useEffect(() => {
    if (!active) {
      setElapsed(0)
      startRef.current = null
      return
    }
    startRef.current = Date.now()
    const id = setInterval(() => {
      if (startRef.current) setElapsed(Date.now() - startRef.current)
    }, 1000)
    return () => clearInterval(id)
  }, [active])

  const secs = Math.floor(elapsed / 1000)
  const mins = Math.floor(secs / 60)
  const formatted = mins > 0 ? `${mins}m ${secs % 60}s` : `${secs}s`

  return { elapsed, formatted }
}

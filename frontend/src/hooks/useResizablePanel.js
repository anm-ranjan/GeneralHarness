import { useState, useCallback, useEffect, useRef } from 'react'

export default function useResizablePanel(initialWidth = 300, min = 200, max = 600, side = 'left') {
  const [width, setWidth] = useState(initialWidth)
  const dragging = useRef(false)

  const onMouseDown = useCallback((e) => {
    e.preventDefault()
    dragging.current = true
  }, [])

  // Keep the width inside the current bounds when they change (the workspace
  // panel raises its ceiling while the file editor is open).
  useEffect(() => {
    setWidth(current => Math.min(max, Math.max(min, current)))
  }, [min, max])

  useEffect(() => {
    const onMouseMove = (e) => {
      if (!dragging.current) return
      const raw = side === 'right' ? window.innerWidth - e.clientX : e.clientX
      setWidth(Math.min(max, Math.max(min, raw)))
    }
    const onMouseUp = () => {
      dragging.current = false
    }
    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
    return () => {
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
    }
  }, [min, max])

  return { width, handleProps: { onMouseDown } }
}

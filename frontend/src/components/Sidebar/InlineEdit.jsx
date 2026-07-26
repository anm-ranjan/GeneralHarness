import { useState, useRef, useEffect } from 'react'

export default function InlineEdit({ value, onSave, onCancel, className = '' }) {
  const [text, setText] = useState(value)
  const inputRef = useRef(null)
  const finishedRef = useRef(false)

  useEffect(() => {
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [])

  function finish(saveChanges) {
    if (finishedRef.current) return
    finishedRef.current = true

    const trimmed = text.trim()
    if (saveChanges && trimmed && trimmed !== value) onSave(trimmed)
    else onCancel()
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter') {
      e.preventDefault()
      finish(true)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      finishedRef.current = true
      onCancel()
    }
  }

  return (
    <input
      ref={inputRef}
      type="text"
      value={text}
      onChange={e => setText(e.target.value)}
      onKeyDown={handleKeyDown}
      onBlur={() => finish(true)}
      className={`bg-surface text-text-default border border-line rounded-sm px-1.5 py-0.5 text-[13px] outline-none focus:border-accent w-full ${className}`}
    />
  )
}

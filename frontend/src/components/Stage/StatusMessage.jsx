import { memo } from 'react'

function StatusMessage({ text }) {
  return (
    <div className="text-[13px] text-faint italic py-1 px-2">
      {text}
    </div>
  )
}

// Transcript items are immutable once appended; memo keeps a long stage
// from re-rendering on every streamed delta.
export default memo(StatusMessage)

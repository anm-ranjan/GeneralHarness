import { memo } from 'react'

function ErrorMessage({ text }) {
  return (
    <div className="border-l-3 border-danger bg-danger-soft rounded-r-md px-4 py-2 text-[13px] text-danger my-1">
      {text}
    </div>
  )
}

// Transcript items are immutable once appended; memo keeps a long stage
// from re-rendering on every streamed delta.
export default memo(ErrorMessage)

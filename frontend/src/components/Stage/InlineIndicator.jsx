import { memo } from 'react'

function InlineIndicator({ text }) {
  return (
    <div className="inline-flex items-center bg-accent-soft text-accent text-[12px] rounded-full px-3 py-0.5 my-1">
      {text}
    </div>
  )
}

// Transcript items are immutable once appended; memo keeps a long stage
// from re-rendering on every streamed delta.
export default memo(InlineIndicator)

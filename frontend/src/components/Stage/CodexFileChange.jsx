import { memo } from 'react'

function CodexFileChange({ path, status }) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 my-0.5 rounded-md border border-line text-[13px]">
      <span>📄</span>
      <code className="font-mono text-[12px] text-text-bright truncate flex-1">{path}</code>
      <span className={`text-[11px] ${status === 'created' || status === 'modified' ? 'text-ok' : 'text-muted'}`}>
        {status}
      </span>
    </div>
  )
}

// Transcript items are immutable once appended; memo keeps a long stage
// from re-rendering on every streamed delta.
export default memo(CodexFileChange)

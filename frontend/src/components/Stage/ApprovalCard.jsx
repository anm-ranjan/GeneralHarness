import { memo } from 'react'
import { truncate } from '../../utils'

function ApprovalCard({ approvalId, toolName, sourceAgent, argsJson, diffPreview, resolved, onRespond }) {
  return (
    <div className={`border rounded-md my-2 px-4 py-3 ${
      resolved === 'approved' ? 'border-ok/30 bg-ok-soft'
        : resolved === 'denied' ? 'border-danger/30 bg-danger-soft'
        : 'border-warn/30 bg-warn-soft'
    }`}>
      <div className="flex items-center gap-2 mb-2">
        <span className={`w-2 h-2 rounded-full ${
          resolved ? (resolved === 'approved' ? 'bg-ok' : 'bg-danger') : 'bg-warn animate-pulse-fast'
        }`} />
        <span className="text-[13px] font-medium text-text-bright">
          Approval: {toolName}
        </span>
        {sourceAgent && <span className="text-[11px] text-muted">{sourceAgent}</span>}
        {resolved && (
          <span className={`text-[11px] ml-auto ${resolved === 'approved' ? 'text-ok' : 'text-danger'}`}>
            {resolved}
          </span>
        )}
      </div>

      {argsJson && (
        <pre className="text-[12px] font-mono text-muted bg-black/20 rounded px-2 py-1 mb-2 overflow-x-auto whitespace-pre-wrap">
          {truncate(argsJson, 1000)}
        </pre>
      )}

      {diffPreview && (
        <pre className="text-[12px] font-mono text-muted bg-black/20 rounded px-2 py-1 mb-2 overflow-x-auto whitespace-pre-wrap">
          {truncate(diffPreview, 2000)}
        </pre>
      )}

      {!resolved && (
        <div className="flex gap-2 mt-2">
          <button
            onClick={() => onRespond(approvalId, true)}
            className="px-3 py-1 text-[12px] font-medium text-ok border border-ok/30 rounded hover:bg-ok-soft transition-colors"
          >
            Approve
          </button>
          <button
            onClick={() => onRespond(approvalId, false)}
            className="px-3 py-1 text-[12px] font-medium text-danger border border-danger/30 rounded hover:bg-danger-soft transition-colors"
          >
            Deny
          </button>
        </div>
      )}
    </div>
  )
}

// Transcript items are immutable once appended; memo keeps a long stage
// from re-rendering on every streamed delta.
export default memo(ApprovalCard)

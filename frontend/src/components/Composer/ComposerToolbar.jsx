import { useApp } from '../../context/AppContext'
import useApi from '../../hooks/useApi'
import { approvalLabel, nextApprovalMode } from '../../approvalMode'
import ModelSelector from './ModelSelector'

// Settings under the reply box, written as words. verbose and clear-context
// are slash commands only (/verbose, /clear).
export default function ComposerToolbar({ className = '' }) {
  const { state } = useApp()
  const { sendSilentCommand } = useApi()

  const sid = state.currentSessionId
  const approval = approvalLabel(state.approvalMode)
  const context = state.contextPercent > 0 ? `${Math.round(state.contextPercent)}% context` : ''
  const contextTitle = [state.contextLabel, state.throughput].filter(Boolean).join(' · ') || 'Context used'

  return (
    <div className={`flex items-center gap-x-4 gap-y-1 text-[12.5px] text-muted flex-wrap ${className}`}>
      <ModelSelector />

      {approval && (
        <button
          onClick={() => sendSilentCommand(sid, `/approve ${nextApprovalMode(state.approvalMode)}`)}
          className="hover:text-accent transition-colors"
          title="Change approval mode"
        >
          {approval}
        </button>
      )}

      {context && <span className="text-faint" title={contextTitle}>{context}</span>}
    </div>
  )
}

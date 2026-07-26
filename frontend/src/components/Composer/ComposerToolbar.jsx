import { useApp } from '../../context/AppContext'
import useApi from '../../hooks/useApi'

export default function ComposerToolbar({ className = '' }) {
  const { state } = useApp()
  const { sendSilentCommand } = useApi()

  const sid = state.currentSessionId
  const indicator = state.isRunning ? 'running' : 'idle'
  const modelLabel = state.currentProvider === 'codex-app-server'
    ? 'Codex App Server'
    : state.currentProvider === 'claude-agent'
      ? 'Claude'
      : (state.model || 'Native')

  function toggleApproval() {
    const next = state.approvalMode === 'auto_approve' ? 'shell_only' : 'auto_approve'
    sendSilentCommand(sid, `/approve ${next}`)
  }

  function toggleVerbose() {
    sendSilentCommand(sid, '/verbose')
  }

  function clearContext() {
    sendSilentCommand(sid, '/clear')
  }

  // Only offer providers the backend can actually accept right now, so the
  // cycle never lands on a switch /model would reject.
  const providerCycle = [
    ...(state.nativeEnabled ? [{ id: 'native', command: 'native' }] : []),
    ...(state.codexAppServerEnabled ? [{ id: 'codex-app-server', command: 'codex' }] : []),
    ...(state.claudeAgentEnabled ? [{ id: 'claude-agent', command: 'claude' }] : []),
  ]
  const canSwitchModel = providerCycle.length > 1

  function toggleModel() {
    if (!canSwitchModel) return
    const current = providerCycle.findIndex((entry) => entry.id === state.currentProvider)
    const next = providerCycle[(current + 1) % providerCycle.length]
    sendSilentCommand(sid, `/model ${next.command}`)
  }

  return (
    <div className={`flex items-center gap-x-4 gap-y-2 text-[12px] text-faint flex-wrap ${className}`}>
      <div className="flex items-center gap-1.5">
        <span className={`w-1.5 h-1.5 rounded-full ${
          indicator === 'running' ? 'bg-ok animate-pulse-slow' : 'bg-faint'
        }`} />
        <span>{indicator}</span>
      </div>

      <button
        onClick={toggleModel}
        disabled={!canSwitchModel || state.isRunning}
        className="hover:text-accent transition-colors disabled:opacity-40"
        title={canSwitchModel ? 'Switch provider' : 'No other provider is available'}
      >
        {modelLabel}
      </button>

      <button onClick={toggleApproval} className="hover:text-accent transition-colors" title="Toggle approval mode">
        {state.approvalMode || '—'}
      </button>

      <button onClick={toggleVerbose} className="hover:text-accent transition-colors" title="Toggle verbose">
        verbose: {state.verbose ? 'on' : 'off'}
      </button>

      <button onClick={clearContext} disabled={state.isRunning} className="hover:text-accent transition-colors disabled:opacity-40" title="Clear context">
        clear ctx
      </button>

      <div className="flex items-center gap-1.5 sm:ml-auto">
        <div className="w-16 h-1 bg-black/30 rounded-full overflow-hidden">
          <div
            className="h-full bg-accent rounded-full transition-all"
            style={{ width: `${state.contextPercent}%` }}
          />
        </div>
        <span>{state.contextLabel || '—'}</span>
      </div>

      {state.throughput && (
        <span className="text-[11px]">{state.throughput}</span>
      )}
    </div>
  )
}

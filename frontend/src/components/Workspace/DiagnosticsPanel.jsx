import { useApp } from '../../context/AppContext'

export default function DiagnosticsPanel() {
  const { state } = useApp()
  const meta = state.currentMeta
  const counts = state.stageItems.reduce((acc, item) => {
    acc[item.type] = (acc[item.type] || 0) + 1
    return acc
  }, {})
  const lastError = [...state.stageItems].reverse().find(item => item.type === 'error')
  const lastStatus = [...state.stageItems].reverse().find(item => item.type === 'status' || item.type === 'indicator')
  const pendingApprovals = state.stageItems.filter(item => item.type === 'approval' && !item.resolved).length
  const runningTools = state.stageItems
    .filter(item => item.type === 'work_group')
    .flatMap(item => item.tools || [])
    .filter(tool => tool.status === 'running')

  return (
    <div className="p-3 space-y-3">
      <Section title="Run">
        <Row label="State" value={state.isCancelling ? 'cancelling' : state.isRunning ? 'running' : 'idle'} accent={state.isRunning} />
        <Row label="Provider" value={state.currentProvider || '—'} />
        <Row label="Server" value={state.serverOnline ? 'online' : 'offline'} accent={state.serverOnline} />
        <Row label="Socket" value={state.wsConnected ? 'connected' : 'disconnected'} accent={state.wsConnected} />
        <Row label="Reconnects" value={state.wsReconnects} />
        <Row label="Pending approvals" value={pendingApprovals} accent={pendingApprovals > 0} />
      </Section>

      <Section title="Context">
        <Row label="Usage" value={state.contextTokens || state.contextLabel || '—'} />
        <Row label="Throughput" value={state.throughput || '—'} />
        <Row label="Iteration" value={state.iterationN !== null ? `${state.iterationN}${state.iterationMax ? `/${state.iterationMax}` : ''}` : '—'} />
      </Section>

      <Section title="Replay">
        <Row label="Status" value={state.isReplaying ? 'replaying' : 'settled'} accent={state.isReplaying} />
        <Row label="Window events" value={state.replayedEventCount} />
        <Row label="Stage items" value={state.stageItems.length} />
        <Row label="Touched files" value={state.touchedFiles.length} />
      </Section>

      <Section title="Thread">
        <Row label="ID" value={meta?.id || '—'} mono />
        <Row label="Messages" value={meta?.message_count ?? '—'} />
        <Row label="Workspace" value={state.currentWorkspaceRoot || '—'} mono />
      </Section>

      <Section title="Signals">
        <Row label="Running tools" value={runningTools.map(tool => tool.name).join(', ') || '—'} />
        <Row label="Last status" value={lastStatus?.text || '—'} />
        <Row label="Last error" value={lastError?.text || '—'} warn={!!lastError} />
      </Section>

      <Section title="Event Mix">
        {Object.entries(counts).length ? Object.entries(counts).map(([type, count]) => (
          <Row key={type} label={type} value={count} />
        )) : <div className="text-[12px] text-muted">No events rendered yet.</div>}
      </Section>
    </div>
  )
}

function Section({ title, children }) {
  return (
    <section className="border border-line rounded-md overflow-hidden">
      <h3 className="px-3 py-2 text-[11px] uppercase tracking-wide font-semibold text-faint border-b border-line bg-black/20">{title}</h3>
      <div className="divide-y divide-line/60">{children}</div>
    </section>
  )
}

function Row({ label, value, accent = false, warn = false, mono = false }) {
  return (
    <div className="grid grid-cols-[110px_1fr] gap-2 px-3 py-2 text-[12px]">
      <span className="text-faint">{label}</span>
      <span className={`${warn ? 'text-danger' : accent ? 'text-accent' : 'text-text-default'} ${mono ? 'font-mono' : ''} min-w-0 break-words`}>
        {String(value)}
      </span>
    </div>
  )
}

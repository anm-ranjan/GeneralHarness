import { useEffect, useState } from 'react'
import { api, wsUrl } from '../../api'
import { useApp } from '../../context/AppContext'
import useSelectSession from '../../hooks/useSelectSession'
import { foldAgentRun } from '../../agentRunState'
import { errorLines, itemCounts, runDisplayStatus } from '../../agentUi'
import AgentRunStatus, { ProgressBar } from './AgentRunStatus'
import StatusPill from './StatusPill'
import ApprovalCard from '../Stage/ApprovalCard'

// A run started from this session with @agent. Display-only: nothing here is
// model context for the parent. Live while the run is active; a later
// follow-up in the run makes it active again and the card reconnects.
export default function AgentRunCard({ item }) {
  const { state, dispatch } = useApp(), selectSession = useSelectSession()
  const runId = item.run_session_id
  const liveState = state.runStates[runId]
  const session = state.sessionsById[runId]
  const active = !!liveState && liveState !== 'idle'
  const [run, setRun] = useState({ items: [], status: 'loading', approvals: {} })
  const [error, setError] = useState('')
  const [open, setOpen] = useState(false)

  useEffect(() => {
    let alive = true, socket = null, timer = null
    const seen = new Set()
    function fold(evt) {
      if (!alive || (evt.id && seen.has(evt.id))) return
      if (evt.id) seen.add(evt.id)
      const d = evt.data || {}
      setRun(old => {
        if (evt.type.startsWith('agent_run_') && evt.type !== 'agent_run_link') return { ...old, items: foldAgentRun(old.items, evt.type, d) }
        if (evt.type === 'approval_required') return { ...old, approvals: { ...old.approvals, [d.approval_id]: d } }
        if (evt.type === 'approval_resolved') { const approvals = { ...old.approvals }; delete approvals[d.approval_id]; return { ...old, approvals } }
        if (evt.type === 'run_finished') return { ...old, status: 'idle', approvals: {} }
        return old
      })
    }
    async function load() {
      try {
        const res = await api('GET', `/api/sessions/${runId}?events=0`)
        if (!alive) return
        setRun(old => ({ ...old, status: res.meta.status }))
        let offset = 0
        do {
          const page = await api('GET', `/api/sessions/${runId}/events?offset=${offset}&limit=1000`)
          if (!alive) return
          page.events.forEach(fold); offset += page.count
          if (!page.count || offset >= page.total) break
        } while (alive)
        if (res.meta.status === 'running') connect()
      } catch (e) { if (alive) setError(errorLines(e).join(' ')) }
    }
    function connect() {
      if (!alive) return
      socket = new WebSocket(wsUrl(runId))
      socket.onmessage = e => {
        const evt = JSON.parse(e.data)
        if (evt.type === 'session_loaded') {
          evt.data.events.forEach(fold)
          setRun(old => ({ ...old, status: evt.data.meta.status, approvals: evt.data.meta.status === 'idle' ? {} : old.approvals }))
          if (evt.data.meta.status === 'idle') { socket.onclose = null; socket.close() }
        } else { fold(evt); if (evt.type === 'run_finished') { socket.onclose = null; socket.close() } }
      }
      socket.onclose = () => { if (alive) timer = setTimeout(load, 3000) }
    }
    load()
    return () => { alive = false; clearTimeout(timer); if (socket) { socket.onclose = null; socket.close() } }
    // Re-run when the run becomes active again (follow-up turn in the run).
  }, [runId, active])

  async function action(path, body) {
    try { setError(''); await api('POST', `/api/sessions/${runId}/${path}`, body) }
    catch (e) { setError(errorLines(e).join(' ')) }
  }
  const latest = run.items[run.items.length - 1]
  const result = latest?.result
  const running = active || run.status === 'running'
  const status = run.status === 'loading' ? 'loading' : runDisplayStatus(liveState || (run.status === 'running' ? 'running' : ''), session, result)
  const approvals = running ? Object.values(run.approvals) : []
  const summary = result?.summary || latest?.progress?.label || (running ? 'Working…' : 'No script output yet')
  const counts = result ? itemCounts(result) : latest?.progress ? `${latest.progress.done} of ${latest.progress.total ?? '?'}` : ''

  return (
    <section className="my-3 rounded-xl border border-line bg-surface/40 p-4 text-[13px] text-text-default">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[12px] text-faint">Agent run</p>
          <p className="font-medium text-text-bright">{item.agent} · Run {item.run_number}</p>
        </div>
        <StatusPill status={status} />
      </div>
      {item.request_text && <p className="mt-2 line-clamp-2 whitespace-pre-wrap text-muted" title={item.request_text}>{item.request_text}</p>}
      <div className="mt-3 space-y-2">
        <div className="flex items-baseline justify-between gap-3">
          <span className="min-w-0 truncate">{summary}</span>
          {counts && <span className="shrink-0 text-[12px] text-faint">{counts}</span>}
        </div>
        {running && latest?.progress && !result && <ProgressBar done={latest.progress.done} total={latest.progress.total} />}
      </div>
      {error && <p role="alert" className="mt-2 text-danger">{error}</p>}
      {approvals.map(a => <ApprovalCard key={a.approval_id} approvalId={a.approval_id} toolName={a.tool_name} argsJson={a.args_json} onRespond={(approval_id, approved) => action('approval', { approval_id, approved })} />)}
      {open && run.items.map(i => <AgentRunStatus key={i.toolCallId} item={i} compact live={running} />)}
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12.5px] text-accent">
        <button type="button" className="hover:underline" onClick={async () => { await selectSession(runId); dispatch({ type: 'SET_SIDEBAR_VIEW', payload: 'agents' }) }}>Open run</button>
        {run.items.some(i => Object.keys(i.items || {}).length) && <button type="button" className="hover:underline" onClick={() => setOpen(v => !v)}>{open ? 'Hide items' : 'Show items'}</button>}
        {running && <button type="button" className="text-danger hover:underline" onClick={() => action('cancel')}>Stop</button>}
        {!running && result && <button type="button" className="hover:underline" onClick={() => dispatch({ type: 'RESTORE_DRAFT', payload: { sessionId: state.currentSessionId, text: `${item.agent} · Run ${item.run_number}: ${result.summary}` } })}>Add result to chat</button>}
      </div>
    </section>
  )
}

import { useState } from 'react'
import { api } from '../../api'
import { useApp } from '../../context/AppContext'
import { errorLines } from '../../agentUi'
import StatusPill from './StatusPill'

// A /create_agent draft: review files, dry run, revise, then save or discard.
export default function AgentDraftCard({ draft }) {
  const { dispatch } = useApp()
  const [file, setFile] = useState('AGENT.md')
  const [error, setError] = useState('')
  const [feedback, setFeedback] = useState('')
  const [busy, setBusy] = useState('')
  const [saved, setSaved] = useState(false)
  const [discarded, setDiscarded] = useState(false)
  const [replace, setReplace] = useState(false)

  async function action(name, body = {}) {
    setBusy(name); setError('')
    try {
      const data = await api(name === 'discard' ? 'DELETE' : 'POST', `/api/agent-drafts/${draft.draft_id}${name === 'discard' ? '' : '/' + name}`, body)
      if (name === 'save') { setSaved(true); dispatch({ type: 'SET_AGENT_PROFILES', payload: await api('GET', '/api/agents') }) }
      if (name === 'revise') { setFeedback(''); dispatch({ type: 'UPSERT_AGENT_DRAFT', payload: data }) }
      if (name === 'discard') setDiscarded(true)
    } catch (e) { setError(errorLines(e).join(' ')) } finally { setBusy('') }
  }

  const invalid = draft.errors.length > 0
  const status = discarded ? ['idle', 'Discarded'] : saved ? ['completed', 'Saved'] : invalid ? ['failed', 'Needs fixes'] : ['ready', 'Ready to save']
  const button = 'rounded-lg border border-line px-3 py-1.5 text-[12.5px] text-text-default transition-colors hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-40'

  return (
    <section className="my-3 space-y-3 rounded-xl border border-line bg-surface/40 p-4 text-[13px] text-text-default">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[12px] text-faint">Agent draft{draft.replaces ? ' · replaces an existing agent' : ''}</p>
          <p className="font-medium text-text-bright">{draft.name}</p>
        </div>
        <StatusPill status={status[0]} label={status[1]} />
      </div>
      {!discarded && <>
        <div role="tablist" aria-label="Draft files" className="flex flex-wrap gap-1 border-b border-line">
          {Object.keys(draft.files).map(f => (
            <button role="tab" aria-selected={file === f} key={f} onClick={() => setFile(f)}
              className={`-mb-px border-b-2 px-2.5 py-1.5 font-mono text-[12px] ${file === f ? 'border-accent text-accent' : 'border-transparent text-faint hover:text-text-default'}`}>{f}</button>
          ))}
        </div>
        <pre className="max-h-96 overflow-auto rounded-lg bg-[var(--color-code-bg)] p-3 text-[12px]">{draft.files[file] || ''}</pre>
        {invalid && (
          <div role="alert" className="rounded-lg border border-danger/40 bg-danger-soft p-3 text-danger">
            <p className="font-medium">Fix these before saving</p>
            <ul className="mt-1 list-disc pl-5">{draft.errors.map((e, i) => <li key={i}>{e}</li>)}</ul>
          </div>
        )}
        {error && <p role="alert" className="text-danger">{error}</p>}
        {draft.output && (
          <div>
            <p className="mb-1 text-[12px] text-faint">Dry-run output</p>
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-[var(--color-code-bg)] p-3 text-[12px]">{draft.output}</pre>
          </div>
        )}
        {draft.replaces && !saved && (
          <label className="flex items-center gap-2 text-[12.5px]">
            <input type="checkbox" className="accent-[var(--color-accent)]" checked={replace} onChange={e => setReplace(e.target.checked)} />
            Replace the existing agent (the current files are backed up first)
          </label>
        )}
        <div className="flex flex-wrap gap-2">
          <button type="button" className="rounded-lg bg-accent px-3 py-1.5 text-[12.5px] font-medium text-bg hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
            disabled={!!busy || saved || invalid || (draft.replaces && !replace)} onClick={() => action('save', { replace })}>{busy === 'save' ? 'Saving…' : 'Save agent'}</button>
          {draft.dry_run && <button type="button" className={button} disabled={!!busy || invalid} onClick={() => action('dry-run')}>{busy === 'dry-run' ? 'Starting…' : 'Dry run'}</button>}
          <button type="button" className={`${button} hover:border-danger hover:text-danger`} disabled={!!busy} onClick={() => action('discard')}>Discard</button>
        </div>
        {!saved && (
          <div className="flex gap-2">
            <input aria-label="Revision feedback" className="min-w-0 flex-1 rounded-lg border border-line bg-transparent px-3 py-1.5 outline-none focus:border-accent" value={feedback} onChange={e => setFeedback(e.target.value)} placeholder="Describe a change, e.g. “also accept a list of folders”" />
            <button type="button" className={button} disabled={!!busy || !feedback.trim()} onClick={() => action('revise', { feedback })}>{busy === 'revise' ? 'Revising…' : 'Revise'}</button>
          </div>
        )}
      </>}
    </section>
  )
}

import { useEffect, useMemo, useState } from 'react'
import { useApp } from '../../context/AppContext'
import { api, getAgent, startAgentRun } from '../../api'
import useSelectSession from '../../hooks/useSelectSession'
import useSessionTree from '../../hooks/useSessionTree'
import { initialValues, validate, toRequest } from '../../agentForm'
import { errorLines, formChanged, splitFieldErrors } from '../../agentUi'
import FileEditor from '../Workspace/FileEditor'
import { isDesktopApp } from '../../desktop'
import AgentForm from './AgentForm'
import RecentAgentRuns from './RecentAgentRuns'
import StatusPill from './StatusPill'

export default function AgentHome({ name }) {
  const { state, dispatch } = useApp()
  const tree = useSessionTree(), selectSession = useSelectSession()
  const [profile, setProfile] = useState(null)
  const [loadError, setLoadError] = useState('')
  const [values, setValues] = useState({})
  const [text, setText] = useState('')
  const [errors, setErrors] = useState({ fields: {}, general: [] })
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(null)
  const [checks, setChecks] = useState(null)

  useEffect(() => {
    let active = true
    setChecks(null)
    api('GET', `/api/agents/${encodeURIComponent(name)}/preflight`)
      .then(c => { if (active) setChecks(c) })
      .catch(e => { if (active) setChecks([{ ok: false, message: errorLines(e).join(' ') }]) })
    return () => { active = false }
  }, [name])

  useEffect(() => {
    let active = true
    setProfile(null); setLoadError(''); setText(''); setErrors({ fields: {}, general: [] })
    getAgent(name)
      .then(p => { if (active) { setProfile(p); setValues(initialValues(p.params)) } })
      .catch(e => { if (active) setLoadError(errorLines(e).join(' ')) })
    return () => { active = false }
  }, [name])

  const initial = useMemo(() => profile ? toRequest(profile.params, initialValues(profile.params)) : {}, [profile])
  const runs = useMemo(() => Object.values(state.sessionsById)
    .filter(s => s.kind === 'agent_run' && s.agent === name)
    .sort((a, b) => new Date(b.created_at) - new Date(a.created_at)), [state.sessionsById, name])

  if (loadError) return <section className="flex-1 overflow-y-auto p-8"><div className="mx-auto max-w-3xl"><p className="text-[13px] text-faint">Agents</p><h1 className="mt-1 font-display text-3xl text-text-bright">{name}</h1><p role="alert" className="mt-4 rounded-lg border border-danger/40 bg-danger-soft p-3 text-[13px] text-danger">{loadError}</p></div></section>
  if (!profile) return <div className="p-8 text-[13px] text-faint">Loading agent…</div>

  if (editing) {
    return (
      <FileEditor
        key={name + '/' + editing}
        path={name + '/' + editing}
        endpoint={`/api/agents/${encodeURIComponent(name)}/files/${editing.split('/').map(encodeURIComponent).join('/')}`}
        readOnly={!isDesktopApp()}
        onClose={() => {
          setEditing(null)
          getAgent(name).then(setProfile).catch(() => {})
          api('GET', '/api/agents').then(p => dispatch({ type: 'SET_AGENT_PROFILES', payload: p })).catch(() => {})
        }}
      />
    )
  }

  const invalid = profile.errors.length > 0
  const failing = (checks || []).filter(c => !c.ok)
  const readiness = invalid ? 'invalid' : checks === null ? 'checking' : failing.length ? 'not_ready' : 'ready'
  const canStart = readiness === 'ready' && !busy
  const current = toRequest(profile.params, values)
  const describing = text.trim().length > 0
  const sendForm = !describing || formChanged(initial, current)
  const hasParams = Object.keys(profile.params).length > 0

  async function start(dry) {
    // A description alone leaves the parameters to the model; edited form
    // values travel with it and must then be valid.
    const local = sendForm ? validate(profile.params, values) : []
    if (local.length) { setErrors(splitFieldErrors(local, profile.params)); return }
    setBusy(true); setErrors({ fields: {}, general: [] })
    try {
      const meta = await startAgentRun(name, { text, ...(sendForm ? { params: current } : {}), dry_run: dry })
      await tree.loadTree(); await selectSession(meta.id); dispatch({ type: 'SET_SIDEBAR_VIEW', payload: 'agents' })
    } catch (e) {
      setErrors(splitFieldErrors(errorLines(e), profile.params))
    } finally { setBusy(false) }
  }

  return (
    <section className="flex-1 overflow-y-auto px-8 pb-12 pt-4">
      <div className="mx-auto grid max-w-6xl gap-8 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 space-y-6">
          <header>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="font-display text-3xl text-text-bright">{profile.name}</h1>
              <StatusPill status={readiness} />
            </div>
            <p className="mt-2 text-[14px] text-text-default">{profile.description}</p>
            <dl className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[12px] text-faint">
              <div><dt className="inline">Script </dt><dd className="inline text-muted">{profile.entry || 'instructions only'}</dd></div>
              <div><dt className="inline">Approval </dt><dd className="inline text-muted">{profile.approval.replace(/_/g, ' ')}</dd></div>
              <div><dt className="inline">Version </dt><dd className="inline text-muted">{profile.version}</dd></div>
              <div className="min-w-0"><dt className="inline">Tools </dt><dd className="inline text-muted">{profile.tools.join(', ')}</dd></div>
            </dl>
          </header>

          {invalid && (
            <div role="alert" className="rounded-lg border border-danger/40 bg-danger-soft p-3 text-[13px] text-danger">
              <p className="font-medium">This agent definition has errors</p>
              <ul className="mt-1 list-disc pl-5">{profile.errors.map((e, n) => <li key={n}>{e}</li>)}</ul>
            </div>
          )}
          {!invalid && failing.length > 0 && (
            <div role="alert" className="rounded-lg border border-warn/40 bg-warn-soft p-3 text-[13px] text-text-default">
              <p className="font-medium text-warn">Not ready on this machine</p>
              <ul className="mt-1 list-disc pl-5">{failing.map((c, n) => <li key={n}>{c.message}</li>)}</ul>
            </div>
          )}

          <div className="rounded-xl border border-line bg-surface/40 p-5">
            <h2 className="mb-4 text-[15px] font-medium text-text-bright">New run</h2>
            {hasParams && <AgentForm agent={profile.name} params={profile.params} values={values} errors={errors.fields} onChange={(k, v) => setValues(old => ({ ...old, [k]: v }))} />}
            <div className={hasParams ? 'mt-6 border-t border-line pt-5' : ''}>
              <label htmlFor="agent-describe" className="text-[13px] font-medium text-text-default">{hasParams ? 'Or describe it in words' : 'Describe the task'}</label>
              <textarea id="agent-describe" className="mt-1.5 w-full rounded-lg border border-line bg-transparent p-3 text-[13.5px] text-text-default outline-none focus:border-accent" rows={3} value={text} onChange={e => setText(e.target.value)} placeholder={hasParams ? 'Describe the run in your own words; the agent fills in the parameters.' : 'What should this run do?'} />
              {describing && hasParams && (
                <p className="mt-1 text-[12px] text-faint">
                  {sendForm ? 'Your description and the form values you changed are sent together.' : 'The agent will work out the parameters from your description.'}
                </p>
              )}
            </div>
            {errors.general.length > 0 && (
              <div role="alert" className="mt-4 rounded-lg border border-danger/40 bg-danger-soft p-3 text-[13px] text-danger">
                {errors.general.map((e, n) => <p key={n}>{e}</p>)}
              </div>
            )}
            <div className="mt-5 flex flex-wrap items-center gap-3">
              <button type="button" disabled={!canStart} onClick={() => start(false)} className="rounded-lg bg-accent px-4 py-2 text-[13px] font-medium text-bg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40">{busy ? 'Starting…' : 'Start run'}</button>
              {profile.dry_run && <button type="button" disabled={!canStart} onClick={() => start(true)} className="rounded-lg border border-line px-4 py-2 text-[13px] text-text-default transition-colors hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-40">Dry run</button>}
              {readiness === 'checking' && <span className="text-[12px] text-faint">Checking requirements…</span>}
              {readiness === 'not_ready' && <span className="text-[12px] text-warn">Fix the requirements above to start a run.</span>}
              {readiness === 'invalid' && <span className="text-[12px] text-danger">Fix the definition to start a run.</span>}
            </div>
          </div>

          <details className="text-[13px]">
            <summary className="cursor-pointer text-accent">{isDesktopApp() ? 'Edit agent files' : 'View agent files'}</summary>
            <ul className="mt-2 flex flex-wrap gap-2">
              {profile.files.map(file => (
                <li key={file}><button type="button" className="rounded border border-line px-2 py-1 font-mono text-[12px] text-text-default hover:border-accent hover:text-accent" onClick={() => setEditing(file)}>{file}</button></li>
              ))}
            </ul>
          </details>
        </div>

        <aside className="min-w-0 lg:sticky lg:top-0 lg:self-start">
          <h2 className="mb-1 text-[15px] font-medium text-text-bright">Recent runs</h2>
          <RecentAgentRuns runs={runs} sessions={state.sessionsById} onSelect={selectSession} />
        </aside>
      </div>
    </section>
  )
}

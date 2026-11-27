import { useEffect, useRef, useState } from 'react'
import { api, isAbandonedRequest } from '../../api'
import { useApp } from '../../context/AppContext'
import ProviderLogo from '../ProviderLogo'
import { modelFromInput, perMillion, rankEndpoints, visibleEndpoints, TOP_PROVIDERS } from '../../nativeModel'
import { REPLAY_OPTIONS, replayHint, replayModeName } from '../../reasoningReplay'
import { MAP_LEVELS, VARIABLE_RE, mapPreview } from '../../reasoningMap'
import {
  checkMessage, checkRequest, launchRequest, scriptsQuery, serverForm, serverSummary,
} from '../../modelServer'

// 400000 -> "400k", 1048576 -> "1.05M": context windows read in round units.
function tokensLabel(count) {
  if (!count) return '—'
  if (count >= 1000000) return `${(count / 1000000).toFixed(2).replace(/\.?0+$/, '')}M`
  return `${Math.round(count / 1000)}k`
}

const AGENT_FIELDS = [
  { key: 'max_iterations', label: 'Max iterations', unit: 'model calls',
    help: 'How many model calls one turn may make before it stops. /maxiters overrides it for one thread.' },
  { key: 'tool_call_checkpoint', label: 'Checkpoint nudge', unit: 'tool calls', zeroOff: true,
    help: 'After this many tool calls without an edit, the agent is asked once whether it already has enough to finish.' },
  { key: 'plan_reminder_after_tool_calls', label: 'Plan reminder', unit: 'tool calls', zeroOff: true,
    help: 'Reminds the agent to update its published plan after this many tool calls without touching it.' },
  { key: 'max_images_per_request', label: 'Images per request', unit: 'images', zeroOff: true,
    help: 'Most images sent to the model in one request. Past it, older images become a short note in one go (down to half), so the prompt cache rarely breaks. A lower limit a provider reports is applied as well.' },
  { key: 'multi_agent_enabled', label: 'Sub-agents', bool: true,
    help: 'Let the agent start child agents for parallel work.' },
  { key: 'max_concurrent_subagents', label: 'Concurrent sub-agents', unit: 'at once', needsMulti: true,
    help: 'Child agents that may run at the same time.' },
  { key: 'max_total_subagents', label: 'Sub-agents per turn', unit: 'in total', needsMulti: true,
    help: 'Child agents one turn may start altogether.' },
  { key: 'max_subagent_depth', label: 'Sub-agent depth', unit: 'levels', needsMulti: true,
    help: 'How deeply child agents may start their own children.' },
  { key: 'reasoning_replay', label: 'Reasoning replay', choice: true, wide: true,
    help: "Whether a thinking model sees its own earlier reasoning again." },
]

function choiceLabel(field, value) {
  return field.key === 'reasoning_replay' ? replayModeName(value) : value
}

function speedLabel(endpoint) {
  return typeof endpoint.throughput === 'number' ? `${Math.round(endpoint.throughput)}` : '—'
}

function latencyLabel(endpoint) {
  const ms = endpoint.latency_ms
  if (typeof ms !== 'number') return '—'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}

function AgentSettings({ agent, onSaved }) {
  const [draft, setDraft] = useState(agent.values)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => { setDraft(agent.values) }, [agent])

  const changed = AGENT_FIELDS.some(f => draft[f.key] !== agent.values[f.key])
  const invalid = AGENT_FIELDS.find(f => {
    if (f.bool || f.choice) return false
    const value = draft[f.key]
    const limit = agent.limits[f.key]
    return !Number.isInteger(value) || value < limit.min || value > limit.max
  })

  async function send(method, body) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      onSaved(await api(method, '/api/native/agent-settings', body))
      setNotice(method === 'DELETE' ? 'Back to agent_config.yaml.' : 'Saved. Applies from the next turn.')
    } catch (err) {
      if (!isAbandonedRequest(err)) setError(err.detail || 'Could not save agent settings.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3 border-t border-line pt-5">
      <div>
        <h4 className="text-[13px] font-bold text-text-bright">Agent loop</h4>
        <p className="text-[12px] text-muted">Limits and nudges for native runs on this host. Each replaces its agent_config.yaml value.</p>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-5 gap-y-3">
        {AGENT_FIELDS.map(field => {
          const value = draft[field.key]
          const fallback = agent.defaults[field.key]
          const overridden = agent.overridden.includes(field.key)
          const disabled = busy || (field.needsMulti && !draft.multi_agent_enabled)
          const limit = agent.limits[field.key]
          return (
            <label key={field.key} className={`block ${field.wide ? 'sm:col-span-2' : ''} ${disabled && !busy ? 'opacity-50' : ''}`} title={field.help}>
              <span className="flex items-baseline gap-1.5 text-[12.5px] font-medium text-text-bright">
                {field.label}
                {overridden && <span className="text-[11px] font-normal text-faint">config: {field.bool ? (fallback ? 'on' : 'off') : field.choice ? choiceLabel(field, fallback) : fallback}</span>}
              </span>
              <span className="block text-[11.5px] text-muted mb-1 leading-snug">{field.help}</span>
              {field.choice ? (
                <>
                  <select
                    value={value ?? ''}
                    disabled={disabled}
                    onChange={e => setDraft({ ...draft, [field.key]: e.target.value })}
                    className={selectClass}
                  >
                    {REPLAY_OPTIONS.filter(o => (agent.choices?.[field.key] || []).includes(o.value)).map(o => (
                      <option key={o.value} value={o.value}>{o.label}</option>
                    ))}
                  </select>
                  <span className="block text-[11.5px] text-muted mt-1 leading-snug">
                    {replayHint(value, agent.replay_model_overrides || [])}
                  </span>
                </>
              ) : field.bool ? (
                <span className="flex items-center gap-2 text-[12.5px] text-text-default">
                  <input
                    type="checkbox"
                    checked={!!value}
                    disabled={disabled}
                    onChange={e => setDraft({ ...draft, [field.key]: e.target.checked })}
                  />
                  {value ? 'Enabled' : 'Disabled'}
                </span>
              ) : (
                <span className="flex items-center gap-2">
                  <input
                    type="number"
                    min={limit.min}
                    max={limit.max}
                    step={1}
                    value={Number.isFinite(value) ? value : ''}
                    disabled={disabled}
                    onChange={e => setDraft({ ...draft, [field.key]: e.target.value === '' ? NaN : Number(e.target.value) })}
                    className={`${inputClass} w-24`}
                  />
                  <span className="text-[12px] text-faint">{field.zeroOff && value === 0 ? 'off' : field.unit}</span>
                </span>
              )}
            </label>
          )
        })}
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={() => send('PUT', { values: draft })}
          disabled={busy || !changed || !!invalid}
          className="px-3 py-1 text-[12px] font-medium text-accent border border-accent/30 rounded hover:bg-accent-soft transition-colors disabled:opacity-40"
        >
          {busy ? 'Saving…' : 'Save agent settings'}
        </button>
        {agent.overridden.length > 0 && (
          <button
            onClick={() => send('DELETE')}
            disabled={busy}
            className="px-3 py-1 text-[12px] text-muted border border-line rounded hover:text-text-bright hover:border-line-hover transition-colors disabled:opacity-40"
          >
            Use config defaults
          </button>
        )}
        {changed && (
          <button
            onClick={() => setDraft(agent.values)}
            disabled={busy}
            className="px-2 py-1 text-[12px] text-faint hover:text-text-bright transition-colors"
          >
            Discard
          </button>
        )}
      </div>
      {invalid && changed && (
        <p className="text-[12px] text-warn">{invalid.label} must be a whole number from {agent.limits[invalid.key].min} to {agent.limits[invalid.key].max}.</p>
      )}
      {error && <p className="text-[12px] text-danger" role="alert">{error}</p>}
      {notice && <p className="text-[12px] text-ok" role="status">{notice}</p>}
    </div>
  )
}

// Local endpoint only: which chat-template variable each thinking level sets.
function ReasoningMapSettings({ map, onSaved }) {
  const [draft, setDraft] = useState(map.values)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => { setDraft(map.values) }, [map])

  const keys = ['variable', ...MAP_LEVELS]
  const changed = keys.some(k => (draft[k] ?? '') !== (map.values[k] ?? ''))
  const badVariable = !VARIABLE_RE.test(String(draft.variable ?? '').trim())

  async function send(method, body) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      onSaved(await api(method, '/api/native/reasoning-map', body))
      setNotice(method === 'DELETE' ? 'Back to the Inkling 2 defaults.' : 'Saved. Applies from the next request.')
    } catch (err) {
      if (!isAbandonedRequest(err)) setError(err.detail || 'Could not save the reasoning map.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3 border-t border-line pt-5">
      <div>
        <h4 className="text-[13px] font-bold text-text-bright">Reasoning map</h4>
        <p className="text-[12px] text-muted leading-snug">
          What the thinking level (low, medium, high; <code className="font-mono">/thinking</code> and the model menu) sends to the local server, as a chat-template variable.
          The defaults suit Inkling 2, whose template turns low, medium and high into effort 0.2, 0.7 and 0.9 (it also accepts <code className="font-mono">none</code> or a number from 0 to 0.99).
          For a template with an on/off switch, such as Qwen3, use <code className="font-mono">enable_thinking</code> with <code className="font-mono">false</code>, <code className="font-mono">true</code>, <code className="font-mono">true</code>.
          <code className="font-mono"> true</code>/<code className="font-mono">false</code> and numbers are sent as such; an empty value sends nothing, so the template keeps its default.
          Changing the level in a thread makes its next request re-read the whole thread once.
        </p>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-3">
        {keys.map(key => (
          <label key={key} className="block">
            <span className="flex items-baseline gap-1.5 text-[12.5px] font-medium text-text-bright">
              {key === 'variable' ? 'Template variable' : key[0].toUpperCase() + key.slice(1)}
              {(draft[key] ?? '') !== map.defaults[key] && (
                <span className="text-[11px] font-normal text-faint">Inkling 2: {map.defaults[key]}</span>
              )}
            </span>
            <input
              value={draft[key] ?? ''}
              onChange={e => setDraft({ ...draft, [key]: e.target.value })}
              disabled={busy}
              placeholder={key === 'variable' ? 'reasoning_effort' : 'empty: send nothing'}
              spellCheck={false}
              className={`${inputClass} mt-1`}
            />
          </label>
        ))}
      </div>
      <div className="text-[11.5px] text-muted font-mono leading-snug">
        {mapPreview(draft).map(line => <div key={line}>{line}</div>)}
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={() => send('PUT', { values: draft })}
          disabled={busy || !changed || badVariable}
          className="px-3 py-1 text-[12px] font-medium text-accent border border-accent/30 rounded hover:bg-accent-soft transition-colors disabled:opacity-40"
        >
          {busy ? 'Saving…' : 'Save reasoning map'}
        </button>
        {map.custom && (
          <button
            onClick={() => send('DELETE')}
            disabled={busy}
            className="px-3 py-1 text-[12px] text-muted border border-line rounded hover:text-text-bright hover:border-line-hover transition-colors disabled:opacity-40"
          >
            Reset to Inkling 2 defaults
          </button>
        )}
        {changed && (
          <button
            onClick={() => setDraft(map.values)}
            disabled={busy}
            className="px-2 py-1 text-[12px] text-faint hover:text-text-bright transition-colors"
          >
            Discard
          </button>
        )}
      </div>
      {badVariable && changed && (
        <p className="text-[12px] text-warn">The template variable must be a name such as reasoning_effort (letters, digits, _).</p>
      )}
      {error && <p className="text-[12px] text-danger" role="alert">{error}</p>}
      {notice && <p className="text-[12px] text-ok" role="status">{notice}</p>}
    </div>
  )
}

// After Launch server, ask the endpoint until it answers (loading a model takes a while).
const READY_POLL_MS = 3000
const READY_TIMEOUT_MS = 5 * 60 * 1000

function Message({ value }) {
  if (!value) return null
  return value.error
    ? <p className="text-[12px] text-danger whitespace-pre-wrap" role="alert">{value.error}</p>
    : <p className="text-[12px] text-ok whitespace-pre-wrap" role="status">{value.ok}</p>
}

// Local endpoint only: start and stop a llama-server on a Linux machine of the
// network over SSH (backend/model_server.py).
function ModelServerSettings({ initial, baseUrl, model, onEndpointCheck }) {
  const [form, setForm] = useState(serverForm(initial))
  const [server, setServer] = useState(initial)
  const [scripts, setScripts] = useState(null)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState(null)
  const waitRef = useRef(0)

  useEffect(() => () => { waitRef.current += 1 }, [])
  useEffect(() => {
    // Ask the model server whether what was started from here still runs.
    if (initial?.launched) api('GET', '/api/native/server').then(setServer).catch(() => {})
  }, [])

  const isDefault = !server?.script_saved && form.script === server?.default_script
  const edit = patch => { setForm(prev => ({ ...prev, ...patch })); setMsg(null) }

  async function waitUntilReady() {
    const { body } = checkRequest(baseUrl, model)
    if (!body) return
    const token = ++waitRef.current
    const started = Date.now()
    while (waitRef.current === token && Date.now() - started < READY_TIMEOUT_MS) {
      const seconds = Math.round((Date.now() - started) / 1000)
      setMsg({ ok: `Started. Waiting for ${body.base_url} to answer… (${seconds} s)` })
      try {
        const result = await api('POST', '/api/native/settings/check', body)
        if (waitRef.current !== token) return
        if (result.ok || result.model_listed === false) {
          setMsg({ ok: 'Server started.' })
          onEndpointCheck(checkMessage(result))
          return
        }
      } catch { /* keep waiting */ }
      await new Promise(resolve => setTimeout(resolve, READY_POLL_MS))
    }
    if (waitRef.current === token) {
      setMsg({ error: 'Started, but the endpoint did not answer within 5 minutes. See the log below or use Refresh.' })
      api('GET', '/api/native/server').then(setServer).catch(() => {})
    }
  }

  async function action(part, method, path, body, done) {
    setBusy(part)
    waitRef.current += 1
    setMsg(null)
    try {
      const next = await api(method, path, body)
      setServer(next)
      if (next.error) setMsg({ error: next.error })
      else if (done) setMsg({ ok: done })
      return next
    } catch (err) {
      if (!isAbandonedRequest(err)) setMsg({ error: err.detail || err.message })
      return null
    } finally {
      setBusy('')
    }
  }

  async function launch() {
    const { body, error } = launchRequest(form, baseUrl)
    if (error) { setMsg({ error }); return }
    if (await action('launch', 'POST', '/api/native/server/launch', body, 'Started.')) waitUntilReady()
  }

  async function findScripts() {
    setBusy('scripts')
    setMsg(null)
    try {
      const result = await api('GET', `/api/native/server/scripts?${scriptsQuery(form, baseUrl, server?.default_dir)}`)
      setScripts(result)
      if (result.scripts.length === 1) edit({ script: result.scripts[0] })
    } catch (err) {
      if (!isAbandonedRequest(err)) setMsg({ error: err.detail || err.message })
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="space-y-3 border-t border-line pt-5">
      <div>
        <h4 className="text-[13px] font-bold text-text-bright">LAN model server</h4>
        <p className="text-[12px] text-muted leading-snug">
          Starts llama-server on a Linux machine of this network: the app runs the launch script there over SSH and can stop what it started.
          The SSH host is the one in the base URL above unless you give a login.
        </p>
      </div>
      <label className="block">
        <span className="flex items-baseline gap-1.5 text-[12.5px] font-medium text-text-bright">
          Launch script
          {isDefault && <span className="text-[11px] font-normal text-accent">default</span>}
        </span>
        <span className="block text-[11.5px] text-muted mb-1 leading-snug">
          The path of a <code className="font-mono">.sh</code> file on the model server that starts <code className="font-mono">llama-server</code> (scripts live in <code className="font-mono">{server?.default_dir || '/archive/models'}</code>).
          It runs with bash from its folder; output goes to <code className="font-mono">~/.cache/myharness/llama-server.log</code> there.
        </span>
        <div className="flex items-center gap-2">
          <input
            value={form.script}
            onChange={e => edit({ script: e.target.value })}
            disabled={!!busy}
            spellCheck={false}
            placeholder={server?.default_script || '/archive/models/run-inkling.sh'}
            className={`${inputClass} ${isDefault ? 'border-accent/60 bg-accent-soft' : ''}`}
          />
          <button
            onClick={findScripts}
            disabled={!!busy}
            className="shrink-0 px-3 py-1 text-[12px] text-muted border border-line rounded hover:text-text-bright hover:border-line-hover transition-colors disabled:opacity-40"
            title="List the .sh files in this script's folder on the model server"
          >
            {busy === 'scripts' ? 'Looking…' : 'Find scripts'}
          </button>
        </div>
        {scripts && (
          <span className="flex flex-wrap items-center gap-1.5 mt-1.5 text-[11.5px] text-muted">
            {scripts.scripts.length ? `In ${scripts.folder} on ${scripts.login}:` : `No .sh files in ${scripts.folder} on ${scripts.login}.`}
            {scripts.scripts.map(path => (
              <button
                key={path}
                onClick={() => edit({ script: path })}
                className={`font-mono px-1.5 py-0.5 border rounded hover:text-accent ${form.script === path ? 'text-accent border-accent/40' : 'text-text-default border-line'}`}
              >
                {path.slice(path.lastIndexOf('/') + 1)}
              </button>
            ))}
          </span>
        )}
      </label>
      <label className="block">
        <span className="block text-[12.5px] font-medium text-text-bright">SSH login</span>
        <span className="block text-[11.5px] text-muted mb-1 leading-snug">
          user@host of the model server. Empty: {server?.default_login || 'your user name at the base URL host'}. Uses your SSH agent, ~/.ssh keys and ~/.ssh/config; it never asks for a password.
        </span>
        <input
          value={form.login}
          onChange={e => edit({ login: e.target.value })}
          disabled={!!busy}
          spellCheck={false}
          placeholder={server?.default_login || 'user@host'}
          className={`${inputClass} sm:w-80`}
        />
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={launch}
          disabled={!!busy || !form.script.trim()}
          className="px-3 py-1 text-[12px] font-medium text-accent border border-accent/30 rounded hover:bg-accent-soft transition-colors disabled:opacity-40"
        >
          {busy === 'launch' ? 'Starting…' : 'Launch server'}
        </button>
        {server?.launched && (
          <>
            <button
              onClick={() => action('stop', 'POST', '/api/native/server/stop', undefined, 'Server stopped.')}
              disabled={!!busy}
              className="px-3 py-1 text-[12px] text-muted border border-line rounded hover:text-text-bright hover:border-line-hover transition-colors disabled:opacity-40"
            >
              {busy === 'stop' ? 'Stopping…' : 'Stop server'}
            </button>
            <button
              onClick={() => action('status', 'GET', '/api/native/server')}
              disabled={!!busy}
              className="px-2 py-1 text-[12px] text-faint hover:text-text-bright transition-colors disabled:opacity-40"
            >
              {busy === 'status' ? 'Asking…' : 'Refresh'}
            </button>
          </>
        )}
        <span className="text-[12px] text-faint">{serverSummary(server)}</span>
      </div>
      {server?.processes?.length > 0 && (
        <pre className="text-[11px] font-mono text-text-default bg-surface border border-line rounded p-2 overflow-x-auto whitespace-pre">{server.processes.join('\n')}</pre>
      )}
      <Message value={msg} />
      {server?.log_tail && (
        <details className="text-[12px] text-muted">
          <summary className="cursor-pointer">Server log ({server.log})</summary>
          <pre className="mt-1 max-h-48 overflow-auto text-[11px] font-mono text-text-default bg-surface border border-line rounded p-2 whitespace-pre-wrap break-all">{server.log_tail}</pre>
        </details>
      )}
    </div>
  )
}

const selectClass = 'w-full bg-surface border border-line rounded px-3 py-1.5 text-[12.5px] text-text-default outline-none focus:border-line-hover'
const inputClass = 'w-full bg-surface border border-line rounded px-3 py-1.5 font-mono text-[12.5px] text-text-default placeholder:text-faint outline-none focus:border-line-hover'

export default function NativeSettings() {
  const { state } = useApp()
  const [settings, setSettings] = useState(null)
  const [model, setModel] = useState('')
  const [providers, setProviders] = useState('')
  const [endpoint, setEndpoint] = useState('config')
  const [baseUrl, setBaseUrl] = useState('')
  const [info, setInfo] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [showAll, setShowAll] = useState(false)
  const [checked, setChecked] = useState(null)

  function adopt(payload) {
    setSettings(payload)
    setModel(payload.model || '')
    setProviders((payload.providers || []).join(', '))
    setEndpoint(payload.endpoint || 'config')
    if (payload.base_url) setBaseUrl(payload.base_url)
    setInfo(payload.model_info || null)
  }

  useEffect(() => {
    let cancelled = false
    setError('')
    api('GET', '/api/native/settings?details=true')
      .then(payload => {
        if (cancelled) return
        adopt(payload)
        if (payload.model_error) setError(payload.model_error)
      })
      .catch(err => { if (!cancelled && !isAbandonedRequest(err)) setError(err.detail || 'Could not read native settings.') })
    return () => { cancelled = true }
  }, [state.activeHostId])

  async function save() {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const local = endpoint === 'local'
      const payload = await api('PUT', '/api/native/settings', local
        ? { model: model.trim(), endpoint, base_url: baseUrl }
        : { model: modelFromInput(model), providers, endpoint })
      adopt(payload)
      setNotice(local
        ? 'Saved. New native requests go to the local server, without an API key; threads that picked their own model keep it.'
        : 'Saved. New native requests use this model; threads that picked their own model keep it.')
    } catch (err) {
      if (!isAbandonedRequest(err)) setError(err.detail || 'Could not save native settings.')
    } finally {
      setBusy(false)
    }
  }

  async function checkEndpoint() {
    const { body, error: invalid } = checkRequest(baseUrl, model)
    setError('')
    setNotice('')
    if (invalid) { setChecked({ error: invalid }); return }
    setBusy(true)
    setChecked(null)
    try {
      setChecked(checkMessage(await api('POST', '/api/native/settings/check', body)))
    } catch (err) {
      if (!isAbandonedRequest(err)) setChecked({ error: err.detail || 'Could not check the endpoint.' })
    } finally {
      setBusy(false)
    }
  }

  async function reset() {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      adopt(await api('DELETE', '/api/native/settings'))
      setNotice('Back to the endpoint and model in agent_config.yaml.')
    } catch (err) {
      if (!isAbandonedRequest(err)) setError(err.detail || 'Could not reset native settings.')
    } finally {
      setBusy(false)
    }
  }

  function addProvider(tag) {
    const current = providers.split(',').map(p => p.trim()).filter(Boolean)
    if (!current.includes(tag)) setProviders([...current, tag].join(', '))
  }

  const local = endpoint === 'local'
  const chosen = providers.split(',').map(p => p.trim()).filter(Boolean)
  const ranked = info ? rankEndpoints(info.endpoints) : []
  const shown = visibleEndpoints(ranked, chosen, showAll)
  const hasSpeed = ranked.some(e => typeof e.throughput === 'number')

  return (
    <div className="space-y-5 max-w-2xl">
      <div className="space-y-3">
        <label className="block">
          <span className="block text-[13px] font-bold text-text-bright">Endpoint</span>
          <span className="block text-[12px] text-muted mb-1.5">
            {local
              ? 'An OpenAI-compatible server on this machine or network, such as llama-server. No API key is sent to it.'
              : 'The api.base_url in agent_config.yaml: OpenRouter, or another hosted OpenAI-compatible API.'}
          </span>
          <select
            value={endpoint}
            onChange={e => { setEndpoint(e.target.value); setError(''); setNotice(''); setChecked(null) }}
            disabled={busy}
            className={selectClass}
          >
            <option value="config">OpenRouter / other (agent_config.yaml)</option>
            <option value="local">Local server (llama-server and others)</option>
          </select>
        </label>
        <label className="block">
          <span className="block text-[13px] font-bold text-text-bright">Model</span>
          <span className="block text-[12px] text-muted mb-1.5">
            {local
              ? <>The model id the server lists at <code className="font-mono">/v1/models</code>: its <code className="font-mono">--alias</code>, or the GGUF path or name it was started with.</>
              : <>The OpenRouter model id, exactly as on its page, for example <code className="font-mono">openai/gpt-6.1-terra</code>. A model page URL works too.</>}
          </span>
          <div className="flex items-center gap-2">
            <ProviderLogo provider="native" model={modelFromInput(model)} />
            <input value={model} onChange={e => setModel(e.target.value)} placeholder={local ? 'inkling-2-mini' : 'author/model'} className={inputClass} spellCheck={false} />
          </div>
        </label>
        {local ? (
          <label className="block">
            <span className="block text-[13px] font-bold text-text-bright">Base URL</span>
            <span className="block text-[12px] text-muted mb-1.5">
              Where the server answers, ending before <code className="font-mono">/chat/completions</code>. From the machine running this backend: use <code className="font-mono">127.0.0.1</code> when the server runs here too.
            </span>
            <input value={baseUrl} onChange={e => setBaseUrl(e.target.value)} placeholder="http://127.0.0.1:8081/v1" className={inputClass} spellCheck={false} />
          </label>
        ) : (
        <label className="block">
          <span className="block text-[13px] font-bold text-text-bright">Provider</span>
          <span className="block text-[12px] text-muted mb-1.5">
            Optional. OpenRouter's provider tag, for example <code className="font-mono">azure/eu</code>. Separate several with commas to try them in order.
            Leave empty to let OpenRouter choose.
          </span>
          <input value={providers} onChange={e => setProviders(e.target.value)} placeholder="Any provider" className={inputClass} spellCheck={false} />
        </label>
        )}
        {settings && !local && settings.config_openrouter === false && (
          <p className="text-[12px] text-warn">The native API is not OpenRouter, so the model is not checked and the provider is ignored.</p>
        )}
        <div className="flex items-center gap-2">
          <button
            onClick={save}
            disabled={busy || !model.trim() || (local && !baseUrl.trim())}
            className="px-3 py-1 text-[12px] font-medium text-accent border border-accent/30 rounded hover:bg-accent-soft transition-colors disabled:opacity-40"
          >
            {busy ? 'Checking…' : 'Check and save'}
          </button>
          {local && (
            <button
              onClick={checkEndpoint}
              disabled={busy || !baseUrl.trim()}
              className="px-3 py-1 text-[12px] text-muted border border-line rounded hover:text-text-bright hover:border-line-hover transition-colors disabled:opacity-40"
              title="Ask the server for its models without saving anything"
            >
              Check endpoint
            </button>
          )}
          {settings?.source === 'settings' && (
            <button
              onClick={reset}
              disabled={busy}
              className="px-3 py-1 text-[12px] text-muted border border-line rounded hover:text-text-bright hover:border-line-hover transition-colors disabled:opacity-40"
              title={`Use ${settings.config_model} from agent_config.yaml`}
            >
              Use config default
            </button>
          )}
          <span className="text-[12px] text-faint">
            {settings ? (settings.source === 'settings'
              ? (settings.endpoint === 'local' ? `Local server ${settings.base_url}, set on this host` : 'Set here, on this host')
              : 'From agent_config.yaml') : ''}
          </span>
        </div>
        {settings?.context_window > 0 && (
          <p className="text-[12px] text-muted" title={settings.context_detail}>
            Context window: <span className="text-text-default">{tokensLabel(settings.context_window)} tokens</span>
            {settings.context_source === 'openrouter' || settings.context_source === 'local'
              ? `, from ${settings.context_detail}` : ', from agent_config.yaml'}.
            {' '}Compacts history at {tokensLabel(settings.compact_threshold)}.
          </p>
        )}
        {error && <p className="text-[12px] text-danger" role="alert">{error}</p>}
        {notice && <p className="text-[12px] text-ok" role="status">{notice}</p>}
        {local && <Message value={checked} />}
      </div>

      {local && settings?.server && (
        <ModelServerSettings
          key={state.activeHostId || 'local'}
          initial={settings.server}
          baseUrl={baseUrl}
          model={model}
          onEndpointCheck={setChecked}
        />
      )}

      {info && !local && (
        <div>
          <h4 className="text-[13px] font-bold text-text-bright">{info.name}</h4>
          <p className="text-[12px] text-muted mb-2">
            {showAll
              ? `All ${ranked.length} providers serving it on OpenRouter, fastest first.`
              : hasSpeed
                ? `The ${TOP_PROVIDERS} fastest providers over the last 30 minutes, plus any you picked.`
                : `OpenRouter reported no recent speeds; showing ${TOP_PROVIDERS} providers, plus any you picked.`}
            {' '}Click a tag to add it.
          </p>
          <table className="w-full text-[12px]">
            <thead className="text-muted">
              <tr className="border-b border-line">
                <th className="py-1.5 text-left font-medium">Provider tag</th>
                <th className="py-1.5 text-right font-medium" title="Median output tokens per second, last 30 minutes">Tok/s</th>
                <th className="py-1.5 text-right font-medium" title="Median time to first token, last 30 minutes">Latency</th>
                <th className="py-1.5 text-right font-medium">Input $/1M</th>
                <th className="py-1.5 text-right font-medium">Output $/1M</th>
                <th className="py-1.5 text-right font-medium">Cache read $/1M</th>
                <th className="py-1.5 text-right font-medium">Context</th>
                <th className="py-1.5 text-left font-medium pl-3">Supports</th>
              </tr>
            </thead>
            <tbody>
              {shown.map(endpoint => (
                <tr key={endpoint.tag} className={`border-b border-line/60 ${endpoint.tools ? '' : 'opacity-50'}`}>
                  <td className="py-1.5">
                    <button
                      onClick={() => addProvider(endpoint.tag)}
                      disabled={!endpoint.tools}
                      className={`font-mono text-[11.5px] hover:text-accent ${chosen.includes(endpoint.tag) ? 'text-accent' : 'text-text-default'}`}
                      title={endpoint.tools ? `Add ${endpoint.tag}` : 'No tool calling: native cannot use it'}
                    >
                      {endpoint.tag}
                    </button>
                    {endpoint.quantization && <span className="ml-1.5 text-faint">{endpoint.quantization}</span>}
                  </td>
                  <td className="py-1.5 text-right text-text-default">{speedLabel(endpoint)}</td>
                  <td className="py-1.5 text-right text-muted">{latencyLabel(endpoint)}</td>
                  <td className="py-1.5 text-right text-muted">{perMillion(endpoint.pricing.prompt)}</td>
                  <td className="py-1.5 text-right text-muted">{perMillion(endpoint.pricing.completion)}</td>
                  <td className="py-1.5 text-right text-muted">{perMillion(endpoint.pricing.input_cache_read)}</td>
                  <td className="py-1.5 text-right text-muted">{endpoint.context_length ? `${Math.round(endpoint.context_length / 1000)}k` : '—'}</td>
                  <td className="py-1.5 pl-3 text-muted">
                    {[endpoint.tools ? 'tools' : 'no tools', endpoint.reasoning && 'reasoning', endpoint.implicit_caching && 'caching'].filter(Boolean).join(' · ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {ranked.length > shown.length || showAll ? (
            <button onClick={() => setShowAll(!showAll)} className="mt-1.5 text-[12px] text-muted hover:text-accent">
              {showAll ? 'Show fewer' : `Show all ${ranked.length} providers`}
            </button>
          ) : null}
        </div>
      )}

      {settings?.agent && (
        <AgentSettings agent={settings.agent} onSaved={agent => setSettings(prev => ({ ...prev, agent }))} />
      )}

      {settings?.endpoint === 'local' && settings.reasoning_map && (
        <ReasoningMapSettings
          map={settings.reasoning_map}
          onSaved={reasoningMap => setSettings(prev => ({ ...prev, reasoning_map: reasoningMap }))}
        />
      )}
    </div>
  )
}

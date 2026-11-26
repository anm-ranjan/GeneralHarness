import { useEffect, useState } from 'react'
import { api, isAbandonedRequest } from '../../api'
import { useApp } from '../../context/AppContext'
import ProviderLogo from '../ProviderLogo'
import { modelFromInput, perMillion, rankEndpoints, visibleEndpoints, TOP_PROVIDERS } from '../../nativeModel'
import { REPLAY_OPTIONS, replayHint, replayModeName } from '../../reasoningReplay'

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

const selectClass = 'w-full bg-surface border border-line rounded px-3 py-1.5 text-[12.5px] text-text-default outline-none focus:border-line-hover'
const inputClass = 'w-full bg-surface border border-line rounded px-3 py-1.5 font-mono text-[12.5px] text-text-default placeholder:text-faint outline-none focus:border-line-hover'

export default function NativeSettings() {
  const { state } = useApp()
  const [settings, setSettings] = useState(null)
  const [model, setModel] = useState('')
  const [providers, setProviders] = useState('')
  const [info, setInfo] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [showAll, setShowAll] = useState(false)

  function adopt(payload) {
    setSettings(payload)
    setModel(payload.model || '')
    setProviders((payload.providers || []).join(', '))
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
      const payload = await api('PUT', '/api/native/settings', { model: modelFromInput(model), providers })
      adopt(payload)
      setNotice('Saved. New native requests use this model; threads that picked their own model keep it.')
    } catch (err) {
      if (!isAbandonedRequest(err)) setError(err.detail || 'Could not save native settings.')
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
      setNotice('Back to the model in agent_config.yaml.')
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

  const chosen = providers.split(',').map(p => p.trim()).filter(Boolean)
  const ranked = info ? rankEndpoints(info.endpoints) : []
  const shown = visibleEndpoints(ranked, chosen, showAll)
  const hasSpeed = ranked.some(e => typeof e.throughput === 'number')

  return (
    <div className="space-y-5 max-w-2xl">
      <div className="space-y-3">
        <label className="block">
          <span className="block text-[13px] font-bold text-text-bright">Model</span>
          <span className="block text-[12px] text-muted mb-1.5">
            The OpenRouter model id, exactly as on its page, for example <code className="font-mono">openai/gpt-6.1-terra</code>. A model page URL works too.
          </span>
          <div className="flex items-center gap-2">
            <ProviderLogo provider="native" model={modelFromInput(model)} />
            <input value={model} onChange={e => setModel(e.target.value)} placeholder="author/model" className={inputClass} spellCheck={false} />
          </div>
        </label>
        <label className="block">
          <span className="block text-[13px] font-bold text-text-bright">Provider</span>
          <span className="block text-[12px] text-muted mb-1.5">
            Optional. OpenRouter's provider tag, for example <code className="font-mono">azure/eu</code>. Separate several with commas to try them in order.
            Leave empty to let OpenRouter choose.
          </span>
          <input value={providers} onChange={e => setProviders(e.target.value)} placeholder="Any provider" className={inputClass} spellCheck={false} />
        </label>
        {settings && !settings.openrouter && (
          <p className="text-[12px] text-warn">The native API is not OpenRouter, so the model is not checked and the provider is ignored.</p>
        )}
        <div className="flex items-center gap-2">
          <button
            onClick={save}
            disabled={busy || !model.trim()}
            className="px-3 py-1 text-[12px] font-medium text-accent border border-accent/30 rounded hover:bg-accent-soft transition-colors disabled:opacity-40"
          >
            {busy ? 'Checking…' : 'Check and save'}
          </button>
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
            {settings ? (settings.source === 'settings' ? 'Set here, on this host' : 'From agent_config.yaml') : ''}
          </span>
        </div>
        {settings?.context_window > 0 && (
          <p className="text-[12px] text-muted" title={settings.context_detail}>
            Context window: <span className="text-text-default">{tokensLabel(settings.context_window)} tokens</span>
            {settings.context_source === 'openrouter' ? `, from ${settings.context_detail}` : ', from agent_config.yaml'}.
            {' '}Compacts history at {tokensLabel(settings.compact_threshold)}.
          </p>
        )}
        {error && <p className="text-[12px] text-danger" role="alert">{error}</p>}
        {notice && <p className="text-[12px] text-ok" role="status">{notice}</p>}
      </div>

      {info && (
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
    </div>
  )
}

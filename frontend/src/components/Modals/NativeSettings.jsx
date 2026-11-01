import { useEffect, useState } from 'react'
import { api, isAbandonedRequest } from '../../api'
import { useApp } from '../../context/AppContext'
import ProviderLogo from '../ProviderLogo'
import { modelFromInput, perMillion } from '../../nativeModel'

// 400000 -> "400k", 1048576 -> "1.05M": context windows read in round units.
function tokensLabel(count) {
  if (!count) return '—'
  if (count >= 1000000) return `${(count / 1000000).toFixed(2).replace(/\.?0+$/, '')}M`
  return `${Math.round(count / 1000)}k`
}

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
          <p className="text-[12px] text-muted mb-2">Providers serving it on OpenRouter. Click a tag to add it.</p>
          <table className="w-full text-[12px]">
            <thead className="text-muted">
              <tr className="border-b border-line">
                <th className="py-1.5 text-left font-medium">Provider tag</th>
                <th className="py-1.5 text-right font-medium">Input $/1M</th>
                <th className="py-1.5 text-right font-medium">Output $/1M</th>
                <th className="py-1.5 text-right font-medium">Cache read $/1M</th>
                <th className="py-1.5 text-right font-medium">Context</th>
                <th className="py-1.5 text-left font-medium pl-3">Supports</th>
              </tr>
            </thead>
            <tbody>
              {info.endpoints.map(endpoint => (
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
        </div>
      )}
    </div>
  )
}

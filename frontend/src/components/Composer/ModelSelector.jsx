import { useEffect, useMemo, useRef, useState } from 'react'
import { api, isAbandonedRequest } from '../../api'
import { useApp } from '../../context/AppContext'
import ProviderLogo from '../ProviderLogo'

const PROVIDERS = [
  { id: 'native', command: 'native', label: 'Native' },
  { id: 'codex-app-server', command: 'codex', label: 'Codex' },
  { id: 'claude-agent', command: 'claude', label: 'Claude' },
]

function providerEnabled(state, provider) {
  if (provider === 'native') return state.nativeEnabled
  if (provider === 'codex-app-server') return state.codexAppServerEnabled
  return state.claudeAgentEnabled
}

export default function ModelSelector() {
  const { state, dispatch } = useApp()
  const [open, setOpen] = useState(false)
  const [catalog, setCatalog] = useState(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const rootRef = useRef(null)

  const sid = state.currentSessionId
  const provider = state.currentProvider
  const configuredModel = state.currentMeta?.run_settings?.model || ''
  const configuredEffort = state.currentMeta?.run_settings?.reasoning_effort || ''

  useEffect(() => {
    setCatalog(null)
    setError('')
    if (!sid) return
    let cancelled = false
    setLoading(true)
    api('GET', `/api/sessions/${encodeURIComponent(sid)}/model-options`)
      .then(data => {
        if (!cancelled) setCatalog(data)
      })
      .catch(err => {
        if (!cancelled && !isAbandonedRequest(err)) {
          setError(err.detail || 'Could not load model options')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [sid, provider, state.activeHostId])

  useEffect(() => {
    if (!open) return
    const close = event => {
      if (!rootRef.current?.contains(event.target)) setOpen(false)
    }
    const escape = event => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', close)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', close)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  const currentModel = configuredModel || catalog?.current_model || ''
  const currentEffort = configuredEffort || catalog?.current_effort || ''
  const selected = useMemo(
    () => catalog?.models?.find(model => model.id === currentModel),
    [catalog, currentModel],
  )
  const modelLabel = selected?.display_name
    || (provider === 'claude-agent' ? 'Claude' : provider === 'codex-app-server' ? 'Codex' : state.model || 'Native')
  const efforts = selected?.supported_efforts || []

  async function updateSettings(model, reasoningEffort) {
    if (saving || state.isRunning) return
    setSaving(true)
    setError('')
    try {
      const meta = await api('PATCH', `/api/sessions/${encodeURIComponent(sid)}/run-settings`, {
        model,
        reasoning_effort: reasoningEffort,
      })
      dispatch({ type: 'SET_RUN_SETTINGS', payload: meta.run_settings || {} })
      setCatalog(current => current ? {
        ...current,
        current_model: model,
        current_effort: reasoningEffort,
      } : current)
    } catch (err) {
      setError(err.detail || 'Could not update run settings')
    } finally {
      setSaving(false)
    }
  }

  function chooseModel(model) {
    const supported = model.supported_efforts || []
    const effort = supported.includes(currentEffort)
      ? currentEffort
      : model.default_effort || supported[0] || ''
    updateSettings(model.id, effort)
  }

  function switchProvider(item) {
    if (item.id === provider || state.isRunning) return
    setOpen(false)
    dispatch({ type: 'SET_SILENT_COMMAND', payload: true })
    dispatch({ type: 'SET_RUNNING' })
    api('POST', `/api/sessions/${encodeURIComponent(sid)}/message`, {
      text: `/model ${item.command}`,
    }).catch(err => {
      dispatch({ type: 'SET_IDLE' })
      dispatch({ type: 'SET_SILENT_COMMAND', payload: false })
      dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: err.detail || err.message } })
    })
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen(value => !value)}
        disabled={state.isRunning}
        className={`model-trigger ${open ? 'model-trigger-open' : ''}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        title="Choose provider, model, and thinking level"
      >
        <span className="model-mark">
          <ProviderLogo provider={provider} className="h-3.5 w-3.5" />
        </span>
        <span className="max-w-32 truncate text-text-bright">{modelLabel}</span>
        {currentEffort && <span className="model-effort">{currentEffort}</span>}
        <svg viewBox="0 0 12 8" className={`h-2 w-3 transition-transform ${open ? 'rotate-180' : ''}`} fill="none" aria-hidden="true">
          <path d="m1 1.5 5 5 5-5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <div className="model-popover" role="dialog" aria-label="Model settings">
          <div className="model-popover-head">
            <div className="flex items-center gap-2">
              <span className="model-mark model-mark-large">
                <ProviderLogo provider={provider} className="h-4 w-4" />
              </span>
              <div>
                <div className="text-[12px] font-semibold text-text-bright">Run configuration</div>
                <div className="text-[10px] text-faint">Applies to this thread</div>
              </div>
            </div>
            {saving && <span className="spinner" aria-label="Saving" />}
          </div>

          <div className="model-section">
            <div className="model-section-label">Model</div>
            {loading && !catalog ? (
              <div className="model-loading"><span className="spinner" /> Loading models…</div>
            ) : (
              <div className="model-list">
                {(catalog?.models || []).map(model => {
                  const active = model.id === currentModel
                  return (
                    <button
                      key={model.id}
                      type="button"
                      className={`model-option ${active ? 'model-option-active' : ''}`}
                      onClick={() => chooseModel(model)}
                      disabled={saving}
                    >
                      <span className="model-option-logo">
                        <ProviderLogo provider={provider} className="h-4 w-4" />
                      </span>
                      <span className="min-w-0 flex-1 text-left">
                        <span className="block truncate text-[12px] font-medium text-text-bright">{model.display_name}</span>
                        {model.description && <span className="block truncate text-[10px] text-faint">{model.description}</span>}
                      </span>
                      <span className={`model-check ${active ? 'model-check-active' : ''}`}>✓</span>
                    </button>
                  )
                })}
              </div>
            )}
          </div>

          {efforts.length > 0 && (
            <div className="model-section">
              <div className="model-section-label">Thinking</div>
              <div className="effort-track">
                {efforts.map(effort => (
                  <button
                    key={effort}
                    type="button"
                    onClick={() => updateSettings(currentModel, effort)}
                    disabled={saving}
                    className={`effort-option ${effort === currentEffort ? 'effort-option-active' : ''}`}
                  >
                    {effort}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="model-section model-provider-section">
            <div className="model-section-label">Provider</div>
            <div className="provider-rail">
              {PROVIDERS.filter(item => providerEnabled(state, item.id)).map(item => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => switchProvider(item)}
                  className={`provider-chip ${item.id === provider ? 'provider-chip-active' : ''}`}
                  disabled={saving || item.id === provider}
                >
                  <ProviderLogo provider={item.id} className="h-3.5 w-3.5" />
                  {item.label}
                </button>
              ))}
            </div>
          </div>

          {error && <div className="model-error">{error}</div>}
        </div>
      )}
    </div>
  )
}

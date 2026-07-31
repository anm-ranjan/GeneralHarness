import { useEffect, useState } from 'react'

const EMPTY_STATUS = {
  native_api_key: { configured: false, stored: false, source: 'missing', environment_override: false },
  stt_api_key: { configured: false, stored: false, source: 'missing', environment_override: false },
}

export default function CredentialSettings() {
  const desktop = typeof window !== 'undefined' && !!window.myharnessDesktop
  const [status, setStatus] = useState(EMPTY_STATUS)
  const [nativeKey, setNativeKey] = useState('')
  const [sttKey, setSttKey] = useState('')
  const [loading, setLoading] = useState(desktop)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!desktop) return
    loadStatus()
  }, [desktop])

  async function request(method, body) {
    const response = await fetch('/api/desktop/credentials', {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(payload.detail || `Credential request failed (${response.status})`)
    return payload
  }

  async function loadStatus() {
    setLoading(true)
    setError('')
    try {
      setStatus(await request('GET'))
    } catch (err) {
      setError(err.message || 'Could not read credential status.')
    } finally {
      setLoading(false)
    }
  }

  async function save() {
    const body = {}
    if (nativeKey.trim()) body.native_api_key = nativeKey
    if (sttKey.trim()) body.stt_api_key = sttKey
    if (!Object.keys(body).length) {
      setNotice('Nothing changed. Enter a replacement key before saving.')
      return
    }
    setSaving(true)
    setError('')
    setNotice('')
    try {
      setStatus(await request('PUT', body))
      setNativeKey('')
      setSttKey('')
      setNotice('Credentials saved for this host. New requests use them immediately.')
    } catch (err) {
      setError(err.message || 'Could not save credentials.')
    } finally {
      setSaving(false)
    }
  }

  async function remove(kind) {
    const label = kind === 'native' ? 'Native API key' : 'STT API key'
    if (!window.confirm(`Remove the stored ${label} from this host?`)) return
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const field = kind === 'native' ? 'remove_native_api_key' : 'remove_stt_api_key'
      setStatus(await request('PUT', { [field]: true }))
      if (kind === 'native') setNativeKey('')
      else setSttKey('')
      setNotice(`${label} removed from the encrypted credential file.`)
    } catch (err) {
      setError(err.message || 'Could not remove the credential.')
    } finally {
      setSaving(false)
    }
  }

  if (!desktop) {
    return (
      <div className="rounded-lg border border-line bg-surface p-5">
        <p className="text-[13px] font-medium text-text-bright">Open Settings in the desktop app</p>
        <p className="mt-1 text-[12px] leading-relaxed text-muted">
          Credential changes are host-local and unavailable from an ordinary browser session.
        </p>
      </div>
    )
  }

  const host = status.host_id || 'this host'
  return (
    <div className="space-y-4">
      <div className="flex items-start gap-4 rounded-lg border border-line bg-surface px-4 py-3">
        <div className="mt-0.5 h-8 w-1 rounded-full bg-accent" />
        <div>
          <p className="text-[13px] font-medium text-text-bright">Encrypted credential vault · {host}</p>
          <p className="mt-0.5 text-[12px] leading-relaxed text-muted">
            This app can only change credentials stored by its own backend. Existing values are never revealed.
          </p>
        </div>
      </div>

      {loading ? (
        <p className="py-8 text-center text-[12px] text-faint">Reading credential status…</p>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          <CredentialCard
            title="Native provider"
            description="OpenAI-compatible agent requests"
            value={nativeKey}
            onChange={setNativeKey}
            status={status.native_api_key}
            disabled={saving}
            onRemove={() => remove('native')}
          />
          <CredentialCard
            title="Speech to text"
            description="API voice transcription requests"
            value={sttKey}
            onChange={setSttKey}
            status={status.stt_api_key}
            disabled={saving}
            onRemove={() => remove('stt')}
          />
        </div>
      )}

      <p className="text-[11px] leading-relaxed text-faint">
        Leave a field blank to keep its current value. Environment variables remain higher priority and are marked below.
      </p>
      {error && <p className="text-[12px] text-danger">{error}</p>}
      {notice && <p className="text-[12px] text-accent">{notice}</p>}
      <div className="flex justify-end">
        <button
          onClick={save}
          disabled={saving || loading}
          className="rounded border border-accent/40 px-4 py-1.5 text-[12px] font-medium text-accent hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save credentials'}
        </button>
      </div>
    </div>
  )
}

function CredentialCard({ title, description, value, onChange, status, disabled, onRemove }) {
  const sourceLabel = {
    environment: 'Environment override active',
    credential: 'Stored in encrypted vault',
    yaml: 'Legacy YAML value active',
    missing: 'Not configured',
  }[status?.source] || 'Not configured'

  return (
    <section className="rounded-lg border border-line bg-panel p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h4 className="text-[13px] font-medium text-text-bright">{title}</h4>
          <p className="text-[11px] text-faint">{description}</p>
        </div>
        <span className={`rounded-full border px-2 py-0.5 text-[10px] ${
          status?.configured ? 'border-ok/30 bg-ok-soft text-ok' : 'border-line text-faint'
        }`}>
          {status?.configured ? 'Configured' : 'Missing'}
        </span>
      </div>
      <label className="mt-4 block">
        <span className="text-[11px] uppercase tracking-wide text-faint">Replace key</span>
        <input
          type="password"
          autoComplete="new-password"
          value={value}
          onChange={event => onChange(event.target.value)}
          disabled={disabled}
          placeholder={status?.configured ? '***' : 'Enter API key'}
          className="mt-1.5 w-full rounded-md border border-line bg-surface px-3 py-2 font-mono text-[12px] text-text-bright placeholder:text-faint focus:border-accent/50 focus:outline-none disabled:opacity-50"
        />
      </label>
      <div className="mt-2 flex items-center justify-between gap-2">
        <span className={status?.environment_override ? 'text-[10px] text-warn' : 'text-[10px] text-faint'}>
          {sourceLabel}
        </span>
        {status?.stored && (
          <button onClick={onRemove} disabled={disabled} className="text-[10px] text-muted hover:text-danger disabled:opacity-50">
            Remove stored key
          </button>
        )}
      </div>
    </section>
  )
}

import { useEffect, useState } from 'react'
import { api, isAbandonedRequest } from '../../api'
import { useApp } from '../../context/AppContext'

// Installs (or removes) an always-on rule and this checkout's skills in
// Antigravity's own global config (~/.gemini/config). They apply to every
// Antigravity session, in this app or not; nothing is written until the user
// presses the button.
export default function AntigravitySettings() {
  const { state } = useApp()
  const [status, setStatus] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [showRule, setShowRule] = useState(false)

  useEffect(() => {
    let cancelled = false
    setError('')
    api('GET', '/api/antigravity/customizations')
      .then(payload => { if (!cancelled) { setStatus(payload); if (payload.error) setError(payload.error) } })
      .catch(err => { if (!cancelled && !isAbandonedRequest(err)) setError(err.detail || 'Could not read Antigravity settings.') })
    return () => { cancelled = true }
  }, [state.activeHostId])

  async function act(method, done) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      setStatus(await api(method, '/api/antigravity/customizations'))
      setNotice(done)
    } catch (err) {
      if (!isAbandonedRequest(err)) setError(err.detail || 'Could not update Antigravity settings.')
    } finally {
      setBusy(false)
    }
  }

  if (!status) {
    return error
      ? <p className="text-[12px] text-danger" role="alert">{error}</p>
      : <p className="text-[13px] text-faint">Loading…</p>
  }

  const anything = status.rule_installed || status.skills_installed
  const installLabel = status.installed ? 'Reinstall' : (status.partial ? 'Update' : 'Install')

  return (
    <div className="space-y-4 max-w-2xl">
      <div>
        <h4 className="text-[13px] font-bold text-text-bright">Global rule and skills</h4>
        <p className="text-[13px] text-muted">
          Adds working habits and skills to Antigravity's own global settings on this host. They apply to
          every Antigravity session, including <code className="font-mono text-[12px]">agy</code> used on its own.
          Antigravity picks them up in new sessions.
        </p>
        <p className={`mt-1.5 text-[13px] ${status.installed ? 'text-ok' : status.partial ? 'text-warn' : 'text-faint'}`}>
          {status.installed ? 'Installed.' : status.partial ? 'Installed, but out of date.' : 'Not installed.'}
        </p>
      </div>

      <div className="space-y-2 text-[13px]">
        <div>
          <div className="text-text-bright">Rule</div>
          <div className="font-mono text-[12px] text-muted break-all">{status.rule_path}</div>
          <button onClick={() => setShowRule(v => !v)} className="text-[12px] text-muted hover:text-text-bright">
            {showRule ? '▾' : '▸'} Show text
          </button>
          {showRule && (
            <pre className="mt-1 whitespace-pre-wrap bg-surface border border-line rounded px-3 py-2 font-mono text-[12px] text-text-default">
              {status.rule_text}
            </pre>
          )}
        </div>
        <div>
          <div className="text-text-bright">Skills</div>
          <div className="font-mono text-[12px] text-muted break-all">
            {status.skills_json_path} → {status.skills_root}
          </div>
          <p className="text-muted">
            {status.skills.length ? status.skills.join(', ') : 'No skills to add.'}
            {status.excluded_skills.length > 0 && (
              <span className="text-faint">
                {' '}· left out: {status.excluded_skills.join(', ')} (needs a session id Antigravity is not given)
              </span>
            )}
          </p>
        </div>
      </div>

      <div className="flex gap-2">
        <button
          onClick={() => act('POST', 'Installed. New Antigravity sessions will use the rule and skills.')}
          disabled={busy || !!status.error}
          className="px-3 py-1 text-[12px] font-medium text-accent border border-accent/40 rounded hover:bg-accent-soft disabled:opacity-50"
        >
          {installLabel}
        </button>
        {anything && (
          <button
            onClick={() => act('DELETE', 'Removed. Other Antigravity settings were left as they were.')}
            disabled={busy || !!status.error}
            className="px-3 py-1 text-[12px] text-muted border border-line rounded hover:text-text-bright disabled:opacity-50"
          >
            Remove
          </button>
        )}
      </div>

      {notice && <p className="text-[12px] text-accent">{notice}</p>}
      {error && <p className="text-[12px] text-danger" role="alert">{error}</p>}
    </div>
  )
}

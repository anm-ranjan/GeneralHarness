import { useEffect, useState } from 'react'
import { api } from '../../api'
import { useApp } from '../../context/AppContext'
import { commandInsertText, commandUnavailableReason } from '../../commands'

export default function CommandsPanel() {
  const { state, dispatch } = useApp()
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const sessionId = state.currentSessionId
  const provider = state.currentMeta?.provider

  useEffect(() => {
    let cancelled = false
    api('GET', '/api/commands')
      .then(res => { if (!cancelled) { setData(res); setError('') } })
      .catch(err => { if (!cancelled) setError(err.detail || err.message) })
    return () => { cancelled = true }
  }, [])

  const insert = usage => {
    if (!sessionId) return
    dispatch({ type: 'RESTORE_DRAFT', payload: { sessionId, text: commandInsertText(usage), replace: true } })
  }

  if (error) return <div className="p-4 text-[13px] text-danger">{error}</div>
  if (!data) return <div className="p-4 text-[13px] text-muted italic">Loading commands…</div>

  return (
    <div className="p-3 space-y-3 text-[13px]">
      <div className="space-y-0.5 text-[12px] text-muted">
        {(data.notes || []).map(note => <p key={note}>{note}</p>)}
        {sessionId && <p>Click a command to put it in the composer.</p>}
      </div>
      <ul className="space-y-2">
        {(data.commands || []).map(command => {
          const unavailable = commandUnavailableReason(command, provider)
          return (
            <li key={command.name} className={`rounded-lg border border-line p-2.5 ${unavailable ? 'opacity-60' : ''}`}>
              <div className="flex flex-wrap items-center gap-1.5">
                {command.usage.map(usage => (
                  <button
                    key={usage}
                    type="button"
                    disabled={!sessionId}
                    onClick={() => insert(usage)}
                    title={sessionId ? 'Put in composer' : 'Select a session first'}
                    className="rounded bg-surface-hover px-1.5 py-0.5 font-mono text-[12px] text-text-bright hover:text-accent disabled:cursor-default disabled:hover:text-text-bright"
                  >
                    {usage}
                  </button>
                ))}
              </div>
              <p className="mt-1.5 text-muted leading-snug">{command.description}</p>
              {unavailable && <p className="mt-1 text-[12px] text-warn">{unavailable}</p>}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

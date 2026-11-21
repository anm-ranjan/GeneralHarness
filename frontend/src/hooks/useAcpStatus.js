import { useEffect, useState } from 'react'
import { api, isAbandonedRequest } from '../api'
import { isAcpProvider } from '../acpAgents'

// Sign-in state and the agent's own slash commands for an ACP thread
// (GET /api/sessions/{id}/acp-status). Fetched when the thread opens and again
// after each run, since a run can sign the agent in or out and refresh its
// commands. null for other providers.
export default function useAcpStatus(sessionId, provider, isRunning) {
  const [status, setStatus] = useState(null)
  const acp = isAcpProvider(provider)

  useEffect(() => {
    setStatus(null)
  }, [sessionId, provider])

  useEffect(() => {
    if (!sessionId || !acp || isRunning) return undefined
    let cancelled = false
    api('GET', `/api/sessions/${encodeURIComponent(sessionId)}/acp-status`)
      .then(data => { if (!cancelled) setStatus(data) })
      .catch(err => { if (!cancelled && !isAbandonedRequest(err)) setStatus(null) })
    return () => { cancelled = true }
  }, [sessionId, acp, isRunning])

  return acp ? status : null
}

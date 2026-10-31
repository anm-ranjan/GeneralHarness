import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useApp } from '../context/AppContext'
import { bridgeAction, normalizeBridgeHosts, stopConfirmation } from '../bridge'
import { hostTag } from '../fleet'

const BRIDGE_POLL_MS = 6000

// Bring online / take offline go to the machine serving this page, never to the
// host currently being viewed: it owns the SSH forwards. Hence bare fetch
// rather than api(), which follows the active host.
async function bridgeRequest(path, body) {
  const res = await fetch(path, body === undefined
    ? undefined
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  let data = null
  try { data = await res.json() } catch { /* empty body */ }
  if (!res.ok) {
    const error = new Error(typeof data?.detail === 'string' ? data.detail : (data?.detail?.message || `Request failed (${res.status})`))
    error.status = res.status
    error.detail = data?.detail
    throw error
  }
  return data
}

/**
 * Every machine in the fleet with the one tag it shows and the one action it
 * offers.
 *
 * Rows come from the fleet list the page already has, so they appear at once.
 * The browser's own status poll answers "is its backend serving" within
 * moments; the slower SSH check (which can take seconds for a machine that is
 * off) only decides between Not serving, Online and Offline. Whatever either
 * learns is written back to the shared host status, so the Switch list and
 * these rows never disagree.
 */
export default function useHostRoster() {
  const { state, dispatch } = useApp()
  const [bridge, setBridge] = useState({ loaded: false, hosts: [] })
  const [pending, setPending] = useState({})
  const [error, setError] = useState('')
  const statusesRef = useRef(state.fleetStatuses)
  statusesRef.current = state.fleetStatuses

  const publish = useCallback(rows => {
    for (const row of rows) {
      const previous = statusesRef.current[row.id]
      dispatch({
        type: 'SET_HOST_STATUS',
        payload: {
          hostId: row.id,
          status: {
            online: row.state === 'online',
            running: row.running,
            waitingApproval: row.waitingApproval,
            reportedId: previous?.reportedId || '',
          },
        },
      })
    }
  }, [dispatch])

  useEffect(() => {
    let cancelled = false
    let timer = null
    async function load() {
      try {
        const rows = normalizeBridgeHosts(await bridgeRequest('/api/fleet/bridge'))
        if (cancelled) return
        setBridge({ loaded: true, hosts: rows })
        publish(rows)
      } catch {
        // An older backend has no bridge route: the rows fall back to the poll.
        if (!cancelled) setBridge({ loaded: true, hosts: [] })
      }
      // After the answer, not on a timer, so a slow SSH check cannot overlap itself.
      if (!cancelled) timer = setTimeout(load, BRIDGE_POLL_MS)
    }
    load()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [publish])

  const run = useCallback(async (host, kind, force) => {
    setError('')
    setPending(prev => ({ ...prev, [host.id]: kind }))
    try {
      const next = await bridgeRequest(`/api/fleet/bridge/${encodeURIComponent(host.id)}/${kind}`, kind === 'stop' ? { force } : {})
      const [row] = normalizeBridgeHosts({ hosts: [next] })
      if (row) {
        setBridge(prev => ({ ...prev, hosts: prev.hosts.map(entry => (entry.id === row.id ? row : entry)) }))
        // The Switch list reads this, so it is ready the moment the backend is.
        publish([row])
      }
    } catch (err) {
      if (err.status === 409 && kind === 'stop') {
        // It became busy between the last check and the click: ask again.
        const d = err.detail || {}
        confirmStop({ ...host, running: d.running || 0, waitingApproval: d.waiting_approval || 0 })
      } else {
        setError(err.message)
      }
    } finally {
      setPending(prev => { const { [host.id]: _done, ...rest } = prev; return rest })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [publish])

  function confirmStop(host) {
    const confirmation = stopConfirmation(host)
    dispatch({
      type: 'OPEN_CONFIRM',
      payload: {
        title: confirmation.title,
        message: confirmation.message,
        confirmLabel: confirmation.confirmLabel,
        tone: confirmation.tone,
        onConfirm: () => run(host, 'stop', confirmation.force),
      },
    })
  }

  const act = useCallback(row => {
    if (!row.action) return
    if (row.action.kind === 'stop') confirmStop({ ...row.bridge, label: row.label })
    else run(row, row.action.kind, false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run])

  const rows = useMemo(() => state.fleetHosts.map(host => {
    const status = state.fleetStatuses[host.id]
    const bridgeRow = bridge.hosts.find(entry => entry.id === host.id) || null
    const busy = pending[host.id] || ''
    return {
      id: host.id,
      label: host.label,
      self: host.self,
      active: host.id === state.activeHostId,
      status,
      bridge: bridgeRow,
      tag: hostTag({ self: host.self, status, bridge: bridgeRow, bridgeLoaded: bridge.loaded }),
      busy,
      action: bridgeRow && !busy ? bridgeAction(bridgeRow) : null,
    }
  }), [state.fleetHosts, state.fleetStatuses, state.activeHostId, bridge, pending])

  return { rows, error, act }
}

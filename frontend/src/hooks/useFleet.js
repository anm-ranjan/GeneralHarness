import { useCallback, useEffect, useRef } from 'react'
import { hostUrl, setActiveHost } from '../api'
import {
  STATUS_TIMEOUT_MS,
  hostStatus,
  hostStorageKey,
  normalizeFleet,
  resolveActiveHost,
} from '../fleet'

const ACTIVE_HOST_KEY = 'myharness:activeHost'

function readSavedHostId() {
  try {
    return localStorage.getItem(ACTIVE_HOST_KEY) || ''
  } catch {
    return ''
  }
}

function saveHostId(hostId) {
  try {
    localStorage.setItem(ACTIVE_HOST_KEY, hostId)
  } catch {
    // Private-mode browsers just lose the preference.
  }
}

/**
 * Poll one host for reachability and blocked runs.
 *
 * Deliberately a bare fetch rather than api(): this asks about a machine the
 * user is *not* viewing, so it must not use the active host's base and must
 * not be cancelled when they switch away.
 */
async function pollHost(host) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), STATUS_TIMEOUT_MS)
  try {
    const res = await fetch(hostUrl(host.url, '/api/fleet/status'), {
      signal: controller.signal,
    })
    if (!res.ok) return { ok: false }
    const data = await res.json()
    return { ok: true, ...data }
  } catch {
    // Unreachable, tunnel down, or too slow to be useful — all "offline".
    return { ok: false }
  } finally {
    clearTimeout(timer)
  }
}

/**
 * Loads the fleet registry, keeps every host's status fresh, and switches the
 * app between machines.
 *
 * Hosts share nothing, so switching is a full teardown: in-flight requests are
 * abandoned by the api layer and every host-owned slice of state is reset by
 * the SWITCH_HOST reducer. The status poll is the one thing that spans hosts,
 * and it exists so a run left blocked on an approval somewhere else stays
 * visible instead of silently stalling.
 */
export default function useFleet(dispatch, stateRef) {
  const pollMsRef = useRef(10000)

  // The registry is read from the host that served this page, once. Reading it
  // from the active host instead would let the list of machines change under
  // the user as they move around it.
  useEffect(() => {
    let cancelled = false

    fetch('/api/fleet')
      .then(res => (res.ok ? res.json() : null))
      .then(payload => {
        if (cancelled || !payload) return
        const hosts = normalizeFleet(payload)
        if (!hosts.length) return

        const active = resolveActiveHost(hosts, readSavedHostId())
        pollMsRef.current = Math.max(2, Number(payload.poll_seconds) || 10) * 1000
        // Adopt the resolved host before anything else loads, so the very
        // first tree and health requests already target the right machine.
        setActiveHost(active)
        saveHostId(active.id)
        dispatch({ type: 'SET_FLEET', payload: { hosts, activeHostId: active.id } })
      })
      .catch(() => {
        // An older backend has no /api/fleet; single-machine mode is correct.
      })

    return () => { cancelled = true }
  }, [dispatch])

  useEffect(() => {
    let cancelled = false
    let timer = null

    async function pollAll() {
      const hosts = stateRef.current.fleetHosts
      if (!hosts.length) return
      await Promise.all(hosts.map(async host => {
        const status = hostStatus(await pollHost(host))
        if (cancelled) return
        dispatch({ type: 'SET_HOST_STATUS', payload: { hostId: host.id, status } })
      }))
    }

    function schedule() {
      // Poll on a fixed interval after completion rather than on a timer, so a
      // slow or dead host cannot stack up overlapping rounds.
      timer = setTimeout(async () => {
        await pollAll()
        if (!cancelled) schedule()
      }, pollMsRef.current)
    }

    pollAll().then(() => { if (!cancelled) schedule() })

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [dispatch, stateRef])

  const switchHost = useCallback((hostId) => {
    const state = stateRef.current
    if (hostId === state.activeHostId) return
    const host = state.fleetHosts.find(entry => entry.id === hostId)
    if (!host) return

    // Abandons every in-flight request first, so a response from the machine
    // being left cannot resolve into the machine being entered.
    setActiveHost(host)
    saveHostId(host.id)
    dispatch({ type: 'SWITCH_HOST', payload: { hostId: host.id } })
  }, [dispatch, stateRef])

  return switchHost
}

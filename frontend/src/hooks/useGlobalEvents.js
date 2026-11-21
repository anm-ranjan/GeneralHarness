import { useEffect, useRef } from 'react'
import { api, globalWsUrl } from '../api'
import { runStateNotification } from '../runStates'

// Application-level WebSocket carrying run-state changes for every session,
// independent of which session is selected. Powers sidebar activity badges
// and desktop notifications for background runs.
//
// Scoped to one host: `activeHostId` tears the socket down and reopens it
// against the machine being switched to, so run states from the previous host
// stop arriving once its sessions are no longer on screen.
export default function useGlobalEvents(dispatch, stateRef, activeHostId) {
  const wsRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    let reconnectTimer = null
    let treeTimer = null
    let hadSocket = false

    // Threads created, moved or deleted elsewhere (the API, another window,
    // an agent) reach this window only as a nudge; refetch the tree, coalescing
    // bursts so a run of changes costs one request.
    function reloadTree() {
      if (cancelled || treeTimer) return
      treeTimer = setTimeout(async () => {
        treeTimer = null
        try {
          const data = await api('GET', '/api/sessions')
          if (!cancelled) dispatch({ type: 'SET_TREE', payload: data })
        } catch {
          // The next change or reconnect retries.
        }
      }, 250)
    }

    function notify(sessionId, state) {
      try {
        if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return
        const current = stateRef.current
        const prev = current.runStates?.[sessionId]
        const title = current.sessionsById?.[sessionId]?.title
        const payload = runStateNotification(prev, sessionId, state, current.currentSessionId, title)
        if (payload) new Notification(payload.title, { body: payload.body })
      } catch {
        // Notifications are best-effort.
      }
    }

    function connect() {
      if (cancelled) return
      const socket = new WebSocket(globalWsUrl())
      wsRef.current = socket

      socket.onopen = () => {
        if (socket !== wsRef.current) return
        // Changes made while the socket was down were never announced.
        if (hadSocket) reloadTree()
        hadSocket = true
        try {
          if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
            Notification.requestPermission().catch(() => {})
          }
        } catch {
          // Environments without the Notification API.
        }
      }

      socket.onmessage = (e) => {
        if (socket !== wsRef.current) return
        try {
          const msg = JSON.parse(e.data)
          if (msg.type === 'run_state_snapshot') {
            dispatch({ type: 'SET_RUN_STATES', payload: msg.sessions || [] })
          } else if (msg.type === 'run_state') {
            if (!stateRef.current.sessionsById?.[msg.session_id]) reloadTree()
            notify(msg.session_id, msg.state)
            dispatch({
              type: 'SET_SESSION_RUN_STATE',
              payload: {
                sessionId: msg.session_id,
                state: msg.state,
                windowFocused: document.hasFocus() && document.visibilityState === 'visible',
              },
            })
          } else if (msg.type === 'native_settings') {
            dispatch({ type: 'SET_NATIVE_MODEL', payload: msg.model || '' })
          } else if (msg.type === 'session_updated') {
            dispatch({ type: 'PATCH_SESSION', payload: { sessionId: msg.session_id, changes: msg.changes || {} } })
          } else if (msg.type === 'sessions_changed') {
            reloadTree()
          } else if (msg.type === 'session_renamed') {
            dispatch({
              type: 'SET_SESSION_TITLE',
              payload: { sessionId: msg.session_id, title: msg.title },
            })
          }
        } catch (err) {
          console.error('Global WS parse error:', err)
        }
      }

      socket.onclose = () => {
        if (socket !== wsRef.current) return
        wsRef.current = null
        if (!cancelled) {
          reconnectTimer = setTimeout(connect, 5000)
        }
      }

      socket.onerror = () => {}
    }

    connect()

    return () => {
      cancelled = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (treeTimer) clearTimeout(treeTimer)
      if (wsRef.current) {
        const socket = wsRef.current
        wsRef.current = null
        socket.close()
      }
    }
  }, [dispatch, stateRef, activeHostId])
}

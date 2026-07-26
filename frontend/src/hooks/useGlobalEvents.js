import { useEffect, useRef } from 'react'
import { globalWsUrl } from '../api'
import { runStateNotification } from '../runStates'

// Application-level WebSocket carrying run-state changes for every session,
// independent of which session is selected. Powers sidebar activity badges
// and desktop notifications for background runs.
export default function useGlobalEvents(dispatch, stateRef) {
  const wsRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    let reconnectTimer = null

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
            notify(msg.session_id, msg.state)
            dispatch({
              type: 'SET_SESSION_RUN_STATE',
              payload: { sessionId: msg.session_id, state: msg.state },
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
      if (wsRef.current) {
        const socket = wsRef.current
        wsRef.current = null
        socket.close()
      }
    }
  }, [dispatch, stateRef])
}

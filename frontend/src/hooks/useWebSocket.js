import { useEffect, useRef } from 'react'
import { wsUrl } from '../api'
import { handleSessionEvent } from '../eventHandlers'

export default function useWebSocket(sessionId, dispatch, stateRef) {
  const wsRef = useRef(null)

  useEffect(() => {
    if (!sessionId) {
      if (wsRef.current) { wsRef.current.close(); wsRef.current = null }
      dispatch({ type: 'SET_WS_CONNECTED', payload: false })
      return
    }

    let cancelled = false
    let reconnectTimer = null

    function connect() {
      if (cancelled) return
      const socket = new WebSocket(wsUrl(sessionId))
      wsRef.current = socket

      socket.onopen = () => {
        if (socket !== wsRef.current) return
        dispatch({ type: 'SET_WS_CONNECTED', payload: true })
      }

      socket.onmessage = (e) => {
        if (socket !== wsRef.current) return
        try {
          const evt = JSON.parse(e.data)
          handleSessionEvent(evt, dispatch, stateRef)
        } catch (err) {
          console.error('WS parse error:', err)
        }
      }

      socket.onclose = () => {
        if (socket !== wsRef.current) return
        wsRef.current = null
        dispatch({ type: 'SET_WS_CONNECTED', payload: false })
        if (!cancelled) {
          dispatch({ type: 'INCREMENT_WS_RECONNECTS' })
          reconnectTimer = setTimeout(connect, 3000)
        }
      }

      socket.onerror = (err) => {
        console.error('WS error:', err)
      }
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
      dispatch({ type: 'SET_WS_CONNECTED', payload: false })
    }
  }, [sessionId, dispatch, stateRef])
}

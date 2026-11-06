import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { errorText } from '../projectNotes'

const SAVE_DELAY_MS = 600

// A text document behind GET/PUT `path` ({ text }) that saves while typing.
// Changing `path` (or setting it to null) flushes unsaved text to the old one
// first, so switching project or view never loses keystrokes.
export default function useAutosaveText(path) {
  const [text, setText] = useState('')
  const [status, setStatus] = useState(path ? 'loading' : 'idle') // idle | loading | saved | dirty | saving | error
  const [error, setError] = useState('')
  const timerRef = useRef(null)
  const pendingRef = useRef(null) // text not yet saved, or null
  const pathRef = useRef(path)

  const load = useCallback(async () => {
    if (!path) return
    try {
      const data = await api('GET', path)
      if (pathRef.current !== path || pendingRef.current !== null) return
      setText(data?.text || '')
      setStatus('saved')
      setError('')
    } catch (err) {
      if (pathRef.current !== path) return
      setError(errorText(err, 'Could not load.'))
      setStatus('error')
    }
  }, [path])

  const save = useCallback(async () => {
    clearTimeout(timerRef.current)
    const value = pendingRef.current
    if (value === null || !path) return
    setStatus('saving')
    try {
      await api('PUT', path, { text: value })
      if (pathRef.current !== path) return
      // Only mark clean if nothing was typed while the request ran.
      if (pendingRef.current === value) {
        pendingRef.current = null
        setStatus('saved')
      } else {
        setStatus('dirty')
      }
      setError('')
    } catch (err) {
      if (pathRef.current !== path) return
      setError(errorText(err, 'Could not save.'))
      setStatus('error')
    }
  }, [path])

  useEffect(() => {
    pathRef.current = path
    pendingRef.current = null
    setText('')
    setError('')
    setStatus(path ? 'loading' : 'idle')
    load()
    // Another window may have changed it: refresh on return, unless there
    // are unsaved edits here.
    const onFocus = () => { if (pendingRef.current === null) load() }
    window.addEventListener('focus', onFocus)
    return () => {
      window.removeEventListener('focus', onFocus)
      clearTimeout(timerRef.current)
      if (pendingRef.current !== null && path) {
        api('PUT', path, { text: pendingRef.current }).catch(() => {})
      }
      pendingRef.current = null
    }
  }, [path, load])

  const change = useCallback((value) => {
    setText(value)
    pendingRef.current = value
    setStatus('dirty')
    clearTimeout(timerRef.current)
    timerRef.current = setTimeout(save, SAVE_DELAY_MS)
  }, [save])

  // Text replaced on the server (append / undo): show it without saving.
  const replace = useCallback((value) => {
    clearTimeout(timerRef.current)
    pendingRef.current = null
    setText(value)
    setStatus('saved')
    setError('')
  }, [])

  return { text, status, error, change, save, reload: load, replace }
}

export function statusLabel(status, error) {
  return {
    idle: '',
    loading: 'Loading…',
    saved: 'Saved',
    dirty: 'Unsaved',
    saving: 'Saving…',
    error: error || 'Error',
  }[status] || ''
}

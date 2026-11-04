import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../api'

const SAVE_DELAY_MS = 600

// A single scratch note shared by every session (saved by the backend in
// data/scratchpad.json), for things like session IDs to paste into another
// session. Saves automatically while typing.
export default function ScratchNotes() {
  const [text, setText] = useState('')
  const [status, setStatus] = useState('loading') // loading | saved | dirty | saving | error
  const [error, setError] = useState('')
  const timerRef = useRef(null)
  const pendingRef = useRef(null) // text not yet saved, or null

  const load = useCallback(async () => {
    try {
      const data = await api('GET', '/api/scratchpad')
      if (pendingRef.current === null) {
        setText(data?.text || '')
        setStatus('saved')
      }
    } catch (err) {
      setError(err.detail || err.message || 'Could not load the note.')
      setStatus('error')
    }
  }, [])

  const save = useCallback(async () => {
    clearTimeout(timerRef.current)
    const value = pendingRef.current
    if (value === null) return
    setStatus('saving')
    try {
      await api('PUT', '/api/scratchpad', { text: value })
      // Only mark clean if nothing was typed while the request ran.
      if (pendingRef.current === value) {
        pendingRef.current = null
        setStatus('saved')
      } else {
        setStatus('dirty')
      }
      setError('')
    } catch (err) {
      setError(err.detail || err.message || 'Could not save the note.')
      setStatus('error')
    }
  }, [])

  useEffect(() => {
    load()
    // Another window may have changed the note: refresh on return, unless
    // there are unsaved edits here.
    const onFocus = () => { if (pendingRef.current === null) load() }
    window.addEventListener('focus', onFocus)
    return () => {
      window.removeEventListener('focus', onFocus)
      clearTimeout(timerRef.current)
      // Leaving the tab must not lose the last keystrokes.
      if (pendingRef.current !== null) {
        api('PUT', '/api/scratchpad', { text: pendingRef.current }).catch(() => {})
      }
    }
  }, [load])

  function handleChange(e) {
    const value = e.target.value
    setText(value)
    pendingRef.current = value
    setStatus('dirty')
    clearTimeout(timerRef.current)
    timerRef.current = setTimeout(save, SAVE_DELAY_MS)
  }

  const statusText = {
    loading: 'Loading…',
    saved: 'Saved',
    dirty: 'Unsaved',
    saving: 'Saving…',
    error: error || 'Error',
  }[status]

  return (
    <div className="flex h-full flex-col gap-2 p-3">
      <div className="flex items-baseline justify-between gap-3 text-[12px]">
        <p className="text-muted">Shared by all threads. Paste thread IDs, paths or snippets to reuse anywhere.</p>
        <span role="status" className={`shrink-0 ${status === 'error' ? 'text-danger' : 'text-faint'}`}>{statusText}</span>
      </div>
      <textarea
        value={text}
        onChange={handleChange}
        onBlur={save}
        disabled={status === 'loading'}
        spellCheck={false}
        placeholder="Scratch notes…"
        aria-label="Scratch notes"
        className="min-h-0 flex-1 resize-none rounded-lg border border-line bg-transparent p-3 font-mono text-[12.5px] leading-relaxed text-text-default placeholder:text-faint outline-none focus:border-accent disabled:opacity-50"
      />
    </div>
  )
}

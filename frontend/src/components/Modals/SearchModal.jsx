import { useState, useEffect, useRef, useCallback } from 'react'
import { api } from '../../api'
import { useApp } from '../../context/AppContext'
import useSelectSession from '../../hooks/useSelectSession'
import { groupHitsBySession, fieldLabel } from '../../search'

export default function SearchModal() {
  const { state, dispatch } = useApp()
  const selectSession = useSelectSession()
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState([])
  const [truncated, setTruncated] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef(null)
  const debounceRef = useRef(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const runSearch = useCallback(async (q) => {
    if (!q.trim()) {
      setHits([])
      setTruncated(false)
      return
    }
    setLoading(true)
    setError('')
    try {
      const res = await api('GET', `/api/search?q=${encodeURIComponent(q)}`)
      setHits(res.hits || [])
      setTruncated(!!res.truncated)
    } catch (err) {
      setError(err.detail || err.message)
      setHits([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => runSearch(query), 250)
    return () => clearTimeout(debounceRef.current)
  }, [query, runSearch])

  function close() {
    dispatch({ type: 'CLOSE_SEARCH' })
  }

  async function openHit(hit) {
    try {
      if (Number.isInteger(hit.event_index)) {
        dispatch({
          type: 'SET_SCROLL_TARGET',
          payload: { sessionId: hit.session_id, eventIndex: hit.event_index },
        })
      }
      await selectSession(hit.session_id)
      close()
    } catch (err) {
      dispatch({ type: 'CLEAR_SCROLL_TARGET' })
      setError(err.detail || err.message)
    }
  }

  function onKeyDown(e) {
    if (e.key === 'Escape') close()
  }

  const grouped = groupHitsBySession(hits)

  return (
    <div className="fixed inset-0 z-50 glass-overlay flex items-start justify-center p-5 pt-[12vh]" onMouseDown={close}>
      <section
        className="w-full max-w-[640px] rounded-lg border border-line bg-bg shadow-2xl overflow-hidden flex flex-col max-h-[70vh]"
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <div className="px-4 py-3 border-b border-line">
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search all thread transcripts…"
            className="w-full bg-transparent text-[14px] text-text-bright placeholder:text-faint outline-none"
          />
        </div>
        <div className="flex-1 overflow-y-auto">
          {error && <p className="px-4 py-3 text-[13px] text-danger">{error}</p>}
          {!error && query.trim() && !loading && hits.length === 0 && (
            <p className="px-4 py-6 text-[13px] text-faint text-center italic">No matches found.</p>
          )}
          {!query.trim() && (
            <p className="px-4 py-6 text-[13px] text-faint text-center italic">
              Search prompts, responses, tool calls, and file paths across every thread.
            </p>
          )}
          {grouped.map((group) => (
            <div key={group.session.session_id} className="border-b border-line/50">
              <div className="px-4 pt-2.5 pb-1 text-[11px] text-muted">
                <span className="font-medium text-text-default">{group.session.project_name}</span>
                <span className="text-faint"> / {group.session.task_name} / </span>
                <span className="font-medium text-text-default">{group.session.session_title}</span>
              </div>
              {group.items.map((hit, i) => (
                <button
                  key={hit.session_id + '-' + hit.event_index + '-' + i}
                  onClick={() => openHit(hit)}
                  className="w-full text-left px-4 py-2 hover:bg-surface-hover transition-colors flex items-start gap-2"
                >
                  <span className="shrink-0 mt-0.5 px-1.5 py-0.5 text-[10px] font-semibold uppercase rounded bg-accent/15 text-accent">
                    {fieldLabel(hit.matched_field)}
                  </span>
                  <span className="flex-1 min-w-0 text-[12px] text-muted leading-5 break-words">{hit.snippet}</span>
                </button>
              ))}
            </div>
          ))}
        </div>
        <div className="px-4 py-2 border-t border-line flex items-center justify-between text-[11px] text-faint">
          <span>{loading ? 'Searching…' : `${hits.length} match${hits.length === 1 ? '' : 'es'}${truncated ? '+' : ''}`}</span>
          <span>Esc to close</span>
        </div>
      </section>
    </div>
  )
}

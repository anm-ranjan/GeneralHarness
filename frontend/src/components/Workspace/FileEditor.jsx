import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useAppSelector, useAppDispatch } from '../../context/AppContext'
import { api } from '../../api'

const TAB = '  '

// Unsaved buffers survive unmount (closing the workspace panel, switching
// sessions and coming back) so edits are only ever discarded deliberately.
const drafts = new Map()

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function countMatches(text, needle, caseSensitive) {
  if (!needle) return 0
  const re = new RegExp(escapeRegExp(needle), caseSensitive ? 'g' : 'gi')
  return (text.match(re) || []).length
}

export default function FileEditor({ path }) {
  const dispatch = useAppDispatch()
  const expanded = useAppSelector(s => s.workspaceEditorExpanded)
  const sessionRunning = useAppSelector(s => s.isRunning)

  const [text, setText] = useState('')
  const [baseHash, setBaseHash] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [dirty, setDirty] = useState(false)

  const [findOpen, setFindOpen] = useState(false)
  const [replaceOpen, setReplaceOpen] = useState(false)
  const [find, setFind] = useState('')
  const [replace, setReplace] = useState('')
  const [caseSensitive, setCaseSensitive] = useState(false)

  const textareaRef = useRef(null)
  const gutterRef = useRef(null)
  const findInputRef = useRef(null)
  const searchFromRef = useRef(0)

  const name = path.split(/[\\/]/).pop()

  const load = useCallback((discardDraft = false) => {
    setLoading(true)
    setError(null)
    setConflict(false)
    if (discardDraft) drafts.delete(path)
    api('GET', `/api/workspace/file?path=${encodeURIComponent(path)}&full=true`)
      .then(data => {
        const draft = drafts.get(path)
        if (draft) {
          setText(draft.text)
          setBaseHash(draft.baseHash)
          setDirty(true)
          return
        }
        setText(data.content ?? '')
        setBaseHash(data.content_hash ?? null)
        setDirty(false)
      })
      .catch(err => setError(err.detail || err.message || 'Failed to load file'))
      .finally(() => setLoading(false))
  }, [path])

  useEffect(() => { load() }, [load])

  const applyText = useCallback(next => {
    setText(next)
    setDirty(true)
    drafts.set(path, { text: next, baseHash })
  }, [path, baseHash])

  const close = useCallback(() => {
    if (dirty && !window.confirm(`Discard unsaved changes to ${name}?`)) return
    drafts.delete(path)
    dispatch({ type: 'CLOSE_WORKSPACE_EDITOR' })
  }, [dirty, name, path, dispatch])

  const reload = useCallback(() => {
    if (dirty && !window.confirm('Discard your unsaved changes and reload from disk?')) return
    load(true)
  }, [dirty, load])

  const save = useCallback(async (force = false) => {
    if (saving) return
    setSaving(true)
    setError(null)
    try {
      const data = await api('PUT', '/api/workspace/file', {
        path,
        content: text,
        base_hash: force ? null : baseHash,
      })
      setBaseHash(data.content_hash ?? null)
      setDirty(false)
      setConflict(false)
      drafts.delete(path)
    } catch (err) {
      if (err.status === 409) {
        setConflict(true)
      } else {
        setError(err.detail || err.message || 'Failed to save file')
      }
    } finally {
      setSaving(false)
    }
  }, [saving, path, text, baseHash])

  // Unsaved work must survive a window close the same way it survives the X.
  useEffect(() => {
    if (!dirty) return undefined
    const warn = event => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  function onEditorKeyDown(event) {
    const mod = event.metaKey || event.ctrlKey
    if (mod && event.key.toLowerCase() === 's') {
      event.preventDefault()
      save()
      return
    }
    if (mod && event.key.toLowerCase() === 'f') {
      event.preventDefault()
      setFindOpen(true)
      setTimeout(() => findInputRef.current?.select(), 0)
      return
    }
    if (mod && event.key.toLowerCase() === 'h') {
      event.preventDefault()
      setFindOpen(true)
      setReplaceOpen(true)
      setTimeout(() => findInputRef.current?.select(), 0)
      return
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      if (findOpen) {
        setFindOpen(false)
        setReplaceOpen(false)
        textareaRef.current?.focus()
      } else {
        close()
      }
      return
    }
    if (event.key === 'Tab') {
      event.preventDefault()
      const el = event.target
      const { selectionStart: start, selectionEnd: end } = el
      const next = `${text.slice(0, start)}${TAB}${text.slice(end)}`
      applyText(next)
      requestAnimationFrame(() => {
        el.selectionStart = el.selectionEnd = start + TAB.length
      })
    }
  }

  const findNext = useCallback((from = null) => {
    const el = textareaRef.current
    if (!find || !el) return false
    const haystack = caseSensitive ? text : text.toLowerCase()
    const needle = caseSensitive ? find : find.toLowerCase()
    const start = from === null ? searchFromRef.current : from
    let index = haystack.indexOf(needle, start)
    if (index === -1) index = haystack.indexOf(needle, 0)
    if (index === -1) return false
    el.focus()
    el.setSelectionRange(index, index + find.length)
    searchFromRef.current = index + find.length
    // Scroll the hit into view: the textarea only auto-scrolls on typing.
    const before = text.slice(0, index).split('\n').length - 1
    const lineHeight = 18
    const target = before * lineHeight - el.clientHeight / 2
    el.scrollTop = Math.max(0, target)
    return true
  }, [find, text, caseSensitive])

  const replaceCurrent = useCallback(() => {
    const el = textareaRef.current
    if (!find || !el) return
    const selected = text.slice(el.selectionStart, el.selectionEnd)
    const matches = caseSensitive
      ? selected === find
      : selected.toLowerCase() === find.toLowerCase()
    if (!matches) {
      findNext()
      return
    }
    const start = el.selectionStart
    const next = `${text.slice(0, start)}${replace}${text.slice(el.selectionEnd)}`
    applyText(next)
    searchFromRef.current = start + replace.length
    requestAnimationFrame(() => {
      el.focus()
      el.setSelectionRange(start + replace.length, start + replace.length)
    })
  }, [find, replace, text, caseSensitive, findNext, applyText])

  const replaceAll = useCallback(() => {
    if (!find) return
    const re = new RegExp(escapeRegExp(find), caseSensitive ? 'g' : 'gi')
    const next = text.replace(re, () => replace)
    if (next === text) return
    applyText(next)
    searchFromRef.current = 0
  }, [find, replace, text, caseSensitive, applyText])

  const lineCount = useMemo(() => text.split('\n').length, [text])
  const matchCount = useMemo(
    () => countMatches(text, find, caseSensitive),
    [text, find, caseSensitive],
  )

  const gutter = useMemo(
    () => Array.from({ length: lineCount }, (_, i) => i + 1).join('\n'),
    [lineCount],
  )

  function syncScroll(event) {
    if (gutterRef.current) gutterRef.current.scrollTop = event.target.scrollTop
  }

  const iconButton = 'p-1 text-muted hover:text-text-bright transition-colors disabled:opacity-30'

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-1 px-2 py-2 border-b border-line">
        <button onClick={close} className={iconButton} title="Back to files">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="15 18 9 12 15 6" />
          </svg>
        </button>
        <span className="text-[12px] font-mono text-text-bright truncate" title={path}>{name}</span>
        {dirty && <span className="shrink-0 h-1.5 w-1.5 rounded-full bg-accent" title="Unsaved changes" />}
        <span className="ml-auto flex items-center gap-1">
          <button
            onClick={() => save()}
            disabled={!dirty || saving || loading}
            className="px-2 py-0.5 text-[11px] rounded bg-accent/15 text-accent hover:bg-accent/25 transition-colors disabled:opacity-30"
            title="Save (Ctrl/Cmd+S)"
          >
            {saving ? 'Saving...' : 'Save'}
          </button>
          <button
            onClick={() => setFindOpen(v => !v)}
            className={iconButton}
            title="Find / replace (Ctrl/Cmd+F)"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
          </button>
          <button
            onClick={() => dispatch({ type: 'TOGGLE_WORKSPACE_EDITOR_EXPANDED' })}
            className={iconButton}
            title={expanded ? 'Collapse to panel' : 'Expand editor'}
          >
            {expanded ? (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="9 3 9 9 3 9" /><polyline points="15 21 15 15 21 15" />
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="15 3 21 3 21 9" /><polyline points="9 21 3 21 3 15" />
              </svg>
            )}
          </button>
          <button onClick={close} className={iconButton} title="Close editor">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </span>
      </div>

      {findOpen && (
        <div className="flex flex-wrap items-center gap-1 px-2 py-1.5 border-b border-line/60 bg-surface/40">
          <input
            ref={findInputRef}
            value={find}
            onChange={e => { setFind(e.target.value); searchFromRef.current = 0 }}
            onKeyDown={e => {
              if (e.key === 'Enter' && e.isComposing) return
              if (e.key === 'Enter') { e.preventDefault(); findNext() }
              if (e.key === 'Escape') { e.preventDefault(); setFindOpen(false); setReplaceOpen(false); textareaRef.current?.focus() }
            }}
            placeholder="Find"
            className="w-28 px-1.5 py-0.5 text-[11px] font-mono bg-surface border border-line rounded text-text outline-none focus:border-accent/60"
          />
          <button onClick={() => findNext()} disabled={!matchCount} className="px-1.5 py-0.5 text-[11px] text-muted hover:text-text-bright disabled:opacity-30">Next</button>
          <button
            onClick={() => setCaseSensitive(v => !v)}
            className={`px-1.5 py-0.5 text-[11px] rounded ${caseSensitive ? 'bg-accent/15 text-accent' : 'text-muted hover:text-text-bright'}`}
            title="Match case"
          >
            Aa
          </button>
          <span className="text-[10px] text-faint">{find ? `${matchCount} match${matchCount === 1 ? '' : 'es'}` : ''}</span>
          <button
            onClick={() => setReplaceOpen(v => !v)}
            className="ml-auto px-1.5 py-0.5 text-[11px] text-muted hover:text-text-bright"
          >
            {replaceOpen ? 'Hide replace' : 'Replace...'}
          </button>
          {replaceOpen && (
            <div className="flex items-center gap-1 w-full">
              <input
                value={replace}
                onChange={e => setReplace(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && e.isComposing) return
                  if (e.key === 'Enter') { e.preventDefault(); replaceCurrent() }
                  if (e.key === 'Escape') { e.preventDefault(); setFindOpen(false); setReplaceOpen(false); textareaRef.current?.focus() }
                }}
                placeholder="Replace with"
                className="w-28 px-1.5 py-0.5 text-[11px] font-mono bg-surface border border-line rounded text-text outline-none focus:border-accent/60"
              />
              <button onClick={replaceCurrent} disabled={!matchCount} className="px-1.5 py-0.5 text-[11px] text-muted hover:text-text-bright disabled:opacity-30">Replace</button>
              <button onClick={replaceAll} disabled={!matchCount} className="px-1.5 py-0.5 text-[11px] text-muted hover:text-text-bright disabled:opacity-30">All</button>
            </div>
          )}
        </div>
      )}

      {conflict && (
        <div className="flex items-center gap-2 px-2 py-1.5 border-b border-line/60 bg-warn/10 text-[11px] text-warn">
          <span className="flex-1">This file changed on disk since you opened it.</span>
          <button onClick={reload} className="px-1.5 py-0.5 rounded hover:bg-warn/15">Reload</button>
          <button onClick={() => save(true)} className="px-1.5 py-0.5 rounded hover:bg-warn/15">Overwrite</button>
        </div>
      )}

      {sessionRunning && (
        <div className="px-2 py-1 border-b border-line/60 text-[10px] text-faint">
          A run is active — the agent may write this file while you edit.
        </div>
      )}

      {error && (
        <div className="px-2 py-1.5 border-b border-line/60 text-[11px] text-danger">{error}</div>
      )}

      <div className="flex-1 flex min-h-0 overflow-hidden">
        {loading ? (
          <div className="p-2 text-[11px] text-muted animate-pulse">Loading...</div>
        ) : (
          <>
            <pre
              ref={gutterRef}
              aria-hidden="true"
              className="shrink-0 overflow-hidden select-none px-2 py-2 text-[12px] font-mono leading-[18px] text-right text-faint bg-surface/30 border-r border-line/50"
            >
              {gutter}
            </pre>
            <textarea
              ref={textareaRef}
              value={text}
              onChange={e => applyText(e.target.value)}
              onKeyDown={onEditorKeyDown}
              onScroll={syncScroll}
              spellCheck={false}
              wrap="off"
              className="flex-1 min-w-0 resize-none overflow-auto px-2 py-2 text-[12px] font-mono leading-[18px] text-text bg-transparent outline-none"
            />
          </>
        )}
      </div>
    </div>
  )
}

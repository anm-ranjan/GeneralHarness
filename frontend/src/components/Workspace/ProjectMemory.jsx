import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../api'
import useSelectSession from '../../hooks/useSelectSession'
import { errorText, memoryDate, parseTags, projectPath } from '../../projectNotes'

const EMPTY_DRAFT = { title: '', description: '', body: '', tags: '' }

const inputClass = 'w-full rounded-md border border-line bg-transparent px-2 py-1.5 text-[12.5px] text-text-default placeholder:text-faint outline-none focus:border-accent'

function PinIcon({ filled }) {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill={filled ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 17v5" /><path d="M9 3h6l-1 7 4 3v2H6v-2l4-3z" />
    </svg>
  )
}

// Notes > "Memory - <project>": the project's saved memory notes. Pinned notes
// go into every session's prompt in full; the model looks up the others.
export default function ProjectMemory({ project, focusId, onFocusHandled, revision, sessionId, notify }) {
  const selectSession = useSelectSession()
  const [entries, setEntries] = useState(null)
  const [limit, setLimit] = useState(200)
  const [error, setError] = useState('')
  const [editing, setEditing] = useState(null) // null | 'new' | memory id
  const [draft, setDraft] = useState(EMPTY_DRAFT)
  const [busy, setBusy] = useState(false)
  const [formError, setFormError] = useState('')
  const base = projectPath(project.id, 'memory')

  const load = useCallback(async () => {
    try {
      const data = await api('GET', base)
      setEntries(data?.entries || [])
      if (data?.limit) setLimit(data.limit)
      setError('')
    } catch (err) {
      setError(errorText(err, 'Could not load project memory.'))
    }
  }, [base])

  useEffect(() => {
    setEntries(null)
    setEditing(null)
    load()
    const onFocus = () => load()
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [load])

  // /remember in this session saved a note: refresh (the mount load above
  // already covers the notes that existed when the view opened).
  const revisionRef = useRef(revision)
  useEffect(() => {
    if (revisionRef.current === revision) return
    revisionRef.current = revision
    load()
  }, [revision, load])

  const startEdit = useCallback((entry) => {
    setFormError('')
    if (!entry) {
      setEditing('new')
      setDraft(EMPTY_DRAFT)
      return
    }
    setEditing(entry.id)
    setDraft({
      title: entry.title || '',
      description: entry.description || '',
      body: entry.body || '',
      tags: (entry.tags || []).join(', '),
    })
  }, [])

  // Opened from the transcript ("Edit") or a save notice ("View").
  useEffect(() => {
    if (!focusId || !entries) return
    const entry = entries.find(e => e.id === focusId)
    if (entry) startEdit(entry)
    onFocusHandled()
  }, [focusId, entries, startEdit, onFocusHandled])

  async function submit(e) {
    e.preventDefault()
    if (!draft.title.trim() && !draft.body.trim()) {
      setFormError('Add a title or some text.')
      return
    }
    setBusy(true)
    setFormError('')
    const fields = {
      title: draft.title.trim(),
      description: draft.description.trim(),
      body: draft.body,
      tags: parseTags(draft.tags),
    }
    try {
      if (editing === 'new') {
        await api('POST', base, {
          ...fields,
          title: fields.title || null,
          describe: !fields.description,
          source_session_id: sessionId || null,
        })
      } else {
        if (!fields.title) { setFormError('A note needs a title.'); setBusy(false); return }
        await api('PATCH', `${base}/${encodeURIComponent(editing)}`, fields)
      }
      setEditing(null)
      await load()
    } catch (err) {
      setFormError(errorText(err, 'Could not save the note.'))
    } finally {
      setBusy(false)
    }
  }

  async function togglePin(entry) {
    try {
      await api('PATCH', `${base}/${encodeURIComponent(entry.id)}`, { pinned: !entry.pinned })
      await load()
    } catch (err) {
      setError(errorText(err, 'Could not change the pin.'))
    }
  }

  async function remove(entry) {
    try {
      const removed = await api('DELETE', `${base}/${encodeURIComponent(entry.id)}`)
      if (editing === entry.id) setEditing(null)
      await load()
      notify({
        text: `Deleted "${entry.title}".`,
        actions: [{
          label: 'Undo',
          run: async () => {
            await api('POST', `${base}/restore`, { entry: removed })
            await load()
          },
        }],
      })
    } catch (err) {
      setError(errorText(err, 'Could not delete the note.'))
    }
  }

  async function openSource(entry) {
    try {
      await selectSession(entry.source_session_id)
    } catch {
      setError('That thread no longer exists.')
    }
  }

  if (editing) {
    return (
      <form onSubmit={submit} className="flex min-h-0 flex-1 flex-col gap-2">
        <input value={draft.title} onChange={e => setDraft(d => ({ ...d, title: e.target.value }))}
          maxLength={120} placeholder={editing === 'new' ? 'Title (default: the first line)' : 'Title'}
          aria-label="Title" className={inputClass} autoFocus />
        <input value={draft.description} onChange={e => setDraft(d => ({ ...d, description: e.target.value }))}
          maxLength={300} placeholder={editing === 'new' ? 'One line: when is this useful? (written for you if empty)' : 'One line: when is this useful?'}
          aria-label="Description" className={inputClass} />
        <textarea value={draft.body} onChange={e => setDraft(d => ({ ...d, body: e.target.value }))}
          maxLength={20000} spellCheck={false} placeholder="What was found, with paths, commands and numbers…"
          aria-label="Note" className={`${inputClass} min-h-0 flex-1 resize-none p-3 font-mono leading-relaxed`} />
        <input value={draft.tags} onChange={e => setDraft(d => ({ ...d, tags: e.target.value }))}
          placeholder="Tags, comma separated" aria-label="Tags" className={inputClass} />
        {formError && <p className="text-[12px] text-danger">{formError}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={() => setEditing(null)} disabled={busy}
            className="rounded-md px-3 py-1.5 text-[12px] text-muted hover:text-text-bright hover:bg-surface-hover transition-colors">Cancel</button>
          <button type="submit" disabled={busy}
            className="rounded-md bg-accent/15 px-3 py-1.5 text-[12px] font-medium text-accent hover:bg-accent/25 transition-colors disabled:opacity-50">
            {busy ? 'Saving…' : editing === 'new' ? 'Add note' : 'Save'}
          </button>
        </div>
      </form>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex items-baseline justify-between gap-3 text-[12px]">
        <p className="text-muted">
          Findings saved from threads in this project. Pinned notes are always given to the model; it looks up the others when they seem relevant. Save more with <span className="font-mono">/remember</span>.
        </p>
        <button type="button" onClick={() => startEdit(null)} disabled={entries !== null && entries.length >= limit}
          className="shrink-0 rounded-md bg-accent/15 px-2.5 py-1 font-medium text-accent hover:bg-accent/25 transition-colors disabled:opacity-50">
          Add note
        </button>
      </div>
      {error && <p className="text-[12px] text-danger">{error}</p>}
      {entries === null && !error && <p className="text-[12.5px] italic text-muted">Loading…</p>}
      {entries?.length === 0 && (
        <p className="text-[12.5px] italic text-faint">No notes yet.</p>
      )}
      {entries?.length > 0 && (
        <ul className="min-h-0 flex-1 space-y-1.5 overflow-y-auto">
          {entries.map(entry => (
            <li key={entry.id} className="group rounded-lg border border-line p-2.5">
              <div className="flex items-start gap-2">
                <button type="button" onClick={() => togglePin(entry)}
                  title={entry.pinned ? 'Unpin: the model looks it up when relevant' : 'Pin: always give it to the model'}
                  aria-label={entry.pinned ? 'Unpin' : 'Pin'} aria-pressed={entry.pinned}
                  className={`mt-0.5 shrink-0 transition-colors ${entry.pinned ? 'text-accent' : 'text-faint hover:text-muted'}`}>
                  <PinIcon filled={entry.pinned} />
                </button>
                <button type="button" onClick={() => startEdit(entry)} className="min-w-0 flex-1 text-left">
                  <p className="truncate text-[13px] font-medium text-text-bright" title={entry.title}>{entry.title}</p>
                  {entry.description && <p className="mt-0.5 text-[12px] leading-snug text-muted">{entry.description}</p>}
                </button>
                <span className="flex shrink-0 gap-2 text-[12px] opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                  <button type="button" onClick={() => startEdit(entry)} className="text-muted hover:text-accent">Edit</button>
                  <button type="button" onClick={() => remove(entry)} className="text-muted hover:text-danger">Delete</button>
                </span>
              </div>
              <p className="mt-1 pl-[21px] text-[11.5px] text-faint">
                {memoryDate(entry)}
                {(entry.tags || []).length > 0 && <> · {entry.tags.join(', ')}</>}
                {entry.source_session_id && (
                  <> · from <button type="button" onClick={() => openSource(entry)} className="hover:text-accent underline-offset-2 hover:underline">
                    {entry.source_session_title || 'a thread'}
                  </button></>
                )}
              </p>
            </li>
          ))}
        </ul>
      )}
      {entries?.length > 0 && <p className="text-right text-[11.5px] text-faint">{entries.length} / {limit}</p>}
    </div>
  )
}

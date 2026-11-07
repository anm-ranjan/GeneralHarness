import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api'
import { useApp } from '../../context/AppContext'
import useAutosaveText, { statusLabel } from '../../hooks/useAutosaveText'
import {
  INSTRUCTIONS_MAX_CHARS, currentProject, errorText, pickNoteText, projectPath, viewLabel,
} from '../../projectNotes'
import ProjectMemory from './ProjectMemory'

const NOTICE_MS = 15000

const textareaClass = 'min-h-0 flex-1 resize-none rounded-lg border border-line bg-transparent p-3 font-mono text-[12.5px] leading-relaxed text-text-default placeholder:text-faint outline-none focus:border-accent disabled:opacity-50'

// Workspace > Notes. Three views:
// - Scratch: one note shared by every session (data/scratchpad.json), with
//   "Save as Project Memory" / "Save as Project Instructions" for the current project.
// - Instructions - <project>: a project description and/or rules, added to
//   every session prompt in the project.
// - Memory - <project>: notes saved from sessions (see ProjectMemory).
// Chats have no project, so only Scratch is available there.
export default function ScratchNotes() {
  const { state, dispatch } = useApp()
  const project = useMemo(() => currentProject(state.currentMeta, state.projects), [state.currentMeta, state.projects])
  const [view, setView] = useState('scratch')
  const [focusId, setFocusId] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState('')
  const [hasSelection, setHasSelection] = useState(false)
  const textareaRef = useRef(null)
  const activeView = project ? view : 'scratch'

  const scratch = useAutosaveText('/api/scratchpad')
  const instructions = useAutosaveText(activeView === 'instructions' ? projectPath(project.id, 'instructions') : null)

  const memoryRevision = useMemo(
    () => state.stageItems.reduce((n, item) => n + (item.type === 'memory_saved' ? 1 : 0), 0),
    [state.stageItems],
  )

  // Opened from the transcript: "Edit" on a saved-memory notice.
  useEffect(() => {
    const target = state.notesTarget
    if (!target) return
    if (target.view && project) setView(target.view)
    if (target.memoryId) setFocusId(target.memoryId)
    dispatch({ type: 'CLEAR_NOTES_TARGET' })
  }, [state.notesTarget, project, dispatch])

  useEffect(() => {
    if (!notice) return undefined
    const timer = setTimeout(() => setNotice(null), NOTICE_MS)
    return () => clearTimeout(timer)
  }, [notice])

  const notify = useCallback(next => setNotice({ ...next, key: Date.now() }), [])
  const clearFocus = useCallback(() => setFocusId(null), [])

  async function runAction(action) {
    try {
      await action.run()
      if (action.done) {
        setNotice({ text: action.done, key: Date.now() })
      } else {
        setNotice(current => current && { ...current, actions: current.actions.filter(a => a !== action) })
      }
    } catch (err) {
      setNotice({ text: errorText(err, 'That did not work.'), error: true, key: Date.now() })
    }
  }

  function trackSelection(e) {
    setHasSelection(e.target.selectionEnd > e.target.selectionStart)
  }

  async function saveAs(kind) {
    if (!project) return
    const el = textareaRef.current
    const pick = pickNoteText(scratch.text, el?.selectionStart, el?.selectionEnd)
    if (!pick.text) return
    const projectId = project.id
    const label = viewLabel(kind === 'memory' ? 'Memory' : 'Instructions', project.name)
    const clear = {
      label: pick.selection ? 'Remove from note' : 'Clear note',
      run: () => { scratch.change(pick.selection ? pick.remainder : ''); setHasSelection(false) },
    }
    setBusy(kind)
    try {
      if (kind === 'memory') {
        const entry = await api('POST', projectPath(projectId, 'memory'), {
          body: pick.text, describe: true, source_session_id: state.currentSessionId || null,
        })
        notify({
          text: `Saved to ${label}: ${entry.title}`,
          actions: [
            { label: 'Undo', done: 'Removed from memory.', run: () => api('DELETE', projectPath(projectId, `memory/${encodeURIComponent(entry.id)}`)) },
            { label: 'View', run: () => { setView('memory'); setFocusId(entry.id) } },
            clear,
          ],
        })
      } else {
        const saved = await api('POST', projectPath(projectId, 'instructions/append'), { text: pick.text })
        notify({
          text: `Added to ${label}.`,
          actions: [
            {
              label: 'Undo',
              done: 'Instructions restored.',
              run: async () => {
                await api('PUT', projectPath(projectId, 'instructions'), { text: saved.previous_text || '' })
                instructions.replace(saved.previous_text || '')
              },
            },
            { label: 'View', run: () => setView('instructions') },
            clear,
          ],
        })
      }
    } catch (err) {
      notify({ text: errorText(err, 'Could not save.'), error: true })
    } finally {
      setBusy('')
    }
  }

  const views = [
    { id: 'scratch', label: 'Scratch', full: 'Scratch: shared by all threads' },
    { id: 'instructions', label: viewLabel('Instructions', project?.name), full: viewLabel('Instructions', project?.name) },
    { id: 'memory', label: viewLabel('Memory', project?.name), full: viewLabel('Memory', project?.name) },
  ]
  const textView = activeView === 'scratch' ? scratch : activeView === 'instructions' ? instructions : null
  const unavailable = 'Available in project sessions'
  const canSave = Boolean(project) && Boolean(scratch.text.trim()) && !busy

  return (
    <div className="flex h-full flex-col gap-2 p-3">
      <div className="flex items-center justify-between gap-3">
        <div role="tablist" aria-label="Notes" className="flex min-w-0 rounded-md border border-line p-0.5 text-[12px]">
          {views.map(v => {
            const disabled = v.id !== 'scratch' && !project
            const selected = activeView === v.id
            return (
              <button
                key={v.id}
                type="button"
                role="tab"
                aria-selected={selected}
                disabled={disabled}
                onClick={() => setView(v.id)}
                title={disabled ? unavailable : v.full}
                className={`min-w-0 truncate rounded px-2 py-0.5 font-medium transition-colors ${
                  v.id === 'scratch' ? 'shrink-0' : 'max-w-[11rem]'
                } ${selected ? 'bg-accent/15 text-accent' : 'text-muted hover:text-text-bright hover:bg-surface-hover'} disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent disabled:hover:text-muted`}
              >
                {v.label}
              </button>
            )
          })}
        </div>
        {textView && (
          <span role="status" className={`shrink-0 text-[12px] ${textView.status === 'error' ? 'text-danger' : 'text-faint'}`}>
            {statusLabel(textView.status, textView.error)}
          </span>
        )}
      </div>

      {activeView === 'scratch' && (
        <>
          <p className="text-[12px] text-muted">Shared by all threads. Paste thread IDs, paths or snippets to reuse anywhere.</p>
          <textarea
            ref={textareaRef}
            value={scratch.text}
            onChange={e => { scratch.change(e.target.value); trackSelection(e) }}
            onSelect={trackSelection}
            onBlur={scratch.save}
            disabled={scratch.status === 'loading'}
            spellCheck={false}
            placeholder="Scratch notes…"
            aria-label="Scratch notes"
            className={textareaClass}
          />
        </>
      )}

      {activeView === 'instructions' && project && (
        <>
          <p className="text-[12px] text-muted">
            Instructions for every thread in {project.name}: describe the project and add any rules or conventions. The model uses the description as background and follows the rules unless your message says otherwise; changes apply from the next message.
          </p>
          <textarea
            value={instructions.text}
            onChange={e => instructions.change(e.target.value)}
            onBlur={instructions.save}
            disabled={instructions.status === 'loading'}
            maxLength={INSTRUCTIONS_MAX_CHARS}
            spellCheck={false}
            placeholder={'No instructions yet. For example:\nThis project simulates … with LS-DYNA. Input decks are in models/, results in runs/.\n\nRules:\n- Use SI units in every answer.\n- Never overwrite files in models/; copy them to runs/ first.'}
            aria-label={viewLabel('Instructions', project.name)}
            className={textareaClass}
          />
          <p className="text-right text-[11.5px] text-faint">{instructions.text.length.toLocaleString()} / {INSTRUCTIONS_MAX_CHARS.toLocaleString()}</p>
        </>
      )}

      {activeView === 'memory' && project && (
        <ProjectMemory
          project={project}
          focusId={focusId}
          onFocusHandled={clearFocus}
          revision={memoryRevision}
          sessionId={state.currentSessionId}
          notify={notify}
        />
      )}

      {notice && (
        <div role="status" className={`flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border px-2.5 py-1.5 text-[12px] ${
          notice.error ? 'border-danger/40 text-danger' : 'border-line text-muted'
        }`}>
          <span className="min-w-0 flex-1 break-words">{notice.text}</span>
          {(notice.actions || []).map(action => (
            <button key={action.label} type="button" onClick={() => runAction(action)}
              className="shrink-0 font-medium text-text-bright hover:text-accent transition-colors">
              {action.label}
            </button>
          ))}
          <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss" className="shrink-0 text-faint hover:text-text-bright">✕</button>
        </div>
      )}

      {activeView === 'scratch' && (
        <div className="flex flex-wrap items-center justify-end gap-2 text-[12px]">
          {project && hasSelection && <span className="mr-auto text-faint">Saves the selected text</span>}
          <button type="button" onClick={() => saveAs('memory')} disabled={!canSave}
            title={project ? viewLabel('Save to Memory', project.name) : unavailable}
            className="rounded-md border border-line px-2.5 py-1 font-medium text-muted hover:text-text-bright hover:bg-surface-hover transition-colors disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent">
            {busy === 'memory' ? 'Saving…' : 'Save as Project Memory'}
          </button>
          <button type="button" onClick={() => saveAs('instructions')} disabled={!canSave}
            title={project ? viewLabel('Add to Instructions', project.name) : unavailable}
            className="rounded-md border border-line px-2.5 py-1 font-medium text-muted hover:text-text-bright hover:bg-surface-hover transition-colors disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent">
            {busy === 'instructions' ? 'Saving…' : 'Save as Project Instructions'}
          </button>
        </div>
      )}
    </div>
  )
}

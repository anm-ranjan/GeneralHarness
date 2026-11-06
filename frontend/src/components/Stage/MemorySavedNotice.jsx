import { memo, useState } from 'react'
import { api } from '../../api'
import { useAppDispatch } from '../../context/AppContext'
import { errorText, projectPath, viewLabel } from '../../projectNotes'

// /remember saved a project memory note. Edit opens it in Notes > Memory;
// Undo deletes it again.
function MemorySavedNotice({ memory, projectId, projectName }) {
  const dispatch = useAppDispatch()
  const [state, setState] = useState('saved') // saved | removing | removed | error
  const [error, setError] = useState('')
  const id = memory?.id

  async function undo() {
    setState('removing')
    try {
      await api('DELETE', projectPath(projectId, `memory/${encodeURIComponent(id)}`))
      setState('removed')
    } catch (err) {
      if (err.status === 404) { setState('removed'); return }
      setError(errorText(err, 'Could not remove the note.'))
      setState('error')
    }
  }

  const removed = state === 'removed'
  return (
    <div className="py-1 px-2 text-[13px] text-faint">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className={`italic ${removed ? 'line-through' : ''}`}>
          Saved to {viewLabel('Memory', projectName || projectId)}: <span className="not-italic text-muted">{memory?.title || 'note'}</span>
        </span>
        {removed ? (
          <span className="text-[12px]">Removed.</span>
        ) : id && projectId ? (
          <span className="flex gap-2 text-[12px]">
            <button type="button" onClick={() => dispatch({ type: 'OPEN_NOTES', payload: { view: 'memory', memoryId: id } })}
              className="text-muted hover:text-accent transition-colors">Edit</button>
            <button type="button" onClick={undo} disabled={state === 'removing'}
              className="text-muted hover:text-accent transition-colors disabled:opacity-50">
              {state === 'removing' ? 'Undoing…' : 'Undo'}
            </button>
          </span>
        ) : null}
      </div>
      {memory?.description && !removed && <p className="mt-0.5 text-[12px] leading-snug">{memory.description}</p>}
      {state === 'error' && <p className="mt-0.5 text-[12px] text-danger">{error}</p>}
    </div>
  )
}

export default memo(MemorySavedNotice)

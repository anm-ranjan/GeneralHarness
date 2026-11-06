import { memo, useState } from 'react'
import { api } from '../../api'
import { useAppSelector } from '../../context/AppContext'
import { currentProject, errorText } from '../../projectNotes'
import MemorySavedNotice from './MemorySavedNotice'

const selectMeta = state => state.currentMeta
const selectProjects = state => state.projects

// "Save to memory" under a finished assistant reply (shown on hover). The
// backend has the native summary model condense the reply, with a short
// timeout, into one project memory note; the result then reads like a
// /remember notice with Edit / Undo. Not offered in chats.
function ReplyActions({ markdown, prompt }) {
  const meta = useAppSelector(selectMeta)
  const projects = useAppSelector(selectProjects)
  const [status, setStatus] = useState('idle') // idle | saving | saved | error
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const project = currentProject(meta, projects)
  if (!project || !meta?.id || !markdown?.trim()) return null

  async function save() {
    setStatus('saving')
    setError('')
    try {
      const data = await api('POST', `/api/sessions/${encodeURIComponent(meta.id)}/memory/from-reply`, {
        reply: markdown,
        prompt: prompt || '',
      })
      setResult(data)
      setStatus('saved')
    } catch (err) {
      setError(errorText(err, 'Could not save to memory.'))
      setStatus('error')
    }
  }

  if (status === 'saved' && result) {
    return (
      <div className="mb-2 -mt-1">
        <MemorySavedNotice memory={result.memory} projectId={result.project_id} projectName={result.project_name} />
      </div>
    )
  }
  if (status === 'saving') {
    return <div className="mb-2 -mt-1 px-2 text-[12px] italic text-faint">Condensing the reply into a memory note…</div>
  }
  if (status === 'error') {
    return (
      <div className="mb-2 -mt-1 flex flex-wrap gap-x-3 px-2 text-[12px]">
        <span className="text-danger">{error}</span>
        <button type="button" onClick={save} className="text-muted hover:text-accent transition-colors">Retry</button>
        <button type="button" onClick={() => setStatus('idle')} className="text-faint hover:text-text-bright transition-colors">Dismiss</button>
      </div>
    )
  }
  return (
    <div className="mb-2 -mt-1 flex text-faint opacity-0 transition-opacity group-hover/reply:opacity-100 focus-within:opacity-100">
      <button
        type="button"
        onClick={save}
        title={`Condense this reply into a note in Memory - ${project.name}`}
        className="flex items-center gap-1.5 px-2 py-0.5 text-[12px] hover:text-accent transition-colors"
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
        </svg>
        Save to memory
      </button>
    </div>
  )
}

export default memo(ReplyActions)

import { useState } from 'react'
import { CHATS_PROJECT_ID } from '../../constants'

// Moves a session to another project. The backend files it under the target's
// unlabeled bucket and keeps the old task name as a label.
export default function MoveSessionModal({ moveRequest, projects, onClose, onMove }) {
  const targets = projects.filter(p => p.id !== CHATS_PROJECT_ID && p.id !== moveRequest.projectId)
  const [targetProjectId, setTargetProjectId] = useState(targets[0]?.id || '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(e) {
    e.preventDefault()
    if (!targetProjectId || busy) return
    setBusy(true)
    setError('')
    try {
      await onMove(moveRequest.session.id, targetProjectId)
      onClose()
    } catch (err) {
      setError(err.detail || err.message || 'Move failed')
    } finally {
      setBusy(false)
    }
  }

  function handleOverlayClick(e) {
    if (e.target === e.currentTarget && !busy) onClose()
  }

  return (
    <div
      className="fixed inset-0 glass-overlay z-50 flex items-center justify-center p-5"
      onClick={handleOverlayClick}
    >
      <form
        onSubmit={submit}
        className="solid-surface border border-line rounded-lg w-full max-w-[360px] overflow-hidden"
      >
        <div className="px-5 py-4 border-b border-line">
          <h3 className="text-[15px] font-semibold text-text-bright">Move Session</h3>
          <p className="mt-1.5 text-[13px] text-muted truncate" title={moveRequest.session.title || moveRequest.session.id}>
            {moveRequest.session.title || moveRequest.session.id}
          </p>
        </div>
        <div className="px-5 py-4 space-y-3">
          <label className="block">
            <span className="block text-[12px] font-medium text-faint mb-1.5">Project</span>
            <select value={targetProjectId} onChange={e => setTargetProjectId(e.target.value)} disabled={busy || targets.length === 0}
              className="w-full bg-surface border border-line rounded-md px-3 py-2 text-[13px] text-text-bright outline-none focus:border-accent/60 disabled:opacity-50">
              {targets.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          {targets.length === 0 && <p className="text-[12px] text-faint">No other projects to move to.</p>}
          {targets.length > 0 && <p className="text-[12px] text-muted">Future runs use the destination workspace. Existing files stay where they are; labels move with the session.</p>}
          {error && (
            <p className="text-[12px] text-danger">{error}</p>
          )}
        </div>
        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-line">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="px-3 py-1.5 text-[13px] font-medium text-muted border border-line rounded-md hover:text-text-bright hover:border-line-hover transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={busy || !targetProjectId}
            className="px-3 py-1.5 text-[13px] font-semibold rounded-md bg-accent text-bg hover:brightness-110 transition-colors disabled:opacity-50"
          >
            {busy ? 'Moving...' : 'Move'}
          </button>
        </div>
      </form>
    </div>
  )
}

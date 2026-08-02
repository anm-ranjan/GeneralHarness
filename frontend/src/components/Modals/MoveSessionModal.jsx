import { useEffect, useMemo, useState } from 'react'

export default function MoveSessionModal({ moveRequest, projects, onClose, onMove }) {
  const [targetTaskId, setTargetTaskId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const project = useMemo(
    () => projects.find(p => p.id === moveRequest.projectId),
    [projects, moveRequest.projectId],
  )
  const targetTasks = useMemo(
    () => (project?.tasks || []).filter(task => task.id !== moveRequest.currentTaskId),
    [project, moveRequest.currentTaskId],
  )

  useEffect(() => {
    setTargetTaskId(targetTasks[0]?.id || '')
  }, [targetTasks])

  async function submit(e) {
    e.preventDefault()
    if (!targetTaskId || busy) return
    setBusy(true)
    setError('')
    try {
      await onMove(moveRequest.session.id, moveRequest.projectId, targetTaskId)
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
        className="glass-surface border border-line rounded-lg w-full max-w-[360px] overflow-hidden"
      >
        <div className="px-5 py-4 border-b border-line">
          <h3 className="text-[15px] font-semibold text-text-bright">Assign Label</h3>
          <p className="mt-1.5 text-[13px] text-muted truncate" title={moveRequest.session.title || moveRequest.session.id}>
            {moveRequest.session.title || moveRequest.session.id}
          </p>
        </div>
        <div className="px-5 py-4 space-y-3">
          <label className="block">
            <span className="block text-[12px] font-medium text-faint mb-1.5">Label</span>
            <select
              value={targetTaskId}
              onChange={e => setTargetTaskId(e.target.value)}
              disabled={busy || targetTasks.length === 0}
              className="w-full bg-surface border border-line rounded-md px-3 py-2 text-[13px] text-text-bright outline-none focus:border-accent/60 disabled:opacity-50"
            >
              {targetTasks.map(task => (
                <option key={task.id} value={task.id}>{task.name}</option>
              ))}
            </select>
          </label>
          {targetTasks.length === 0 && (
            <p className="text-[12px] text-faint">No other labels in this project.</p>
          )}
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
            disabled={busy || !targetTaskId}
            className="px-3 py-1.5 text-[13px] font-semibold rounded-md bg-accent text-bg hover:brightness-110 transition-colors disabled:opacity-50"
          >
            {busy ? 'Assigning...' : 'Assign'}
          </button>
        </div>
      </form>
    </div>
  )
}

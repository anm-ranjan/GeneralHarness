import { useState, useEffect } from 'react'
import { useApp } from '../../context/AppContext'
import { api } from '../../api'
import useRevertFile from '../../hooks/useRevertFile'

export default function DiffViewer() {
  const { state, dispatch } = useApp()
  const revertFile = useRevertFile()
  const filePath = state.diffViewerFile
  const [diff, setDiff] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!filePath || !state.currentSessionId) return
    setLoading(true)
    setError(null)
    api('POST', '/api/workspace/diff', {
      session_id: state.currentSessionId,
      file_path: filePath,
    })
      .then(data => setDiff(data))
      .catch(err => setError(err.message || 'Failed to load diff'))
      .finally(() => setLoading(false))
  }, [filePath, state.currentSessionId])

  if (!filePath) return null

  const close = () => dispatch({ type: 'CLOSE_DIFF_VIEWER' })

  const root = state.currentWorkspaceRoot
  const displayPath = root && filePath.startsWith(root)
    ? filePath.slice(root.length).replace(/^\//, '')
    : filePath

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={close}>
      <div
        className="glass-surface w-[90vw] max-w-[800px] max-h-[85vh] flex flex-col rounded-xl border border-line shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 px-5 py-3 border-b border-line">
          <span className="text-[13px] font-mono text-accent truncate flex-1">{displayPath}</span>
          {diff && (
            <span className="text-[11px] text-muted">
              {diff.before_lines} &rarr; {diff.after_lines} lines
            </span>
          )}
          {!state.isRunning && (
            <button
              type="button"
              title="Revert this file to its pre-run state"
              className="px-2 py-1 text-[11px] font-semibold rounded border border-line text-muted hover:text-danger hover:border-danger transition-colors"
              onClick={() => revertFile(filePath, close)}
            >
              Revert
            </button>
          )}
          <button onClick={close} className="p-1 text-muted hover:text-text-bright transition-colors">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
        <div className="flex-1 overflow-auto p-4">
          {loading && <div className="text-[13px] text-muted italic">Loading diff...</div>}
          {error && <div className="text-[13px] text-danger">{error}</div>}
          {diff && !diff.diff_text && (
            <div className="text-[13px] text-muted italic">No differences (file may be unchanged or newly created).</div>
          )}
          {diff && diff.diff_text && (
            <pre className="text-[12px] font-mono leading-[1.6] whitespace-pre overflow-x-auto">
              {diff.diff_text.split('\n').map((line, i) => (
                <div
                  key={i}
                  className={
                    line.startsWith('+++') || line.startsWith('---')
                      ? 'text-muted font-semibold'
                      : line.startsWith('@@')
                      ? 'text-info bg-info-soft'
                      : line.startsWith('+')
                      ? 'text-ok bg-ok-soft'
                      : line.startsWith('-')
                      ? 'text-danger bg-danger-soft'
                      : 'text-text'
                  }
                >
                  {line}
                </div>
              ))}
            </pre>
          )}
        </div>
      </div>
    </div>
  )
}

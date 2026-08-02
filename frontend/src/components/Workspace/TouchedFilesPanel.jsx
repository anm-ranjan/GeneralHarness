import { useApp } from '../../context/AppContext'
import useRevertFile from '../../hooks/useRevertFile'
import { relativeTime } from '../../utils'

const ACTION_STYLES = {
  created: 'bg-ok-soft text-ok',
  modified: 'bg-accent/15 text-accent',
  deleted: 'bg-danger-soft text-danger',
  reverted: 'bg-warn-soft text-warn',
}

export default function TouchedFilesPanel() {
  const { state, dispatch } = useApp()
  const revertFile = useRevertFile()
  const { touchedFiles, currentWorkspaceRoot } = state

  if (!touchedFiles.length) {
    return (
      <div className="p-4 text-[13px] text-muted italic">
        No file changes yet in this thread.
      </div>
    )
  }

  const sorted = [...touchedFiles].reverse()

  function relativePath(fullPath) {
    if (currentWorkspaceRoot && fullPath.startsWith(currentWorkspaceRoot)) {
      const rel = fullPath.slice(currentWorkspaceRoot.length)
      return rel.startsWith('/') ? rel.slice(1) : rel
    }
    return fullPath
  }

  return (
    <div className="flex flex-col">
      {sorted.map((f, i) => {
        const rel = relativePath(f.path)
        const dir = rel.includes('/') ? rel.slice(0, rel.lastIndexOf('/') + 1) : ''
        const name = rel.includes('/') ? rel.slice(rel.lastIndexOf('/') + 1) : rel

        return (
          <div
            key={f.path + '-' + i}
            className="group flex items-center gap-2 px-3 py-2 border-b border-line/50 hover:bg-surface-hover transition-colors cursor-pointer"
            title={`${f.path} — click for diff`}
            onClick={() => dispatch({ type: 'OPEN_DIFF_VIEWER', payload: f.path })}
          >
            <span className={`shrink-0 px-1.5 py-0.5 text-[10px] font-semibold uppercase rounded ${ACTION_STYLES[f.action] || 'text-muted'}`}>
              {f.action?.charAt(0)}
            </span>
            <div className="flex-1 min-w-0 text-[12px] font-mono truncate">
              {dir && <span className="text-muted">{dir}</span>}
              <span className="text-text-bright">{name}</span>
            </div>
            {f.action !== 'reverted' && !state.isRunning && (
              <button
                type="button"
                title="Revert this file to its pre-run state"
                className="shrink-0 opacity-0 group-hover:opacity-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase rounded border border-line text-muted hover:text-danger hover:border-danger transition-all"
                onClick={(e) => {
                  e.stopPropagation()
                  revertFile(f.path)
                }}
              >
                Revert
              </button>
            )}
            {f.timestamp && (
              <span className="shrink-0 text-[10px] text-faint">
                {relativeTime(f.timestamp)}
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}

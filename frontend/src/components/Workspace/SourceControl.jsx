import { useState, useEffect, useCallback } from 'react'
import { api } from '../../api'
import { useApp } from '../../context/AppContext'

const STATUS_STYLES = {
  modified: 'bg-accent/15 text-accent',
  added: 'bg-ok-soft text-ok',
  deleted: 'bg-danger-soft text-danger',
  renamed: 'bg-accent/15 text-accent',
  untracked: 'bg-surface text-muted',
  unmerged: 'bg-warn-soft text-warn',
}

function relativePath(fullOrRel, root) {
  if (root && fullOrRel.startsWith(root)) {
    const rel = fullOrRel.slice(root.length)
    return rel.startsWith('/') ? rel.slice(1) : rel
  }
  return fullOrRel
}

function FileRow({ file, root, action, actionLabel }) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 border-b border-line/40 hover:bg-surface-hover transition-colors">
      <span className={`shrink-0 px-1.5 py-0.5 text-[10px] font-semibold uppercase rounded ${STATUS_STYLES[file.status] || 'text-muted'}`}>
        {(file.status || '?').charAt(0)}
      </span>
      <span className="flex-1 min-w-0 text-[12px] font-mono truncate text-text-default" title={file.path}>
        {relativePath(file.path, root)}
      </span>
      {action && (
        <button
          onClick={() => action(file.path)}
          className="shrink-0 text-[11px] text-muted hover:text-accent px-1 opacity-70 hover:opacity-100"
          title={actionLabel}
        >
          {actionLabel}
        </button>
      )}
    </div>
  )
}

export default function SourceControl() {
  const { state } = useApp()
  const root = state.currentWorkspaceRoot
  const writesEnabled = state.gitWritesEnabled
  const [status, setStatus] = useState(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    if (!root) return
    try {
      const res = await api('GET', `/api/workspace/git/status?path=${encodeURIComponent(root)}`)
      setStatus(res)
      setError('')
    } catch (err) {
      setError(err.detail || err.message)
      setStatus(null)
    }
  }, [root])

  useEffect(() => { load() }, [load, state.isRunning, state.touchedFiles.length])

  const doStage = useCallback(async (paths, unstage) => {
    if (!writesEnabled) return
    setBusy(true)
    setNotice('')
    try {
      const res = await api('POST', '/api/workspace/git/stage', { path: root, files: paths, unstage })
      setStatus(res)
      setError('')
    } catch (err) {
      setError(err.detail || err.message)
    } finally {
      setBusy(false)
    }
  }, [root, writesEnabled])

  const doCommit = useCallback(async () => {
    if (!writesEnabled || !message.trim()) return
    setBusy(true)
    setNotice('')
    try {
      const res = await api('POST', '/api/workspace/git/commit', { path: root, message })
      setStatus(res.status)
      setMessage('')
      setNotice(`Committed ${res.hash || ''}`.trim())
      setError('')
    } catch (err) {
      setError(err.detail || err.message)
    } finally {
      setBusy(false)
    }
  }, [root, message, writesEnabled])

  if (!root) {
    return <div className="p-4 text-[13px] text-muted italic">No workspace selected.</div>
  }
  if (error && !status) {
    return <div className="p-4 text-[13px] text-danger">{error}</div>
  }
  if (status && status.is_repo === false) {
    return <div className="p-4 text-[13px] text-muted italic">This workspace is not a git repository.</div>
  }
  if (!status) {
    return <div className="p-4 text-[13px] text-muted italic">Loading source control…</div>
  }
  if (status.error) {
    return <div className="p-4 text-[13px] text-danger">{status.error}</div>
  }

  const staged = status.staged || []
  const unstaged = status.unstaged || []
  const untracked = status.untracked || []
  const allUnstaged = [...unstaged, ...untracked]

  return (
    <div className="flex flex-col">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2 text-[12px]">
        <span className="px-1.5 py-0.5 rounded bg-accent/15 text-accent font-mono">{status.branch || 'detached'}</span>
        {status.ahead > 0 && <span className="text-muted">↑{status.ahead}</span>}
        {status.behind > 0 && <span className="text-muted">↓{status.behind}</span>}
        <button onClick={load} className="ml-auto text-[11px] text-muted hover:text-accent" title="Refresh">↻</button>
      </div>

      {error && <p className="px-3 py-2 text-[12px] text-danger">{error}</p>}
      {notice && <p className="px-3 py-2 text-[12px] text-ok">{notice}</p>}
      {!writesEnabled && (
        <p className="px-3 py-2 text-[11px] text-faint italic border-b border-line/40">
          Read-only. Set ui.git_writes_enabled to stage and commit from here.
        </p>
      )}

      {staged.length > 0 && (
        <div>
          <div className="px-3 pt-2 pb-1 text-[11px] uppercase tracking-wide text-muted">Staged ({staged.length})</div>
          {staged.map((f, i) => (
            <FileRow key={'s' + i} file={f} root={root}
              action={writesEnabled ? ((p) => doStage([p], true)) : null} actionLabel="unstage" />
          ))}
        </div>
      )}

      <div>
        <div className="px-3 pt-2 pb-1 text-[11px] uppercase tracking-wide text-muted flex items-center">
          Changes ({allUnstaged.length})
          {writesEnabled && allUnstaged.length > 0 && (
            <button onClick={() => doStage(allUnstaged.map(f => f.path), false)}
              className="ml-auto text-[11px] text-muted hover:text-accent" disabled={busy}>stage all</button>
          )}
        </div>
        {allUnstaged.length === 0 && <p className="px-3 py-2 text-[12px] text-faint italic">Working tree clean.</p>}
        {allUnstaged.map((f, i) => (
          <FileRow key={'u' + i} file={f} root={root}
            action={writesEnabled ? ((p) => doStage([p], false)) : null} actionLabel="stage" />
        ))}
      </div>

      {writesEnabled && staged.length > 0 && (
        <div className="p-3 border-t border-line mt-1">
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="Commit message"
            rows={2}
            className="w-full text-[12px] bg-surface border border-line rounded px-2 py-1.5 text-text-bright placeholder:text-faint outline-none focus:border-accent/40 resize-none"
          />
          <button
            onClick={doCommit}
            disabled={busy || !message.trim()}
            className="mt-2 w-full py-1.5 text-[12px] font-medium text-bg bg-accent rounded hover:brightness-110 transition disabled:opacity-40"
          >
            Commit {staged.length} file{staged.length === 1 ? '' : 's'}
          </button>
        </div>
      )}
    </div>
  )
}

import { useState, useCallback, useEffect, useMemo, useRef } from 'react'
import { useAppSelector, useAppDispatch } from '../../context/AppContext'
import { api } from '../../api'
import FilePreview from './FilePreview'
import { isDesktopApp } from '../../desktop'

function relativePath(path, root) {
  if (!root || !path) return path
  const normalizedRoot = root.replace(/[\\/]+$/, '')
  if (path === normalizedRoot) return '.'
  if (path.startsWith(`${normalizedRoot}/`) || path.startsWith(`${normalizedRoot}\\`)) {
    return path.slice(normalizedRoot.length + 1)
  }
  return path
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.select()
  document.execCommand('copy')
  textarea.remove()
}

function TreeContextMenu({ entry, position, root, onClose, onPreview, onToggle, onRename }) {
  useEffect(() => {
    const close = () => onClose()
    const onKey = event => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('pointerdown', close)
    window.addEventListener('keydown', onKey)
    window.addEventListener('scroll', close, true)
    return () => {
      window.removeEventListener('pointerdown', close)
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', close, true)
    }
  }, [onClose])

  async function run(action) {
    try {
      await action()
      onClose()
    } catch (error) {
      window.alert(error.detail || error.message || 'Action failed')
    }
  }

  const itemClass = 'w-full text-left px-3 py-1.5 text-[12px] text-text hover:bg-surface-hover hover:text-text-bright transition-colors'

  return (
    <div
      className="fixed z-50 min-w-44 overflow-hidden rounded-md border border-line bg-panel shadow-xl py-1"
      style={{ left: position.x, top: position.y }}
      onPointerDown={event => event.stopPropagation()}
      onContextMenu={event => event.preventDefault()}
    >
      <button className={itemClass} onClick={() => run(entry.is_dir ? onToggle : onPreview)}>
        {entry.is_dir ? 'Expand / collapse' : (isDesktopApp() ? 'Open in editor' : 'Preview file')}
      </button>
      <div className="my-1 border-t border-line" />
      <button className={itemClass} onClick={() => run(() => copyText(entry.name))}>Copy filename</button>
      <button className={itemClass} onClick={() => run(() => copyText(entry.path))}>Copy full path</button>
      <button className={itemClass} onClick={() => run(() => copyText(relativePath(entry.path, root)))}>Copy relative path</button>
      <div className="my-1 border-t border-line" />
      <button className={itemClass} onClick={() => run(onRename)}>Rename...</button>
    </div>
  )
}

function TreeNode({ entry, touchedPaths, depth, root, onEntryRenamed }) {
  const dispatch = useAppDispatch()
  const [nodeEntry, setNodeEntry] = useState(entry)
  const [expanded, setExpanded] = useState(false)
  const [children, setChildren] = useState(null)
  const [loading, setLoading] = useState(false)
  const [previewFile, setPreviewFile] = useState(null)
  const [menu, setMenu] = useState(null)

  useEffect(() => {
    setNodeEntry(entry)
  }, [entry])

  const isTouched = touchedPaths.has(nodeEntry.path)

  const openFile = useCallback(() => {
    // Desktop opens the panel editor; the browser keeps the read-only preview,
    // since saving is refused outside the Electron shell anyway.
    if (isDesktopApp()) {
      dispatch({ type: 'OPEN_WORKSPACE_EDITOR', payload: nodeEntry.path })
      return
    }
    setPreviewFile(prev => prev ? null : nodeEntry.path)
  }, [nodeEntry, dispatch])

  const toggle = useCallback(() => {
    if (!nodeEntry.is_dir) {
      openFile()
      return
    }
    if (expanded) {
      setExpanded(false)
      return
    }
    if (children !== null) {
      setExpanded(true)
      return
    }
    setLoading(true)
    api('GET', `/api/workspace/tree?path=${encodeURIComponent(nodeEntry.path)}`)
      .then(data => {
        setChildren(data.entries || [])
        setExpanded(true)
      })
      .catch(() => setChildren([]))
      .finally(() => setLoading(false))
  }, [nodeEntry, expanded, children, openFile])

  const preview = useCallback(() => {
    if (!nodeEntry.is_dir) openFile()
  }, [nodeEntry, openFile])

  const rename = useCallback(async () => {
    const nextName = window.prompt('Rename to:', nodeEntry.name)
    if (nextName === null || nextName.trim() === nodeEntry.name) return
    const data = await api('PATCH', '/api/workspace/entry', { path: nodeEntry.path, name: nextName })
    const renamed = data.entry
    setNodeEntry(renamed)
    onEntryRenamed?.(nodeEntry.path, renamed)
    setChildren(null)
    setExpanded(false)
    setPreviewFile(null)
  }, [nodeEntry, onEntryRenamed])

  const handleChildRenamed = useCallback((oldPath, renamed) => {
    setChildren(current => current
      ? current.map(child => child.path === oldPath ? renamed : child)
      : current)
  }, [])

  function openMenu(event) {
    event.preventDefault()
    event.stopPropagation()
    const width = 184
    const height = 190
    setMenu({
      x: Math.max(8, Math.min(event.clientX, window.innerWidth - width - 8)),
      y: Math.max(8, Math.min(event.clientY, window.innerHeight - height - 8)),
    })
  }

  const indent = depth * 16

  return (
    <>
      <div
        className="flex items-center gap-1.5 px-2 py-1 hover:bg-surface-hover transition-colors cursor-pointer select-none"
        style={{ paddingLeft: `${8 + indent}px` }}
        onClick={toggle}
        onContextMenu={openMenu}
      >
        <span className="text-[11px] text-muted w-4 text-center shrink-0">
          {nodeEntry.is_dir ? (expanded ? '▾' : '▸') : ' '}
        </span>
        <span className={`text-[12px] font-mono truncate ${nodeEntry.is_dir ? 'text-text-bright font-medium' : 'text-text'}`}>
          {nodeEntry.name}
        </span>
        {isTouched && (
          <span className="shrink-0 h-1.5 w-1.5 rounded-full bg-accent" title="Modified in this thread" />
        )}
        {!nodeEntry.is_dir && nodeEntry.size !== undefined && (
          <span className="ml-auto text-[10px] text-faint shrink-0">
            {nodeEntry.size < 1024 ? `${nodeEntry.size}B` : `${(nodeEntry.size / 1024).toFixed(1)}K`}
          </span>
        )}
        {loading && <span className="ml-auto text-[10px] text-muted animate-pulse">...</span>}
      </div>
      {expanded && children && children.map(child => (
        <TreeNode
          key={child.path}
          entry={child}
          touchedPaths={touchedPaths}
          depth={depth + 1}
          root={root}
          onEntryRenamed={handleChildRenamed}
        />
      ))}
      {previewFile && <FilePreview path={previewFile} onClose={() => setPreviewFile(null)} />}
      {menu && (
        <TreeContextMenu
          entry={nodeEntry}
          position={menu}
          root={root}
          onClose={() => setMenu(null)}
          onPreview={preview}
          onToggle={toggle}
          onRename={rename}
        />
      )}
    </>
  )
}

export default function FileTree() {
  const root = useAppSelector(state => state.currentWorkspaceRoot)
  const touchedFiles = useAppSelector(state => state.touchedFiles)
  const [entries, setEntries] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [query, setQuery] = useState('')
  const requestId = useRef(0)

  const touchedPaths = useMemo(
    () => new Set(touchedFiles.map(f => f.path)),
    [touchedFiles],
  )

  const load = useCallback(() => {
    if (!root) return
    const currentRequest = ++requestId.current
    setLoading(true)
    setError(null)
    api('GET', `/api/workspace/tree?path=${encodeURIComponent(root)}`)
      .then(data => {
        if (currentRequest === requestId.current) setEntries(data.entries || [])
      })
      .catch(err => {
        if (currentRequest === requestId.current) setError(err.message || 'Failed to load')
      })
      .finally(() => {
        if (currentRequest === requestId.current) setLoading(false)
      })
  }, [root])

  const handleRootEntryRenamed = useCallback((oldPath, renamed) => {
    setEntries(current => current
      ? current.map(entry => entry.path === oldPath ? renamed : entry)
      : current)
  }, [])

  useEffect(() => {
    setEntries(null)
    setError(null)
    if (root) load()
    return () => { requestId.current += 1 }
  }, [root, load])

  if (!root) {
    return <div className="p-4 text-[13px] text-muted italic">No workspace selected.</div>
  }

  if (error) {
    return <div className="p-4 text-[13px] text-danger">{error}</div>
  }

  if (entries === null) {
    return <div className="p-4 text-[13px] text-muted italic animate-pulse">Loading file tree...</div>
  }

  return (
    <div className="flex flex-col py-1">
      <div className="sticky top-0 z-10 bg-panel/95 backdrop-blur border-b border-line p-2 flex gap-2">
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Search loaded files..."
          className="min-w-0 flex-1 bg-surface border border-line rounded-md px-2 py-1 text-[12px] text-text-default outline-none focus:border-line-hover"
        />
        <button
          onClick={load}
          disabled={loading}
          className="px-2 py-1 text-[12px] text-muted border border-line rounded-md hover:text-text-bright disabled:opacity-40"
          title="Refresh tree"
        >
          refresh
        </button>
      </div>
      {entries && filterEntries(entries, query).map(entry => (
        <TreeNode
          key={entry.path}
          entry={entry}
          touchedPaths={touchedPaths}
          depth={0}
          root={root}
          onEntryRenamed={handleRootEntryRenamed}
        />
      ))}
    </div>
  )
}

function filterEntries(entries, query) {
  const q = query.trim().toLowerCase()
  if (!q) return entries
  return entries.filter(entry => entry.name.toLowerCase().includes(q) || entry.path.toLowerCase().includes(q))
}

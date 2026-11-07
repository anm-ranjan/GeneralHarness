import { useState, useEffect } from 'react'
import { useAppDispatch } from '../../context/AppContext'
import useSessionTree from '../../hooks/useSessionTree'
import { api } from '../../api'

// Project picker by default. With onSelect it returns a path; with multiple it
// stays open so several folders/files can be added (agent "paths" fields).
export default function DirectoryPicker({ onSelect, onClose, kind = 'dir', multiple = false, selected = [] } = {}) {
  const dispatch = useAppDispatch()
  const tree = useSessionTree()
  const close = () => onClose ? onClose() : dispatch({ type: 'CLOSE_DIR_PICKER' })
  const [currentPath, setCurrentPath] = useState('')
  const [entries, setEntries] = useState([])
  const [parent, setParent] = useState(null)
  const [isRootList, setIsRootList] = useState(false)
  const [directPath, setDirectPath] = useState('')
  const [message, setMessage] = useState('')
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState(null) // null = closed, string = name being typed
  const [makingFolder, setMakingFolder] = useState(false)
  const [added, setAdded] = useState(() => new Set(selected))
  const pickFiles = kind === 'file'

  function add(path) {
    if (!path || added.has(path)) return
    onSelect(path)
    setAdded(old => new Set([...old, path]))
  }

  async function loadDir(path) {
    try {
      setMessage('')
      const data = await api('POST', '/api/browse', { path: path || '', include_files: kind === 'file' })
      setCurrentPath(data.current)
      setEntries(data.entries || [])
      setParent(data.parent || null)
      setIsRootList(data.is_root_list || false)
      setDirectPath(data.current || '')
    } catch (err) {
      setMessage(err.status === 403
        ? 'This path is not within the allowed directories.'
        : `Could not open this folder: ${err.detail || err.message}`)
    }
  }

  useEffect(() => { loadDir('') }, [])

  async function handleSelect() {
    const path = directPath.trim() || currentPath
    if (!path || creating) return
    setCreating(true)
    setMessage('')
    try {
      if (onSelect && multiple) {add(path);return}
      if (onSelect) {onSelect(path);close();return}
      const name = path.split(/[\\/]/).filter(Boolean).pop() || path
      await tree.createProject(name, path)
      close()
    } catch (err) {
      console.error('Failed to create project:', err)
      setMessage(err.status === 403
        ? 'This path is not within the allowed directories.'
        : `Could not create project: ${err.detail || err.message}`)
    } finally {
      setCreating(false)
    }
  }

  // Create the folder inside the one being viewed, then step into it so
  // "Select this directory" picks the new folder.
  async function handleNewFolder(e) {
    e.preventDefault()
    const name = (newName || '').trim()
    if (!name || !currentPath || makingFolder) return
    setMakingFolder(true)
    setMessage('')
    try {
      const data = await api('POST', '/api/browse/mkdir', { parent: currentPath, name })
      setNewName(null)
      await loadDir(data.path)
    } catch (err) {
      setMessage(err.status === 403
        ? 'This path is not within the allowed directories.'
        : (err.detail || `Could not create the folder: ${err.message}`))
    } finally {
      setMakingFolder(false)
    }
  }

  function handleDirectPathSubmit(e) {
    e.preventDefault()
    if (directPath.trim()) loadDir(directPath.trim())
  }

  function handleOverlayClick(e) {
    if (e.target === e.currentTarget) close()
  }

  return (
    <div
      className="fixed inset-0 glass-overlay z-50 flex items-center justify-center"
      onClick={handleOverlayClick}
    >
      <div className="solid-surface border border-line rounded-lg w-[480px] max-h-[70vh] flex flex-col">
        <div className="px-5 pt-5 pb-3 border-b border-line">
          <h3 className="text-[15px] font-semibold text-text-bright mb-3">{onSelect ? (multiple ? (pickFiles ? 'Add files' : 'Add folders') : (pickFiles ? 'Select file' : 'Select directory')) : 'Select Project Directory'}</h3>
          <form onSubmit={handleDirectPathSubmit} className="flex gap-2">
            <input
              type="text"
              value={directPath}
              onChange={e => setDirectPath(e.target.value)}
              placeholder="Enter path directly..."
              className="flex-1 bg-surface border border-line rounded px-3 py-1.5 text-[13px] text-text-default placeholder:text-faint outline-none focus:border-line-hover"
            />
            <button
              type="submit"
              className="px-3 py-1.5 text-[12px] font-medium text-accent border border-accent/30 rounded hover:bg-accent-soft transition-colors"
            >
              Go
            </button>
          </form>
          {newName === null ? (
            <button
              type="button"
              onClick={() => setNewName('')}
              disabled={isRootList || !currentPath}
              title={isRootList ? 'Open a folder first' : 'Create a folder here'}
              className="mt-2 px-3 py-1 text-[12px] font-medium text-muted border border-line rounded hover:text-text-bright hover:border-line-hover transition-colors disabled:opacity-40"
            >
              New folder
            </button>
          ) : (
            <form onSubmit={handleNewFolder} className="flex gap-2 mt-2">
              <input
                autoFocus
                type="text"
                value={newName}
                onChange={e => setNewName(e.target.value)}
                onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); setNewName(null) } }}
                placeholder="New folder name"
                className="flex-1 bg-surface border border-line rounded px-3 py-1.5 text-[13px] text-text-default placeholder:text-faint outline-none focus:border-line-hover"
              />
              <button
                type="submit"
                disabled={makingFolder || !newName.trim()}
                className="px-3 py-1.5 text-[12px] font-medium text-accent border border-accent/30 rounded hover:bg-accent-soft transition-colors disabled:opacity-40"
              >
                {makingFolder ? 'Creating...' : 'Create'}
              </button>
              <button
                type="button"
                onClick={() => setNewName(null)}
                className="px-3 py-1.5 text-[12px] text-muted border border-line rounded hover:bg-surface transition-colors"
              >
                Cancel
              </button>
            </form>
          )}
          {message && <p className="text-[12px] text-danger mt-2">{message}</p>}
        </div>

        <div className="flex-1 overflow-y-auto px-2 py-2">
          {parent && (
            <button
              onClick={() => loadDir(parent)}
              className="w-full text-left px-3 py-1.5 text-[13px] text-muted hover:bg-surface rounded transition-colors"
            >
              ↑ ..
            </button>
          )}
          {entries.map(entry => {
            const isFile = entry.is_dir === false
            const addable = multiple && (pickFiles ? isFile : !isFile)
            return (
              <div key={entry.path} className="group flex items-center rounded hover:bg-surface">
                <button
                  onClick={() => isFile ? (multiple ? add(entry.path) : setDirectPath(entry.path)) : loadDir(entry.path)}
                  className="min-w-0 flex-1 truncate px-3 py-1.5 text-left text-[13px] text-text-default"
                >
                  {isFile ? '📄' : '📂'} {entry.name}
                </button>
                {addable && (added.has(entry.path)
                  ? <span className="shrink-0 px-3 text-[12px] text-ok">Added</span>
                  : <button type="button" onClick={() => add(entry.path)} className="shrink-0 px-3 text-[12px] text-accent opacity-70 hover:opacity-100 group-hover:opacity-100">Add</button>)}
              </div>
            )
          })}
          {entries.length === 0 && !parent && (
            <p className="text-[13px] text-faint italic text-center py-4">Empty directory</p>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-line">
          {multiple && <span className="mr-auto text-[12px] text-faint">{added.size ? `${added.size} selected` : 'Nothing selected yet'}</span>}
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); close() }}
            className="px-4 py-1.5 text-[13px] text-muted border border-line rounded hover:bg-surface transition-colors"
          >
            {multiple ? 'Done' : 'Cancel'}
          </button>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); handleSelect() }}
            disabled={creating || !(directPath.trim() || currentPath) || (multiple && added.has(directPath.trim() || currentPath))}
            className="px-4 py-1.5 text-[13px] font-medium text-accent border border-accent/30 rounded hover:bg-accent-soft transition-colors disabled:opacity-40"
          >
            {creating ? 'Creating...' : multiple ? (pickFiles ? 'Add this path' : 'Add this directory') : (pickFiles ? 'Select file path' : 'Select this directory')}
          </button>
        </div>
      </div>
    </div>
  )
}

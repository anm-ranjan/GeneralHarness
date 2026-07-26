import { useState, useEffect } from 'react'
import { useAppDispatch } from '../../context/AppContext'
import useSessionTree from '../../hooks/useSessionTree'
import { api } from '../../api'

export default function DirectoryPicker() {
  const dispatch = useAppDispatch()
  const tree = useSessionTree()
  const [currentPath, setCurrentPath] = useState('')
  const [entries, setEntries] = useState([])
  const [parent, setParent] = useState(null)
  const [isRootList, setIsRootList] = useState(false)
  const [directPath, setDirectPath] = useState('')
  const [message, setMessage] = useState('')
  const [creating, setCreating] = useState(false)

  async function loadDir(path) {
    try {
      setMessage('')
      const data = await api('POST', '/api/browse', { path: path || '' })
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
      const name = path.split(/[\\/]/).filter(Boolean).pop() || path
      await tree.createProject(name, path)
      dispatch({ type: 'CLOSE_DIR_PICKER' })
    } catch (err) {
      console.error('Failed to create project:', err)
      setMessage(err.status === 403
        ? 'This path is not within the allowed directories.'
        : `Could not create project: ${err.detail || err.message}`)
    } finally {
      setCreating(false)
    }
  }

  function handleDirectPathSubmit(e) {
    e.preventDefault()
    if (directPath.trim()) loadDir(directPath.trim())
  }

  function handleOverlayClick(e) {
    if (e.target === e.currentTarget) dispatch({ type: 'CLOSE_DIR_PICKER' })
  }

  return (
    <div
      className="fixed inset-0 glass-overlay z-50 flex items-center justify-center"
      onClick={handleOverlayClick}
    >
      <div className="glass-surface border border-line rounded-lg w-[480px] max-h-[70vh] flex flex-col">
        <div className="px-5 pt-5 pb-3 border-b border-line">
          <h3 className="text-[15px] font-semibold text-text-bright mb-3">Select Project Directory</h3>
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
          {entries.map(entry => (
            <button
              key={entry.path}
              onClick={() => loadDir(entry.path)}
              className="w-full text-left px-3 py-1.5 text-[13px] text-text-default hover:bg-surface rounded transition-colors truncate"
            >
              📂 {entry.name}
            </button>
          ))}
          {entries.length === 0 && !parent && (
            <p className="text-[13px] text-faint italic text-center py-4">Empty directory</p>
          )}
        </div>

        <div className="flex justify-end gap-2 px-5 py-3 border-t border-line">
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); dispatch({ type: 'CLOSE_DIR_PICKER' }) }}
            className="px-4 py-1.5 text-[13px] text-muted border border-line rounded hover:bg-surface transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); handleSelect() }}
            disabled={creating || !(directPath.trim() || currentPath)}
            className="px-4 py-1.5 text-[13px] font-medium text-accent border border-accent/30 rounded hover:bg-accent-soft transition-colors disabled:opacity-40"
          >
            {creating ? 'Creating...' : 'Select this directory'}
          </button>
        </div>
      </div>
    </div>
  )
}

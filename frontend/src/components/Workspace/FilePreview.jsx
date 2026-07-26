import { useState, useEffect } from 'react'
import { api } from '../../api'

export default function FilePreview({ path, onClose }) {
  const [content, setContent] = useState(null)
  const [meta, setMeta] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [offset, setOffset] = useState(0)

  useEffect(() => {
    setLoading(true)
    setError(null)
    api('GET', `/api/workspace/file?path=${encodeURIComponent(path)}&offset=${offset}&lines=400`)
      .then(data => {
        setContent(data.content)
        setMeta(data)
      })
      .catch(err => setError(err.message || 'Failed to load'))
      .finally(() => setLoading(false))
  }, [path, offset])

  const name = path.split('/').pop()

  return (
    <div className="mx-2 mb-1 border border-line rounded-md overflow-hidden bg-surface/50">
      <div className="flex items-center px-2 py-1 border-b border-line/50 bg-surface">
        <span className="text-[11px] font-mono text-muted truncate flex-1">{name}</span>
        {meta && (
          <span className="text-[10px] text-faint mr-2">
            {meta.offset + 1}-{meta.offset + meta.lines} / {meta.total_lines || '?'}
          </span>
        )}
        <button
          onClick={() => setOffset(Math.max(0, offset - 400))}
          disabled={offset === 0 || loading}
          className="text-[10px] text-muted hover:text-text-bright px-1 disabled:opacity-30"
        >
          prev
        </button>
        <button
          onClick={() => setOffset(offset + 400)}
          disabled={!meta?.has_more || loading}
          className="text-[10px] text-muted hover:text-text-bright px-1 disabled:opacity-30"
        >
          next
        </button>
        <button onClick={onClose} className="text-[10px] text-muted hover:text-text-bright px-1">close</button>
      </div>
      <div className="max-h-[200px] overflow-auto">
        {loading && <div className="p-2 text-[11px] text-muted animate-pulse">Loading...</div>}
        {error && <div className="p-2 text-[11px] text-danger">{error}</div>}
        {content !== null && (
          <pre className="p-2 text-[11px] font-mono leading-[1.5] text-text whitespace-pre overflow-x-auto">
            {content.split('\n').map((line, i) => (
              <div key={i} className="flex">
                <span className="text-faint select-none w-10 text-right pr-2 shrink-0">{offset + i + 1}</span>
                <span>{line}</span>
              </div>
            ))}
          </pre>
        )}
      </div>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { api } from '../../api'
import { renderMarkdown } from '../../utils'
import { artifactUrl } from '../../artifacts'
import { useAppDispatch } from '../../context/AppContext'
import { isDesktopApp } from '../../desktop'

export default function ArtifactViewer({ path }) {
  const dispatch = useAppDispatch()
  const [content, setContent] = useState(null)
  const [error, setError] = useState('')
  const [source, setSource] = useState(false)
  const [version, setVersion] = useState(0)
  const [interactive, setInteractive] = useState(false)
  const html = /\.html?$/i.test(path)
  const markdown = /\.(md|markdown)$/i.test(path)
  useEffect(() => {
    let active = true
    setContent(null); setError('')
    api('GET', `/api/workspace/file?path=${encodeURIComponent(path)}&full=true`)
      .then(data => { if (active) setContent(data.content) })
      .catch(err => { if (active) setError(err.detail || err.message) })
    return () => { active = false }
  }, [path, version])
  function openLink(e) {
    const link = e.target.closest('[data-artifact-path]')
    if (link) { e.preventDefault(); dispatch({ type: 'OPEN_ARTIFACT', payload: link.dataset.artifactPath }) }
  }
  return <div className="flex flex-col h-full min-h-0">
    <div className="flex flex-wrap gap-2 items-center border-b border-line px-3 py-2 text-[12px] text-muted">
      <span className="min-w-0 flex-1 truncate font-mono" title={path}>{path.split(/[\\/]/).pop()}</span>
      {(html || markdown) && <button onClick={() => setSource(!source)}>{source ? 'Preview' : 'Source'}</button>}
      {html && <button title="Run this page’s JavaScript in an isolated frame" aria-pressed={interactive} onClick={() => setInteractive(!interactive)}>{interactive ? 'Static' : 'Interactive'}</button>}
      <button onClick={() => setVersion(v => v + 1)}>Refresh</button>
      {isDesktopApp() && <button onClick={() => dispatch({ type: 'EDIT_ARTIFACT', payload: path })}>Edit</button>}
      <button onClick={() => dispatch({ type: 'TOGGLE_WORKSPACE_EDITOR_EXPANDED' })}>Expand</button>
      <button aria-label="Close preview" onClick={() => dispatch({ type: 'CLOSE_ARTIFACT' })}>×</button>
    </div>
    {error ? <p className="p-3 text-[13px] text-danger">{error}</p> : content === null ? <p className="p-3 text-muted">Loading…</p> :
      html && !source ? <iframe key={`${path}:${version}:${interactive}`} title={path} src={`${artifactUrl(path)}?interactive=${interactive}`} sandbox={interactive ? 'allow-scripts' : ''} referrerPolicy="no-referrer" className="w-full flex-1 border-0 bg-document-canvas" /> :
      markdown && !source ? <div className="markdown-body p-4 overflow-auto text-text-default text-[14px]" onClick={openLink}
        dangerouslySetInnerHTML={{ __html: renderMarkdown(content, path.replace(/[\\/][^\\/]*$/, '')) }} /> :
      <pre className="flex-1 overflow-auto whitespace-pre p-4 font-mono text-[12px] text-text-default">{content}</pre>}
  </div>
}

import { memo, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { renderMarkdown } from '../../utils'
import FormattedText from './FormattedText'
import { isImageCollapsed, setImageCollapsed } from '../../imageCollapse'
import { useAppSelector, useAppDispatch } from '../../context/AppContext'

const selectWorkspaceRoot = state => state.currentWorkspaceRoot

// HTML previews post their document height; size the matching frame to fit.
if (typeof window !== 'undefined') {
  window.addEventListener('message', (e) => {
    if (!e.data?.myharnessHtmlPreview) return
    for (const frame of document.querySelectorAll('iframe.html-frame')) {
      if (frame.contentWindow === e.source) {
        frame.style.height = `${Math.min(Math.max(Number(e.data.height) || 0, 80), 900)}px`
        break
      }
    }
  })
}

function toggleHtmlPreview(button) {
  const block = button.closest('.html-preview')
  const frame = block?.querySelector('iframe')
  const pre = block?.querySelector('pre')
  if (!frame || !pre) return
  const showCode = pre.hidden
  pre.hidden = !showCode
  frame.hidden = showCode
  button.textContent = showCode ? 'Preview' : 'Code'
}

function handleMarkdownClick(e) {
  const toggle = e.target.closest('.code-toggle')
  if (toggle) { toggleHtmlPreview(toggle); return }
  const button = e.target.closest('.code-copy')
  if (!button) return
  const code = button.closest('.code-block')?.querySelector('code')
  if (!code) return
  navigator.clipboard?.writeText(code.textContent || '').then(() => {
    button.textContent = 'Copied'
    button.classList.add('copied')
    setTimeout(() => {
      button.textContent = 'Copy'
      button.classList.remove('copied')
    }, 1500)
  })
}

function applyImageState(wrapper, collapsed) {
  wrapper.classList.toggle('collapsed', collapsed)
  const toggle = wrapper.querySelector('.md-image-toggle')
  if (toggle) {
    toggle.setAttribute('aria-expanded', String(!collapsed))
    toggle.title = collapsed ? 'Show image' : 'Collapse image'
  }
}

function toggleMarkdownImage(button) {
  const wrapper = button.closest('.md-image')
  if (!wrapper) return
  const collapsed = !wrapper.classList.contains('collapsed')
  applyImageState(wrapper, collapsed)
  setImageCollapsed(wrapper.dataset.imageSrc, collapsed)
}

function ChevronIcon({ open }) {
  return (
    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
      className={`shrink-0 transition-transform ${open ? 'rotate-90' : ''}`} aria-hidden="true">
      <polyline points="9 6 15 12 9 18" />
    </svg>
  )
}

// Image attachment on a user message: a name chip when collapsed, the
// thumbnail (opening the full image) when expanded.
function AttachmentImage({ attachment, name, defaultCollapsed }) {
  const [collapsed, setCollapsed] = useState(() => isImageCollapsed(attachment.url, defaultCollapsed))
  const toggle = () => {
    setCollapsed(!collapsed)
    setImageCollapsed(attachment.url, !collapsed)
  }
  return (
    <div className={`rounded-lg border border-line text-[12px] text-muted ${collapsed ? '' : 'p-2'}`}>
      <button
        type="button"
        onClick={toggle}
        aria-expanded={!collapsed}
        title={collapsed ? 'Show image' : 'Collapse image'}
        className={`flex w-full min-w-0 items-center gap-1.5 text-left hover:text-accent ${collapsed ? 'px-2 py-1.5' : 'mb-1.5'}`}
      >
        <ChevronIcon open={!collapsed} />
        <span className="min-w-0 truncate">{name}</span>
      </button>
      {!collapsed && (
        <a href={attachment.url} target="_blank" rel="noreferrer" title={name} className="block">
          <img src={attachment.url} alt={name} className="max-h-44 rounded-md object-contain" />
        </a>
      )}
    </div>
  )
}

function MessageBubble({ role, text, displayText = '', markdown, images = [], attachments = [], streaming = false, steering = false, historical = false }) {
  const dispatch = useAppDispatch()
  const markdownRef = useRef(null)
  const workspaceRoot = useAppSelector(selectWorkspaceRoot)
  const displayAttachments = attachments.length ? attachments : images

  const html = useMemo(() => {
    if (role === 'assistant') return renderMarkdown(markdown || text, workspaceRoot, { htmlPreview: !streaming })
    return null
  }, [role, text, markdown, workspaceRoot, streaming])

  // Markdown is injected as HTML, so apply each image's collapsed state after
  // render: replayed (historical) messages start collapsed, live ones expanded.
  useLayoutEffect(() => {
    const root = markdownRef.current
    if (!root) return
    for (const wrapper of root.querySelectorAll('.md-image')) {
      applyImageState(wrapper, isImageCollapsed(wrapper.dataset.imageSrc, historical))
    }
  }, [html, historical])

  return (
    <article className={`min-w-0 max-w-full ${role === 'user' ? 'mb-4' : 'mb-5'} ${streaming ? '' : 'animate-fade-up'}`}>
      {steering && <div className="text-[12px] text-faint mb-0.5">You added, while it was working:</div>}
      {role === 'assistant' ? (
        <div
          ref={markdownRef}
          className={`markdown-body max-w-full overflow-x-auto text-[15px] leading-relaxed ${streaming ? 'stream-caret' : ''}`}
          onClick={e => {
            const link = e.target.closest('[data-artifact-path]')
            if (link) { e.preventDefault(); dispatch({ type: 'OPEN_ARTIFACT', payload: link.dataset.artifactPath }); return }
            const imageToggle = e.target.closest('.md-image-toggle')
            if (imageToggle) { toggleMarkdownImage(imageToggle); return }
            handleMarkdownClick(e)
          }}
          dangerouslySetInnerHTML={{ __html: html }}
        />
      ) : (
        <div className={`whitespace-pre-wrap [overflow-wrap:anywhere] text-[15px] leading-relaxed ${steering ? 'text-muted' : 'font-medium text-text-bright'}`}>
          {text && <div><FormattedText text={displayText || text} /></div>}
          {displayAttachments.length > 0 && (
            <div className="mt-3 grid grid-cols-2 gap-2 font-normal">
              {displayAttachments.map((attachment, i) => attachment.mime?.startsWith('image/') ? (
                <AttachmentImage
                  key={`${attachment.url || attachment.name || 'attachment'}_${i}`}
                  attachment={attachment}
                  name={attachment.name || `Attachment ${i + 1}`}
                  defaultCollapsed={historical}
                />
              ) : (
                <a
                  key={`${attachment.url || attachment.name || 'attachment'}_${i}`}
                  href={attachment.url}
                  target="_blank"
                  rel="noreferrer"
                  className="block rounded-lg border border-line p-2 text-[12px] text-muted hover:border-line-hover hover:text-accent"
                  title={attachment.name || `Attachment ${i + 1}`}
                >
                  <div className="flex min-h-16 items-center gap-2">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded border border-line/70 text-[10px] font-semibold">
                      {fileExtension(attachment.name)}
                    </span>
                    <span className="min-w-0 truncate">{attachment.name || `Attachment ${i + 1}`}</span>
                  </div>
                </a>
              ))}
            </div>
          )}
        </div>
      )}
    </article>
  )
}

function fileExtension(name = '') {
  const ext = String(name).split('.').pop()
  return ext && ext !== name ? ext.slice(0, 8).toUpperCase() : 'FILE'
}

// Settled bubbles never change; only the streaming tail gets new props.
export default memo(MessageBubble)

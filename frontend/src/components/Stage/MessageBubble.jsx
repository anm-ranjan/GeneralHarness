import { memo, useMemo } from 'react'
import { renderMarkdown } from '../../utils'
import { useAppSelector } from '../../context/AppContext'

const selectWorkspaceRoot = state => state.currentWorkspaceRoot

function handleMarkdownClick(e) {
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

function MessageBubble({ role, text, markdown, images = [], attachments = [], streaming = false }) {
  const workspaceRoot = useAppSelector(selectWorkspaceRoot)
  const displayAttachments = attachments.length ? attachments : images

  const html = useMemo(() => {
    if (role === 'assistant') return renderMarkdown(markdown || text, workspaceRoot)
    return null
  }, [role, text, markdown, workspaceRoot])

  return (
    <article className={`flex min-w-0 flex-col max-w-[min(720px,85%)] mb-3 ${streaming ? '' : 'animate-fade-up'} ${role === 'user' ? 'self-start' : 'self-end'}`}>
      <div className="text-[11px] text-faint uppercase tracking-wider mb-1 px-1">{role}</div>
      {role === 'assistant' ? (
        <div
          className={`markdown-body max-w-full overflow-x-auto bg-surface rounded-lg px-4 py-3 text-[14px] leading-relaxed ${streaming ? 'stream-caret' : ''}`}
          onClick={handleMarkdownClick}
          dangerouslySetInnerHTML={{ __html: html }}
        />
      ) : (
        <div className="bg-surface-raised rounded-lg px-4 py-3 text-[14px] leading-relaxed whitespace-pre-wrap">
          {text && <div>{text}</div>}
          {displayAttachments.length > 0 && (
            <div className="mt-3 grid grid-cols-2 gap-2">
              {displayAttachments.map((attachment, i) => (
                <a
                  key={`${attachment.url || attachment.name || 'attachment'}_${i}`}
                  href={attachment.url}
                  target="_blank"
                  rel="noreferrer"
                  className="block rounded-md border border-line bg-black/10 p-2 text-[12px] text-muted hover:border-line-hover hover:text-accent"
                  title={attachment.name || `Attachment ${i + 1}`}
                >
                  {attachment.mime?.startsWith('image/') ? (
                    <img src={attachment.url} alt={attachment.name || `Attachment ${i + 1}`} className="max-h-44 rounded-md object-contain" />
                  ) : (
                    <div className="flex min-h-16 items-center gap-2">
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded border border-line/70 text-[10px] font-semibold">
                        {fileExtension(attachment.name)}
                      </span>
                      <span className="min-w-0 truncate">{attachment.name || `Attachment ${i + 1}`}</span>
                    </div>
                  )}
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

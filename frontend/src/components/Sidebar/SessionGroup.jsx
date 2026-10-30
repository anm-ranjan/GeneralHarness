import { memo, useEffect, useState } from 'react'
import SessionItem from './SessionItem'
import { lastSessionId, isRowAction } from '../../sidebarState'

// Collapsible flat list used for Chats and Archived. Unlike ProjectNode the
// group itself cannot be renamed or deleted — only the sessions in it.
function SessionGroup({
  title, sessions, labelsById, subtitles, currentSessionId, emptyText, action, defaultCollapsed = false,
  filtering = false, muted = false,
  onSelectSession, onRenameSession, onSessionContextMenu, renameSessionId, onRenameHandled,
}) {
  const [collapsed, setCollapsed] = useState(() => defaultCollapsed && !sessions.some(s => s.id === (currentSessionId || lastSessionId())))
  const containsActive = sessions.some(session => session.id === currentSessionId)

  useEffect(() => {
    if (containsActive) setCollapsed(false)
  }, [containsActive, currentSessionId])

  const open = (filtering && sessions.length > 0) || !collapsed

  return (
    <div className={`mb-2 rounded-md transition-colors ${containsActive ? 'bg-accent-glow/40' : ''}`}>
      <div className={`flex items-center gap-1.5 group rounded-md px-1.5 py-1 ${containsActive ? 'text-accent' : ''}`}
        tabIndex={0} aria-expanded={open}
        onClick={e => { if (!isRowAction(e.target)) setCollapsed(v => !v) }}
        onKeyDown={e => { if (!isRowAction(e.target) && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); setCollapsed(v => !v) } }}>
        <button
          onClick={() => setCollapsed(!collapsed)}
          className={`text-faint text-[10px] w-4 h-4 shrink-0 transition-transform ${open ? '' : '-rotate-90'}`}
          aria-label={open ? `Collapse ${title}` : `Expand ${title}`}
          aria-expanded={open}
        >
          ▼
        </button>
        <span className={`text-[13px] font-semibold truncate cursor-pointer ${muted ? 'text-muted' : 'text-text-bright'}`}>
          {title}
        </span>
        <span className="ml-1 text-[10px] text-faint tabular-nums">{sessions.length}</span>
        {action && <div className="flex gap-0.5 ml-auto shrink-0">{action}</div>}
      </div>

      <div className={`collapse-grid ${open ? 'open' : ''}`}>
        <div hidden={!open} className="mt-0.5 pl-4 space-y-px">
          {sessions.length === 0 ? (
            <p className="text-[12px] text-faint italic py-1 px-2">{emptyText}</p>
          ) : (
            sessions.map(session => (
              <SessionItem
                key={session.id}
                session={session}
                labels={labelsById?.[session.id]}
                subtitle={subtitles?.[session.id] || ''}
                isActive={session.id === currentSessionId}
                onSelect={onSelectSession}
                onRename={onRenameSession}
                onContextMenu={onSessionContextMenu}
                renameRequested={renameSessionId === session.id}
                onRenameDone={onRenameHandled}
              />
            ))
          )}
        </div>
      </div>
    </div>
  )
}

export default memo(SessionGroup)

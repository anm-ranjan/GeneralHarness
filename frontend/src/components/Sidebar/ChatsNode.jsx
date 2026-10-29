import { useEffect, useState } from 'react'
import SessionItem from './SessionItem'
import { lastSessionId, isRowAction } from '../../sidebarState'

// Flat list of general-purpose chats rendered at the bottom of the sidebar.
// Unlike ProjectNode there is no task layer and the collection itself cannot be
// renamed or deleted — only individual chats.
export default function ChatsNode({
  chats, sessionsById, currentSessionId,
  onStartChat, onSelectSession, onRenameSession, onDeleteSession,
}) {
  const [collapsed, setCollapsed] = useState(() => !chats.some(s => s.id === (currentSessionId || lastSessionId())))
  const containsActiveChat = chats.some(session => session.id === currentSessionId)

  useEffect(() => {
    if (containsActiveChat) setCollapsed(false)
  }, [containsActiveChat, currentSessionId])

  return (
    <div className={`mb-3 rounded-md transition-colors ${containsActiveChat ? 'bg-accent-glow/40' : ''}`}>
      <div className={`flex items-center gap-1.5 group rounded-md px-1.5 py-1 ${containsActiveChat ? 'text-accent' : ''}`}
        tabIndex={0} aria-expanded={!collapsed}
        onClick={e => { if (!isRowAction(e.target)) setCollapsed(v => !v) }}
        onKeyDown={e => { if (!isRowAction(e.target) && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); setCollapsed(v => !v) } }}>
        <button
          onClick={() => setCollapsed(!collapsed)}
          className={`text-faint text-[10px] w-4 h-4 shrink-0 transition-transform ${collapsed ? '-rotate-90' : ''}`}
          aria-label={collapsed ? 'Expand chats' : 'Collapse chats'}
          aria-expanded={!collapsed}
        >
          ▼
        </button>
        <span className="text-[13px] font-semibold text-text-bright truncate cursor-pointer">
          Chats
        </span>
        <span className="ml-1 text-[10px] text-faint tabular-nums">{chats.length}</span>
        <div className="flex gap-0.5 ml-auto shrink-0">
          <button
            onClick={() => onStartChat()}
            className="text-faint hover:text-ok text-[11px] px-0.5"
            title="New chat"
          >+ chat</button>
        </div>
      </div>

      <div className={`collapse-grid ${collapsed ? '' : 'open'}`}>
        <div hidden={collapsed} className="mt-1 pl-4">
          {chats.length === 0 ? (
            <p className="text-[12px] text-faint italic py-1">No chats yet.</p>
          ) : (
            chats.map(session => (
              <SessionItem
                key={session.id}
                session={session}
                isActive={session.id === currentSessionId}
                onSelect={onSelectSession}
                onRename={onRenameSession}
                onDelete={(sid) => onDeleteSession(sid)}
              />
            ))
          )}
        </div>
      </div>
    </div>
  )
}

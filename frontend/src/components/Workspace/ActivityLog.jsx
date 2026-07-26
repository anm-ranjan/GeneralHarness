import { useApp } from '../../context/AppContext'
import { relativeTime } from '../../utils'

const ACTION_ICONS = {
  created: '+',
  modified: '~',
  deleted: '-',
  shell: '$',
}

const ACTION_COLORS = {
  created: 'text-ok',
  modified: 'text-accent',
  deleted: 'text-danger',
  shell: 'text-info',
}

export default function ActivityLog() {
  const { state } = useApp()
  const { touchedFiles, stageItems, currentWorkspaceRoot } = state

  const entries = []

  for (const f of touchedFiles) {
    entries.push({
      key: `fc-${f.path}-${f.timestamp}`,
      time: f.timestamp,
      icon: ACTION_ICONS[f.action] || '~',
      color: ACTION_COLORS[f.action] || 'text-muted',
      text: relativePath(f.path, currentWorkspaceRoot),
      badge: f.action,
      badgeColor: f.action,
    })
  }

  for (const item of stageItems) {
    if (item.type === 'work_group') {
      for (const tool of (item.tools || [])) {
        if (tool.name === 'shell_run' && tool.detail) {
          entries.push({
            key: `sh-${item._id}-${tool.detail}`,
            time: null,
            icon: '$',
            color: 'text-info',
            text: tool.detail.length > 80 ? tool.detail.slice(0, 77) + '...' : tool.detail,
            badge: 'shell',
            badgeColor: 'shell',
          })
        }
      }
    }
  }

  if (!entries.length) {
    return (
      <div className="p-4 text-[13px] text-muted italic">
        No activity yet in this session.
      </div>
    )
  }

  return (
    <div className="flex flex-col">
      {entries.map(e => (
        <div key={e.key} className="flex items-start gap-2 px-3 py-2 border-b border-line/50">
          <span className={`shrink-0 font-mono font-bold text-[13px] w-4 text-center ${e.color}`}>
            {e.icon}
          </span>
          <div className="flex-1 min-w-0">
            <div className="text-[12px] font-mono text-text-bright truncate">{e.text}</div>
            <div className="flex items-center gap-2 mt-0.5">
              <span className={`text-[10px] uppercase font-semibold ${ACTION_COLORS[e.badgeColor] || 'text-muted'}`}>
                {e.badge}
              </span>
              {e.time && (
                <span className="text-[10px] text-faint">{relativeTime(e.time)}</span>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

function relativePath(fullPath, root) {
  if (root && fullPath.startsWith(root)) {
    const rel = fullPath.slice(root.length)
    return rel.startsWith('/') ? rel.slice(1) : rel
  }
  return fullPath
}

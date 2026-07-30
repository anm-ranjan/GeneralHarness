import { useState } from 'react'
import { useApp, useSwitchHost } from '../../context/AppContext'
import {
  describeHostStatus,
  hostNeedsAttention,
  summarizeOtherHosts,
} from '../../fleet'

function ComputerIcon({ className }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="2.5" y="4" width="19" height="12" rx="1.5" />
      <path d="M8.5 20h7M12 16v4" />
    </svg>
  )
}

/**
 * Picks which machine the whole app is working against.
 *
 * Hosts do not share projects, tasks, or sessions, so this swaps the entire
 * workspace rather than filtering one. The status line under each host is the
 * only thing that spans machines: it exists so a run left waiting on an
 * approval elsewhere is visible from here.
 */
export default function HostSwitcher() {
  const { state } = useApp()
  const switchHost = useSwitchHost()
  const [open, setOpen] = useState(false)

  // No fleet configured means there is nothing to switch between.
  if (!state.fleetHosts.length) return null

  const active = state.fleetHosts.find(host => host.id === state.activeHostId)
  const others = summarizeOtherHosts(state.fleetHosts, state.fleetStatuses, state.activeHostId)
  const elsewhereNeedsAttention = others.waitingApproval > 0

  const summary = elsewhereNeedsAttention
    ? `${others.waitingApproval} waiting approval elsewhere`
    : others.running > 0
      ? `${others.running} running elsewhere`
      : others.offline > 0
        ? `${others.offline} host${others.offline === 1 ? '' : 's'} unreachable`
        : ''

  return (
    <div className="mb-3">
      <button
        onClick={() => setOpen(value => !value)}
        className="w-full flex items-center gap-2 px-2 py-1.5 text-left border border-line rounded-md hover:border-accent/30 hover:bg-accent-soft transition-colors"
        title="Switch which machine you are working on"
        aria-expanded={open}
      >
        <ComputerIcon className="w-4 h-4 text-accent shrink-0" />
        <span className="flex-1 min-w-0">
          <span className="block text-[13px] font-medium truncate">
            {active ? active.label : 'Select a host'}
          </span>
          {summary && (
            <span
              className={`block text-[11px] truncate ${
                elsewhereNeedsAttention ? 'text-amber-500' : 'text-faint'
              }`}
            >
              {summary}
            </span>
          )}
        </span>
        {state.hostSwitching && (
          <span className="text-[10px] text-faint shrink-0">switching…</span>
        )}
        <span className="text-[10px] text-faint shrink-0">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <ul className="mt-1 border border-line rounded-md overflow-hidden">
          {state.fleetHosts.map(host => {
            const status = state.fleetStatuses[host.id]
            const isActive = host.id === state.activeHostId
            // A host that is not answering has nothing to show, so switching
            // to it would only produce an empty, broken workspace. The poll
            // re-enables it automatically once it comes back.
            const reachable = Boolean(status?.online)
            const selectable = reachable && !isActive

            return (
              <li key={host.id}>
                <button
                  disabled={!selectable}
                  onClick={() => {
                    setOpen(false)
                    switchHost(host.id)
                  }}
                  className={`w-full flex items-center gap-2 px-2 py-1.5 text-left transition-colors ${
                    isActive
                      ? 'bg-accent-soft'
                      : selectable
                        ? 'hover:bg-accent-soft'
                        : 'opacity-50 cursor-not-allowed'
                  }`}
                  title={
                    isActive
                      ? `Working on ${host.label}`
                      : reachable
                        ? `Switch to ${host.label} (${host.url})`
                        : `${host.label} is not responding at ${host.url}`
                  }
                >
                  <ComputerIcon
                    className={`w-4 h-4 shrink-0 ${isActive ? 'text-accent' : 'text-muted'}`}
                  />
                  <span className="flex-1 min-w-0">
                    <span className="block text-[13px] truncate">{host.label}</span>
                    <span
                      className={`block text-[11px] truncate ${
                        hostNeedsAttention(status) ? 'text-amber-500' : 'text-faint'
                      }`}
                    >
                      {describeHostStatus(status)}
                    </span>
                  </span>
                  {isActive && <span className="text-[11px] text-accent shrink-0">●</span>}
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

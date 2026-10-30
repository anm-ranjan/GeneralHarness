import { useState } from 'react'
import { useApp, useSwitchHost } from '../../context/AppContext'
import {
  describeHostStatus,
  hostIdMismatch,
  hostNeedsAttention,
  summarizeOtherHosts,
} from '../../fleet'

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

  const hostName = active ? active.label : 'no host selected'

  return (
    <div className="mt-4 text-[12.5px] text-muted">
      {open && (
        <ul className="mb-2 border-t border-line">
          {state.fleetHosts.map(host => {
            const status = state.fleetStatuses[host.id]
            const isActive = host.id === state.activeHostId
            // A host that is not answering has nothing to show, so switching
            // to it would only produce an empty, broken workspace. The poll
            // re-enables it automatically once it comes back.
            const reachable = Boolean(status?.online)
            const selectable = reachable && !isActive
            // The host answers, but calls itself something else: the fleet
            // lists were not kept identical across machines. Switching still
            // works, so this warns rather than blocks.
            const mismatch = hostIdMismatch(host, status)

            return (
              <li key={host.id}>
                <button
                  disabled={!selectable}
                  onClick={() => {
                    setOpen(false)
                    switchHost(host.id)
                  }}
                  className={`w-full flex items-center gap-2 px-1 py-1.5 text-left transition-colors ${
                    isActive
                      ? 'bg-accent-soft'
                      : selectable
                        ? 'hover:bg-accent-soft'
                        : 'opacity-50 cursor-not-allowed'
                  }`}
                  title={
                    mismatch
                      ? `${host.label} is configured as "${host.id}" here but calls itself `
                        + `"${mismatch}". Use the same fleet.hosts list on every machine, `
                        + 'changing only fleet.self.'
                      : isActive
                        ? `Working on ${host.label}`
                        : reachable
                          ? `Switch to ${host.label} (${host.url})`
                          : `${host.label} is not responding at ${host.url}`
                  }
                >
                                    <span className="flex-1 min-w-0">
                    <span className="block text-[13px] truncate">{host.label}</span>
                    <span
                      className={`block text-[11px] truncate ${
                        hostNeedsAttention(status) ? 'text-warn' : 'text-faint'
                      }`}
                    >
                      {describeHostStatus(status)}
                    </span>
                    {mismatch && (
                      <span className="block text-[11px] text-warn truncate">
                        calls itself “{mismatch}”, not “{host.id}”
                      </span>
                    )}
                  </span>
                  {isActive && <span className="text-[11px] text-accent shrink-0">●</span>}
                </button>
              </li>
            )
          })}
        </ul>
      )}

      <p>
        Working on {hostName}.{' '}
        <button
          onClick={() => setOpen(value => !value)}
          className="underline decoration-line underline-offset-4 hover:text-accent transition-colors"
          title="Switch which machine you are working on"
          aria-expanded={open}
        >
          {state.hostSwitching ? 'Switching…' : 'Switch'}
        </button>
      </p>
      {summary && (
        <p className={elsewhereNeedsAttention ? 'text-warn' : 'text-faint'}>{summary}</p>
      )}
    </div>
  )
}

// Pure helpers for working across machines. Hosts keep entirely separate
// projects, tasks, and sessions, so nothing here merges data — it only decides
// which host is being viewed, how to key that host's saved UI state, and how to
// describe the hosts you are not looking at.
//
// Kept free of React and browser globals so it can be unit tested with
// node:test like the other frontend modules.

/** Host id used for saved state before any fleet is configured. */
export const SINGLE_HOST_ID = 'local'

/** How long a host may go unanswered before the UI calls it unreachable. */
export const STATUS_TIMEOUT_MS = 4000

/**
 * Drop malformed entries from the registry the backend served.
 *
 * The backend already validates its own config, but the page may be served by
 * an older host, so treat the payload as untrusted rather than assuming shape.
 */
export function normalizeFleet(payload) {
  const hosts = Array.isArray(payload?.hosts) ? payload.hosts : []
  const seen = new Set()
  const cleaned = []
  for (const host of hosts) {
    const id = String(host?.id || '').trim()
    const url = String(host?.url || '').trim().replace(/\/+$/, '')
    if (!id || !url || seen.has(id)) continue
    seen.add(id)
    cleaned.push({
      id,
      url,
      label: String(host?.label || id).trim(),
      self: host?.self === true,
    })
  }
  // One host is not a fleet: there is nothing to switch between, and showing a
  // switcher with a single entry is just noise.
  if (cleaned.length < 2) return []
  return cleaned
}

/**
 * The host a session should open against on load: the last one used if it is
 * still in the registry, otherwise this machine.
 *
 * Falling back to `self` rather than to the saved-but-unknown host matters when
 * a host is removed from the config — otherwise the UI boots pointed at a
 * machine it can no longer describe.
 */
export function resolveActiveHost(hosts, savedId) {
  if (!hosts.length) return null
  return (
    hosts.find(host => host.id === savedId) ||
    hosts.find(host => host.self) ||
    hosts[0]
  )
}

/**
 * Namespace a browser-storage key to one host.
 *
 * Session ids are only unique within a machine, so unprefixed keys would let
 * one host's drafts and recents surface under another's.
 */
export function hostStorageKey(hostId, key) {
  return `myharness:${hostId || SINGLE_HOST_ID}:${key}`
}

/**
 * Fold a /api/fleet/status response into the shape the switcher renders.
 * A rejected poll is not an error state to report, just an offline host.
 */
export function hostStatus(result) {
  if (!result || result.ok !== true) {
    return { online: false, running: 0, waitingApproval: 0, reportedId: '' }
  }
  return {
    online: true,
    running: Math.max(0, Number(result.running) || 0),
    waitingApproval: Math.max(0, Number(result.waiting_approval) || 0),
    // What that machine calls itself. The fleet list is meant to be identical
    // everywhere, so this should equal the id configured for the host.
    reportedId: String(result.host_id || '').trim(),
  }
}

/**
 * The id a host calls itself when it disagrees with the id configured for it,
 * or '' when they match.
 *
 * Host ids namespace saved per-host state and are how every machine refers to
 * every other, so two configs that disagree are a real misconfiguration -- but
 * a silent one, because each machine works fine on its own. Reporting it is
 * the difference between a five-minute fix and a confusing afternoon.
 *
 * An empty reported id means the peer has no fleet configured (or predates
 * this check), which is not something to complain about.
 */
export function hostIdMismatch(host, status) {
  if (!status || !status.online) return ''
  const reported = String(status.reportedId || '').trim()
  if (!reported || reported === host.id) return ''
  return reported
}

/**
 * One-line description of a host for the switcher.
 * Approvals come first: a run blocked on a prompt is stuck until someone
 * switches over, which is the whole reason to show other hosts at all.
 */
export function describeHostStatus(status) {
  if (!status || !status.online) return 'unreachable'
  const parts = []
  if (status.waitingApproval > 0) {
    parts.push(`${status.waitingApproval} waiting approval`)
  }
  if (status.running > 0) parts.push(`${status.running} running`)
  return parts.length ? parts.join(' · ') : 'idle'
}

/** True when a host needs the user's attention, for the switcher's dot. */
export function hostNeedsAttention(status) {
  return Boolean(status?.online && status.waitingApproval > 0)
}

/**
 * Fleet-wide summary for the collapsed switcher: what is happening on the
 * machines you are not currently looking at.
 */
export function summarizeOtherHosts(hosts, statuses, activeHostId) {
  let waitingApproval = 0
  let running = 0
  let offline = 0
  for (const host of hosts) {
    if (host.id === activeHostId) continue
    const status = statuses?.[host.id]
    if (!status || !status.online) {
      offline += 1
      continue
    }
    waitingApproval += status.waitingApproval
    running += status.running
  }
  return { waitingApproval, running, offline }
}

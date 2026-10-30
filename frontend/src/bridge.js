// Pure helpers for bringing fleet peers online and offline from Settings.
//
// A peer is online while its backend answers; the machine you are sitting at
// owns the SSH forwards and runs the start and stop, whichever host the UI is
// currently viewing. Kept free of React and browser globals so node:test can
// cover it.

const STATE_LABELS = {
  online: 'Online',
  off: 'Off',
  unreachable: 'Unreachable',
}

/** Drop malformed rows from /api/fleet/bridge; the page may predate the route. */
export function normalizeBridgeHosts(payload) {
  const hosts = Array.isArray(payload?.hosts) ? payload.hosts : []
  return hosts
    .map(host => ({
      id: String(host?.id || '').trim(),
      label: String(host?.label || host?.id || '').trim(),
      state: STATE_LABELS[host?.state] ? host.state : 'unreachable',
      running: Math.max(0, Number(host?.running) || 0),
      waitingApproval: Math.max(0, Number(host?.waiting_approval) || 0),
    }))
    .filter(host => host.id)
}

export function bridgeStateLabel(state) {
  return STATE_LABELS[state] || STATE_LABELS.unreachable
}

/** The one action a row offers, or null when nothing can be done (unreachable). */
export function bridgeAction(host) {
  if (host.state === 'online') return { kind: 'stop', label: 'Take offline' }
  if (host.state === 'off') return { kind: 'start', label: 'Bring online' }
  return null
}

/** What taking a host offline will interrupt, as a short phrase ('' when idle). */
export function describeActivity(host) {
  const parts = []
  if (host.waitingApproval > 0) parts.push(`${host.waitingApproval} waiting for approval`)
  if (host.running > 0) parts.push(`${host.running} running`)
  return parts.join(' and ')
}

export function stopConfirmation(host) {
  const activity = describeActivity(host)
  return {
    title: `Take ${host.label} offline?`,
    message: activity
      ? `${host.label} has ${activity}. Stopping its backend ends those runs, and the host disappears for every client until it is brought online again.`
      : `This stops the backend on ${host.label}. The host disappears for every client until it is brought online again.`,
    confirmLabel: 'Take offline',
    tone: 'danger',
    force: activity !== '',
  }
}

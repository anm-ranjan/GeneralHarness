// Pure helpers for the application-level run-state stream (/api/events),
// kept out of the hook so they can be unit-tested with node:test.

// Non-idle states are tracked; idle removes the entry so the map stays small.
export function applyRunState(runStates, sessionId, state) {
  if (!sessionId) return runStates
  const next = { ...runStates }
  if (!state || state === 'idle') {
    delete next[sessionId]
  } else {
    next[sessionId] = state
  }
  return next
}

export function snapshotRunStates(sessions) {
  const map = {}
  for (const s of sessions || []) {
    if (s.session_id && s.state && s.state !== 'idle') {
      map[s.session_id] = s.state
    }
  }
  return map
}

// Dot colour and tooltip for a session's run state, shared by every place that
// shows the indicator so the states cannot drift apart between them.
export function runStateBadge(runState) {
  if (runState === 'waiting_approval') {
    return { dotClass: 'bg-warn shadow-[0_0_5px_var(--color-warn)]', label: 'Waiting for approval' }
  }
  if (runState === 'waiting_input') {
    return { dotClass: 'bg-accent shadow-[0_0_5px_var(--color-accent)]', label: 'Waiting for an answer' }
  }
  return { dotClass: 'bg-ok animate-pulse', label: 'Run in progress' }
}

// Decides whether a run-state transition for a non-selected session warrants
// a desktop notification. Returns { title, body } or null.
export function runStateNotification(prevState, sessionId, state, currentSessionId, sessionTitle) {
  if (!sessionId || sessionId === currentSessionId) return null
  const label = sessionTitle || sessionId
  if (state === 'waiting_approval') {
    return { title: 'Approval needed', body: `Thread "${label}" is waiting for a tool approval.` }
  }
  if (state === 'waiting_input') {
    return { title: 'Question waiting', body: `Thread "${label}" is waiting for an answer.` }
  }
  if (state === 'idle' && (prevState === 'running' || prevState === 'waiting_approval' || prevState === 'waiting_input')) {
    return { title: 'Run finished', body: `Thread "${label}" completed its run.` }
  }
  return null
}

const ACTIVE_STATES = new Set(['running', 'waiting_approval', 'waiting_input'])

// Threads whose run finished while the user was not looking at them (another
// thread open, or the window not focused). Running again or being opened
// clears the mark.
export function trackUnseenFinished(unseen, prevState, sessionId, state, viewing) {
  if (!sessionId) return unseen
  const idle = !state || state === 'idle'
  if (idle && ACTIVE_STATES.has(prevState) && !viewing) {
    return unseen[sessionId] ? unseen : { ...unseen, [sessionId]: true }
  }
  // Running again, or finishing in front of the user, leaves nothing unseen.
  if (!idle || viewing) return withoutSession(unseen, sessionId)
  return unseen
}

export function withoutSession(unseen, sessionId) {
  if (!sessionId || !unseen[sessionId]) return unseen
  const next = { ...unseen }
  delete next[sessionId]
  return next
}

// The Dock badge: finished threads not yet looked at, plus threads waiting for
// the user's approval or answer.
export function attentionCount(runStates, unseen) {
  const waiting = Object.entries(runStates || {})
    .filter(([id, state]) => (state === 'waiting_approval' || state === 'waiting_input') && !unseen?.[id])
    .length
  return Object.keys(unseen || {}).length + waiting
}

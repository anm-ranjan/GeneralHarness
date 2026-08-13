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

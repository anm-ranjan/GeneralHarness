// Child agents (native orchestration, Codex and Claude subagents) as reported by
// `agent_event`. Native events carry the full node (agent_path, status, role,
// task); Codex and Claude send the same core fields with their own extras.

// Actions that change an agent's own state, as opposed to progress lines a child
// emits about its work ("status", "tool_call", "plan", ...).
const LIFECYCLE = new Set(['spawned', 'started', 'completed', 'failed', 'interrupted', 'interrupting', 'error'])

/** Status an `agent_event` implies, or undefined when it says nothing about state. */
export function agentStatus(data = {}) {
  switch (data.action) {
    case 'started': return 'running'
    case 'spawned': return data.status || 'pending'
    case 'completed': case 'failed': case 'interrupted': case 'interrupting': return data.action
    case 'error': return 'failed'
    default: break
  }
  // Codex reports a thread status of its own ("active", "idle", ...).
  if (data.status === 'active') return 'running'
  return data.status
}

/** Whether an event is about an agent's state (and so belongs in the panel). */
export function isAgentStateEvent(data = {}) {
  return LIFECYCLE.has(data.action) || data.status !== undefined
}

const LABELS = {
  pending: 'waiting',
  running: 'working',
  interrupting: 'stopping',
  completed: 'done',
  failed: 'failed',
  interrupted: 'stopped',
  idle: 'idle',
}

export function agentStatusLabel(status) {
  return LABELS[status] || String(status || '')
}

export function agentIsLive(agent) {
  return agent?.status === 'running' || agent?.status === 'pending' || agent?.status === 'interrupting'
}

// Root first, then live agents before finished ones, then by path.
export function sortedAgents(agentsById = {}) {
  const rank = agent => (agent.agent_path === '/root' ? -1 : agentIsLive(agent) ? 0 : 1)
  return Object.values(agentsById).sort((a, b) =>
    rank(a) - rank(b) || String(a.agent_path || a.agent_id).localeCompare(String(b.agent_path || b.agent_id)))
}

// Only native agents run under an orchestrator that can stop them, and the root
// agent is the run itself.
export function canInterruptAgent(agent) {
  return agent?.provider === 'native' && agent.agent_path !== '/root' && agentIsLive(agent) && agent.status !== 'interrupting'
}

export function agentLabel(agent) {
  return agent?.nickname || agent?.agent_path || agent?.agent_id || 'agent'
}

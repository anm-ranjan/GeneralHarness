// ACP agent providers are named "acp:<agent key>", e.g. "acp:antigravity".
// The backend lists the usable ones in /api/health as acp_agents.

export const ACP_PREFIX = 'acp:'

export function isAcpProvider(provider) {
  return String(provider || '').startsWith(ACP_PREFIX)
}

export function acpKey(provider) {
  return isAcpProvider(provider) ? String(provider).slice(ACP_PREFIX.length) : ''
}

// Display name: the configured label when known, else the key in title case.
export function acpLabel(provider, agents = []) {
  const agent = (agents || []).find(item => item.provider === provider)
  if (agent?.label) return agent.label
  const key = acpKey(provider)
  return key ? key.charAt(0).toUpperCase() + key.slice(1) : ''
}

export function acpAgentEnabled(state, provider) {
  return (state?.acpAgents || []).some(item => item.provider === provider)
}

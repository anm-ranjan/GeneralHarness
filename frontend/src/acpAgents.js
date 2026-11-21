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

// The composer notice for a signed-out agent, or '' when none is needed.
export function signInNotice(status) {
  if (!status || status.signed_in !== false) return ''
  return `${status.label} is not signed in. In a terminal, run \`${status.login_command}\` and sign in with your Google account, then send your message.`
}

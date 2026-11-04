// Pure helpers for the Workspace "Commands" tab (GET /api/commands).

// Text placed in the composer for one usage line: everything before the first
// <placeholder>, with a trailing space so the argument can be typed directly.
export function commandInsertText(usage) {
  const text = String(usage || '').trim()
  const placeholder = text.indexOf('<')
  if (placeholder === -1) return text
  return `${text.slice(0, placeholder).trimEnd()} `
}

// Providers the command does not support for the current session, or null.
export function commandUnavailableReason(command, provider) {
  const providers = command?.providers
  if (!provider || !Array.isArray(providers) || providers.includes(provider)) return null
  const labels = { native: 'native', 'codex-app-server': 'Codex', 'claude-agent': 'Claude' }
  return `Only for ${providers.map(p => labels[p] || p).join(', ')} sessions`
}

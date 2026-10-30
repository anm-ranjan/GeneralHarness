// Plain-language names for the approval modes the backend exposes through
// `/approve <mode>`. Unknown modes are shown as-is rather than hidden.
export const APPROVAL_MODES = {
  always_ask: 'Asks before changes and commands',
  shell_only: 'Asks before shell commands',
  auto_approve: 'Runs commands without asking',
}

export function approvalLabel(mode) {
  if (!mode) return ''
  return APPROVAL_MODES[mode] || mode
}

export function nextApprovalMode(mode) {
  return mode === 'auto_approve' ? 'shell_only' : 'auto_approve'
}

// "gpt-6.1-terra, medium thinking"
export function modelSentence(modelLabel, effort) {
  return effort ? `${modelLabel}, ${effort} thinking` : modelLabel
}

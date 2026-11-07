// Pure display helpers for routine agents (labels, errors, status tones).
// Kept free of JSX so `node --test` can exercise them directly.

// "timeout_per_model" → "Timeout per model". Manifests may omit labels.
export function humanize(name = '') {
  const text = String(name).replace(/[_-]+/g, ' ').trim()
  return text ? text[0].toUpperCase() + text.slice(1) : ''
}

export function fieldLabel(name, spec = {}) {
  return spec.label || humanize(name)
}

// FastAPI details arrive as a string, a list of strings (our validation), or
// a list of {loc, msg} objects (request-model validation). Return lines.
export function errorLines(error) {
  const detail = error?.detail ?? error?.message ?? error
  if (detail == null || detail === '') return ['Something went wrong.']
  if (Array.isArray(detail)) return detail.flatMap(d => typeof d === 'string' ? [d] : d?.msg ? [`${(d.loc || []).filter(x => x !== 'body').join('.') || 'request'}: ${d.msg}`] : [JSON.stringify(d)])
  if (typeof detail === 'object') return [detail.msg || JSON.stringify(detail)]
  return [String(detail)]
}

// Split "name: message" lines into per-field messages using the form's labels;
// anything else stays general.
export function splitFieldErrors(lines, params = {}) {
  const fields = {}
  const general = []
  for (const line of lines) {
    const match = /^([A-Za-z_][A-Za-z0-9_]*): (.*)$/s.exec(line)
    if (match && params[match[1]]) (fields[match[1]] ||= []).push(sentence(match[2]))
    else general.push(line)
  }
  return { fields, general }
}

function sentence(text) {
  const value = String(text).trim()
  return value ? value[0].toUpperCase() + value.slice(1) : value
}

const STATUS = {
  completed: { label: 'Completed', tone: 'ok' },
  failed: { label: 'Failed', tone: 'danger' },
  stopped: { label: 'Stopped', tone: 'warn' },
  timed_out: { label: 'Timed out', tone: 'danger' },
  running: { label: 'Running', tone: 'info' },
  waiting_approval: { label: 'Needs approval', tone: 'warn' },
  waiting_input: { label: 'Needs answer', tone: 'warn' },
  starting: { label: 'Starting', tone: 'info' },
  loading: { label: 'Loading', tone: 'faint' },
  idle: { label: 'No script run', tone: 'faint' },
  ready: { label: 'Ready', tone: 'ok' },
  not_ready: { label: 'Not ready', tone: 'warn' },
  invalid: { label: 'Invalid', tone: 'danger' },
  checking: { label: 'Checking…', tone: 'faint' },
}

export function statusInfo(status) {
  return STATUS[status] || { label: humanize(status || 'unknown'), tone: 'faint' }
}

// Live state wins (running / approval); otherwise the last script outcome.
export function runDisplayStatus(liveState, session, lastResult) {
  if (liveState && liveState !== 'idle') return liveState
  if (session?.status === 'running') return 'running'
  return lastResult?.status || session?.agent_last_result?.status || 'idle'
}

export function itemCounts(result) {
  if (!result || result.items_total == null) return ''
  const parts = [`${result.items_ok ?? 0} ok`]
  if (result.items_failed) parts.push(`${result.items_failed} failed`)
  const other = (result.items_total ?? 0) - (result.items_ok ?? 0) - (result.items_failed ?? 0)
  if (other > 0) parts.push(`${other} other`)
  return parts.join(' · ')
}

// Free text plus edited form values are sent together; untouched defaults are
// left to the model so a description alone does not trip required fields.
export function formChanged(initial, current) {
  return JSON.stringify(initial) !== JSON.stringify(current)
}

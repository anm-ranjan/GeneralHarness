export function initialValues(params = {}) {
  return Object.fromEntries(Object.entries(params).map(([name, p]) => [name,
    ['paths', 'strings'].includes(p.type) ? (p.default || []).join('\n') : p.default ?? (p.type === 'bool' ? false : ''),
  ]))
}
export function toRequest(params, values) {
  const result = {}
  for (const [name, p] of Object.entries(params)) {
    const value = values[name]
    if (value === '' || value == null) continue
    result[name] = ['paths', 'strings'].includes(p.type) ? (Array.isArray(value) ? value : value.split('\n').map(v => v.trim()).filter(Boolean))
      : ['int', 'float'].includes(p.type) ? Number(value) : value
  }
  return result
}
export function validate(params, values) {
  const errors = []
  const data = toRequest(params, values)
  for (const [name, p] of Object.entries(params)) {
    const value = data[name]
    if (value == null || value === '' || (Array.isArray(value) && !value.length)) {
      if (p.required) errors.push(`${name}: required`)
      continue
    }
    if (['int', 'float'].includes(p.type) && (!Number.isFinite(value) || (p.type === 'int' && !Number.isInteger(value)) || value < (p.min ?? -Infinity) || value > (p.max ?? Infinity))) errors.push(`${name}: outside allowed range`)
    if (p.type === 'bool' && typeof value !== 'boolean') errors.push(`${name}: must be boolean`)
    if (p.type === 'enum' && !p.options.includes(value)) errors.push(`${name}: invalid option`)
    if (Array.isArray(value) && value.length > (p.max_items ?? 200)) errors.push(`${name}: too many items`)
    for (const v of Array.isArray(value) ? value : [value]) {
      if (typeof v === 'string' && v.length > (p.max_length ?? 200000)) errors.push(`${name}: too long`)
      if (p.pattern && !new RegExp(`^(?:${p.pattern})$`).test(v)) errors.push(`${name}: does not match pattern`)
    }
  }
  return errors
}

// Unsent agent-form entries, kept per agent for this window only so leaving an
// agent's page (to a session or another agent) does not lose them. A reload
// starts again from the agent's current defaults.
const drafts = new Map()
export function saveAgentDraft(name, draft) { drafts.set(name, draft) }
export function clearAgentDraft(name) { drafts.delete(name) }
// Saved values for parameters the agent still has, over its current defaults.
export function restoreAgentDraft(name, params = {}) {
  const defaults = initialValues(params)
  const saved = drafts.get(name)
  if (!saved) return { values: defaults, text: '' }
  const values = { ...defaults }
  for (const key of Object.keys(defaults)) if (Object.hasOwn(saved.values || {}, key)) values[key] = saved.values[key]
  return { values, text: typeof saved.text === 'string' ? saved.text : '' }
}

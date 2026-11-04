// Shapes a compaction's memory block (the native task state the model sees
// in place of older history) into what CompactionNotice draws.

const TASK_STATE = 'myharness-native-task-state-v1'

function text(value) {
  return typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value)
}

export function factLine(fact) {
  if (!fact || typeof fact !== 'object') return { label: 'fact', text: text(fact) }
  if ('assistant_report' in fact) return { label: 'Turn report', text: text(fact.assistant_report) }
  const target = fact.command || fact.path || fact.job_id
    || (fact.parameters && Object.keys(fact.parameters).length ? JSON.stringify(fact.parameters) : '')
  const failed = fact.ok === false
  const exit = fact.exit_code != null && fact.exit_code !== 0 ? ` (exit ${fact.exit_code})` : failed ? ' (failed)' : ''
  return { label: `${fact.name || 'tool'}${exit}`, text: text(target), mono: Boolean(fact.command || fact.path), bad: failed }
}

// The model summary is JSON with string-array fields when valid; anything
// else is shown as plain text. null means there was no summary.
export function summaryView(summary) {
  if (!summary) return null
  try {
    const parsed = JSON.parse(summary)
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return {
        sections: Object.entries(parsed).map(([key, items]) => ({
          key,
          items: Array.isArray(items) ? items.map(text) : [text(items)],
        })),
      }
    }
  } catch { /* not JSON: show as text */ }
  return { text: text(summary) }
}

export function compactionView(memory) {
  if (!memory || memory.schema !== TASK_STATE) return { taskState: false }
  const list = value => (Array.isArray(value) ? value : [])
  return {
    taskState: true,
    requests: list(memory.requests).map(text),
    summary: summaryView(memory.summary),
    facts: list(memory.facts).map(factLine),
    omitted: Number(memory.omitted_facts) || 0,
    priorMemory: list(memory.prior_memory).map(text),
  }
}

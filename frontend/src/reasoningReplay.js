// reasoning_replay modes (agent_config.yaml reasoning_replay.mode, or
// Settings -> Native Settings -> Agent loop) and what each one does.
export const REPLAY_OPTIONS = [
  { value: 'off', label: 'Off: show it only (default)',
    hint: "The model's thinking is shown in the transcript but never sent back to it. Uses the least context; each step of a task starts its reasoning afresh." },
  { value: 'strip_always', label: 'Within a task: keep it between tool calls',
    hint: 'Between tool calls of one task the model sees its own earlier reasoning, so it does not re-plan every step. Uses more context during long tasks; dropped when the task ends. The one to try first with a thinking model.' },
  { value: 'keep', label: 'Always: keep it in later tasks too',
    hint: 'The model also sees its reasoning from earlier tasks, until compaction summarizes them. Some hosted thinking APIs require this. Fills the context fastest.' },
  { value: 'strip_on_compact', label: 'Until compaction: keep it, clear it when compacting',
    hint: 'Like Always, but each compaction also clears the reasoning from the messages it keeps, so the context is rebuilt once. Suits local llama-server models with prompt caching.' },
]

export const REPLAY_NOTE = 'Only affects models that return their reasoning as text; OpenRouter passes its own reasoning back either way.'

/** Explanation of the selected mode, the general note, and any per-model entries that win over it. */
export function replayHint(mode, overrides = []) {
  const option = REPLAY_OPTIONS.find(o => o.value === mode)
  const parts = [option?.hint, REPLAY_NOTE]
  if (overrides.length) parts.push(`Per-model settings in agent_config.yaml take precedence for: ${overrides.join(', ')}.`)
  return parts.filter(Boolean).join(' ')
}

/** Short name of a mode ("Within a task"), for "config: ..." annotations. */
export function replayModeName(mode) {
  const option = REPLAY_OPTIONS.find(o => o.value === mode)
  return option ? option.label.split(':')[0] : String(mode ?? '')
}

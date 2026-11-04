import assert from 'node:assert/strict'
import test from 'node:test'

import { compactionView, factLine, summaryView } from './compaction.js'

const memory = {
  schema: 'myharness-native-task-state-v1',
  requests: ['Copy the session file.', '[Answer to agent question] Which contour range?\nminimum 0, maximum 0.5'],
  summary: JSON.stringify({ decisions: ['Range fixed at 0-0.5'], unfinished_work: [] }),
  facts: [
    { name: 'shell_run', command: 'meta_post64.bat -b -s run.ses', exit_code: 1, ok: false },
    { name: 'apply_patch', path: 'D:/scripts/x.py', ok: true },
    { assistant_report: 'Turn 1 done.', complete: false },
  ],
  omitted_facts: 160,
  prior_memory: [],
}

test('a task-state memory block becomes requests, summary sections and fact lines', () => {
  const view = compactionView(memory)
  assert.equal(view.taskState, true)
  assert.equal(view.requests.length, 2)
  assert.match(view.requests[1], /maximum 0\.5/)
  assert.deepEqual(view.summary.sections, [
    { key: 'decisions', items: ['Range fixed at 0-0.5'] },
    { key: 'unfinished_work', items: [] },
  ])
  assert.equal(view.omitted, 160)
  assert.deepEqual(view.facts[0], { label: 'shell_run (exit 1)', text: 'meta_post64.bat -b -s run.ses', mono: true, bad: true })
  assert.equal(view.facts[2].label, 'Turn report')
})

test('a missing or non-JSON summary is reported as such', () => {
  assert.equal(summaryView(''), null)
  assert.deepEqual(summaryView('free text'), { text: 'free text' })
  assert.equal(compactionView({ ...memory, summary: '' }).summary, null)
})

test('other memory formats fall back to the raw block', () => {
  assert.deepEqual(compactionView({ schema: 'myharness-context-ledger-v1', completed_turns: [] }), { taskState: false })
  assert.deepEqual(compactionView(null), { taskState: false })
  assert.equal(factLine({ name: 'file_read', parameters: {} }).text, '')
})

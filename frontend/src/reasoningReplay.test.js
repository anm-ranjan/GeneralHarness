import test from 'node:test'
import assert from 'node:assert/strict'
import { REPLAY_OPTIONS, REPLAY_NOTE, replayHint, replayModeName } from './reasoningReplay.js'

test('every backend mode has a label and an explanation', () => {
  assert.deepEqual(REPLAY_OPTIONS.map(o => o.value), ['off', 'strip_always', 'keep', 'strip_on_compact'])
  for (const option of REPLAY_OPTIONS) assert.ok(option.label && option.hint)
})

test('hint explains the selected mode and names per-model overrides', () => {
  const hint = replayHint('strip_always')
  assert.ok(hint.startsWith('Between tool calls of one task'))
  assert.ok(hint.endsWith(REPLAY_NOTE))
  assert.ok(replayHint('keep', ['inkling-*', 'glm-5.2']).endsWith('take precedence for: inkling-*, glm-5.2.'))
  assert.equal(replayHint('bogus'), REPLAY_NOTE)
})

test('short mode names', () => {
  assert.equal(replayModeName('strip_on_compact'), 'Until compaction')
  assert.equal(replayModeName('off'), 'Off')
  assert.equal(replayModeName('other'), 'other')
})

import test from 'node:test'
import assert from 'node:assert/strict'
import { templateValue, kwargsFor, mapPreview, VARIABLE_RE } from './reasoningMap.js'

const INKLING = { variable: 'reasoning_effort', low: 'low', medium: 'medium', high: 'high' }

test('values become booleans, numbers or text like the backend', () => {
  assert.equal(templateValue('false'), false)
  assert.equal(templateValue(' TRUE '), true)
  assert.equal(templateValue('0.2'), 0.2)
  assert.equal(templateValue('3'), 3)
  assert.equal(templateValue('low'), 'low')
  assert.equal(templateValue('1.2.3'), '1.2.3')
})

test('the Inkling 2 defaults send the level name', () => {
  assert.deepEqual(kwargsFor(INKLING, 'medium'), { reasoning_effort: 'medium' })
  assert.deepEqual(mapPreview(INKLING), [
    'low → {"reasoning_effort":"low"}', 'medium → {"reasoning_effort":"medium"}', 'high → {"reasoning_effort":"high"}'])
})

test('an on/off template and empty values', () => {
  const qwen = { variable: 'enable_thinking', low: 'false', medium: 'true', high: '' }
  assert.deepEqual(kwargsFor(qwen, 'low'), { enable_thinking: false })
  assert.equal(kwargsFor(qwen, 'high'), null)
  assert.equal(mapPreview(qwen)[2], 'high → nothing (template default)')
  assert.equal(kwargsFor({ ...qwen, variable: '' }, 'low'), null)
})

test('variable names', () => {
  assert.ok(VARIABLE_RE.test('reasoning_effort'))
  assert.ok(!VARIABLE_RE.test('1abc'))
  assert.ok(!VARIABLE_RE.test('a-b'))
})

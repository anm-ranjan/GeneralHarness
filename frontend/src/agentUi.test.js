import test from 'node:test'
import assert from 'node:assert/strict'
import { errorLines, fieldLabel, humanize, itemCounts, runDisplayStatus, splitFieldErrors, statusInfo } from './agentUi.js'

test('labels fall back to a readable parameter name', () => {
  assert.equal(humanize('timeout_per_model'), 'Timeout per model')
  assert.equal(fieldLabel('status', {}), 'Status')
  assert.equal(fieldLabel('paths', { label: 'Run folders' }), 'Run folders')
})

test('error details become plain lines', () => {
  assert.deepEqual(errorLines({ detail: ['paths: required'] }), ['paths: required'])
  assert.deepEqual(errorLines({ detail: 'Agent not found' }), ['Agent not found'])
  assert.deepEqual(errorLines({ detail: [{ loc: ['body', 'text'], msg: 'too long' }] }), ['text: too long'])
  assert.deepEqual(errorLines(new Error('offline')), ['offline'])
})

test('field errors are split from general ones', () => {
  const { fields, general } = splitFieldErrors(['paths: required', 'Working directory must be allowed'], { paths: {} })
  assert.deepEqual(fields, { paths: ['Required'] })
  assert.deepEqual(general, ['Working directory must be allowed'])
})

test('run status prefers live state, then the last result', () => {
  assert.equal(runDisplayStatus('waiting_approval', {}, null), 'waiting_approval')
  assert.equal(runDisplayStatus(undefined, { agent_last_result: { status: 'stopped' } }, null), 'stopped')
  assert.equal(runDisplayStatus(undefined, {}, null), 'idle')
  assert.equal(statusInfo('timed_out').label, 'Timed out')
  assert.equal(statusInfo('failed').tone, 'danger')
})

test('item counts read naturally', () => {
  assert.equal(itemCounts({ items_total: 4, items_ok: 2, items_failed: 1 }), '2 ok · 1 failed · 1 other')
  assert.equal(itemCounts({ items_total: 2, items_ok: 2, items_failed: 0 }), '2 ok')
  assert.equal(itemCounts(null), '')
})

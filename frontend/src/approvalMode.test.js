import test from 'node:test'
import assert from 'node:assert/strict'
import { approvalLabel, modelSentence, nextApprovalMode } from './approvalMode.js'

test('approval modes read as sentences, unknown modes pass through', () => {
  assert.equal(approvalLabel('always_ask'), 'Asks before changes and commands')
  assert.equal(approvalLabel('shell_only'), 'Asks before shell commands')
  assert.equal(approvalLabel('auto_approve'), 'Runs commands without asking')
  assert.equal(approvalLabel('custom_mode'), 'custom_mode')
  assert.equal(approvalLabel(''), '')
})

test('toggling flips between the two known modes', () => {
  assert.equal(nextApprovalMode('auto_approve'), 'shell_only')
  assert.equal(nextApprovalMode('shell_only'), 'auto_approve')
  assert.equal(nextApprovalMode(''), 'auto_approve')
  // always_ask has no sentence of its own to flip back to: it goes to auto_approve, as before.
  assert.equal(nextApprovalMode('always_ask'), 'auto_approve')
})

test('model sentence appends thinking only when an effort is set', () => {
  assert.equal(modelSentence('gpt-6.1-terra', 'medium'), 'gpt-6.1-terra, medium thinking')
  assert.equal(modelSentence('Claude', ''), 'Claude')
})

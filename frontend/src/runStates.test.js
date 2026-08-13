import assert from 'node:assert/strict'
import test from 'node:test'

import { applyRunState, snapshotRunStates, runStateBadge, runStateNotification } from './runStates.js'

test('applyRunState tracks non-idle states and drops idle sessions', () => {
  let states = {}
  states = applyRunState(states, 'a', 'running')
  states = applyRunState(states, 'b', 'waiting_approval')
  assert.deepEqual(states, { a: 'running', b: 'waiting_approval' })

  states = applyRunState(states, 'a', 'idle')
  assert.deepEqual(states, { b: 'waiting_approval' })

  assert.deepEqual(applyRunState(states, '', 'running'), states)
})

test('snapshotRunStates builds the map from the initial snapshot', () => {
  const map = snapshotRunStates([
    { session_id: 'a', state: 'running' },
    { session_id: 'b', state: 'idle' },
    { session_id: 'c', state: 'waiting_approval' },
  ])
  assert.deepEqual(map, { a: 'running', c: 'waiting_approval' })
  assert.deepEqual(snapshotRunStates(undefined), {})
})

test('runStateNotification fires for background approvals and completions', () => {
  const approval = runStateNotification(undefined, 'a', 'waiting_approval', 'other', 'My Session')
  assert.equal(approval.title, 'Approval needed')
  assert.ok(approval.body.includes('My Session'))

  const finished = runStateNotification('running', 'a', 'idle', 'other', 'My Session')
  assert.equal(finished.title, 'Run finished')

  // No notification for the selected session or a cold idle message.
  assert.equal(runStateNotification('running', 'a', 'idle', 'a', 'My Session'), null)
  assert.equal(runStateNotification(undefined, 'a', 'idle', 'other', 'My Session'), null)
  assert.equal(runStateNotification(undefined, 'a', 'running', 'other', 'My Session'), null)
})

test('a session waiting on an answer gets its own badge and notification', () => {
  assert.equal(runStateBadge('waiting_input').label, 'Waiting for an answer')
  assert.notEqual(runStateBadge('waiting_input').dotClass, runStateBadge('waiting_approval').dotClass)
  assert.equal(runStateBadge('running').label, 'Run in progress')

  const asking = runStateNotification('running', 'ses_b', 'waiting_input', 'ses_a', 'Parser work')
  assert.equal(asking.title, 'Question waiting')

  // Finishing from a waiting-for-answer state still reads as a completed run.
  const done = runStateNotification('waiting_input', 'ses_b', 'idle', 'ses_a', 'Parser work')
  assert.equal(done.title, 'Run finished')
})

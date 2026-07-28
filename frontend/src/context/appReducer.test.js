import assert from 'node:assert/strict'
import test from 'node:test'

import { initialState, reducer } from './appReducer.js'

test('BATCH folds actions into a single state transition', () => {
  const next = reducer(initialState, {
    type: 'BATCH',
    payload: [
      { type: 'APPEND_STAGE_ITEM', payload: { type: 'user_message', text: 'one' } },
      { type: 'APPEND_STAGE_ITEM', payload: { type: 'assistant_message', markdown: 'two' } },
      { type: 'SET_IDLE' },
    ],
  })

  assert.equal(next.stageItems.length, 2)
  assert.deepEqual(next.stageItems.map(i => i.type), ['user_message', 'assistant_message'])
  assert.equal(next.isRunning, false)
  assert.notEqual(next, initialState)
})

test('BATCH matches action-by-action dispatch', () => {
  const actions = [
    { type: 'APPEND_STAGE_ITEM', payload: { type: 'user_message', text: 'hi' } },
    { type: 'APPEND_TOOL_CALL', payload: { callId: 't1', name: 'file_read', status: 'running' } },
    { type: 'UPDATE_TOOL_RESULT', payload: { callId: 't1', name: 'file_read', preview: 'ok', ok: true } },
    { type: 'FINALIZE_WORK_GROUP' },
  ]
  const stepwise = actions.reduce(reducer, initialState)
  const batched = reducer(initialState, { type: 'BATCH', payload: actions })

  // _id carries a timestamp/random suffix, so compare structure without it.
  const strip = items => items.map(({ _id, ...rest }) => rest)
  assert.deepEqual(strip(batched.stageItems), strip(stepwise.stageItems))
})

test('SET_HEALTH returns the identical state when nothing changed', () => {
  const payload = {
    codex_app_server_enabled: false,
    app_name: 'MyHarness',
    model: 'test-model',
    approval_mode: 'shell_only',
    verbose: false,
    audio: { enabled: false },
  }
  const first = reducer(initialState, { type: 'SET_HEALTH', payload })
  const second = reducer(first, { type: 'SET_HEALTH', payload })

  assert.notEqual(first, initialState)
  // Identity preserved: the 5s health poll must not re-render the app.
  assert.equal(second, first)
})

test('SET_HEALTH still produces new state when a field changes', () => {
  const first = reducer(initialState, { type: 'SET_HEALTH', payload: { model: 'a' } })
  const second = reducer(first, { type: 'SET_HEALTH', payload: { model: 'b' } })

  assert.notEqual(second, first)
  assert.equal(second.model, 'b')
})

test('no-op toggles preserve state identity', () => {
  const connected = reducer(initialState, { type: 'SET_WS_CONNECTED', payload: true })
  assert.equal(reducer(connected, { type: 'SET_WS_CONNECTED', payload: true }), connected)

  const online = reducer(initialState, { type: 'SET_SERVER_ONLINE', payload: true })
  assert.equal(reducer(online, { type: 'SET_SERVER_ONLINE', payload: true }), online)
})

test('unknown actions leave state untouched', () => {
  assert.equal(reducer(initialState, { type: 'NOPE' }), initialState)
})

test('SET_PLAN replaces the plan and defaults a missing payload to empty', () => {
  const items = [{ content: 'Read the file', status: 'completed' }, { content: 'Fix the bug', status: 'in_progress' }]
  const withPlan = reducer(initialState, { type: 'SET_PLAN', payload: items })
  assert.deepEqual(withPlan.plan, items)

  assert.deepEqual(reducer(withPlan, { type: 'SET_PLAN', payload: null }).plan, [])
})

test('SELECT_SESSION and CLEAR_SESSION reset the plan', () => {
  const withPlan = reducer(initialState, { type: 'SET_PLAN', payload: [{ content: 'step', status: 'pending' }] })

  const selected = reducer(withPlan, {
    type: 'SELECT_SESSION',
    payload: { meta: { id: 's1', status: 'idle' }, workspaceRoot: '' },
  })
  assert.deepEqual(selected.plan, [])

  const withPlanAgain = reducer(selected, { type: 'SET_PLAN', payload: [{ content: 'step', status: 'pending' }] })
  assert.deepEqual(reducer(withPlanAgain, { type: 'CLEAR_SESSION' }).plan, [])
})

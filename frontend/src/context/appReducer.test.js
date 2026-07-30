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

// ── host switching ────────────────────────────────────────────────
//
// Hosts share no projects, tasks, or sessions, so the danger is a slice of the
// machine being left surviving into the machine being entered and rendering
// under the wrong label.

const FLEET = [
  { id: 'mac', label: 'MacBook', url: 'http://127.0.0.1:8420', self: true },
  { id: 'jarvis', label: 'Jarvis', url: 'http://127.0.0.1:8421', self: false },
]

function loadedHostState() {
  let state = reducer(initialState, {
    type: 'SET_FLEET',
    payload: { hosts: FLEET, activeHostId: 'mac' },
  })
  state = reducer(state, {
    type: 'SET_TREE',
    payload: {
      projects: [{ id: 'p1', name: 'Proj', root: '/Users/a/proj', tasks: [{ id: 't1', name: 'Task', sessions: ['s1'] }] }],
      sessions: { s1: { id: 's1', project_id: 'p1', task_id: 't1', title: 'S', updated_at: '2026-01-01T00:00:00Z' } },
    },
  })
  state = reducer(state, {
    type: 'SELECT_SESSION',
    payload: {
      meta: { id: 's1', project_id: 'p1', task_id: 't1', title: 'S' },
      workspaceRoot: '/Users/a/proj',
    },
  })
  return reducer(state, { type: 'APPEND_STAGE_ITEM', payload: { type: 'user_message', text: 'hi' } })
}

test('SWITCH_HOST clears every slice owned by the machine being left', () => {
  const before = loadedHostState()
  assert.equal(before.currentSessionId, 's1')
  assert.equal(before.projects.length, 1)

  const after = reducer(before, { type: 'SWITCH_HOST', payload: { hostId: 'jarvis' } })

  assert.equal(after.activeHostId, 'jarvis')
  assert.equal(after.hostSwitching, true)
  assert.equal(after.currentSessionId, null)
  assert.equal(after.currentMeta, null)
  assert.equal(after.currentWorkspaceRoot, '')
  assert.deepEqual(after.projects, [])
  assert.deepEqual(after.sessionsById, {})
  assert.deepEqual(after.projectRoots, {})
  assert.deepEqual(after.stageItems, [])
  assert.deepEqual(after.runStates, {})
  assert.equal(after.isRunning, false)
  // Capabilities belong to the previous machine and must be re-fetched.
  assert.equal(after.serverOnline, false)
  assert.equal(after.claudeAgentEnabled, false)
})

test('SWITCH_HOST keeps the fleet registry and cross-host statuses', () => {
  let state = loadedHostState()
  state = reducer(state, {
    type: 'SET_HOST_STATUS',
    payload: { hostId: 'jarvis', status: { online: true, running: 1, waitingApproval: 0 } },
  })

  const after = reducer(state, { type: 'SWITCH_HOST', payload: { hostId: 'jarvis' } })

  assert.deepEqual(after.fleetHosts, FLEET)
  assert.deepEqual(after.fleetStatuses.jarvis, { online: true, running: 1, waitingApproval: 0 })
})

test('SWITCH_HOST preserves panel layout, which is a user preference', () => {
  let state = loadedHostState()
  state = reducer(state, { type: 'TOGGLE_WORKSPACE_PANEL' })
  state = reducer(state, { type: 'SET_WORKSPACE_TAB', payload: 'files' })

  const after = reducer(state, { type: 'SWITCH_HOST', payload: { hostId: 'jarvis' } })

  assert.equal(after.workspacePanelOpen, state.workspacePanelOpen)
  assert.equal(after.workspacePanelTab, 'files')
})

test('the new host tree ends the switching state', () => {
  const switched = reducer(loadedHostState(), { type: 'SWITCH_HOST', payload: { hostId: 'jarvis' } })
  const loaded = reducer(switched, {
    type: 'SET_TREE',
    payload: { projects: [], sessions: {} },
  })
  assert.equal(loaded.hostSwitching, false)
})

test('SET_HOST_STATUS returns the same state when nothing changed', () => {
  const status = { online: true, running: 0, waitingApproval: 0 }
  const first = reducer(initialState, { type: 'SET_HOST_STATUS', payload: { hostId: 'jarvis', status } })
  const second = reducer(first, { type: 'SET_HOST_STATUS', payload: { hostId: 'jarvis', status: { ...status } } })
  assert.equal(second, first)
})

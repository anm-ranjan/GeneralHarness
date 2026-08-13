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

  // Generated ids and work-group start times can differ by a millisecond even
  // though the two state transitions are otherwise identical.
  const strip = items => items.map(({ _id, startTime, ...rest }) => rest)
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

test('SET_HEALTH exposes the audio processor to the recorder', () => {
  const next = reducer(initialState, {
    type: 'SET_HEALTH',
    payload: { audio: { enabled: true, processor: 'api', max_upload_mb: 25 } },
  })

  assert.equal(next.audioEnabled, true)
  assert.equal(next.audioProcessor, 'api')
  assert.equal(next.audioMaxUploadMb, 25)
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

test('SET_RUN_SETTINGS updates current meta and the session tree copy', () => {
  const selected = reducer(initialState, {
    type: 'SELECT_SESSION',
    payload: {
      meta: { id: 's1', status: 'idle', run_settings: {} },
      workspaceRoot: '',
    },
  })
  const next = reducer(selected, {
    type: 'SET_RUN_SETTINGS',
    payload: { model: 'opus', reasoning_effort: 'high' },
  })

  assert.deepEqual(next.currentMeta.run_settings, {
    model: 'opus',
    reasoning_effort: 'high',
  })
  assert.deepEqual(next.sessionsById.s1.run_settings, next.currentMeta.run_settings)
})

// ── host switching ────────────────────────────────────────────────
//
// Hosts share no projects, tasks, or sessions, so the danger is a slice of the
// machine being left surviving into the machine being entered and rendering
// under the wrong label.

const FLEET = [
  { id: 'laptop', label: 'Laptop', url: 'http://127.0.0.1:8420', self: true },
  { id: 'workstation', label: 'Workstation', url: 'http://127.0.0.1:8421', self: false },
]

function loadedHostState() {
  let state = reducer(initialState, {
    type: 'SET_FLEET',
    payload: { hosts: FLEET, activeHostId: 'laptop' },
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

  const after = reducer(before, { type: 'SWITCH_HOST', payload: { hostId: 'workstation' } })

  assert.equal(after.activeHostId, 'workstation')
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
    payload: { hostId: 'workstation', status: { online: true, running: 1, waitingApproval: 0 } },
  })

  const after = reducer(state, { type: 'SWITCH_HOST', payload: { hostId: 'workstation' } })

  assert.deepEqual(after.fleetHosts, FLEET)
  assert.deepEqual(after.fleetStatuses.workstation, { online: true, running: 1, waitingApproval: 0 })
})

test('SWITCH_HOST preserves panel layout, which is a user preference', () => {
  let state = loadedHostState()
  state = reducer(state, { type: 'TOGGLE_WORKSPACE_PANEL' })
  state = reducer(state, { type: 'SET_WORKSPACE_TAB', payload: 'files' })

  const after = reducer(state, { type: 'SWITCH_HOST', payload: { hostId: 'workstation' } })

  assert.equal(after.workspacePanelOpen, state.workspacePanelOpen)
  assert.equal(after.workspacePanelTab, 'files')
})

test('the new host tree ends the switching state', () => {
  const switched = reducer(loadedHostState(), { type: 'SWITCH_HOST', payload: { hostId: 'workstation' } })
  const loaded = reducer(switched, {
    type: 'SET_TREE',
    payload: { projects: [], sessions: {} },
  })
  assert.equal(loaded.hostSwitching, false)
})

test('SET_HOST_STATUS returns the same state when nothing changed', () => {
  const status = { online: true, running: 0, waitingApproval: 0 }
  const first = reducer(initialState, { type: 'SET_HOST_STATUS', payload: { hostId: 'workstation', status } })
  const second = reducer(first, { type: 'SET_HOST_STATUS', payload: { hostId: 'workstation', status: { ...status } } })
  assert.equal(second, first)
})

test('thinking deltas accumulate into one live trace item', () => {
  const next = ['Loo', 'king at ', 'the parser'].reduce(
    (state, payload) => reducer(state, { type: 'APPEND_THINKING_DELTA', payload }),
    initialState,
  )

  assert.deepEqual(next.stageItems.map(i => i.type), ['thinking_stream'])
  assert.equal(next.stageItems[0].markdown, 'Looking at the parser')
})

test('consecutive completed thinking events merge into one block', () => {
  const next = ['first thought', 'second thought'].reduce(
    (state, payload) => reducer(state, { type: 'APPEND_THINKING', payload }),
    initialState,
  )

  assert.deepEqual(next.stageItems.map(i => i.type), ['thinking'])
  assert.equal(next.stageItems[0].markdown, 'first thought\n\nsecond thought')
})

test('a live trace is superseded by whatever follows it', () => {
  const streamed = reducer(initialState, { type: 'APPEND_THINKING_DELTA', payload: 'partial' })
  const answered = reducer(streamed, {
    type: 'APPEND_STAGE_ITEM',
    payload: { type: 'assistant_message', markdown: 'done' },
  })

  assert.deepEqual(answered.stageItems.map(i => i.type), ['assistant_message'])
})

test('CLEAR_THINKING_STREAM only drops a trailing live trace', () => {
  const streamed = reducer(initialState, { type: 'APPEND_THINKING_DELTA', payload: 'partial' })
  assert.deepEqual(reducer(streamed, { type: 'CLEAR_THINKING_STREAM' }).stageItems, [])

  const settled = reducer(initialState, {
    type: 'APPEND_STAGE_ITEM',
    payload: { type: 'assistant_message', markdown: 'done' },
  })
  assert.equal(reducer(settled, { type: 'CLEAR_THINKING_STREAM' }), settled)
})

test('SET_SESSION_TITLE updates the sidebar entry and the open session', () => {
  const seeded = reducer(initialState, {
    type: 'SET_TREE',
    payload: {
      projects: [],
      sessions: { ses_1: { id: 'ses_1', title: 'Thread 2026-08-14 09:30', project_id: 'p', task_id: 't' } },
    },
  })
  const opened = { ...seeded, currentSessionId: 'ses_1', currentMeta: seeded.sessionsById.ses_1 }

  const next = reducer(opened, {
    type: 'SET_SESSION_TITLE',
    payload: { sessionId: 'ses_1', title: 'Stream Codex reasoning' },
  })

  assert.equal(next.sessionsById.ses_1.title, 'Stream Codex reasoning')
  assert.equal(next.currentMeta.title, 'Stream Codex reasoning')

  // Unknown sessions and no-op renames leave state identical.
  assert.equal(reducer(next, { type: 'SET_SESSION_TITLE', payload: { sessionId: 'ses_x', title: 'x' } }), next)
  assert.equal(
    reducer(next, { type: 'SET_SESSION_TITLE', payload: { sessionId: 'ses_1', title: 'Stream Codex reasoning' } }),
    next,
  )
})

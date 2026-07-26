import assert from 'node:assert/strict'
import test from 'node:test'

import { handleSessionEvent, toolDetail } from './eventHandlers.js'
import { effectiveWorkspaceRoot, workspaceDisplayName } from './sessionWorkspace.js'

// Session replay is dispatched as a single BATCH action; flatten it so these
// assertions describe the logical action sequence either way.
function flatten(actions) {
  return actions.flatMap(a => (a.type === 'BATCH' ? flatten(a.payload) : [a]))
}

function collect(initialState = {}) {
  const actions = []
  const stateRef = {
    current: {
      silentCommand: false,
      isRunning: false,
      currentProvider: 'native',
      ...initialState,
    },
  }
  return {
    actions,
    stateRef,
    dispatch(action) {
      actions.push(action)
    },
  }
}

test('toolDetail prefers useful fields', () => {
  assert.equal(toolDetail('file_read', { file_path: '/tmp/source.py' }), '/tmp/source.py')
  assert.equal(toolDetail('file_search', { directory: '/tmp', pattern: '*.py' }), '/tmp · *.py')
  assert.equal(toolDetail('file_read', { path: '/tmp/a' }), '/tmp/a')
  assert.equal(toolDetail('shell_run', { command: 'npm test' }), 'npm test')
  assert.equal(toolDetail('content_search', { query: 'needle' }), 'needle')
})

test('verbose tool events retain status, correlation, duration, and failure state', () => {
  const ctx = collect()
  handleSessionEvent({
    type: 'tool_call',
    created_at: '2026-06-20T12:00:00Z',
    data: {
      verbose: true,
      call_id: 'tool_1',
      name: 'shell_run',
      status_line: 'Run: npm test',
      args: { command: 'npm test', working_directory: '/workspace' },
    },
  }, ctx.dispatch, ctx.stateRef)
  handleSessionEvent({
    type: 'tool_result',
    data: { verbose: true, call_id: 'tool_1', name: 'shell_run', preview: 'Exit code: 1', ok: false, duration_ms: 1250 },
  }, ctx.dispatch, ctx.stateRef)

  // tool_call first clears any live assistant stream bubble.
  assert.equal(ctx.actions[0].type, 'CLEAR_ASSISTANT_STREAM')
  assert.equal(ctx.actions[1].payload.callId, 'tool_1')
  assert.equal(ctx.actions[1].payload.statusLine, 'Run: npm test')
  assert.deepEqual(ctx.actions[2].payload, {
    callId: 'tool_1', name: 'shell_run', preview: 'Exit code: 1', ok: false, durationMs: 1250,
  })
})

test('session_loaded replays events and settles idle sessions', () => {
  const ctx = collect()
  handleSessionEvent({
    type: 'session_loaded',
    data: {
      meta: { status: 'idle' },
      events: [
        { type: 'user_message', data: { text: 'hello' } },
        { type: 'assistant_message', data: { markdown: 'hi' } },
      ],
    },
  }, ctx.dispatch, ctx.stateRef)

  assert.equal(ctx.actions.length, 1)
  assert.equal(ctx.actions[0].type, 'BATCH')
  assert.deepEqual(flatten(ctx.actions).map(a => a.type), [
    'SET_REPLAYING',
    'SET_EVENT_WINDOW',
    'INCREMENT_REPLAYED_EVENTS',
    'APPEND_STAGE_ITEM',
    'INCREMENT_REPLAYED_EVENTS',
    'CLEAR_ASSISTANT_STREAM',
    'FINALIZE_WORK_GROUP',
    'HIDE_CODEX_RUNNING',
    'CLEAR_ITERATION',
    'APPEND_STAGE_ITEM',
    'SET_REPLAYING',
    'SET_REPLAY_IDLE',
  ])
})

test('session_loaded stamps replayed stage items with absolute event indices', () => {
  const ctx = collect()
  handleSessionEvent({
    type: 'session_loaded',
    data: {
      meta: { status: 'idle' },
      event_offset: 100,
      event_total: 104,
      events: [
        { type: 'user_message', data: { text: 'hello' } },
        { type: 'tool_call', data: { verbose: true, call_id: 't1', name: 'file_read', args: { file_path: '/a' } } },
        { type: 'tool_result', data: { verbose: true, call_id: 't1', name: 'file_read', preview: 'ok' } },
        { type: 'assistant_message', data: { markdown: 'hi' } },
      ],
    },
  }, ctx.dispatch, ctx.stateRef)

  const replayed = flatten(ctx.actions)
  const windowAction = replayed.find(a => a.type === 'SET_EVENT_WINDOW')
  assert.deepEqual(windowAction.payload, { offset: 100, total: 104 })

  const appended = replayed.filter(a => a.type === 'APPEND_STAGE_ITEM')
  assert.deepEqual(appended.map(a => a.payload.eventIndex), [100, 103])

  const toolCall = replayed.find(a => a.type === 'APPEND_TOOL_CALL')
  assert.equal(toolCall.payload.eventIndex, 101)
})

test('live events are not stamped with event indices', () => {
  const ctx = collect()
  handleSessionEvent({ type: 'assistant_message', data: { markdown: 'live' } }, ctx.dispatch, ctx.stateRef)
  const appended = ctx.actions.find(a => a.type === 'APPEND_STAGE_ITEM')
  assert.equal('eventIndex' in appended.payload, false)
})

test('run_finished clears running indicators and silent command state', () => {
  const ctx = collect()
  handleSessionEvent({ type: 'run_finished', data: { reason: 'interrupted' } }, ctx.dispatch, ctx.stateRef)

  assert.deepEqual(ctx.actions.map(a => a.type), [
    'CLEAR_ASSISTANT_STREAM',
    'FINALIZE_WORK_GROUP',
    'APPEND_STAGE_ITEM',
    'SET_IDLE',
    'SET_SILENT_COMMAND',
  ])
})

test('generated artifacts render as replayable assistant images', () => {
  const ctx = collect()
  handleSessionEvent({
    type: 'generated_artifact',
    data: { path: '/workspace/result.png', name: 'result.png', media_type: 'image/png', version: '42-10' },
  }, ctx.dispatch, ctx.stateRef)

  assert.deepEqual(ctx.actions, [{
    type: 'APPEND_STAGE_ITEM',
    payload: { type: 'assistant_message', markdown: '![result.png](/workspace/result.png?myharness_v=42-10)' },
  }])
})

test('workspace_changed updates the effective session root', () => {
  const ctx = collect()
  handleSessionEvent({
    type: 'workspace_changed',
    data: { current: '/workspace/subdir', working_directory: '/workspace/subdir' },
  }, ctx.dispatch, ctx.stateRef)

  assert.deepEqual(ctx.actions, [{
    type: 'SET_WORKSPACE_ROOT',
    payload: { root: '/workspace/subdir', workingDirectory: '/workspace/subdir' },
  }])
})

test('workspace_changed is skipped during replay so historical state does not blink', () => {
  const ctx = collect({ isReplaying: true })
  handleSessionEvent({
    type: 'workspace_changed',
    data: {
      current: '/workspace/analysis',
      working_directory: '/workspace/analysis',
    },
  }, ctx.dispatch, ctx.stateRef)

  assert.deepEqual(ctx.actions, [])
})

test('provider_switch keeps its transcript note but skips the provider mutation during replay', () => {
  const live = collect()
  handleSessionEvent({
    type: 'provider_switch',
    data: { provider: 'codex-app-server', text: 'Switched to Codex App Server.' },
  }, live.dispatch, live.stateRef)
  assert.deepEqual(live.actions.map(a => a.type), ['SET_PROVIDER', 'APPEND_STAGE_ITEM'])

  const replaying = collect({ isReplaying: true })
  handleSessionEvent({
    type: 'provider_switch',
    data: { provider: 'codex-app-server', text: 'Switched to Codex App Server.' },
  }, replaying.dispatch, replaying.stateRef)
  assert.deepEqual(replaying.actions.map(a => a.type), ['APPEND_STAGE_ITEM'])
})

test('effectiveWorkspaceRoot keeps project root as default for sessions without override', () => {
  assert.equal(effectiveWorkspaceRoot({ working_directory: '' }, '/project/root'), '/project/root')
  assert.equal(effectiveWorkspaceRoot({ working_directory: '/other/root' }, '/project/root'), '/other/root')
})

test('workspaceDisplayName shows only the bound directory name', () => {
  assert.equal(workspaceDisplayName('/workspace/analysis'), 'analysis')
  assert.equal(workspaceDisplayName('/workspace/analysis/'), 'analysis')
  assert.equal(workspaceDisplayName('Y:\\Shoulder_simulation\\Run_01'), 'Run_01')
  assert.equal(workspaceDisplayName('ProjectName'), 'ProjectName')
})

test('assistant_delta streams live and is dropped during replay', () => {
  const ctx = collect()
  handleSessionEvent({ type: 'assistant_delta', data: { text: 'Hel' } }, ctx.dispatch, ctx.stateRef)
  handleSessionEvent({ type: 'assistant_delta', data: { text: 'lo' } }, ctx.dispatch, ctx.stateRef)

  const deltas = ctx.actions.filter(a => a.type === 'APPEND_ASSISTANT_DELTA')
  assert.deepEqual(deltas.map(a => a.payload), ['Hel', 'lo'])

  const replaying = collect({ isReplaying: true })
  handleSessionEvent({ type: 'assistant_delta', data: { text: 'ghost' } }, replaying.dispatch, replaying.stateRef)
  assert.equal(replaying.actions.filter(a => a.type === 'APPEND_ASSISTANT_DELTA').length, 0)
})

test('assistant_message and tool_call clear the live stream bubble', () => {
  const ctx = collect()
  handleSessionEvent({ type: 'assistant_message', data: { markdown: 'final' } }, ctx.dispatch, ctx.stateRef)
  assert.equal(ctx.actions[0].type, 'CLEAR_ASSISTANT_STREAM')
  assert.equal(ctx.actions.at(-1).type, 'APPEND_STAGE_ITEM')
  assert.equal(ctx.actions.at(-1).payload.markdown, 'final')

  const toolCtx = collect()
  handleSessionEvent({ type: 'tool_call', data: { verbose: false, name: 'file_read' } }, toolCtx.dispatch, toolCtx.stateRef)
  assert.equal(toolCtx.actions[0].type, 'CLEAR_ASSISTANT_STREAM')
})

test('run_finished and error clear any dangling stream bubble', () => {
  const runCtx = collect()
  handleSessionEvent({ type: 'run_finished', data: { reason: 'completed' } }, runCtx.dispatch, runCtx.stateRef)
  assert.ok(runCtx.actions.some(a => a.type === 'CLEAR_ASSISTANT_STREAM'))

  const errCtx = collect()
  handleSessionEvent({ type: 'error', data: { text: 'boom' } }, errCtx.dispatch, errCtx.stateRef)
  assert.ok(errCtx.actions.some(a => a.type === 'CLEAR_ASSISTANT_STREAM'))
})

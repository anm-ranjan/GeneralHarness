import assert from 'node:assert/strict'
import test from 'node:test'

import { handleSessionEvent, isCodexProtocolStatus, toolDetail } from './eventHandlers.js'
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

test('generated reports replay as preview links instead of images', () => {
  const ctx = collect()
  handleSessionEvent({ type: 'generated_artifact', data: { path: '/tmp/my report.html', name: 'my report.html', media_type: 'text/html' } }, ctx.dispatch, ctx.stateRef)
  assert.match(ctx.actions[0].payload.markdown, /\[View my report.html\]/)
  assert.doesNotMatch(ctx.actions[0].payload.markdown, /!\[/)
  assert.equal(ctx.actions[1].payload.path, '/tmp/my report.html')
})

test('toolDetail prefers useful fields', () => {
  assert.equal(toolDetail('file_read', { file_path: '/tmp/source.py' }), '/tmp/source.py')
  assert.equal(toolDetail('file_search', { directory: '/tmp', pattern: '*.py' }), '/tmp · *.py')
  assert.equal(toolDetail('file_read', { path: '/tmp/a' }), '/tmp/a')
  assert.equal(toolDetail('shell_run', { command: 'npm test' }), 'npm test')
  assert.equal(toolDetail('content_search', { query: 'needle' }), 'needle')
})

test('subagent lifecycle events update the Agents list, and also show as status items when verbose', () => {
  const event = {
    type: 'agent_event',
    data: { agent_id: '/root/reviewer', agent_path: '/root/reviewer', action: 'completed', result: 'Looks good' },
  }
  const quiet = collect()
  handleSessionEvent(event, quiet.dispatch, quiet.stateRef)
  assert.equal(quiet.actions.length, 1)
  assert.equal(quiet.actions[0].type, 'UPSERT_AGENT')
  assert.equal(quiet.actions[0].payload.status, 'completed')

  const verbose = collect({ verbose: true })
  handleSessionEvent(event, verbose.dispatch, verbose.stateRef)
  assert.deepEqual(verbose.actions.map(a => a.type), ['UPSERT_AGENT', 'APPEND_STAGE_ITEM'])
  assert.deepEqual(verbose.actions[1].payload, { type: 'status', text: '[/root/reviewer] completed: Looks good' })
})

test('a failing child shows as an error item when verbose, and as a failed agent otherwise', () => {
  const data = { agent_id: '/root/a', agent_path: '/root/a', action: 'failed', error: 'boom' }
  const quiet = collect()
  handleSessionEvent({ type: 'agent_event', data }, quiet.dispatch, quiet.stateRef)
  assert.equal(quiet.actions[0].payload.status, 'failed')
  assert.equal(quiet.actions[0].payload.error, 'boom')
  const verbose = collect({ verbose: true })
  handleSessionEvent({ type: 'agent_event', data }, verbose.dispatch, verbose.stateRef)
  assert.equal(verbose.actions[1].payload.type, 'error')
})

test('Codex thread statuses reach the Agents list as working or idle', () => {
  const ctx = collect()
  handleSessionEvent({ type: 'agent_event', data: { provider: 'codex-app-server', action: 'status', agent_id: 'thr_1', status: 'active' } }, ctx.dispatch, ctx.stateRef)
  assert.equal(ctx.actions[0].payload.status, 'running')
})

test('a new prompt clears last turn\'s agents and the end of a run finalizes the live ones', () => {
  const withAgents = collect({ agentsById: { a: { agent_path: '/root/a', status: 'running' } } })
  handleSessionEvent({ type: 'user_message', data: { text: 'next' } }, withAgents.dispatch, withAgents.stateRef)
  assert.ok(withAgents.actions.some(a => a.type === 'CLEAR_AGENTS'))

  const steering = collect({ agentsById: { a: { agent_path: '/root/a', status: 'running' } } })
  handleSessionEvent({ type: 'user_message', data: { text: 'nudge', steering: true } }, steering.dispatch, steering.stateRef)
  assert.ok(!steering.actions.some(a => a.type === 'CLEAR_AGENTS'))

  const finishing = collect({ agentsById: { a: { agent_path: '/root/a', status: 'running' } } })
  handleSessionEvent({ type: 'run_finished', data: { reason: 'completed' } }, finishing.dispatch, finishing.stateRef)
  assert.ok(finishing.actions.some(a => a.type === 'FINALIZE_AGENTS'))
  const idle = collect()
  handleSessionEvent({ type: 'run_finished', data: { reason: 'completed' } }, idle.dispatch, idle.stateRef)
  assert.ok(!idle.actions.some(a => a.type === 'FINALIZE_AGENTS'))
})

test('composer throughput shows only the generation speed', () => {
  const ctx = collect()
  handleSessionEvent({ type: 'api_metrics', data: {
    input_tokens: 1000, output_tokens: 20, tps: 10,
    cache_read_tokens: 0, cache_write_tokens: 800,
    estimated_input: { instructions: 300, tool_results: 700, total: 1000 },
  } }, ctx.dispatch, ctx.stateRef)
  assert.deepEqual(ctx.actions, [{ type: 'SET_THROUGHPUT', payload: '10 tok/s' }])
  const old = collect()
  handleSessionEvent({ type: 'api_metrics', data: { input_tokens: 4 } }, old.dispatch, old.stateRef)
  assert.deepEqual(old.actions, [], 'no speed, nothing to show')
})

test('compaction usage does not replace visible answer throughput', () => {
  const ctx = collect()
  handleSessionEvent({ type: 'api_metrics', data: {
    purpose: 'history_compaction', input_tokens: 3000, output_tokens: 100,
    estimated_input: { total: 3100 },
  } }, ctx.dispatch, ctx.stateRef)
  assert.deepEqual(ctx.actions, [])
})

test('child agent progress is hidden unless verbose', () => {
  const ctx = collect()
  handleSessionEvent({
    type: 'agent_event',
    data: { agent_path: '/root/reviewer', action: 'tool_call', text: 'Reading file' },
  }, ctx.dispatch, ctx.stateRef)
  assert.equal(ctx.actions.length, 0)
})

test('Codex protocol statuses are hidden only when verbose mode is off', () => {
  assert.equal(isCodexProtocolStatus('Codex turn accepted in 0.4s.'), true)
  assert.equal(isCodexProtocolStatus('Codex app-server resume failed.'), false)

  const quiet = collect({ currentProvider: 'codex-app-server', verbose: false })
  handleSessionEvent({
    type: 'status',
    data: { text: 'Waiting for Codex response…' },
  }, quiet.dispatch, quiet.stateRef)
  assert.deepEqual(quiet.actions, [])

  const verbose = collect({ currentProvider: 'codex-app-server', verbose: true })
  handleSessionEvent({
    type: 'status',
    data: { text: 'Waiting for Codex response…' },
  }, verbose.dispatch, verbose.stateRef)
  assert.equal(verbose.actions[0].type, 'APPEND_STAGE_ITEM')

  const warning = collect({ currentProvider: 'codex-app-server', verbose: false })
  handleSessionEvent({
    type: 'status',
    data: { text: 'Codex app-server resume failed.' },
  }, warning.dispatch, warning.stateRef)
  assert.equal(warning.actions[0].type, 'APPEND_STAGE_ITEM')
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
    'DROP_SHOWN_GENERATED_IMAGES',
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
  assert.equal(toolCall, undefined)
  assert.ok(appended.every(a => a.payload.historical))
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
    'CLEAR_THINKING_STREAM',
    'FINALIZE_WORK_GROUP',
    'APPEND_STAGE_ITEM',
    'APPEND_STAGE_ITEM',
    'SET_IDLE',
    'SET_SILENT_COMMAND',
  ])
  // The first appended item records how the run ended, for the prompt actions.
  assert.ok(ctx.actions.some(a => a.payload?.type === 'run_end' && a.payload.reason === 'interrupted'))

  const command = collect()
  handleSessionEvent({ type: 'run_finished', data: { reason: 'command' } }, command.dispatch, command.stateRef)
  assert.ok(!command.actions.some(a => a.payload?.type === 'run_end'))
})

test('generated artifacts render as replayable assistant images', () => {
  const ctx = collect()
  handleSessionEvent({
    type: 'generated_artifact',
    data: { path: '/workspace/result.png', name: 'result.png', media_type: 'image/png', version: '42-10' },
  }, ctx.dispatch, ctx.stateRef)

  assert.deepEqual(ctx.actions, [{
    type: 'APPEND_STAGE_ITEM',
    payload: { type: 'assistant_message', markdown: '![result.png](/workspace/result.png?myharness_v=42-10)', generatedPath: '/workspace/result.png' },
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

test('plan_update sets the plan, defaulting a missing items list to empty', () => {
  const ctx = collect()
  const items = [{ content: 'Read the file', status: 'completed' }, { content: 'Fix the bug', status: 'in_progress' }]
  handleSessionEvent({ type: 'plan_update', data: { items } }, ctx.dispatch, ctx.stateRef)
  assert.deepEqual(ctx.actions, [{ type: 'SET_PLAN', payload: items }])

  const ctxEmpty = collect()
  handleSessionEvent({ type: 'plan_update', data: {} }, ctxEmpty.dispatch, ctxEmpty.stateRef)
  assert.deepEqual(ctxEmpty.actions, [{ type: 'SET_PLAN', payload: [] }])
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
  assert.equal(workspaceDisplayName('Y:\\Example_project\\Run_01'), 'Run_01')
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

test('new Codex file cards do not duplicate dedicated change records', () => {
  const current = collect()
  handleSessionEvent({
    type: 'codex_file_change',
    data: { path: 'src/app.js', status: 'modified', records_change: false },
  }, current.dispatch, current.stateRef)
  assert.deepEqual(current.actions.map(a => a.type), ['APPEND_STAGE_ITEM'])

  const legacy = collect()
  handleSessionEvent({
    type: 'codex_file_change',
    data: { path: 'src/legacy.js', status: 'modified' },
  }, legacy.dispatch, legacy.stateRef)
  assert.deepEqual(legacy.actions.map(a => a.type), [
    'APPEND_STAGE_ITEM',
    'APPEND_FILE_CHANGE',
  ])
})

test('thinking_delta streams live and is dropped during replay', () => {
  const ctx = collect()
  handleSessionEvent({ type: 'thinking_delta', data: { text: 'Che' } }, ctx.dispatch, ctx.stateRef)
  handleSessionEvent({ type: 'thinking_delta', data: { text: 'cking' } }, ctx.dispatch, ctx.stateRef)

  const deltas = ctx.actions.filter(a => a.type === 'APPEND_THINKING_DELTA')
  assert.deepEqual(deltas.map(a => a.payload), ['Che', 'cking'])

  const replaying = collect({ isReplaying: true })
  handleSessionEvent({ type: 'thinking_delta', data: { text: 'ghost' } }, replaying.dispatch, replaying.stateRef)
  assert.equal(replaying.actions.filter(a => a.type === 'APPEND_THINKING_DELTA').length, 0)
})

test('a completed thinking event replaces the streamed trace', () => {
  const ctx = collect()
  handleSessionEvent({ type: 'thinking', data: { markdown: 'full reasoning' } }, ctx.dispatch, ctx.stateRef)

  assert.deepEqual(ctx.actions.map(a => a.type), ['CLEAR_THINKING_STREAM', 'APPEND_THINKING'])
  assert.equal(ctx.actions.at(-1).payload, 'full reasoning')
})

test('question_required clears live streams and appends an unanswered question', () => {
  const ctx = collect()
  handleSessionEvent({
    type: 'question_required',
    data: {
      question_id: 'qst_1',
      question: 'Which parser?',
      options: ['fast', 'strict'],
      allow_free_text: true,
    },
  }, ctx.dispatch, ctx.stateRef)

  assert.deepEqual(ctx.actions.map(a => a.type), [
    'CLEAR_ASSISTANT_STREAM',
    'CLEAR_THINKING_STREAM',
    'CLEAR_ITERATION',
    'APPEND_STAGE_ITEM',
  ])
  assert.deepEqual(ctx.actions.at(-1).payload, {
    type: 'question',
    questionId: 'qst_1',
    question: 'Which parser?',
    options: ['fast', 'strict'],
    allowFreeText: true,
    answer: null,
    answered: null,
    submitting: false,
    submissionError: null,
  })
})

test('question_resolved records the answer, including when there was none', () => {
  const ctx = collect()
  handleSessionEvent({
    type: 'question_resolved',
    data: { question_id: 'qst_1', answer: 'strict', answered: true },
  }, ctx.dispatch, ctx.stateRef)
  assert.deepEqual(ctx.actions[0].payload, { questionId: 'qst_1', answer: 'strict', answered: true })

  const timedOut = collect()
  handleSessionEvent({
    type: 'question_resolved',
    data: { question_id: 'qst_2', answer: null, answered: false },
  }, timedOut.dispatch, timedOut.stateRef)
  assert.deepEqual(timedOut.actions[0].payload, { questionId: 'qst_2', answer: '', answered: false })
})

test('idle replay settles an interrupted unresolved question as unanswered', () => {
  const ctx = collect()
  handleSessionEvent({
    type: 'session_loaded',
    data: {
      meta: { status: 'idle' },
      events: [{
        type: 'question_required',
        data: { question_id: 'qst_interrupted', question: 'Continue?', options: [] },
      }],
    },
  }, ctx.dispatch, ctx.stateRef)

  const actions = flatten(ctx.actions)
  const resolution = actions.find(action => action.type === 'RESOLVE_QUESTION')
  assert.deepEqual(resolution.payload, {
    questionId: 'qst_interrupted', answer: '', answered: false,
  })
})

test('running replay keeps an unresolved live question interactive', () => {
  const ctx = collect()
  handleSessionEvent({
    type: 'session_loaded',
    data: {
      meta: { status: 'running' },
      events: [{
        type: 'question_required',
        data: { question_id: 'qst_live', question: 'Continue?', options: [] },
      }],
    },
  }, ctx.dispatch, ctx.stateRef)

  assert.equal(flatten(ctx.actions).some(action => action.type === 'RESOLVE_QUESTION'), false)
})


test('completed history hides tools while reconnecting active turn keeps tools and approval', () => {
  const ctx = collect()
  handleSessionEvent({ type: 'session_loaded', data: {
    meta: { status: 'running' }, events: [
      { type: 'user_message', data: { text: 'old question' } },
      { type: 'tool_call', data: { verbose: true, call_id: 'old', name: 'shell_run' } },
      { type: 'assistant_message', data: { markdown: 'old answer' } },
      { type: 'run_finished', data: {} },
      { type: 'user_message', data: { text: 'current question' } },
      { type: 'tool_call', data: { verbose: true, call_id: 'live', name: 'shell_run' } },
      { type: 'approval_required', data: { approval_id: 'approval', tool_name: 'shell_run' } },
    ],
  } }, ctx.dispatch, ctx.stateRef)
  const actions = flatten(ctx.actions)
  assert.deepEqual(actions.filter(a => a.type === 'APPEND_TOOL_CALL').map(a => a.payload.callId), ['live'])
  assert.ok(actions.some(a => a.type === 'APPEND_STAGE_ITEM' && a.payload.type === 'approval'))
})

test('final answer embedding a generated image replaces the early preview', async () => {
  const { reducer, initialState } = await import('./context/appReducer.js')
  let state = initialState
  const ctx = { dispatch: action => { state = reducer(state, action) }, stateRef: { get current() { return state } } }
  handleSessionEvent({ type: 'generated_artifact', data: { path: '/w/a b.png', name: 'a b.png', media_type: 'image/png' } }, ctx.dispatch, ctx.stateRef)
  handleSessionEvent({ type: 'generated_artifact', data: { path: '/w/other.png', name: 'other.png', media_type: 'image/png' } }, ctx.dispatch, ctx.stateRef)
  handleSessionEvent({ type: 'assistant_message', data: { markdown: 'Chart: ![a](/w/a%20b.png)' } }, ctx.dispatch, ctx.stateRef)
  const markdowns = state.stageItems.map(item => item.markdown)
  assert.equal(markdowns.length, 2)
  assert.match(markdowns[0], /other\.png/)
  assert.equal(markdowns[1], 'Chart: ![a](/w/a%20b.png)')
})

test('turn_note events become transcript items, empty ones are ignored', () => {
  const ctx = collect()
  handleSessionEvent({ type: 'turn_note', data: { text: 'Found the cause.' } }, ctx.dispatch, ctx.stateRef)
  handleSessionEvent({ type: 'turn_note', data: { text: '' } }, ctx.dispatch, ctx.stateRef)
  assert.equal(ctx.actions.length, 1)
  assert.deepEqual(ctx.actions[0], { type: 'APPEND_STAGE_ITEM', payload: { type: 'turn_note', text: 'Found the cause.' } })
})

test('a reloaded finished turn keeps its answered questions and its error', () => {
  const ctx = collect()
  handleSessionEvent({
    type: 'session_loaded',
    data: {
      meta: { status: 'idle' },
      events: [
        { type: 'user_message', data: { text: 'Interview me' } },
        { type: 'thinking', data: { markdown: 'planning' } },
        { type: 'question_required', data: { question_id: 'q1', question: 'Where should it run?', options: ['Phone', 'Server'] } },
        { type: 'question_resolved', data: { question_id: 'q1', answer: 'Server', answered: true } },
        { type: 'error', data: { text: 'Codex made no progress for 30 minutes' } },
        { type: 'run_finished', data: { reason: 'error' } },
      ],
    },
  }, ctx.dispatch, ctx.stateRef)

  const kept = flatten(ctx.actions).filter(a => a.type === 'APPEND_STAGE_ITEM').map(a => a.payload.type)
  assert.deepEqual(kept, ['user_message', 'question', 'error'])
  const resolved = flatten(ctx.actions).find(a => a.type === 'RESOLVE_QUESTION')
  assert.deepEqual(resolved.payload, { questionId: 'q1', answer: 'Server', answered: true })
})

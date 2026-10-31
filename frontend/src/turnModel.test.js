import test from 'node:test'
import assert from 'node:assert/strict'
import { buildTurns, formatElapsed, notesForTurn, nowLine, sameTurn, summarizeTools, turnDurationMs, unansweredLastTurn } from './turnModel.js'

const tool = (name, statusLine, extra = {}) => ({ name, statusLine, status: 'done', ...extra })

test('turns start at each non-steering user message', () => {
  const user1 = { type: 'user_message', text: 'a' }
  const user2 = { type: 'user_message', text: 'b' }
  const steer = { type: 'user_message', text: 's', steering: true }
  const turns = buildTurns([
    { type: 'status', text: 'loaded' },
    user1,
    { type: 'work_group', tools: [tool('file_read', 'Read a.py')], startTime: 5 },
    { type: 'thinking', markdown: 'hm' },
    { type: 'assistant_message', markdown: 'ok' },
    user2,
    steer,
    { type: 'assistant_message', markdown: 'done' },
  ])
  assert.equal(turns.length, 3)
  assert.equal(turns[0].user, null)
  assert.equal(turns[1].user, user1)
  assert.equal(turns[1].tools.length, 1)
  assert.equal(turns[1].thinking.length, 1)
  assert.equal(turns[1].startTime, 5)
  assert.deepEqual(turns[2].talk.map(item => item.type), ['user_message', 'assistant_message'])
})

test('codex commands and file changes become notes like native tools', () => {
  const turns = buildTurns([
    { type: 'user_message', text: 'go' },
    { type: 'codex_command', command: 'pytest -q', status: 'done' },
    { type: 'codex_file_change', path: '/repo/src/app.py', status: 'modified' },
  ])
  const notes = summarizeTools(turns[0].tools)
  assert.deepEqual(notes.map(n => n.kind), ['tests', 'edit'])
  assert.equal(notes[1].text, 'Changed app.py.')
})

test('reads, edits and searches are merged into single sentences', () => {
  const notes = summarizeTools([
    tool('file_read', 'Read session_store.py'),
    tool('file_read', 'Read session_store.py (lines)'),
    tool('file_read', 'Read web_app.py'),
    tool('content_search', "Grep . for 'tasks'"),
    tool('content_search', "Grep . for 'sessions'"),
    tool('file_replace', 'Update session_store.py'),
    tool('file_write', 'Write test_store.py'),
  ])
  assert.deepEqual(notes.map(n => n.text), [
    'Read session_store.py, web_app.py.',
    'Searched the code 2 times.',
    'Changed session_store.py, test_store.py.',
  ])
  assert.deepEqual(notes[2].files, ['session_store.py', 'test_store.py'])
})

test('many files collapse to a count', () => {
  const reads = ['a', 'b', 'c', 'd'].map(n => tool('file_read', `Read ${n}.py`))
  assert.equal(summarizeTools(reads)[0].text, 'Read 4 files.')
})

test('test runs report their sequence and counts', () => {
  const notes = summarizeTools([
    tool('shell_run', 'Run: pytest tests/', { status: 'failed', resultPreview: 'Exit code: 1\n1 failed, 10 passed' }),
    tool('shell_run', 'Run: pytest tests/', { resultPreview: 'Exit code: 0\n11 passed' }),
  ])
  assert.equal(notes.length, 1)
  assert.equal(notes[0].text, 'Tests failed, then passed (11 passed).')
  assert.equal(notes[0].tone, '')
  const bad = summarizeTools([tool('shell_run', 'Run: npm test', { status: 'failed', resultPreview: '2 failed' })])
  assert.equal(bad[0].text, 'Tests failed (2 failed).')
  assert.equal(bad[0].tone, 'bad')
})

test('other commands are named, or counted when there are many', () => {
  const two = summarizeTools([tool('shell_run', 'Run: ls -la'), tool('shell_run', 'Run: git status', { status: 'failed' })])
  assert.deepEqual(two.map(n => n.text), ['Ran ls -la.', 'git status exited with an error.'])
  assert.equal(two[1].tone, 'bad')
  const many = summarizeTools([1, 2, 3].map(i => tool('shell_run', `Run: echo ${i}`)))
  assert.equal(many[0].text, 'Ran 3 commands.')
})

test('running, stopped and silent tools are left out of the notes', () => {
  const notes = summarizeTools([
    tool('file_read', 'Read a.py', { status: 'running' }),
    tool('file_read', 'Read b.py', { status: 'stopped' }),
    tool('plan_update', 'Plan: 3 steps'),
    tool('ask_user', 'Ask: ok?'),
  ])
  assert.deepEqual(notes, [])
})

test('the live line names what is running now', () => {
  assert.deepEqual(nowLine([tool('file_replace', 'Update Sidebar.jsx', { status: 'running' })]), { verb: 'Editing', rest: 'Sidebar.jsx' })
  assert.deepEqual(nowLine([tool('shell_run', 'Run: npm test', { status: 'running' })]), { verb: 'Running', rest: 'npm test' })
  assert.deepEqual(nowLine([tool('apply_patch', 'Apply patch', { status: 'running' })]), { verb: 'Applying a patch', rest: '' })
  assert.equal(nowLine([tool('file_read', 'Read a.py')]), null)
})

test('duration spans the first call to the last result', () => {
  assert.equal(turnDurationMs([
    { startedAt: 1000, durationMs: 500 },
    { startedAt: 40000, durationMs: 1000 },
  ]), 40000)
  assert.equal(turnDurationMs([{ durationMs: 5 }]), 0)
  assert.equal(formatElapsed(41000), '41 s')
  assert.equal(formatElapsed(72000), '1 min 12 s')
})

test('sameTurn compares by item identity', () => {
  const user = { type: 'user_message' }
  const reply = { type: 'assistant_message' }
  const a = buildTurns([user, reply])[0]
  const b = buildTurns([user, reply])[0]
  const c = buildTurns([user, { ...reply }])[0]
  assert.ok(sameTurn(a, b))
  assert.ok(!sameTurn(a, c))
})

test('model-written notes lead, and only trustworthy facts stay rule-based', () => {
  const turns = buildTurns([
    { type: 'user_message', text: 'fix it' },
    { type: 'work_group', startTime: 1, tools: [
      tool('file_read', 'Read store.py'),
      tool('content_search', "Grep . for 'tasks'"),
      tool('file_replace', 'Update store.py'),
      tool('shell_run', 'Run: pytest', { resultPreview: '11 passed' }),
      tool('shell_run', 'Run: ls', { status: 'failed' }),
    ] },
    { type: 'turn_note', text: 'Found that sessions points at tasks.' },
    { type: 'assistant_message', markdown: 'done' },
  ])
  assert.equal(turns[0].modelNotes.length, 1)
  assert.deepEqual(turns[0].talk.map(item => item.type), ['assistant_message'])
  const notes = notesForTurn(turns[0])
  assert.deepEqual(notes.map(n => n.kind), ['model', 'edit', 'tests', 'commands'])
  assert.equal(notes[0].text, 'Found that sessions points at tasks.')
  assert.equal(notes[3].tone, 'bad')
})

test('without model notes the rule-based notes are used unchanged', () => {
  const turns = buildTurns([
    { type: 'user_message', text: 'go' },
    { type: 'work_group', startTime: 1, tools: [tool('file_read', 'Read a.py')] },
  ])
  assert.deepEqual(notesForTurn(turns[0]).map(n => n.text), ['Read a.py.'])
})

test('the latest prompt is unanswered when it has no assistant text and nothing is running', () => {
  const failed = buildTurns([
    { type: 'user_message', text: 'first' },
    { type: 'assistant_message', markdown: 'answer' },
    { type: 'user_message', text: 'second' },
    { type: 'work_group', startTime: 1, tools: [tool('file_read', 'Read a.py')] },
    { type: 'error', text: 'FhGenie timed out' },
  ])
  assert.equal(unansweredLastTurn(failed, false), failed[1])
  assert.equal(unansweredLastTurn(failed, true), null)
})

test('a stopped or failed run is unfinished even with interim text; a normal end is not', () => {
  const u = { type: 'user_message', text: 'q' }
  const interim = { type: 'assistant_message', markdown: 'let me look' }
  for (const reason of ['interrupted', 'error', 'api timeout']) {
    const turns = buildTurns([u, interim, { type: 'run_end', reason }])
    assert.equal(unansweredLastTurn(turns, false), turns[0], reason)
  }
  for (const reason of ['completed', 'max iterations reached', 'no content returned']) {
    assert.equal(unansweredLastTurn(buildTurns([u, { type: 'run_end', reason }]), false), null, reason)
  }
  // The marker is bookkeeping: it is not drawn as conversation.
  assert.deepEqual(buildTurns([u, { type: 'run_end', reason: 'interrupted' }])[0].talk, [])
})

test('an answered, streaming, empty or prompt-less last turn offers nothing', () => {
  assert.equal(unansweredLastTurn(buildTurns([{ type: 'user_message', text: 'q' }, { type: 'assistant_message', markdown: 'a' }]), false), null)
  assert.equal(unansweredLastTurn(buildTurns([{ type: 'user_message', text: 'q' }, { type: 'assistant_stream', text: 'par' }]), false), null)
  assert.notEqual(unansweredLastTurn(buildTurns([{ type: 'user_message', text: 'q' }, { type: 'assistant_message', markdown: '  ' }]), false), null)
  assert.equal(unansweredLastTurn(buildTurns([{ type: 'status', text: 'only a status' }]), false), null)
  assert.equal(unansweredLastTurn([], false), null)
})

test("a provider's own tool call updates one entry through start, input and end", () => {
  const user = { type: 'user_message', text: 'redo the UI' }
  const call = (status, label) => ({ type: 'codex_command', command: label, label, tool: 'file_write', callId: 'toolu_1', status })
  const running = buildTurns([user, call('running', 'Write a file')])[0]
  assert.equal(running.tools.length, 1)
  assert.equal(nowLine(running.tools).verb, 'Writing')
  assert.equal(nowLine(running.tools).rest, 'a file')

  const named = buildTurns([user, call('running', 'Write a file'), call('running', 'Write index.html')])[0]
  assert.equal(named.tools.length, 1)
  assert.equal(nowLine(named.tools).rest, 'index.html')

  const done = buildTurns([user, call('running', 'Write a file'), call('running', 'Write index.html'), call('completed', 'Write index.html')])[0]
  assert.equal(done.tools.length, 1)
  assert.equal(done.tools[0].status, 'done')
  assert.equal(nowLine(done.tools), null)
  assert.deepEqual(summarizeTools(done.tools).map(note => note.text), ['Changed index.html.'])
})

test("Claude's reads and failed calls read like native ones in the notes", () => {
  const user = { type: 'user_message', text: 'go' }
  const turn = buildTurns([
    user,
    { type: 'codex_command', command: 'Read a.py', label: 'Read a.py', tool: 'file_read', callId: 'r1', status: 'completed' },
    { type: 'codex_command', command: 'Run: make', label: 'Run: make', tool: 'shell_run', callId: 'b1', status: 'failed' },
  ])[0]
  assert.deepEqual(summarizeTools(turn.tools).map(note => note.text), ['Read a.py.', 'make exited with an error.'])
})

test('Codex commands without a call id still add an entry each', () => {
  const turn = buildTurns([
    { type: 'user_message', text: 'go' },
    { type: 'codex_command', command: 'ls', status: 'running' },
    { type: 'codex_command', command: 'ls', status: 'completed' },
  ])[0]
  assert.equal(turn.tools.length, 2)
})

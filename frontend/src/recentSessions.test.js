import assert from 'node:assert/strict'
import test from 'node:test'

import { recentSessions, RECENT_SESSION_LIMIT } from './recentSessions.js'

const projects = [
  {
    id: 'myharness',
    name: 'MyHarness',
    tasks: [{ id: 'task_1', name: 'Splash' }],
  },
  {
    id: '__chats__',
    name: 'Chats',
    tasks: [{ id: 'default', name: 'Chats' }],
  },
]

function session(id, updatedAt, extra = {}) {
  return {
    id,
    project_id: 'myharness',
    task_id: 'task_1',
    title: `Session ${id}`,
    updated_at: updatedAt,
    ...extra,
  }
}

test('orders sessions newest first and caps at the limit', () => {
  const sessionsById = {
    a: session('a', '2026-07-20T10:00:00Z'),
    b: session('b', '2026-07-26T10:00:00Z'),
    c: session('c', '2026-07-22T10:00:00Z'),
    d: session('d', '2026-07-25T10:00:00Z'),
    e: session('e', '2026-07-21T10:00:00Z'),
    f: session('f', '2026-07-19T10:00:00Z'),
  }
  const recent = recentSessions(sessionsById, projects)
  assert.equal(recent.length, RECENT_SESSION_LIMIT)
  assert.deepEqual(recent.map(s => s.id), ['b', 'd', 'c', 'e', 'a'])
})

test('respects an explicit limit and handles empty input', () => {
  const sessionsById = {
    a: session('a', '2026-07-20T10:00:00Z'),
    b: session('b', '2026-07-21T10:00:00Z'),
  }
  assert.equal(recentSessions(sessionsById, projects, 1).length, 1)
  assert.deepEqual(recentSessions({}, projects), [])
  assert.deepEqual(recentSessions(undefined, undefined), [])
})

test('labels chats flatly and project sessions as project / task', () => {
  const sessionsById = {
    chat: session('chat', '2026-07-26T10:00:00Z', {
      project_id: '__chats__',
      task_id: 'default',
      kind: 'chat',
    }),
    proj: session('proj', '2026-07-25T10:00:00Z'),
  }
  const [chat, proj] = recentSessions(sessionsById, projects)
  assert.equal(chat.contextLabel, 'Chat')
  assert.equal(chat.kind, 'chat')
  assert.equal(proj.contextLabel, 'MyHarness / Splash')
  assert.equal(proj.kind, 'project')
})

test('falls back when the project or task is not in the tree', () => {
  const sessionsById = {
    orphan: session('orphan', '2026-07-26T10:00:00Z', { project_id: 'gone' }),
    noTask: session('noTask', '2026-07-25T10:00:00Z', { task_id: 'gone' }),
  }
  const [orphan, noTask] = recentSessions(sessionsById, projects)
  assert.equal(orphan.contextLabel, 'gone')
  assert.equal(noTask.contextLabel, 'MyHarness')
})

test('falls back to a short id when a session has no title', () => {
  const sessionsById = {
    sess_abcdef123456: session('sess_abcdef123456', '2026-07-26T10:00:00Z', { title: '' }),
  }
  assert.equal(recentSessions(sessionsById, projects)[0].title, 'sess_abc')
})

test('defaults provider and status, and skips malformed entries', () => {
  const sessionsById = {
    a: session('a', '2026-07-26T10:00:00Z'),
    b: session('b', '2026-07-25T10:00:00Z', { provider: 'codex-app-server', status: 'running' }),
    bad: null,
    alsoBad: { title: 'no id' },
  }
  const recent = recentSessions(sessionsById, projects)
  assert.equal(recent.length, 2)
  assert.equal(recent[0].provider, 'native')
  assert.equal(recent[0].status, 'idle')
  assert.equal(recent[1].provider, 'codex-app-server')
  assert.equal(recent[1].status, 'running')
})

test('breaks timestamp ties deterministically by id', () => {
  const sessionsById = {
    z: session('z', '2026-07-26T10:00:00Z'),
    a: session('a', '2026-07-26T10:00:00Z'),
  }
  assert.deepEqual(recentSessions(sessionsById, projects).map(s => s.id), ['a', 'z'])
})

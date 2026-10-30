import test from 'node:test'
import assert from 'node:assert/strict'
import {
  sessionLabels, taskNamesFor, labelsFor, projectSessions, activitySessions, archivedSessions,
  filterOptions, matchesFilter, sessionReference, modeLabel,
} from './sessionLabels.js'

const projects = [
  { id: 'p1', name: 'Crash', tasks: [
    { id: 'new_task', name: 'New Task', sessions: ['a'] },
    { id: 'mesh', name: 'Mesh check', sessions: ['b', 'c'] },
  ] },
  { id: 'p2', name: 'Other', tasks: [{ id: 'new_task', name: 'Postprocessing', sessions: ['d'] }] },
]
const sessions = {
  a: { id: 'a', project_id: 'p1', task_id: 'new_task', title: 'First', updated_at: '2026-10-01T00:00:00Z' },
  b: { id: 'b', project_id: 'p1', task_id: 'mesh', title: 'Second', labels: ['Urgent', 'mesh check'], updated_at: '2026-10-03T00:00:00Z', provider: 'claude-agent' },
  c: { id: 'c', project_id: 'p1', task_id: 'mesh', title: 'Old', archived: true, updated_at: '2026-10-05T00:00:00Z' },
  d: { id: 'd', project_id: 'p2', task_id: 'new_task', title: 'Plot', updated_at: '2026-10-02T00:00:00Z' },
}
const names = taskNamesFor(projects)

test('task names become labels per project, placeholders hidden, duplicates merged', () => {
  assert.deepEqual(labelsFor(sessions.a, names), [])
  assert.deepEqual(labelsFor(sessions.b, names), ['Mesh check', 'Urgent'])
  assert.deepEqual(labelsFor(sessions.d, names), ['Postprocessing'])
  assert.deepEqual(sessionLabels(null), [])
})

test('project and activity lists are flat, recency-sorted, and split archived', () => {
  assert.deepEqual(projectSessions(projects[0], sessions).map(s => s.id), ['b', 'a'])
  assert.deepEqual(projectSessions(projects[0], sessions, { archived: true }).map(s => s.id), ['c'])
  assert.deepEqual(activitySessions(sessions).map(s => s.id), ['b', 'd', 'a'])
  assert.deepEqual(archivedSessions(sessions).map(s => s.id), ['c'])
})

test('filters combine label, mode, and text query', () => {
  const options = filterOptions(sessions, names)
  assert.deepEqual(options.labels, ['Mesh check', 'Postprocessing', 'Urgent'])
  assert.deepEqual(options.modes, ['Native', 'Claude'])
  assert.equal(matchesFilter(sessions.b, { label: 'Urgent' }, names), true)
  assert.equal(matchesFilter(sessions.a, { label: 'Urgent' }, names), false)
  assert.equal(matchesFilter(sessions.b, { mode: 'Native' }, names), false)
  assert.equal(matchesFilter(sessions.d, { query: 'post' }, names), true)
  assert.equal(matchesFilter(sessions.d, { query: 'other' }, names, 'Other'), true)
  assert.equal(modeLabel({}), 'Native')
})

test('session reference names project and session only', () => {
  assert.equal(sessionReference(sessions.b, { p1: 'Crash' }), 'Crash / Second — session_id: b')
  assert.equal(sessionReference({ id: 'x', kind: 'chat', title: 'Hi' }), 'Chat / Hi — session_id: x')
})

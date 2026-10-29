import test from 'node:test'
import assert from 'node:assert/strict'
import {
  activitySessions, archivedSessions, withoutArchived, projectSessions, sessionPlace, sessionReference,
} from './sessionList.js'

const sessions = {
  a: { id: 'a', project_id: 'p', task_id: 'general', title: 'Old', updated_at: '2026-10-01T00:00:00Z' },
  b: { id: 'b', project_id: 'p', task_id: 'bugs', title: 'New', updated_at: '2026-10-03T00:00:00Z' },
  c: { id: 'c', project_id: 'p', task_id: 'bugs', title: 'Gone', updated_at: '2026-10-04T00:00:00Z', archived: true },
  d: { id: 'd', project_id: '__chats__', kind: 'chat', task_id: '__chats__', title: 'Chat', updated_at: '2026-10-02T00:00:00Z' },
}
const projects = [{ id: 'p', name: 'Proj', tasks: [{ id: 'general', sessions: ['a'] }, { id: 'bugs', sessions: ['b', 'c'] }] }]

test('activity lists live sessions newest first and archived ones separately', () => {
  assert.deepEqual(activitySessions(sessions).map(s => s.id), ['b', 'd', 'a'])
  assert.deepEqual(archivedSessions(sessions).map(s => s.id), ['c'])
})

test('archived sessions leave the label tree without mutating it', () => {
  const live = withoutArchived(projects, sessions)
  assert.deepEqual(live[0].tasks[1].sessions, ['b'])
  assert.deepEqual(projects[0].tasks[1].sessions, ['b', 'c'])
  assert.deepEqual(projectSessions(projects[0], sessions).map(s => s.id), ['b', 'a'])
})

test('places and references name the project, label and session', () => {
  const names = { p: 'Proj' }
  const labels = { bugs: 'Bugs' }
  assert.equal(sessionPlace(sessions.b, names, labels), 'Proj · Bugs')
  assert.equal(sessionPlace(sessions.d, names, labels), 'Chat')
  assert.equal(sessionReference(sessions.b, names, labels), 'Proj / Bugs / New — session_id: b')
  assert.equal(sessionReference(sessions.d, names, labels), 'Chat / Chat — session_id: d')
})

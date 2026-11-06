import assert from 'node:assert/strict'
import test from 'node:test'

import { currentProject, parseTags, pickNoteText, projectPath, viewLabel } from './projectNotes.js'
import { handleSessionEvent } from './eventHandlers.js'
import { reducer as appReducer, initialState } from './context/appReducer.js'
import { isSlashCommand } from './constants.js'

test('current project comes from the session, never for chats', () => {
  const projects = [{ id: 'myharness', name: 'MyHarness' }]
  assert.deepEqual(currentProject({ project_id: 'myharness' }, projects), { id: 'myharness', name: 'MyHarness' })
  assert.deepEqual(currentProject({ project_id: 'gone' }, projects), { id: 'gone', name: 'gone' })
  assert.equal(currentProject({ project_id: '__chats__' }, projects), null)
  assert.equal(currentProject({ project_id: 'myharness', kind: 'chat' }, projects), null)
  assert.equal(currentProject(null, projects), null)
})

test('view labels name the project', () => {
  assert.equal(viewLabel('Instructions', 'MyHarness'), 'Instructions - MyHarness')
  assert.equal(viewLabel('Memory', 'MyHarness'), 'Memory - MyHarness')
  assert.equal(viewLabel('Instructions', ''), 'Instructions')
  assert.equal(projectPath('a b', 'memory'), '/api/projects/a%20b/memory')
})

test('save buttons send the selection, else the whole note', () => {
  const note = 'keep\nsend this\nkeep too'
  const start = note.indexOf('send')
  const end = start + 'send this'.length
  assert.deepEqual(pickNoteText(note, start, end), { text: 'send this', selection: true, remainder: 'keep\n\nkeep too' })
  assert.deepEqual(pickNoteText('  whole  ', 3, 3), { text: 'whole', selection: false, remainder: '' })
  // A whitespace-only selection falls back to the whole note.
  assert.equal(pickNoteText('a  b', 1, 3).text, 'a  b')
})

test('tags are trimmed, deduplicated and capped', () => {
  assert.deepEqual(parseTags(' mesh, Mesh ,, solver  setup'), ['mesh', 'solver setup'])
  assert.equal(parseTags(Array.from({ length: 12 }, (_, i) => `t${i}`).join(',')).length, 8)
})

test('memory_saved becomes a transcript notice', () => {
  const actions = []
  const stateRef = { current: { silentCommand: false, isRunning: false, currentProvider: 'native' } }
  const memory = { id: 'mem_1', title: 'Solver', description: 'dt' }
  handleSessionEvent({ type: 'memory_saved', data: { memory, project_id: 'p', project_name: 'P' } }, a => actions.push(a), stateRef)
  assert.deepEqual(actions, [{ type: 'APPEND_STAGE_ITEM', payload: { type: 'memory_saved', memory, projectId: 'p', projectName: 'P' } }])
})

test('OPEN_NOTES opens the Notes tab at a view', () => {
  const opened = appReducer({ ...initialState, artifactFile: '/x.html' }, { type: 'OPEN_NOTES', payload: { view: 'memory', memoryId: 'mem_1' } })
  assert.equal(opened.workspacePanelOpen, true)
  assert.equal(opened.workspacePanelTab, 'notes')
  assert.equal(opened.artifactFile, null)
  assert.deepEqual(opened.notesTarget, { view: 'memory', memoryId: 'mem_1' })
  assert.equal(appReducer(opened, { type: 'CLEAR_NOTES_TARGET' }).notesTarget, null)
})

test('/remember is a slash command', () => {
  assert.equal(isSlashCommand('/remember'), true)
  assert.equal(isSlashCommand('/remember solver settings'), true)
})

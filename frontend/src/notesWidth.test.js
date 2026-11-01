import test from 'node:test'
import assert from 'node:assert/strict'
import { clampNotesWidth, readNotesWidth, saveNotesWidth, NOTES_MIN, THREAD_MIN } from './notesWidth.js'

function memoryStorage() {
  const data = new Map()
  return { getItem: k => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, v), removeItem: k => data.delete(k) }
}

test('the notes never shrink below their minimum', () => {
  assert.equal(clampNotesWidth(40, 1440), NOTES_MIN)
})

test('the thread always keeps its minimum room', () => {
  assert.equal(clampNotesWidth(5000, 1440), 1440 - 40 - THREAD_MIN)
})

test('a width inside the range is kept, rounded', () => {
  assert.equal(clampNotesWidth(301.6, 1440), 302)
})

test('a dragged width is remembered and can be reset to automatic', () => {
  const storage = memoryStorage()
  assert.equal(readNotesWidth(storage), null)
  saveNotesWidth(320, storage)
  assert.equal(readNotesWidth(storage), 320)
  saveNotesWidth(null, storage)
  assert.equal(readNotesWidth(storage), null)
})

test('a broken stored value falls back to automatic', () => {
  const storage = memoryStorage()
  storage.setItem('myharness.notesWidth', 'wide')
  assert.equal(readNotesWidth(storage), null)
})

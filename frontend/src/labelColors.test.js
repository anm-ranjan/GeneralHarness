import test from 'node:test'
import assert from 'node:assert/strict'
import { LABEL_COLOR_COUNT, labelColorIndex, labelColorStyle } from './labelColors.js'

test('label colours are stable and stay within the theme palette', () => {
  const first = labelColorIndex('task_backend')
  assert.equal(labelColorIndex('task_backend'), first)
  assert.ok(first >= 0 && first < LABEL_COLOR_COUNT)
  assert.deepEqual(labelColorStyle('task_backend'), {
    '--label-color': `var(--color-label-${first + 1})`,
  })
})

test('label colours accept missing and non-string ids', () => {
  assert.ok(labelColorIndex(undefined) >= 0)
  assert.ok(labelColorIndex(42) < LABEL_COLOR_COUNT)
})

test('explicit label colours map directly to palette tokens', () => {
  assert.deepEqual(labelColorStyle('teal'), { '--label-color': 'var(--color-label-3)' })
})

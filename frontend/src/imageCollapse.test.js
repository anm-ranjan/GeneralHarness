import test from 'node:test'
import assert from 'node:assert/strict'
import { imageKey, imageLabel, isImageCollapsed, setImageCollapsed } from './imageCollapse.js'

function memoryStorage() {
  const data = new Map()
  return {
    getItem: key => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
  }
}

test('local image keys ignore the cache-busting version', () => {
  const a = '/api/files/image?path=%2Fws%2Fplot.png&v=1'
  const b = '/api/files/image?path=%2Fws%2Fplot.png&v=2'
  assert.equal(imageKey(a), 'file:/ws/plot.png')
  assert.equal(imageKey(a), imageKey(b))
  assert.equal(imageKey('/api/sessions/s1/attachments/x.png?t=1'), '/api/sessions/s1/attachments/x.png')
  // apiUrl() prefixes the backend origin on desktop and remote hosts.
  assert.equal(imageKey('http://127.0.0.1:8421/api/files/image?path=%2Fws%2Fplot.png&v=9'), 'file:/ws/plot.png')
})

test('labels prefer alt text, then the file name', () => {
  assert.equal(imageLabel('/api/files/image?path=%2Fws%2Fresult%20plot.png', ''), 'result plot.png')
  assert.equal(imageLabel('/api/files/image?path=%2Fws%2Fa.png', 'Stress plot'), 'Stress plot')
  assert.equal(imageLabel('https://example.com/img/chart.svg?x=1'), 'chart.svg')
})

test('collapsed state round-trips through storage', () => {
  const previous = globalThis.localStorage
  globalThis.localStorage = memoryStorage()
  try {
    const src = '/api/files/image?path=%2Fws%2Fa.png&v=3'
    assert.equal(isImageCollapsed(src), false)
    setImageCollapsed(src, true)
    assert.equal(isImageCollapsed('/api/files/image?path=%2Fws%2Fa.png&v=4'), true)
    setImageCollapsed(src, false)
    assert.equal(isImageCollapsed(src), false)
    // An explicit choice overrides the default in both directions.
    assert.equal(isImageCollapsed(src, true), false)
    assert.equal(isImageCollapsed('/api/files/image?path=%2Fws%2Fother.png', true), true)
    setImageCollapsed('/api/files/image?path=%2Fws%2Fb.png', true)
    assert.equal(isImageCollapsed('/api/files/image?path=%2Fws%2Fb.png', false), true)
  } finally {
    globalThis.localStorage = previous
  }
})

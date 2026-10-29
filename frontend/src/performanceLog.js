import { api } from './api.js'
export function reportAfterPaint(phase, started, count) {
  if (typeof requestAnimationFrame !== 'function') return
  requestAnimationFrame(() => requestAnimationFrame(() => {
    api('POST', '/api/diagnostics/performance', { phase, elapsed_ms: performance.now() - started, count }).catch(() => {})
  }))
}

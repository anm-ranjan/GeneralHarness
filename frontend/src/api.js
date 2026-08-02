// All requests target exactly one machine at a time: the "active host".
//
// Hosts never share data — switching swaps the whole workspace rather than
// merging anything — so the only cross-host concern here is making sure a
// response from the machine you just left cannot land in the view of the one
// you switched to. Every request joins an epoch that is aborted on switch, so
// stale responses never reach a `.then` at all.

// '' means the host serving this page, which keeps the single-machine case
// same-origin and identical to how this worked before fleets existed.
let activeBase = ''
let activeHostId = ''
let epoch = new AbortController()

/** Base URL of the host currently being viewed ('' for the origin host). */
export function getActiveBase() {
  return activeBase
}

export function getActiveHostId() {
  return activeHostId
}

/**
 * Point every subsequent request at `host` and abandon in-flight ones.
 * Returns false when the host was already active, so callers can skip the
 * (expensive) teardown and reload.
 */
export function setActiveHost(host) {
  const base = String(host?.url || '').replace(/\/+$/, '')
  const id = String(host?.id || '')
  if (base === activeBase && id === activeHostId) return false
  activeBase = base
  activeHostId = id
  // Anything still in flight belongs to the previous host's view.
  epoch.abort()
  epoch = new AbortController()
  return true
}

/** True for the rejection produced by switching hosts mid-request. */
export function isAbandonedRequest(err) {
  return err?.name === 'AbortError' || err?.abandoned === true
}

/** Absolute URL for a path on the active host, for `<img src>` and downloads. */
export function apiUrl(path) {
  return `${activeBase}${path}`
}

/** Absolute URL for a path on a specific host, for fleet-wide status polling. */
export function hostUrl(base, path) {
  return `${String(base || '').replace(/\/+$/, '')}${path}`
}

export async function api(method, path, body) {
  const opts = {
    method,
    headers: {},
    signal: epoch.signal,
  }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  let res
  try {
    res = await fetch(apiUrl(path), opts)
  } catch (err) {
    if (err?.name === 'AbortError') throw err
    // A dead host fails at the transport layer; give callers the same shape
    // as an HTTP failure so host-down and error responses handle alike.
    const wrapped = new Error(`API ${method} ${path}: unreachable`)
    wrapped.status = 0
    wrapped.detail = err?.message || 'Host unreachable'
    throw wrapped
  }
  if (!res.ok) {
    const err = new Error(`API ${method} ${path}: ${res.status}`)
    err.status = res.status
    try { err.detail = (await res.json()).detail } catch {}
    throw err
  }
  const text = await res.text()
  return text ? JSON.parse(text) : null
}

function wsBase() {
  if (!activeBase) {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${proto}//${location.host}`
  }
  return activeBase.replace(/^http:/, 'ws:').replace(/^https:/, 'wss:')
}

export function wsUrl(sessionId) {
  return `${wsBase()}/api/sessions/${encodeURIComponent(sessionId)}/events`
}

export function globalWsUrl() {
  return `${wsBase()}/api/events`
}

async function downloadBlob(path, fallbackName, failureLabel) {
  const res = await fetch(apiUrl(path), { signal: epoch.signal })
  if (!res.ok) {
    const err = new Error(`${failureLabel} failed: ${res.status}`)
    try { err.detail = (await res.json()).detail } catch {}
    throw err
  }
  const blob = await res.blob()
  const disposition = res.headers.get('Content-Disposition') || ''
  const match = disposition.match(/filename="?([^"]+)"?/)
  const filename = match ? match[1] : fallbackName
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export async function downloadSessionBackup(sessionId) {
  await downloadBlob(
    `/api/sessions/${encodeURIComponent(sessionId)}/backup`,
    'thread.myharness.zip',
    'Backup',
  )
}

export async function importSessionBackup(file, projectId, taskId) {
  const base64 = await new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error('Could not read backup file'))
    reader.readAsDataURL(file)
  })
  return api('POST', '/api/sessions/import', {
    data: base64,
    project_id: projectId || '',
    task_id: taskId || '',
  })
}

export async function downloadSessionExport(sessionId, format = 'md') {
  await downloadBlob(
    `/api/sessions/${encodeURIComponent(sessionId)}/export?format=${format}`,
    `thread.${format}`,
    'Export',
  )
}

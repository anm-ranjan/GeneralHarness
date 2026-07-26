const BASE = ''

export async function api(method, path, body) {
  const opts = {
    method,
    headers: {},
  }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(`${BASE}${path}`, opts)
  if (!res.ok) {
    const err = new Error(`API ${method} ${path}: ${res.status}`)
    err.status = res.status
    try { err.detail = (await res.json()).detail } catch {}
    throw err
  }
  const text = await res.text()
  return text ? JSON.parse(text) : null
}

export function wsUrl(sessionId) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}/api/sessions/${encodeURIComponent(sessionId)}/events`
}

export function globalWsUrl() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}/api/events`
}

export async function downloadSessionBackup(sessionId) {
  const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/backup`)
  if (!res.ok) {
    const err = new Error(`Backup failed: ${res.status}`)
    try { err.detail = (await res.json()).detail } catch {}
    throw err
  }
  const blob = await res.blob()
  const disposition = res.headers.get('Content-Disposition') || ''
  const match = disposition.match(/filename="?([^"]+)"?/)
  const filename = match ? match[1] : 'session.myharness.zip'
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
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
  const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/export?format=${format}`)
  if (!res.ok) {
    const err = new Error(`Export failed: ${res.status}`)
    try { err.detail = (await res.json()).detail } catch {}
    throw err
  }
  const blob = await res.blob()
  const disposition = res.headers.get('Content-Disposition') || ''
  const match = disposition.match(/filename="?([^"]+)"?/)
  const filename = match ? match[1] : `session.${format}`
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

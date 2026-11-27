// Native Settings: "Check endpoint" and the LAN model server (llama-server) launcher.

const URL_RE = /^https?:\/\/[^/\s]+/

/** POST /api/native/settings/check body, or { error }. Saves nothing. */
export function checkRequest(baseUrl, model) {
  const base = String(baseUrl || '').trim()
  if (!URL_RE.test(base)) return { error: 'Enter the base URL (http:// or https://), for example http://127.0.0.1:8081/v1' }
  return { body: { base_url: base, model: String(model || '').trim() } }
}

/** The check result as { ok } or { error }, naming the served ids when the model is missing. */
export function checkMessage(result) {
  if (!result) return null
  if (result.ok) return { ok: result.message }
  const served = result.model_listed === false && result.models?.length ? ` It serves: ${result.models.join(', ')}` : ''
  return { error: result.message + served }
}

/** Form state for the launcher from GET /api/native/settings (`server`). */
export function serverForm(server) {
  return { script: server?.script || server?.default_script || '', login: server?.login || '' }
}

/** Mirrors model_server.normalize_script: an absolute Linux path of a .sh file. */
export function scriptError(script) {
  const text = String(script || '').trim()
  if (!text) return 'Enter the launch script: its Linux path on the model server.'
  if (/[\r\n]/.test(text)) return 'The launch script must be one path.'
  if (text.includes('\\') || /^[a-zA-Z]:/.test(text)) return 'Enter the Linux path on the model server (/archive/...), not a Windows path.'
  if (!(text.startsWith('/') || text === '~' || text.startsWith('~/'))) return 'The launch script must be an absolute path such as /archive/models/run-inkling.sh.'
  if (!text.endsWith('.sh')) return 'The launch script must be a .sh file (a path, not a command line).'
  return ''
}

/** POST /api/native/server/launch body, or { error }. The base URL picks the SSH host when the login is empty. */
export function launchRequest(form, baseUrl) {
  const script = String(form.script || '').trim()
  const error = scriptError(script)
  if (error) return { error }
  const login = String(form.login || '').trim()
  if (login && /\s/.test(login)) return { error: 'SSH login must look like user@host.' }
  return { body: { script, login, base_url: String(baseUrl || '').trim() } }
}

/** Query string for GET /api/native/server/scripts: the folder of the script typed, else the default folder. */
export function scriptsQuery(form, baseUrl, defaultDir) {
  const script = String(form.script || '').trim()
  const folder = script.startsWith('/') && script.lastIndexOf('/') > 0
    ? script.slice(0, script.lastIndexOf('/'))
    : (defaultDir || '/archive/models')
  const params = new URLSearchParams({ folder, login: String(form.login || '').trim(), base_url: String(baseUrl || '').trim() })
  return params.toString()
}

/** One line about the launched server from GET /api/native/server. */
export function serverSummary(server) {
  const launched = server?.launched
  if (!launched) return 'No server started from here.'
  const since = launched.started_at ? new Date(launched.started_at).toLocaleString() : ''
  const where = `${launched.login}${since ? ` since ${since}` : ''} (process group ${launched.pgid})`
  if (server.running === true) return `Running on ${where}.`
  if (server.running === false) return `Started on ${where}, but no longer running.`
  return `Started on ${where}.`
}

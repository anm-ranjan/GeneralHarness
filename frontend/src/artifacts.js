export function artifactPath(value, workspaceRoot = '') {
  let path = String(value || '').trim()
  try { path = decodeURIComponent(path) } catch {}
  if (/^(https?:|mailto:|data:|javascript:)/i.test(path) || path.startsWith('//')) return ''
  if (path.startsWith('file://')) path = path.slice(7)
  if (!/\.(html?|md|markdown|txt|dat)$/i.test(path)) return ''
  if (!path.startsWith('/') && !/^[a-z]:[\\/]/i.test(path) && !path.startsWith('\\\\')) {
    if (!workspaceRoot) return ''
    path = `${workspaceRoot.replace(/[\\/]+$/, '')}/${path}`
  }
  return path
}

export function artifactUrl(path) {
  if (path.startsWith('\\\\')) return '/api/workspace/view/' + encodeURIComponent(path.replace(/\\/g, '/'))
  return '/api/workspace/view/' + path.replace(/\\/g, '/').replace(/^\/+/, '').split('/').map(encodeURIComponent).join('/')
}

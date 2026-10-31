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

/**
 * Whether Markdown links to `path`, as an absolute link (plain, URL-encoded or
 * file://) or as a link relative to the file's folder or workspace.
 */
export function linksToPath(markdown, path) {
  const wanted = String(path || '')
  if (!wanted || !markdown) return false
  for (const match of String(markdown).matchAll(/\]\(\s*(?:<([^>]+)>|([^)\s]+))/g)) {
    let target = match[1] || match[2]
    try { target = decodeURIComponent(target) } catch {}
    target = target.replace(/^file:\/\//, '').replace(/[?#].*$/, '')
    if (target === wanted) return true
    const relative = target.replace(/^\.\//, '')
    if (relative && !relative.startsWith('/') && wanted.endsWith(`/${relative}`)) return true
  }
  return false
}

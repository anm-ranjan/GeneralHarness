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

// Compare paths with forward slashes; Windows drive paths case-insensitively.
// URL-style drive paths (`/C:/x`, `file:///C:/x`) lose the leading slash.
function comparablePath(value) {
  let path = String(value || '').trim().replace(/^file:\/\/\/?(?=[a-z]:)/i, '').replace(/^file:\/\//, '')
  path = path.replace(/[?#].*$/, '').replace(/\\/g, '/').replace(/^\.\//, '').replace(/^\/(?=[a-z]:\/)/i, '')
  return /^[a-z]:\//i.test(path) ? path.toLowerCase() : path
}

// Whether any Markdown target names `path`, absolute or relative to its folder
// or workspace.
function anyTargetIs(targets, path) {
  const wanted = comparablePath(path)
  if (!wanted) return false
  const wantedLower = wanted.toLowerCase()
  const windows = /^[a-z]:\//.test(wanted)
  for (let target of targets) {
    try { target = decodeURIComponent(target) } catch {}
    target = comparablePath(target)
    if (!target) continue
    if (target === wanted) return true
    const isAbsolute = target.startsWith('/') || /^[a-z]:\//i.test(target)
    if (!isAbsolute && (windows ? wantedLower.endsWith(`/${target.toLowerCase()}`) : wanted.endsWith(`/${target}`))) return true
  }
  return false
}

/**
 * Whether Markdown points at `path` in a way the transcript renders clickable:
 * a link (plain, URL-encoded, <bracketed> or file://) or an inline code span,
 * either absolute or relative to the file's folder or workspace. Windows
 * backslash, forward-slash and `/C:/` spellings of the same path match.
 */
export function linksToPath(markdown, path) {
  if (!path || !markdown) return false
  const text = String(markdown)
  return anyTargetIs([
    ...[...text.matchAll(/\]\(\s*(?:<([^>]+)>|([^)\s]+))/g)].map(match => match[1] || match[2]),
    ...[...text.matchAll(/(?<!`)`([^`\n]+)`(?!`)/g)].map(match => match[1]),
  ], path)
}

/**
 * Whether Markdown embeds `path` as an image (`![alt](path)`), matched the same
 * way as linksToPath. Only an embed shows the picture, so a mere mention or
 * link does not count.
 */
export function embedsImage(markdown, path) {
  if (!path || !markdown) return false
  const targets = [...String(markdown).matchAll(/!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^)\s]+))/g)].map(match => match[1] || match[2])
  return anyTargetIs(targets, path)
}

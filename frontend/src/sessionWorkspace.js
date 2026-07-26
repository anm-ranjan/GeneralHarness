export function effectiveWorkspaceRoot(meta, projectRoot = '') {
  return (meta?.working_directory || projectRoot || '').trim()
}

export function workspaceDisplayName(workspace = '') {
  const value = String(workspace || '').trim()
  if (!value) return ''
  const trimmed = value.replace(/[\\/]+$/, '')
  if (!trimmed) return value
  const parts = trimmed.split(/[\\/]+/)
  return parts[parts.length - 1] || trimmed
}

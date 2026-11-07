// Pure helpers for the Notes tab's project views (Instructions / Memory) and the
// transcript's "saved to memory" notice. Backend: backend/project_knowledge.py.
import { CHATS_PROJECT_ID } from './constants.js'

export const INSTRUCTIONS_MAX_CHARS = 16000

export function projectPath(projectId, suffix = '') {
  return `/api/projects/${encodeURIComponent(projectId)}/${suffix}`
}

// The current session's project, or null for chats / no session.
export function currentProject(meta, projects) {
  if (!meta || ['chat','agent_run'].includes(meta.kind) || !meta.project_id || meta.project_id === CHATS_PROJECT_ID) return null
  const project = (projects || []).find(p => p.id === meta.project_id)
  return { id: meta.project_id, name: project?.name || meta.project_id }
}

// "Instructions - MyHarness" / "Memory - MyHarness".
export function viewLabel(kind, projectName) {
  return projectName ? `${kind} - ${projectName}` : kind
}

// What a Notes "Save as …" button sends: the selection when there is one,
// otherwise the whole note. `remainder` is the note after removing what was
// sent (offered as "Clear" afterwards).
export function pickNoteText(text, start, end) {
  const value = String(text || '')
  const hasSelection = Number.isInteger(start) && Number.isInteger(end) && end > start
  if (hasSelection) {
    const picked = value.slice(start, end)
    if (picked.trim()) {
      return { text: picked.trim(), selection: true, remainder: value.slice(0, start) + value.slice(end) }
    }
  }
  return { text: value.trim(), selection: false, remainder: '' }
}

export function memoryDate(entry) {
  return String(entry?.updated_at || entry?.created_at || '').slice(0, 10)
}

export function parseTags(value) {
  const seen = new Set()
  const tags = []
  for (const raw of String(value || '').split(',')) {
    const tag = raw.trim().replace(/\s+/g, ' ').slice(0, 40)
    if (tag && !seen.has(tag.toLowerCase())) {
      seen.add(tag.toLowerCase())
      tags.push(tag)
    }
  }
  return tags.slice(0, 8)
}

export function errorText(err, fallback) {
  const detail = err?.detail
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg
  return err?.message || fallback
}

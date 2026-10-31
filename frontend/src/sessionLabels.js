// Pure helpers for the flat Project → Session sidebar. The stored index still
// nests sessions under their primary label (a task); the sidebar shows every
// label as a chip on the session row instead of as a tree level.

import { CHATS_PROJECT_ID } from './constants.js'

// Placeholder task names that carry no meaning as a label.
const UNLABELED_TASKS = new Set(['general', 'unlabelled', 'unlabeled', 'new task', 'chats'])

export const MODE_LABELS = { native: 'Native', 'codex-app-server': 'Codex', 'claude-agent': 'Claude' }

export function modeLabel(session) {
  return MODE_LABELS[session?.provider || 'native'] || session?.provider || 'Native'
}

// Primary label (unless a placeholder) followed by the session's other labels.
export function sessionLabels(session, taskNames = {}) {
  if (!session) return []
  const labels = []
  const taskName = String(taskNames[session.task_id] || '').trim()
  if (taskName && !UNLABELED_TASKS.has(taskName.toLowerCase())) labels.push(taskName)
  for (const label of session.labels || []) {
    if (label && !labels.some(l => l.toLowerCase() === label.toLowerCase())) labels.push(label)
  }
  return labels
}

// Task names are keyed by task id, which repeats across projects ("new_task").
// Resolve each session's task name inside its own project.
export function taskNamesFor(projects) {
  const byProject = {}
  for (const project of projects || []) {
    const names = {}
    for (const task of project.tasks || []) names[task.id] = task.name
    byProject[project.id] = names
  }
  return byProject
}

export function labelsFor(session, taskNamesByProject) {
  return sessionLabels(session, taskNamesByProject?.[session?.project_id] || {})
}

function byRecency(a, b) {
  const diff = new Date(b.updated_at || 0) - new Date(a.updated_at || 0)
  return diff || String(a.id).localeCompare(String(b.id))
}

export function projectSessionIds(project) {
  return (project?.tasks || []).flatMap(task => task.sessions || [])
}

// A project's threads, newest activity first, or in the order the user dragged
// them into (project.session_order). Threads not in that order yet, such as new
// ones, go on top, newest first.
export function projectSessions(project, sessionsById, { archived = false } = {}) {
  const sessions = projectSessionIds(project)
    .map(id => sessionsById?.[id])
    .filter(s => s && !!s.archived === archived)
    .sort(byRecency)
  const order = Array.isArray(project?.session_order) ? project.session_order : []
  if (archived || order.length === 0) return sessions
  const rank = new Map(order.map((id, index) => [id, index]))
  const unplaced = sessions.filter(s => !rank.has(s.id))
  const placed = sessions.filter(s => rank.has(s.id)).sort((a, b) => rank.get(a.id) - rank.get(b.id))
  return [...unplaced, ...placed]
}

/** `ids` with `movedId` placed before/after `targetId`, or at the end when no target. */
export function reorderIds(ids, movedId, targetId = null, place = 'after') {
  const rest = ids.filter(id => id !== movedId)
  if (!targetId || !rest.includes(targetId)) return [...rest, movedId]
  const index = rest.indexOf(targetId) + (place === 'before' ? 0 : 1)
  return [...rest.slice(0, index), movedId, ...rest.slice(index)]
}

// Every non-archived session across projects and chats, newest first.
export function activitySessions(sessionsById) {
  return Object.values(sessionsById || {})
    .filter(s => s && s.id && !s.archived)
    .sort(byRecency)
}

export function archivedSessions(sessionsById) {
  return Object.values(sessionsById || {})
    .filter(s => s && s.id && s.archived)
    .sort(byRecency)
}

// Labels (and modes) in use, most used first; labels feed the editor suggestions.
export function filterOptions(sessionsById, taskNamesByProject) {
  const counts = new Map()
  const modes = new Map()
  for (const s of Object.values(sessionsById || {})) {
    if (!s || s.archived) continue
    for (const label of labelsFor(s, taskNamesByProject)) counts.set(label, (counts.get(label) || 0) + 1)
    const mode = modeLabel(s)
    modes.set(mode, (modes.get(mode) || 0) + 1)
  }
  const sorted = map => [...map.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([name]) => name)
  return { labels: sorted(counts), modes: modes.size > 1 ? sorted(modes) : [] }
}

// filter: { query, label, mode } — all optional, all must match.
export function matchesFilter(session, filter, taskNamesByProject, projectName = '') {
  if (!session) return false
  const { query = '', label = '', mode = '' } = filter || {}
  const labels = labelsFor(session, taskNamesByProject)
  if (label && !labels.includes(label)) return false
  if (mode && modeLabel(session) !== mode) return false
  if (!query) return true
  const q = query.toLowerCase()
  return (session.title || '').toLowerCase().includes(q) ||
    projectName.toLowerCase().includes(q) ||
    labels.some(l => l.toLowerCase().includes(q))
}

export function isChatSession(session) {
  return session?.kind === 'chat' || session?.project_id === CHATS_PROJECT_ID
}

// Text copied by "Copy session reference": "<Project> / <Session> — session_id: <id>".
export function sessionReference(session, projectNames = {}) {
  const title = session.title || String(session.id).slice(0, 8)
  const place = isChatSession(session) ? 'Chat' : (projectNames[session.project_id] || session.project_id)
  return `${place} / ${title}`.replace(/\s+/g, ' ') + ` — session_id: ${session.id}`
}

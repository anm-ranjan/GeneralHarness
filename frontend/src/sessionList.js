// Pure helpers for the sidebar's Activity and Archived views, the session
// context menu, and the header breadcrumb. The Project → Label → Session tree
// itself is unchanged; these only add flat, recency-ordered views over it.

import { CHATS_PROJECT_ID } from './constants.js'

function byRecency(a, b) {
  const diff = new Date(b.updated_at || 0) - new Date(a.updated_at || 0)
  return diff || String(a.id).localeCompare(String(b.id))
}

export function isChatSession(session) {
  return session?.kind === 'chat' || session?.project_id === CHATS_PROJECT_ID
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

// The tree with archived sessions removed from every label, so the existing
// project/label components render only live sessions.
export function withoutArchived(projects, sessionsById) {
  return (projects || []).map(project => ({
    ...project,
    tasks: (project.tasks || []).map(task => ({
      ...task,
      sessions: (task.sessions || []).filter(id => !sessionsById?.[id]?.archived),
    })),
  }))
}

// Live sessions of one project, newest first.
export function projectSessions(project, sessionsById) {
  const ids = new Set((project?.tasks || []).flatMap(task => task.sessions || []))
  return [...ids].map(id => sessionsById?.[id]).filter(s => s && !s.archived).sort(byRecency)
}

// "Project · Label" for flat lists; chats are project-less.
export function sessionPlace(session, projectNames = {}, taskNames = {}) {
  if (isChatSession(session)) return 'Chat'
  const project = projectNames[session.project_id] || session.project_id || ''
  const label = taskNames[session.task_id] || ''
  return label ? `${project} · ${label}` : project
}

// Text copied by "Copy session reference".
export function sessionReference(session, projectNames = {}, taskNames = {}) {
  const title = session.title || String(session.id).slice(0, 8)
  const place = isChatSession(session)
    ? 'Chat'
    : [projectNames[session.project_id] || session.project_id, taskNames[session.task_id] || session.task_id].filter(Boolean).join(' / ')
  return `${place} / ${title}`.replace(/\s+/g, ' ') + ` — session_id: ${session.id}`
}

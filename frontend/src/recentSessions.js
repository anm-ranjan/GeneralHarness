// Pure helpers for the splash-screen recent-session list, kept out of the React
// component so they can be unit-tested with node:test like the other frontend modules.

import { CHATS_PROJECT_ID } from './constants.js'

export const RECENT_SESSION_LIMIT = 5

// Human label for where a session lives: chats are project-less, so they get a flat
// "Chat" label while project sessions show "<project> / <task>". Falls back to the raw
// ids when the tree has not caught up with a session's project/task yet.
function contextLabel(session, projects) {
  if (session.kind === 'chat' || session.project_id === CHATS_PROJECT_ID) return 'Chat'
  const project = (projects || []).find(p => p.id === session.project_id)
  if (!project) return session.project_id || ''
  const task = (project.tasks || []).find(t => t.id === session.task_id)
  return task ? `${project.name} / ${task.name}` : project.name
}

// The most recently touched sessions across every project plus chats, newest first.
// `updated_at` is the store's own metadata timestamp, which is what the sidebar already
// sorts chats by, so ordering here matches what the user sees there.
export function recentSessions(sessionsById, projects, limit = RECENT_SESSION_LIMIT) {
  return Object.values(sessionsById || {})
    .filter(session => session && session.id)
    .sort((a, b) => {
      const diff = new Date(b.updated_at || 0) - new Date(a.updated_at || 0)
      // Stable tiebreak so equal timestamps do not reshuffle between renders.
      return diff || String(a.id).localeCompare(String(b.id))
    })
    .slice(0, Math.max(0, limit))
    .map(session => ({
      id: session.id,
      title: session.title || String(session.id).slice(0, 8),
      kind: session.kind || 'project',
      provider: session.provider || 'native',
      status: session.status || 'idle',
      contextLabel: contextLabel(session, projects),
      updatedAt: session.updated_at || '',
    }))
}

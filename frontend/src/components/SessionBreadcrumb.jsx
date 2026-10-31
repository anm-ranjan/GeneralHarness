import { useEffect, useMemo, useRef, useState } from 'react'
import { useAppDispatch, useAppSelector } from '../context/AppContext'
import useSelectSession from '../hooks/useSelectSession'
import useSessionTree from '../hooks/useSessionTree'
import { CHATS_PROJECT_ID } from '../constants'
import { relativeTime } from '../utils'
import { isChatSession, labelsFor, projectSessions, taskNamesFor } from '../sessionLabels'

const selectProjects = state => state.projects
const selectSessionsById = state => state.sessionsById

// "Project / Thread" header. Clicking either part opens a list: projects jump
// to their most recent thread, threads switch within the current project.
export default function SessionBreadcrumb({ meta }) {
  const dispatch = useAppDispatch()
  const projects = useAppSelector(selectProjects)
  const sessionsById = useAppSelector(selectSessionsById)
  const selectSession = useSelectSession()
  const tree = useSessionTree()
  const [open, setOpen] = useState(null)
  const isChat = isChatSession(meta)
  const projectId = isChat ? CHATS_PROJECT_ID : meta.project_id
  const project = projects.find(p => p.id === projectId)
  const projectName = isChat ? 'Chats' : (project?.name || meta.project_id)
  const sessionTitle = meta.title || meta.id?.slice(0, 8)
  const taskNamesByProject = useMemo(() => taskNamesFor(projects), [projects])

  const projectItems = useMemo(() => {
    const ordered = [...projects.filter(p => p.id !== CHATS_PROJECT_ID), ...projects.filter(p => p.id === CHATS_PROJECT_ID)]
    return ordered.map(p => {
      const sessions = projectSessions(p, sessionsById)
      return {
        id: p.id,
        label: p.id === CHATS_PROJECT_ID ? 'Chats' : p.name,
        detail: sessions.length ? relativeTime(sessions[0].updated_at) : 'empty',
        title: p.root || p.name,
        current: p.id === projectId,
        latest: sessions[0]?.id || '',
      }
    })
  }, [projects, sessionsById, projectId])

  const sessionItems = useMemo(() => {
    const sessions = project ? projectSessions(project, sessionsById) : []
    if (meta.archived && !sessions.some(s => s.id === meta.id)) sessions.unshift(meta)
    return sessions.map(s => ({
      id: s.id,
      label: s.title || s.id.slice(0, 8),
      detail: relativeTime(s.updated_at),
      tags: labelsFor(s, taskNamesByProject),
      title: s.title || s.id,
      current: s.id === meta.id,
    }))
  }, [project, sessionsById, meta, taskNamesByProject])

  async function chooseProject(item) {
    setOpen(null)
    if (item.current) return
    try {
      if (item.latest) {
        await selectSession(item.latest)
      } else if (item.id !== CHATS_PROJECT_ID) {
        const created = await tree.createSession(item.id, '', '', meta.provider || 'native')
        await selectSession(created.id)
      }
    } catch (err) {
      dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: err.detail || err.message } })
    }
  }

  function chooseSession(item) {
    setOpen(null)
    if (!item.current) selectSession(item.id).catch(err => console.error('Failed to select session:', err))
  }

  return (
    // Both labels truncate inside their wrappers (a button only truncates as a
    // block bounded by its parent); the project gives way first, the title
    // keeps a few words. Not overflow-hidden: the pickers drop down from here.
    <nav className="flex items-baseline gap-1.5 min-w-0" aria-label="Session location">
      <div className="relative min-w-[5rem] max-w-[32rem] shrink">
        <button
          type="button"
          onClick={() => setOpen(v => v === 'session' ? null : 'session')}
          aria-haspopup="listbox"
          aria-expanded={open === 'session'}
          className={`block max-w-full truncate rounded px-1 -mx-1 font-display text-[20px] font-bold leading-tight text-text-bright hover:bg-surface-hover transition-colors ${open === 'session' ? 'bg-surface-hover' : ''}`}
          title={`${sessionTitle} — switch thread`}
        >
          {sessionTitle}
        </button>
        {open === 'session' && (
          <BreadcrumbMenu items={sessionItems} onChoose={chooseSession} onClose={() => setOpen(null)} filterPlaceholder="Find thread" />
        )}
      </div>
      <span className="shrink-0 text-[13px] text-faint select-none">in</span>
      <div className="relative min-w-[2.5rem] max-w-[16rem] shrink-[4]">
        <button
          type="button"
          onClick={() => setOpen(v => v === 'project' ? null : 'project')}
          aria-haspopup="listbox"
          aria-expanded={open === 'project'}
          className={`block max-w-full truncate rounded px-1 -mx-1 text-[13px] text-muted hover:text-text-bright hover:bg-surface-hover transition-colors ${open === 'project' ? 'bg-surface-hover text-text-bright' : ''}`}
          title={`${projectName} — switch project`}
        >
          {projectName}
        </button>
        {open === 'project' && (
          <BreadcrumbMenu items={projectItems} onChoose={chooseProject} onClose={() => setOpen(null)} filterPlaceholder="Find project" />
        )}
      </div>
    </nav>
  )
}

function BreadcrumbMenu({ items, onChoose, onClose, filterPlaceholder }) {
  const ref = useRef(null)
  const inputRef = useRef(null)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const showFilter = items.length > 8
  const q = query.trim().toLowerCase()
  const visible = q
    ? items.filter(item => item.label.toLowerCase().includes(q) || (item.tags || []).some(t => t.toLowerCase().includes(q)))
    : items

  useEffect(() => {
    const current = Math.max(0, visible.findIndex(item => item.current))
    setActive(q ? 0 : current)
  }, [q])

  useEffect(() => {
    (inputRef.current || ref.current)?.focus()
    function onPointer(e) { if (!ref.current?.parentElement?.contains(e.target)) onClose() }
    window.addEventListener('mousedown', onPointer, true)
    return () => window.removeEventListener('mousedown', onPointer, true)
  }, [onClose])

  useEffect(() => {
    ref.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [active])

  function onKeyDown(e) {
    if (e.key === 'Escape') { e.preventDefault(); onClose(); return }
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(i => Math.min(i + 1, visible.length - 1)) }
    if (e.key === 'ArrowUp') { e.preventDefault(); setActive(i => Math.max(i - 1, 0)) }
    if (e.key === 'Enter' && visible[active]) { e.preventDefault(); onChoose(visible[active]) }
  }

  return (
    <div
      ref={ref}
      tabIndex={-1}
      onKeyDown={onKeyDown}
      className="absolute left-0 top-full mt-1 z-40 w-[22rem] max-w-[80vw] rounded-lg border border-line bg-panel shadow-2xl text-[13px] font-normal outline-none"
    >
      {showFilter && (
        <div className="p-1.5 border-b border-line">
          <input
            ref={inputRef}
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder={filterPlaceholder}
            className="w-full bg-surface border border-line rounded px-2 py-1 text-[12px] text-text-bright outline-none focus:border-accent/60"
          />
        </div>
      )}
      <ul role="listbox" className="max-h-[50vh] overflow-y-auto py-1">
        {visible.length === 0 && <li className="px-3 py-1.5 text-faint italic">No matches.</li>}
        {visible.map((item, i) => (
          <li
            key={item.id}
            role="option"
            aria-selected={item.current}
            data-index={i}
            title={item.title}
            onMouseEnter={() => setActive(i)}
            onClick={() => onChoose(item)}
            className={`flex items-center gap-2 px-3 py-1.5 cursor-pointer ${i === active ? 'bg-surface-hover' : ''} ${item.current ? 'text-accent' : 'text-text-default'}`}
          >
            <span className="min-w-0 flex-1">
              <span className="block truncate">{item.label}</span>
              {item.tags?.length > 0 && (
                <span className="block truncate text-[11.5px] text-faint">{item.tags.join(', ')}</span>
              )}
            </span>
            <span className="shrink-0 text-[11px] text-faint">{item.detail}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

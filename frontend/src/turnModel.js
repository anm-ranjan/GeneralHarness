// Turns the flat transcript into conversation turns, and turns each turn's tool
// calls into short plain-language notes for the margin. Pure functions only:
// the components just render what this returns.

const TEST_COMMAND = /\b(pytest|py\.test|unittest|vitest|jest|mocha|node --test|npm (run )?test|yarn test|pnpm test|cargo test|go test|ctest|tox)\b/i
const SILENT_TOOLS = new Set(['plan_update', 'ask_user', 'shell_check', 'shell_kill', 'skill_list', 'list_agents', 'wait_agent'])

export function baseName(path = '') {
  const clean = String(path).replace(/[\\/]+$/, '')
  return clean.split(/[\\/]/).pop() || clean
}

// Codex runs its own tools; present them in the same shape as native ones so
// the notes read the same whichever provider did the work.
function codexTool(item) {
  if (item.type === 'codex_command' && item.tool) {
    // A provider reporting its own call in native shape (Claude's Read, Grep, ...).
    const done = item.status === 'done' || item.status === 'completed'
    return {
      name: item.tool,
      statusLine: item.label || item.command,
      status: item.status === 'failed' ? 'failed' : done ? 'done' : 'running',
      callId: item.callId || '',
    }
  }
  if (item.type === 'codex_command') {
    return {
      name: 'shell_run',
      statusLine: `Run: ${item.command}`,
      status: item.status === 'done' || item.status === 'completed' ? 'done' : 'running',
    }
  }
  return { name: 'file_replace', statusLine: `Update ${baseName(item.path)}`, status: 'done' }
}

/**
 * Split stage items into turns. A turn starts at each non-steering user
 * message; items before the first one form a leading turn with no user.
 * Thinking and tool activity go to the notes side, everything else stays in the
 * conversation column.
 */
export function buildTurns(items) {
  const turns = []
  let turn = null
  const open = user => {
    turn = { id: user?._id || `turn_${turns.length}`, user, talk: [], thinking: [], modelNotes: [], tools: [], anchors: [], startTime: null, endReason: null }
    turns.push(turn)
  }
  for (const item of items) {
    if (item.type === 'user_message' && !item.steering) { open(item); continue }
    if (!turn) open(null)
    switch (item.type) {
      case 'work_group':
        turn.tools.push(...item.tools)
        turn.anchors.push(item)
        if (turn.startTime == null && item.startTime) turn.startTime = item.startTime
        break
      case 'codex_command':
      case 'codex_file_change': {
        const tool = codexTool(item)
        // The same call reports again as it starts, gets its input and ends:
        // that updates one entry rather than adding three.
        const existing = tool.callId ? turn.tools.find(entry => entry.callId === tool.callId) : null
        if (existing) {
          existing.status = tool.status
          existing.statusLine = tool.statusLine
          existing.name = tool.name
          break
        }
        turn.tools.push(tool)
        turn.anchors.push(item)
        break
      }
      case 'thinking':
      case 'thinking_stream':
        turn.thinking.push(item)
        break
      case 'turn_note':
        turn.modelNotes.push(item)
        break
      // How the run ended: bookkeeping for the prompt actions, never drawn.
      case 'run_end':
        turn.endReason = item.reason
        break
      default:
        turn.talk.push(item)
    }
  }
  return turns
}

function commandOf(tool) {
  const match = /^Run(?: \(background\))?: ([\s\S]*)$/.exec(tool.statusLine || '')
  return match ? match[1].trim() : ''
}

function fileOf(tool, verb) {
  const match = new RegExp(`^${verb} (.+?)(?: \\([^)]*\\))?$`).exec(tool.statusLine || '')
  return match ? match[1] : ''
}

function hostOf(tool) {
  const url = (/^Fetch (.+)$/.exec(tool.statusLine || '') || [])[1] || ''
  try { return new URL(url).host } catch { return url.split('/')[0] }
}

function unique(list) { return [...new Set(list.filter(Boolean))] }

function nameList(names, noun = 'files') {
  return names.length <= 3 ? names.join(', ') : `${names.length} ${noun}`
}

function count(preview, word) {
  const match = new RegExp(`(\\d+)\\s+${word}`, 'i').exec(preview || '')
  return match ? Number(match[1]) : 0
}

function testNote(runs) {
  // Collapse repeats so "failed, failed, passed" reads "failed, then passed".
  const steps = []
  for (const run of runs) {
    const label = run.ok ? 'passed' : 'failed'
    if (steps[steps.length - 1] !== label) steps.push(label)
  }
  const last = runs[runs.length - 1]
  const passed = count(last.preview, 'passed')
  const failed = count(last.preview, 'failed')
  const detail = last.ok ? (passed ? ` (${passed} passed)` : '') : (failed ? ` (${failed} failed)` : '')
  const text = steps.length === 1
    ? `Tests ${steps[0]}${detail}.`
    : `Tests ${steps.join(', then ')}${detail}.`
  return { kind: 'tests', text, tone: last.ok ? '' : 'bad' }
}

/**
 * Plain-language notes for finished tool calls. Running calls are left out (the
 * live "now" line covers them) and so are interrupted ones. Each note is
 * `{ kind, text, tone, files? }`; `files` lets the UI link to a diff.
 */
export function summarizeTools(tools) {
  const done = tools.filter(tool => tool.status === 'done' || tool.status === 'failed')
  const groups = new Map()
  const add = (kind, fn) => {
    if (!groups.has(kind)) groups.set(kind, [])
    fn(groups.get(kind))
  }

  for (const tool of done) {
    const failed = tool.status === 'failed'
    const name = tool.name
    if (SILENT_TOOLS.has(name)) continue
    if (name === 'file_read' || name === 'image_read') {
      add('read', list => list.push(fileOf(tool, name === 'file_read' ? 'Read' : 'Inspect image')))
    } else if (name === 'file_search' || name === 'content_search' || name === 'file_list') {
      add('search', list => list.push(name))
    } else if (name === 'file_write' || name === 'file_replace' || name === 'apply_patch') {
      const file = name === 'file_write' ? fileOf(tool, 'Write') : fileOf(tool, 'Update')
      add(failed ? 'edit_failed' : 'edit', list => list.push({ file, patch: name === 'apply_patch' }))
    } else if (name === 'shell_run') {
      const command = commandOf(tool)
      const ok = !failed
      if (TEST_COMMAND.test(command)) add('tests', list => list.push({ ok, preview: tool.resultPreview }))
      else add('commands', list => list.push({ command, ok }))
    } else if (name === 'web_fetch' || name === 'web_request') {
      add('web', list => list.push(hostOf(tool)))
    } else if (name === 'skill_read') {
      add('skill', list => list.push((/^Skill: (.+)$/.exec(tool.statusLine || '') || [])[1] || 'a skill'))
    } else if (name === 'spawn_agent') {
      add('agent', list => list.push(name))
    } else {
      add('other', list => list.push(name))
    }
  }

  const notes = []
  for (const [kind, list] of groups) {
    if (kind === 'read') {
      const names = unique(list)
      notes.push({ kind, tone: '', text: names.length ? `Read ${nameList(names)}.` : 'Read some files.', files: names.length <= 3 ? names : [] })
    } else if (kind === 'search') {
      notes.push({ kind, tone: '', text: list.length > 1 ? `Searched the code ${list.length} times.` : 'Searched the code.' })
    } else if (kind === 'edit') {
      const names = unique(list.map(entry => entry.file))
      const text = names.length
        ? `Changed ${nameList(names)}.`
        : (list.some(entry => entry.patch) ? 'Applied a patch.' : 'Changed some files.')
      notes.push({ kind, tone: '', text, files: names })
    } else if (kind === 'edit_failed') {
      const names = unique(list.map(entry => entry.file))
      notes.push({ kind, tone: 'bad', text: names.length ? `Could not change ${nameList(names)}.` : 'A file change failed.' })
    } else if (kind === 'tests') {
      notes.push(testNote(list))
    } else if (kind === 'commands') {
      const failed = list.filter(entry => !entry.ok)
      if (list.length <= 2) {
        for (const entry of list) {
          const shown = entry.command.length > 48 ? `${entry.command.slice(0, 45)}...` : entry.command
          notes.push({ kind, tone: entry.ok ? '' : 'bad', text: entry.ok ? `Ran ${shown}.` : `${shown} exited with an error.`, code: shown })
        }
      } else {
        notes.push({ kind, tone: '', text: `Ran ${list.length} commands.` })
        if (failed.length) notes.push({ kind, tone: 'bad', text: `${failed.length} of them exited with an error.` })
      }
    } else if (kind === 'web') {
      const hosts = unique(list)
      notes.push({ kind, tone: '', text: hosts.length === 1 ? `Fetched ${hosts[0]}.` : `Fetched ${hosts.length} pages.` })
    } else if (kind === 'skill') {
      notes.push({ kind, tone: '', text: `Used ${nameList(unique(list), 'skills')}.` })
    } else if (kind === 'agent') {
      notes.push({ kind, tone: '', text: list.length > 1 ? `Started ${list.length} helper agents.` : 'Started a helper agent.' })
    } else {
      notes.push({ kind, tone: '', text: `Used ${unique(list).join(', ')}.` })
    }
  }
  return notes
}

const NOW_VERBS = [
  [/^Read /, 'Reading '],
  [/^Inspect image /, 'Looking at '],
  [/^Write /, 'Writing '],
  [/^Update /, 'Editing '],
  [/^Apply patch/, 'Applying a patch'],
  [/^List /, 'Listing '],
  [/^Search /, 'Searching '],
  [/^Grep /, 'Searching '],
  [/^Run \(background\): /, 'Running '],
  [/^Run: /, 'Running '],
  [/^Fetch /, 'Fetching '],
  [/^Skill: /, 'Using skill '],
]

/** "Update Sidebar.jsx" -> { verb: 'Editing', rest: 'Sidebar.jsx' } for the live line. */
export function nowLine(tools) {
  const running = [...tools].reverse().find(tool => tool.status === 'running')
  if (!running) return null
  const line = running.statusLine || running.name
  for (const [pattern, verb] of NOW_VERBS) {
    if (pattern.test(line)) {
      const rest = line.replace(pattern, '')
      return { verb: verb.trim(), rest: verb.endsWith(' ') ? rest : '' }
    }
  }
  return { verb: line, rest: '' }
}

/** Milliseconds a turn's tool work took, from the first call to the last result. */
export function turnDurationMs(tools) {
  let start = Infinity
  let end = 0
  for (const tool of tools) {
    if (!tool.startedAt) continue
    start = Math.min(start, tool.startedAt)
    end = Math.max(end, tool.startedAt + (tool.durationMs || 0))
  }
  return Number.isFinite(start) && end > start ? end - start : 0
}

export function formatElapsed(ms) {
  const secs = Math.max(0, Math.round(ms / 1000))
  if (secs < 60) return `${secs} s`
  const mins = Math.floor(secs / 60)
  return `${mins} min ${secs % 60} s`
}

// When the model wrote its own notes they carry the story (what was read,
// searched, found). Only facts the user must be able to trust or act on are
// still derived from the tool calls: test results, changed files with their
// diff links, and anything that failed.
const FACT_KINDS = new Set(['tests', 'edit', 'edit_failed'])

export function notesForTurn(turn) {
  const rules = summarizeTools(turn.tools)
  if (!turn.modelNotes.length) return rules
  const written = turn.modelNotes.map(item => ({ kind: 'model', tone: '', text: item.text }))
  return [...written, ...rules.filter(note => FACT_KINDS.has(note.kind) || note.tone === 'bad')]
}

// Mirrors UNANSWERED_REASONS in backend/turn_delete.py: runs that were stopped
// or failed, whose turn the run loop rolls back out of the model context.
const UNFINISHED_REASONS = new Set(['interrupted', 'error', 'api timeout'])

/**
 * The latest turn when its prompt never got a final answer: nothing running,
 * and either the run was stopped/failed (interim text does not count) or it
 * produced no assistant text at all. A run that ended normally without a reply
 * (max iterations, no content) stays in the model's context and is excluded.
 * The server makes the real decision; this only decides whether to offer the action.
 */
export function unansweredLastTurn(turns, running) {
  if (running || !turns.length) return null
  const last = turns[turns.length - 1]
  if (!last.user) return null
  if (UNFINISHED_REASONS.has(last.endReason)) return last
  if (last.endReason) return null
  const replied = last.talk.some(item => (
    (item.type === 'assistant_message' && String(item.markdown || '').trim())
    || item.type === 'assistant_stream'
  ))
  return replied ? null : last
}

function sameItems(a, b) {
  return a.length === b.length && a.every((item, i) => item === b[i])
}

/** Memo comparator: stage items are immutable once appended, so identity is enough. */
export function sameTurn(a, b) {
  return a.user === b.user
    && a.endReason === b.endReason
    && sameItems(a.talk, b.talk)
    && sameItems(a.thinking, b.thinking)
    && sameItems(a.modelNotes, b.modelNotes)
    && sameItems(a.anchors, b.anchors)
    && a.tools.length === b.tools.length
    && a.tools.every((tool, i) => tool === b.tools[i])
}

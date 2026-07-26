export const SLASH_CMDS = ['/verbose', '/approve', '/clear', '/chdir', '/model', '/maxiters', '/thinking', '/skills', '/reconnect']

// Reserved project id that holds general (project-less) chat sessions.
export const CHATS_PROJECT_ID = '__chats__'

export const SUGGESTED_SLASH_COMMANDS = [
  '/model native',
  '/model codex',
  '/model claude',
  '/approve shell_only',
  '/approve auto_approve',
  '/verbose',
  '/clear',
  '/chdir ./subdir',
  '/chdir --reset',
  '/maxiters 20',
  '/thinking medium',
  '/skills',
  '/skills frontend-design',
  '/reconnect',
]

// Detect macOS so keyboard hints match the platform: ⌘ on Mac, Ctrl elsewhere.
export const IS_MAC = typeof navigator !== 'undefined' &&
  /mac/i.test(navigator.platform || navigator.userAgentData?.platform || navigator.userAgent || '')

// Modifier symbol/word for the primary command key (Search uses metaKey || ctrlKey).
export const MOD_KEY_LABEL = IS_MAC ? '⌘' : 'Ctrl+'

export function shortcutLabel(key) {
  return `${MOD_KEY_LABEL}${key}`
}

export function isSlashCommand(text) {
  const t = (text || '').trim().toLowerCase()
  return SLASH_CMDS.some(cmd => t === cmd || t.startsWith(cmd + ' '))
}

export const TOOL_ICONS = {
  file_read: '📄',
  file_write: '✏️',
  file_replace: '✏️',
  apply_patch: '🩹',
  file_list: '📂',
  file_search: '🔍',
  content_search: '🔍',
  shell_run: '⚡',
  web_request: '↗',
  skill_list: '🧰',
  skill_read: '📘',
  read_file: '📄',
  write_file: '✏️',
  edit_file: '✏️',
  shell: '⚡',
  run_shell: '⚡',
  search: '🔍',
  grep_search: '🔍',
  find_files: '🔍',
  list_directory: '📂',
  default: '⚙️',
}

export function toolIcon(name) {
  return TOOL_ICONS[name] || TOOL_ICONS.default
}

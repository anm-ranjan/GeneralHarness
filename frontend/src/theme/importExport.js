// Import third-party themes so the catalog is not limited to what ships here.
// Three input shapes are accepted:
//   1. A MyHarness palette (what `exportPalette` produces)
//   2. A base16 scheme (base00…base0F, JSON or the flat YAML the schemes ship as)
//   3. A VS Code theme (its `colors` block, plus `type`)

import { appearanceOf, isColor } from './colors.js'

const slug = name => String(name || 'custom')
  .toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'custom'

const hex = value => {
  if (typeof value !== 'string') return null
  const v = value.trim().startsWith('#') ? value.trim() : `#${value.trim()}`
  // VS Code allows #rrggbbaa; drop the alpha since palettes are opaque.
  const opaque = v.length === 9 ? v.slice(0, 7) : v
  return isColor(opaque) ? opaque.toLowerCase() : null
}

/** Parse the flat `key: "value"` YAML that base16 schemes are distributed as. */
function parseFlatYaml(text) {
  const out = {}
  for (const line of String(text).split('\n')) {
    const m = /^\s*([A-Za-z0-9_-]+)\s*:\s*"?([^"#\n]*(?:#[0-9a-fA-F]{3,8})?[^"\n]*)"?\s*$/.exec(line)
    if (!m) continue
    out[m[1]] = m[2].trim().replace(/^"|"$/g, '')
  }
  return out
}

function fromBase16(raw) {
  const b = k => hex(raw[k])
  if (!b('base00') || !b('base05')) return null
  const name = raw.scheme || raw.name || 'Base16'
  return {
    id: `custom-${slug(name)}`,
    name: String(name),
    appearance: appearanceOf(b('base00')),
    bg: b('base00'),
    panel: b('base01') || undefined,
    surface: b('base02') || undefined,
    text: b('base05'),
    muted: b('base04') || undefined,
    faint: b('base03') || undefined,
    accent: b('base0D') || b('base0A'),
    ok: b('base0B') || undefined,
    warn: b('base0A') || undefined,
    danger: b('base08') || undefined,
    info: b('base0C') || undefined,
    syntax: {
      keyword: b('base0E') || undefined,
      string: b('base0B') || undefined,
      number: b('base09') || undefined,
      comment: b('base03') || undefined,
    },
  }
}

function fromVsCode(raw) {
  const c = raw.colors || {}
  const bg = hex(c['editor.background'])
  const fg = hex(c['editor.foreground']) || hex(c['foreground'])
  if (!bg || !fg) return null
  const name = raw.name || 'VS Code theme'
  return {
    id: `custom-${slug(name)}`,
    name: String(name),
    appearance: raw.type === 'light' ? 'light' : (raw.type === 'dark' ? 'dark' : appearanceOf(bg)),
    bg,
    panel: hex(c['sideBar.background']) || hex(c['activityBar.background']) || undefined,
    surface: hex(c['editorWidget.background']) || hex(c['input.background']) || undefined,
    line: hex(c['panel.border']) || hex(c['editorGroup.border']) || undefined,
    text: fg,
    accent: hex(c['focusBorder']) || hex(c['button.background']) || hex(c['textLink.foreground']),
    ok: hex(c['gitDecoration.addedResourceForeground']) || undefined,
    warn: hex(c['editorWarning.foreground']) || undefined,
    danger: hex(c['editorError.foreground']) || hex(c['errorForeground']) || undefined,
    info: hex(c['editorInfo.foreground']) || hex(c['textLink.foreground']) || undefined,
  }
}

function fromMyHarness(raw) {
  if (!hex(raw.bg) || !hex(raw.text)) return null
  const name = raw.name || 'Custom'
  const pick = {}
  for (const key of ['bg', 'panel', 'surface', 'surfaceRaised', 'surfaceHover',
    'text', 'textBright', 'muted', 'faint', 'accent', 'accent2', 'accent3',
    'ok', 'warn', 'danger', 'info']) {
    const value = hex(raw[key])
    if (value) pick[key] = value
  }
  // `line` is the one token allowed to be a raw rgba() string.
  if (typeof raw.line === 'string' && raw.line) pick.line = raw.line
  const syntax = {}
  for (const key of ['keyword', 'string', 'number', 'comment']) {
    const value = hex(raw.syntax?.[key])
    if (value) syntax[key] = value
  }
  return {
    id: raw.id && String(raw.id).startsWith('custom-') ? raw.id : `custom-${slug(name)}`,
    name: String(name),
    appearance: raw.appearance === 'light' || raw.appearance === 'dark'
      ? raw.appearance
      : appearanceOf(pick.bg),
    ...pick,
    ...(Object.keys(syntax).length ? { syntax } : {}),
  }
}

/**
 * Parse pasted theme text into a palette.
 * Returns `{ palette }` or `{ error }` — never throws.
 */
export function importPalette(text) {
  const source = String(text || '').trim()
  if (!source) return { error: 'Nothing to import.' }

  let raw = null
  try {
    raw = JSON.parse(source)
  } catch {
    raw = parseFlatYaml(source)
  }
  if (!raw || typeof raw !== 'object') return { error: 'Could not parse that as JSON or base16 YAML.' }

  const palette = fromBase16(raw) || fromVsCode(raw) || fromMyHarness(raw)
  if (!palette) {
    return { error: 'Unrecognised theme. Expected a base16 scheme, a VS Code theme, or a Harness palette.' }
  }
  // Strip the `undefined`s so the stored palette stays small and derivable.
  return { palette: JSON.parse(JSON.stringify(palette)) }
}

/** Serialize a palette for sharing. */
export function exportPalette(palette) {
  return JSON.stringify(palette, null, 2)
}

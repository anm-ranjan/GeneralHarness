// Theme engine: turns a compact theme spec into the CSS custom properties that
// `index.css` (and therefore every Tailwind utility) reads.
//
// Tailwind v4 compiles `text-accent` to `color: var(--color-accent)` and
// `bg-accent/10` to a color-mix over the same var, so writing these properties
// onto :root at runtime retints the whole app with no rebuild.

import { alpha, appearanceOf, contrastRatio, elevate, isColor, mix } from './colors.js'

export const STORAGE_KEY = 'myharness.theme'
export const CUSTOM_PALETTES_KEY = 'myharness.theme.custom'

export const FONT_PRESETS = [
  {
    id: 'default',
    name: 'DM Sans / IBM Plex Mono',
    sans: '"DM Sans", system-ui, -apple-system, sans-serif',
    mono: '"IBM Plex Mono", "Cascadia Code", "Liberation Mono", Menlo, monospace',
  },
  {
    id: 'inter',
    name: 'Inter / JetBrains Mono',
    sans: '"Inter", system-ui, -apple-system, sans-serif',
    mono: '"JetBrains Mono", "IBM Plex Mono", Menlo, monospace',
  },
  {
    id: 'system',
    name: 'System UI',
    sans: 'system-ui, -apple-system, "Segoe UI", sans-serif',
    mono: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
  },
  {
    id: 'mono',
    name: 'All mono',
    sans: '"IBM Plex Mono", "Cascadia Code", Menlo, monospace',
    mono: '"IBM Plex Mono", "Cascadia Code", Menlo, monospace',
  },
]

export const RADIUS_PRESETS = [
  { id: 'sharp', name: 'Sharp', lg: '8px', md: '5px', sm: '3px' },
  { id: 'default', name: 'Default', lg: '20px', md: '12px', sm: '8px' },
  { id: 'round', name: 'Round', lg: '28px', md: '18px', sm: '12px' },
]

// `opacity` is how far each translucent surface is pushed toward fully opaque.
export const GLASS_PRESETS = [
  { id: 'off', name: 'Off', blur: '0px', saturate: '1', opacity: 1 },
  { id: 'subtle', name: 'Subtle', blur: '12px', saturate: '1.15', opacity: 0.5 },
  { id: 'full', name: 'Full', blur: '28px', saturate: '1.35', opacity: 0 },
]

export const DEFAULT_SPEC = {
  paletteId: 'jarvis',
  accent: null,
  fontId: 'default',
  radiusId: 'default',
  glassId: 'full',
  overrides: {},
}

const byId = (list, id, fallbackIndex) =>
  list.find(item => item.id === id) || list[fallbackIndex]

/** Fill in everything a palette left unspecified, deriving from bg/text/accent. */
export function completePalette(palette) {
  const bg = palette.bg || '#101214'
  const appearance = palette.appearance || appearanceOf(bg)
  const text = palette.text || (appearance === 'dark' ? '#d9dee7' : '#25292f')
  const accent = palette.accent || '#f59e0b'
  const up = amount => elevate(bg, appearance, amount)

  return {
    id: palette.id,
    name: palette.name,
    appearance,
    bg,
    text,
    accent,
    panel: palette.panel || up(0.05),
    surface: palette.surface || up(0.1),
    surfaceRaised: palette.surfaceRaised || up(0.17),
    surfaceHover: palette.surfaceHover || up(0.14),
    line: palette.line || null,
    textBright: palette.textBright || mix(text, appearance === 'dark' ? '#ffffff' : '#000000', 0.45),
    muted: palette.muted || mix(text, bg, 0.34),
    faint: palette.faint || mix(text, bg, 0.58),
    accent2: palette.accent2 || mix(text, bg, 0.68),
    accent3: palette.accent3 || mix(text, bg, 0.52),
    ok: palette.ok || '#22c55e',
    warn: palette.warn || '#fbbf24',
    danger: palette.danger || '#ef4444',
    info: palette.info || '#60a5fa',
    syntax: {
      keyword: palette.syntax?.keyword || '#c792ea',
      string: palette.syntax?.string || '#9ece6a',
      number: palette.syntax?.number || '#ff9e64',
      comment: palette.syntax?.comment || mix(text, bg, 0.58),
    },
  }
}

/**
 * Derive the full CSS custom property map for a palette + spec.
 * Returns plain `{ '--color-bg': '#101214', … }` so it can be diffed in tests.
 */
export function deriveTokens(rawPalette, spec = {}) {
  const p = completePalette(rawPalette)
  const accent = isColor(spec.accent) ? spec.accent : p.accent
  const dark = p.appearance === 'dark'
  const glass = byId(GLASS_PRESETS, spec.glassId, 2)
  const font = byId(FONT_PRESETS, spec.fontId, 0)
  const radius = byId(RADIUS_PRESETS, spec.radiusId, 1)

  // Push translucent surfaces toward opaque as the glass level drops.
  const solidify = a => a + (1 - a) * glass.opacity
  const depth = dark ? 0.35 : 0.06

  const tokens = {
    '--color-bg': p.bg,
    '--color-panel': alpha(p.panel, solidify(0.82)),
    '--color-surface': alpha(p.surface, solidify(0.72)),
    '--color-surface-raised': alpha(p.surfaceRaised, solidify(0.82)),
    '--color-surface-hover': alpha(p.surfaceHover, solidify(0.6)),
    '--color-line': p.line || alpha(p.textBright, dark ? 0.13 : 0.16),
    '--color-line-hover': alpha(accent, 0.34),
    '--color-text-default': p.text,
    '--color-text-bright': p.textBright,
    '--color-muted': p.muted,
    '--color-faint': p.faint,
    '--color-accent': accent,
    '--color-accent-2': p.accent2,
    '--color-accent-3': p.accent3,
    '--color-accent-soft': alpha(accent, 0.12),
    '--color-accent-glow': alpha(accent, 0.1),
    '--color-ok': p.ok,
    '--color-ok-soft': alpha(p.ok, 0.14),
    '--color-warn': p.warn,
    '--color-warn-soft': alpha(p.warn, 0.12),
    '--color-danger': p.danger,
    '--color-danger-soft': alpha(p.danger, 0.12),
    '--color-info': p.info,
    '--color-info-soft': alpha(p.info, 0.1),

    // Surfaces that used to be hardcoded black washes; they have to invert on
    // light palettes or code blocks turn into holes.
    '--color-code-bg': alpha(dark ? '#000000' : '#0b1220', depth),
    '--color-code-head-bg': alpha(dark ? '#000000' : '#0b1220', depth + 0.1),
    '--color-overlay': alpha('#000000', dark ? 0.55 : 0.35),
    '--color-scanline': alpha(dark ? '#ffffff' : '#000000', 0.035),

    '--color-syntax-keyword': p.syntax.keyword,
    '--color-syntax-string': p.syntax.string,
    '--color-syntax-number': p.syntax.number,
    '--color-syntax-comment': p.syntax.comment,

    '--font-sans': font.sans,
    '--font-mono': font.mono,

    '--radius-lg': radius.lg,
    '--radius-md': radius.md,
    '--radius-sm': radius.sm,

    '--glass-blur': glass.blur,
    '--glass-saturate': glass.saturate,

    '--appearance': p.appearance,
  }

  for (const [key, value] of Object.entries(spec.overrides || {})) {
    if (key.startsWith('--') && typeof value === 'string' && value) tokens[key] = value
  }
  return tokens
}

/** Readability report for the picker: flags combinations that fail WCAG AA-ish. */
export function auditTokens(tokens) {
  const bg = tokens['--color-bg']
  const check = (label, key, min) => {
    const ratio = contrastRatio(tokens[key], bg)
    return { label, ratio: Math.round(ratio * 100) / 100, min, ok: ratio >= min }
  }
  return [
    check('Body text', '--color-text-default', 4.5),
    check('Muted text', '--color-muted', 3),
    check('Accent', '--color-accent', 3),
  ]
}

/** Write tokens onto an element's inline style (defaults to :root). */
export function applyTokens(tokens, root) {
  const target = root || (typeof document !== 'undefined' ? document.documentElement : null)
  if (!target?.style) return
  for (const [key, value] of Object.entries(tokens)) {
    target.style.setProperty(key, value)
  }
  const appearance = tokens['--appearance']
  if (appearance && target.setAttribute) target.setAttribute('data-theme', appearance)
}

/** Snapshot the properties a theme owns, so a preview can be rolled back. */
export function snapshotTokens(keys, root) {
  const target = root || (typeof document !== 'undefined' ? document.documentElement : null)
  const snapshot = {}
  if (!target?.style) return snapshot
  for (const key of keys) snapshot[key] = target.style.getPropertyValue(key)
  return snapshot
}

/** Restore a snapshot, clearing properties that were previously unset. */
export function restoreTokens(snapshot, root) {
  const target = root || (typeof document !== 'undefined' ? document.documentElement : null)
  if (!target?.style) return
  for (const [key, value] of Object.entries(snapshot)) {
    if (value) target.style.setProperty(key, value)
    else target.style.removeProperty(key)
  }
}

export function normalizeSpec(spec) {
  const s = spec && typeof spec === 'object' ? spec : {}
  return {
    paletteId: typeof s.paletteId === 'string' ? s.paletteId : DEFAULT_SPEC.paletteId,
    accent: isColor(s.accent) ? s.accent : null,
    fontId: byId(FONT_PRESETS, s.fontId, 0).id,
    radiusId: byId(RADIUS_PRESETS, s.radiusId, 1).id,
    glassId: byId(GLASS_PRESETS, s.glassId, 2).id,
    overrides: s.overrides && typeof s.overrides === 'object' ? { ...s.overrides } : {},
  }
}

function readJson(key, fallback) {
  try {
    const raw = globalThis.localStorage?.getItem(key)
    return raw ? JSON.parse(raw) : fallback
  } catch {
    return fallback
  }
}

function writeJson(key, value) {
  try {
    globalThis.localStorage?.setItem(key, JSON.stringify(value))
  } catch {
    /* private mode or quota: theming is not worth failing the app over */
  }
}

export const loadSpec = () => normalizeSpec(readJson(STORAGE_KEY, null))
export const saveSpec = spec => writeJson(STORAGE_KEY, normalizeSpec(spec))
export const loadCustomPalettes = () => {
  const list = readJson(CUSTOM_PALETTES_KEY, [])
  return Array.isArray(list) ? list.filter(p => p && typeof p.id === 'string') : []
}
export const saveCustomPalettes = list => writeJson(CUSTOM_PALETTES_KEY, list)

import test from 'node:test'
import assert from 'node:assert/strict'

import { alpha, contrastRatio, elevate, isColor, mix, parseHex } from './colors.js'
import { GLASS_PRESETS, applyTokens, auditTokens, completePalette, deriveTokens, normalizeSpec, restoreTokens, snapshotTokens } from './engine.js'
import { PALETTES, findPalette } from './palettes.js'
import { exportPalette, importPalette } from './importExport.js'

test('parseHex handles 3, 6 and 8 digit forms', () => {
  assert.deepEqual(parseHex('#fff'), { r: 255, g: 255, b: 255, a: 1 })
  assert.deepEqual(parseHex('101214'), { r: 16, g: 18, b: 20, a: 1 })
  assert.equal(parseHex('#00000080').a, 128 / 255)
  assert.equal(parseHex('rgb(1,2,3)'), null)
  assert.equal(isColor('#zzzzzz'), false)
})

test('mix and alpha stay in range and emit the right css form', () => {
  assert.equal(mix('#000000', '#ffffff', 0.5), '#808080')
  assert.equal(mix('#000000', '#ffffff', 5), '#ffffff')
  assert.equal(alpha('#ff0000', 0.5), 'rgba(255, 0, 0, 0.5)')
  assert.equal(alpha('#ff0000', 1), '#ff0000')
})

test('elevate lightens dark themes and darkens light ones', () => {
  assert.equal(elevate('#000000', 'dark', 0.5), '#808080')
  assert.equal(elevate('#ffffff', 'light', 0.5), '#808080')
})

test('contrastRatio matches known WCAG values', () => {
  assert.equal(Math.round(contrastRatio('#ffffff', '#000000')), 21)
  assert.equal(Math.round(contrastRatio('#777777', '#777777')), 1)
})

test('the default palette reproduces the original index.css tokens', () => {
  const tokens = deriveTokens(findPalette('ember'), normalizeSpec({}))
  assert.equal(tokens['--color-bg'], '#101214')
  assert.equal(tokens['--color-panel'], 'rgba(22, 24, 27, 0.82)')
  assert.equal(tokens['--color-surface'], 'rgba(31, 35, 40, 0.72)')
  assert.equal(tokens['--color-surface-raised'], 'rgba(42, 47, 54, 0.82)')
  assert.equal(tokens['--color-line'], 'rgba(226, 232, 240, 0.13)')
  assert.equal(tokens['--color-line-hover'], 'rgba(245, 158, 11, 0.34)')
  assert.equal(tokens['--color-accent'], '#f59e0b')
  assert.equal(tokens['--color-accent-soft'], 'rgba(245, 158, 11, 0.12)')
  assert.equal(tokens['--color-accent-glow'], 'rgba(245, 158, 11, 0.1)')
  assert.equal(tokens['--color-ok-soft'], 'rgba(34, 197, 94, 0.14)')
  assert.equal(tokens['--color-danger-soft'], 'rgba(239, 68, 68, 0.12)')
  assert.equal(tokens['--color-label-1'], '#f59e0b')
  assert.equal(tokens['--color-label-2'], '#60a5fa')
  assert.equal(tokens['--color-label-5'], '#c792ea')
  assert.equal(tokens['--font-sans'], '"DM Sans", system-ui, -apple-system, sans-serif')
  assert.equal(tokens['--radius-lg'], '20px')
})

test('every bundled palette derives a complete, valid token set', () => {
  const reference = Object.keys(deriveTokens(findPalette('ember'), normalizeSpec({})))
  for (const palette of PALETTES) {
    const tokens = deriveTokens(palette, normalizeSpec({}))
    assert.deepEqual(Object.keys(tokens), reference, `${palette.id} token keys`)
    for (const [key, value] of Object.entries(tokens)) {
      assert.ok(typeof value === 'string' && value.length > 0, `${palette.id} ${key} empty`)
      assert.ok(!value.includes('undefined') && !value.includes('NaN'), `${palette.id} ${key} = ${value}`)
    }
    assert.ok(['dark', 'light'].includes(tokens['--appearance']))
  }
})

test('bundled palettes keep body text readable on their own background', () => {
  for (const palette of PALETTES) {
    const tokens = deriveTokens(palette, normalizeSpec({}))
    const [body] = auditTokens(tokens)
    assert.ok(body.ratio >= 4.5, `${palette.id} body contrast ${body.ratio}`)
  }
})

test('a minimal palette derives everything from bg/text/accent', () => {
  const p = completePalette({ id: 'x', name: 'X', bg: '#101010', text: '#e0e0e0', accent: '#00ff88' })
  assert.equal(p.appearance, 'dark')
  for (const key of ['panel', 'surface', 'surfaceRaised', 'muted', 'faint', 'textBright']) {
    assert.ok(isColor(p[key]), `${key} = ${p[key]}`)
  }
  assert.ok(isColor(p.syntax.comment))
})

test('accent override wins over the palette accent and retints derivatives', () => {
  const tokens = deriveTokens(findPalette('nord'), normalizeSpec({ accent: '#ff0000' }))
  assert.equal(tokens['--color-accent'], '#ff0000')
  assert.equal(tokens['--color-accent-soft'], 'rgba(255, 0, 0, 0.12)')
  assert.equal(tokens['--color-line-hover'], 'rgba(255, 0, 0, 0.34)')
})

test('glass level pushes surfaces toward opaque', () => {
  const full = deriveTokens(findPalette('ember'), normalizeSpec({ glassId: 'full' }))
  const off = deriveTokens(findPalette('ember'), normalizeSpec({ glassId: 'off' }))
  assert.equal(full['--color-panel'], 'rgba(22, 24, 27, 0.82)')
  assert.equal(off['--color-panel'], '#16181b')
  assert.equal(off['--glass-blur'], '0px')
  assert.equal(GLASS_PRESETS.find(g => g.id === 'off').opacity, 1)
})

test('light palettes invert the code-block wash and the console scanline', () => {
  const dark = deriveTokens(findPalette('ember'), normalizeSpec({}))
  const light = deriveTokens(findPalette('github-light'), normalizeSpec({}))
  assert.equal(dark['--color-scanline'], 'rgba(255, 255, 255, 0.035)')
  assert.equal(light['--color-scanline'], 'rgba(0, 0, 0, 0.035)')
  assert.equal(dark['--color-code-bg'], 'rgba(0, 0, 0, 0.35)')
  assert.equal(light['--color-code-bg'], 'rgba(11, 18, 32, 0.06)')
})

test('overrides are applied last and ignore junk keys', () => {
  const tokens = deriveTokens(findPalette('ember'), normalizeSpec({
    overrides: { '--color-bg': '#123456', 'color-bg': '#ff0000', '--color-ok': '' },
  }))
  assert.equal(tokens['--color-bg'], '#123456')
  assert.equal(tokens['--color-ok'], '#22c55e')
  assert.equal(tokens['color-bg'], undefined)
})

test('normalizeSpec falls back for unknown ids and bad accents', () => {
  const spec = normalizeSpec({ fontId: 'nope', radiusId: 'nope', glassId: 'nope', accent: 'blue' })
  assert.equal(spec.fontId, 'default')
  assert.equal(spec.radiusId, 'default')
  assert.equal(spec.glassId, 'full')
  assert.equal(spec.accent, null)
  assert.equal(normalizeSpec(null).paletteId, 'ember')
})

test('findPalette prefers user palettes and falls back to the default', () => {
  const custom = { id: 'nord', name: 'My Nord', bg: '#000000', text: '#ffffff', accent: '#ff0000' }
  assert.equal(findPalette('nord', [custom]).name, 'My Nord')
  assert.equal(findPalette('does-not-exist').id, 'ember')
})

test('applyTokens writes to a target and snapshots round-trip', () => {
  const store = new Map()
  const attrs = {}
  const fake = {
    style: {
      setProperty: (k, v) => store.set(k, v),
      getPropertyValue: k => store.get(k) || '',
      removeProperty: k => store.delete(k),
    },
    setAttribute: (k, v) => { attrs[k] = v },
  }
  applyTokens({ '--color-bg': '#111111', '--appearance': 'dark' }, fake)
  assert.equal(store.get('--color-bg'), '#111111')
  assert.equal(attrs['data-theme'], 'dark')

  const snap = snapshotTokens(['--color-bg', '--color-accent'], fake)
  applyTokens({ '--color-bg': '#222222', '--color-accent': '#00ff00' }, fake)
  restoreTokens(snap, fake)
  assert.equal(store.get('--color-bg'), '#111111')
  assert.equal(store.has('--color-accent'), false, 'previously unset props are cleared')
})

test('applyTokens is a no-op without a target', () => {
  assert.doesNotThrow(() => applyTokens({ '--color-bg': '#000' }, null))
})

test('imports a base16 scheme from flat yaml', () => {
  const { palette, error } = importPalette([
    'scheme: "Tokyo Night"',
    'author: "someone"',
    'base00: "1a1b26"',
    'base03: "565f89"',
    'base05: "c0caf5"',
    'base08: "f7768e"',
    'base0B: "9ece6a"',
    'base0D: "7aa2f7"',
    'base0E: "bb9af7"',
  ].join('\n'))
  assert.equal(error, undefined)
  assert.equal(palette.name, 'Tokyo Night')
  assert.equal(palette.bg, '#1a1b26')
  assert.equal(palette.accent, '#7aa2f7')
  assert.equal(palette.danger, '#f7768e')
  assert.equal(palette.syntax.keyword, '#bb9af7')
  assert.equal(palette.appearance, 'dark')
  assert.ok(palette.id.startsWith('custom-'))
})

test('imports a base16 scheme from json with # prefixes', () => {
  const { palette } = importPalette(JSON.stringify({
    scheme: 'Light One', base00: '#ffffff', base05: '#222222', base0D: '#0969da',
  }))
  assert.equal(palette.appearance, 'light')
  assert.equal(palette.bg, '#ffffff')
})

test('imports a VS Code theme and drops #rrggbbaa alpha', () => {
  const { palette, error } = importPalette(JSON.stringify({
    name: 'My VS Theme',
    type: 'dark',
    colors: {
      'editor.background': '#0d1117',
      'editor.foreground': '#c9d1d9',
      'sideBar.background': '#010409',
      'focusBorder': '#58a6ffcc',
      'editorError.foreground': '#f85149',
    },
  }))
  assert.equal(error, undefined)
  assert.equal(palette.name, 'My VS Theme')
  assert.equal(palette.bg, '#0d1117')
  assert.equal(palette.accent, '#58a6ff')
  assert.equal(palette.danger, '#f85149')
  assert.equal(palette.panel, '#010409')
})

test('imported palettes derive a full token set', () => {
  const { palette } = importPalette(JSON.stringify({
    name: 'Sparse', colors: { 'editor.background': '#101010', 'editor.foreground': '#eeeeee' },
  }))
  const tokens = deriveTokens(palette, normalizeSpec({}))
  for (const value of Object.values(tokens)) {
    assert.ok(!String(value).includes('undefined'))
  }
})

test('export round-trips back through import', () => {
  const original = completePalette(findPalette('dracula'))
  const { palette, error } = importPalette(exportPalette(original))
  assert.equal(error, undefined)
  assert.equal(palette.bg, original.bg)
  assert.equal(palette.accent, original.accent)
  assert.equal(palette.syntax.keyword, original.syntax.keyword)
})

test('import reports errors instead of throwing', () => {
  assert.match(importPalette('').error, /Nothing/)
  assert.ok(importPalette('%%%not a theme%%%').error)
  assert.ok(importPalette(JSON.stringify({ hello: 'world' })).error)
})

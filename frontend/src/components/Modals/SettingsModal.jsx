import { useEffect, useMemo, useRef, useState } from 'react'
import { useApp } from '../../context/AppContext'
import ThemeSwatch from './ThemeSwatch'
import {
  DEFAULT_SPEC, FONT_PRESETS, GLASS_PRESETS, PALETTES, RADIUS_PRESETS,
  applySpec, auditTokens, completePalette, exportPalette, findPalette,
  importPalette, isColor, loadCustomPalettes, loadSpec, normalizeSpec,
  saveCustomPalettes, tokensForSpec,
} from '../../theme'

// Tokens the advanced editor exposes. Translucent tokens (panel, surface, line)
// are left out: they derive from these, and <input type="color"> cannot show
// alpha anyway.
const ADVANCED_TOKENS = [
  ['--color-bg', 'Background'],
  ['--color-text-default', 'Text'],
  ['--color-text-bright', 'Text bright'],
  ['--color-muted', 'Muted'],
  ['--color-faint', 'Faint'],
  ['--color-accent', 'Accent'],
  ['--color-ok', 'Success'],
  ['--color-warn', 'Warning'],
  ['--color-danger', 'Danger'],
  ['--color-info', 'Info'],
  ['--color-syntax-keyword', 'Keyword'],
  ['--color-syntax-string', 'String'],
  ['--color-syntax-number', 'Number'],
  ['--color-syntax-comment', 'Comment'],
]

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'dark', label: 'Dark' },
  { id: 'light', label: 'Light' },
]

export default function SettingsModal() {
  const { state, dispatch } = useApp()
  const originalSpec = useRef(loadSpec())
  const [spec, setSpec] = useState(originalSpec.current)
  const [customPalettes, setCustomPalettes] = useState(loadCustomPalettes)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [advanced, setAdvanced] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [importText, setImportText] = useState('')
  const [notice, setNotice] = useState(null)

  // Live preview: every edit paints the running app, but nothing is persisted
  // until Done, so Cancel can put the old theme back.
  useEffect(() => {
    applySpec(spec, { persist: false, customPalettes })
  }, [spec, customPalettes])

  function close(save) {
    if (save) applySpec(spec, { customPalettes })
    else applySpec(originalSpec.current, { customPalettes: loadCustomPalettes() })
    dispatch({ type: 'CLOSE_SETTINGS' })
  }

  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape') { e.stopPropagation(); close(false) }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  })

  const patch = update => setSpec(prev => normalizeSpec({ ...prev, ...update }))

  const tokens = useMemo(() => tokensForSpec(spec, customPalettes), [spec, customPalettes])
  const audit = useMemo(() => auditTokens(tokens), [tokens])
  const activePalette = findPalette(spec.paletteId, customPalettes)

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return [...customPalettes, ...PALETTES].filter(p => {
      const appearance = p.appearance || completePalette(p).appearance
      if (filter !== 'all' && appearance !== filter) return false
      return !q || p.name.toLowerCase().includes(q)
    })
  }, [customPalettes, query, filter])

  function saveAsCustom() {
    const base = completePalette(activePalette)
    const name = window.prompt('Name this theme', `${base.name} (custom)`)
    if (!name) return
    const id = `custom-${Date.now().toString(36)}`
    const overrides = spec.overrides || {}
    const fromToken = (key, fallback) => overrides[key] || fallback
    const palette = {
      id,
      name,
      appearance: base.appearance,
      bg: fromToken('--color-bg', base.bg),
      panel: base.panel,
      surface: base.surface,
      line: base.line || undefined,
      text: fromToken('--color-text-default', base.text),
      textBright: fromToken('--color-text-bright', base.textBright),
      muted: fromToken('--color-muted', base.muted),
      faint: fromToken('--color-faint', base.faint),
      accent: spec.accent || fromToken('--color-accent', base.accent),
      ok: fromToken('--color-ok', base.ok),
      warn: fromToken('--color-warn', base.warn),
      danger: fromToken('--color-danger', base.danger),
      info: fromToken('--color-info', base.info),
      syntax: {
        keyword: fromToken('--color-syntax-keyword', base.syntax.keyword),
        string: fromToken('--color-syntax-string', base.syntax.string),
        number: fromToken('--color-syntax-number', base.syntax.number),
        comment: fromToken('--color-syntax-comment', base.syntax.comment),
      },
    }
    const next = [palette, ...customPalettes]
    setCustomPalettes(next)
    saveCustomPalettes(next)
    // The saved palette now carries the tweaks, so drop them from the spec.
    setSpec(normalizeSpec({ ...spec, paletteId: id, accent: null, overrides: {} }))
    setNotice(`Saved “${name}”.`)
  }

  function deleteCustom(id) {
    const next = customPalettes.filter(p => p.id !== id)
    setCustomPalettes(next)
    saveCustomPalettes(next)
    if (spec.paletteId === id) patch({ paletteId: DEFAULT_SPEC.paletteId })
  }

  function doImport() {
    const { palette, error } = importPalette(importText)
    if (error) { setNotice(error); return }
    const unique = { ...palette, id: `${palette.id}-${Date.now().toString(36)}` }
    const next = [unique, ...customPalettes]
    setCustomPalettes(next)
    saveCustomPalettes(next)
    setSpec(normalizeSpec({ ...spec, paletteId: unique.id, accent: null, overrides: {} }))
    setImportText('')
    setImportOpen(false)
    setNotice(`Imported “${unique.name}”.`)
  }

  async function doExport() {
    const base = completePalette(activePalette)
    const text = exportPalette({ ...base, id: base.id, name: base.name })
    try {
      await navigator.clipboard.writeText(text)
      setNotice('Theme JSON copied to clipboard.')
    } catch {
      setImportText(text)
      setImportOpen(true)
      setNotice('Clipboard unavailable — copy the JSON below.')
    }
  }

  function setOverride(key, value) {
    const overrides = { ...(spec.overrides || {}) }
    if (value) overrides[key] = value
    else delete overrides[key]
    patch({ overrides })
  }

  const dirty = spec.accent || Object.keys(spec.overrides || {}).length > 0

  return (
    <div
      className="fixed inset-0 glass-overlay z-50 flex items-center justify-center p-6"
      onClick={e => { if (e.target === e.currentTarget) close(false) }}
    >
      <div className="solid-surface border border-line rounded-lg w-[860px] max-w-full max-h-[86vh] flex flex-col shadow-2xl">
        <div className="flex items-center gap-3 px-5 py-3 border-b border-line">
          <h3 className="text-[15px] font-semibold text-text-bright">Appearance</h3>
          <span className="text-[12px] text-faint">Applies to this browser or desktop app</span>
          <button
            onClick={() => close(false)}
            className="ml-auto px-2 text-muted hover:text-text-bright text-[16px] leading-none"
            title="Cancel (Esc)"
          >
            ×
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
          {/* ── Theme grid ── */}
          <div className="flex items-center gap-2">
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder={`Search ${PALETTES.length + customPalettes.length} themes…`}
              className="flex-1 bg-surface border border-line rounded-md px-3 py-1.5 text-[13px] text-text-bright placeholder:text-faint focus:outline-none focus:border-accent/50"
            />
            <div className="flex gap-1">
              {FILTERS.map(f => (
                <button
                  key={f.id}
                  onClick={() => setFilter(f.id)}
                  className={`px-2.5 py-1.5 text-[12px] border rounded-md transition-colors ${
                    filter === f.id
                      ? 'text-accent border-accent/40 bg-accent-soft'
                      : 'text-muted border-line hover:text-text-bright'
                  }`}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2.5">
            {visible.map(p => (
              <ThemeSwatch
                key={p.id}
                palette={p}
                selected={p.id === spec.paletteId}
                accentOverride={spec.accent}
                onSelect={id => patch({ paletteId: id })}
                onDelete={p.id.startsWith('custom-') ? deleteCustom : null}
              />
            ))}
            {visible.length === 0 && (
              <p className="col-span-full text-[13px] text-faint py-6 text-center">No themes match “{query}”.</p>
            )}
          </div>

          {/* ── Axes ── */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-1 border-t border-line">
            <Field label="Accent">
              <div className="flex items-center gap-2">
                <input
                  type="color"
                  value={isColor(spec.accent) ? spec.accent : (tokens['--color-accent'] || '#f59e0b')}
                  onChange={e => patch({ accent: e.target.value })}
                  className="h-7 w-9 bg-transparent border border-line rounded cursor-pointer"
                />
                <input
                  value={spec.accent || ''}
                  onChange={e => patch({ accent: isColor(e.target.value) ? e.target.value : null })}
                  placeholder={tokens['--color-accent']}
                  className="w-24 bg-surface border border-line rounded px-2 py-1 text-[12px] font-mono text-text-bright focus:outline-none focus:border-accent/50"
                />
                {spec.accent && (
                  <button onClick={() => patch({ accent: null })} className="text-[12px] text-muted hover:text-accent">
                    Use theme's
                  </button>
                )}
              </div>
            </Field>

            <Field label="Typeface">
              <Segmented options={FONT_PRESETS} value={spec.fontId} onChange={id => patch({ fontId: id })} />
            </Field>

            <Field label="Corners">
              <Segmented options={RADIUS_PRESETS} value={spec.radiusId} onChange={id => patch({ radiusId: id })} />
            </Field>

            <Field label="Glass">
              <Segmented options={GLASS_PRESETS} value={spec.glassId} onChange={id => patch({ glassId: id })} />
            </Field>
          </div>

          {/* ── Readability ── */}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px]">
            {audit.map(a => (
              <span key={a.label} className={a.ok ? 'text-muted' : 'text-warn'}>
                {a.label} contrast {a.ratio}:1{a.ok ? '' : ` — below ${a.min}:1`}
              </span>
            ))}
          </div>

          {/* ── Advanced token editor ── */}
          <div className="border-t border-line pt-3">
            <button
              onClick={() => setAdvanced(v => !v)}
              className="text-[12px] text-muted hover:text-text-bright"
            >
              {advanced ? '▾' : '▸'} Customize individual colors
              {dirty ? <span className="text-accent"> · modified</span> : null}
            </button>
            {advanced && (
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2 pt-3">
                {ADVANCED_TOKENS.map(([key, label]) => {
                  const overridden = !!spec.overrides?.[key]
                  return (
                    <label key={key} className="flex items-center gap-2 text-[12px] text-muted">
                      <input
                        type="color"
                        value={normalizeForInput(spec.overrides?.[key] || tokens[key])}
                        onChange={e => setOverride(key, e.target.value)}
                        className="h-6 w-8 shrink-0 bg-transparent border border-line rounded cursor-pointer"
                      />
                      <span className={`truncate ${overridden ? 'text-accent' : ''}`}>{label}</span>
                      {overridden && (
                        <button
                          onClick={() => setOverride(key, null)}
                          title="Reset"
                          className="ml-auto text-faint hover:text-text-bright"
                        >
                          ↺
                        </button>
                      )}
                    </label>
                  )
                })}
              </div>
            )}
          </div>

          {/* ── Import ── */}
          {importOpen && (
            <div className="border-t border-line pt-3 space-y-2">
              <p className="text-[12px] text-muted">
                Paste a base16 scheme, a VS Code theme, or a {state.appName} theme JSON.
              </p>
              <textarea
                value={importText}
                onChange={e => setImportText(e.target.value)}
                rows={6}
                spellCheck={false}
                className="w-full bg-surface border border-line rounded-md px-3 py-2 text-[12px] font-mono text-text-bright focus:outline-none focus:border-accent/50"
              />
              <div className="flex gap-2">
                <button onClick={doImport} className="px-3 py-1 text-[12px] text-accent border border-accent/30 rounded hover:bg-accent-soft">
                  Import
                </button>
                <button onClick={() => setImportOpen(false)} className="px-3 py-1 text-[12px] text-muted border border-line rounded hover:bg-surface">
                  Close
                </button>
              </div>
            </div>
          )}

          {notice && <p className="text-[12px] text-accent">{notice}</p>}
        </div>

        <div className="flex items-center gap-2 px-5 py-3 border-t border-line">
          <button onClick={saveAsCustom} className="px-3 py-1 text-[12px] text-muted border border-line rounded hover:text-accent hover:border-accent/30">
            Save as theme
          </button>
          <button onClick={() => setImportOpen(v => !v)} className="px-3 py-1 text-[12px] text-muted border border-line rounded hover:text-accent hover:border-accent/30">
            Import
          </button>
          <button onClick={doExport} className="px-3 py-1 text-[12px] text-muted border border-line rounded hover:text-accent hover:border-accent/30">
            Export
          </button>
          <button
            onClick={() => setSpec(DEFAULT_SPEC)}
            className="px-3 py-1 text-[12px] text-muted border border-line rounded hover:text-text-bright"
          >
            Reset to default
          </button>
          <div className="ml-auto flex gap-2">
            <button onClick={() => close(false)} className="px-3 py-1 text-[12px] text-muted border border-line rounded hover:bg-surface">
              Cancel
            </button>
            <button onClick={() => close(true)} className="px-4 py-1 text-[12px] font-medium text-accent border border-accent/40 rounded hover:bg-accent-soft">
              Done
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function Field({ label, children }) {
  return (
    <div className="space-y-1.5">
      <div className="text-[11px] uppercase tracking-wide text-faint">{label}</div>
      {children}
    </div>
  )
}

function Segmented({ options, value, onChange }) {
  return (
    <div className="flex flex-wrap gap-1">
      {options.map(o => (
        <button
          key={o.id}
          onClick={() => onChange(o.id)}
          className={`px-2.5 py-1 text-[12px] border rounded-md transition-colors ${
            value === o.id
              ? 'text-accent border-accent/40 bg-accent-soft'
              : 'text-muted border-line hover:text-text-bright'
          }`}
        >
          {o.name}
        </button>
      ))}
    </div>
  )
}

/** <input type="color"> only accepts #rrggbb, so fall back for rgba() tokens. */
function normalizeForInput(value) {
  return isColor(value) && value.startsWith('#') && value.length === 7 ? value : '#000000'
}

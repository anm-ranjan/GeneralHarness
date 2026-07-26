// Public entry point for theming: resolves a spec against the palette catalog
// (plus any user-saved palettes) and writes it to :root.

import { deriveTokens, applyTokens, loadCustomPalettes, loadSpec, normalizeSpec, saveSpec } from './engine.js'
import { findPalette } from './palettes.js'

export * from './engine.js'
export { PALETTES, DEFAULT_PALETTE_ID, findPalette } from './palettes.js'
export { importPalette, exportPalette } from './importExport.js'
export { contrastRatio, isColor } from './colors.js'

/** Resolve a spec to its token map, honouring user-saved palettes. */
export function tokensForSpec(spec, customPalettes = loadCustomPalettes()) {
  const normalized = normalizeSpec(spec)
  return deriveTokens(findPalette(normalized.paletteId, customPalettes), normalized)
}

/** Apply a spec immediately. Pass `persist: false` for live previews. */
export function applySpec(spec, { persist = true, customPalettes, root } = {}) {
  const tokens = tokensForSpec(spec, customPalettes)
  applyTokens(tokens, root)
  if (persist) saveSpec(spec)
  return tokens
}

/** Called once before React mounts so there is no unthemed first paint. */
export function bootstrapTheme(root) {
  applySpec(loadSpec(), { persist: false, root })
}

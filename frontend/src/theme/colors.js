// Small color helpers for the theme engine. Kept dependency-free and pure so
// `node --test` can exercise them without a DOM.

const HEX_RE = /^#?([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i

/** Parse #rgb / #rgba / #rrggbb / #rrggbbaa into {r,g,b,a}. Returns null when
 *  the input is not a hex color. */
export function parseHex(value) {
  if (typeof value !== 'string') return null
  const m = HEX_RE.exec(value.trim())
  if (!m) return null
  let h = m[1]
  if (h.length === 3 || h.length === 4) h = h.split('').map(c => c + c).join('')
  const int = parseInt(h, 16)
  if (h.length === 8) {
    return {
      r: (int >>> 24) & 255,
      g: (int >>> 16) & 255,
      b: (int >>> 8) & 255,
      a: ((int & 255) / 255),
    }
  }
  return { r: (int >> 16) & 255, g: (int >> 8) & 255, b: int & 255, a: 1 }
}

/** True when `value` is a color this module can do math on. */
export function isColor(value) {
  return parseHex(value) !== null
}

const clamp255 = n => Math.max(0, Math.min(255, Math.round(n)))

export function toHex({ r, g, b }) {
  const h = n => clamp255(n).toString(16).padStart(2, '0')
  return `#${h(r)}${h(g)}${h(b)}`
}

/** CSS color string, emitting rgba() only when translucent. */
export function toCss({ r, g, b, a = 1 }) {
  if (a >= 1) return toHex({ r, g, b })
  return `rgba(${clamp255(r)}, ${clamp255(g)}, ${clamp255(b)}, ${Number(a.toFixed(3))})`
}

/** Blend `amount` (0..1) of `top` into `base`, in sRGB. Alpha follows base. */
export function mix(base, top, amount) {
  const a = parseHex(base)
  const b = parseHex(top)
  if (!a || !b) return base
  const t = Math.max(0, Math.min(1, amount))
  return toCss({
    r: a.r + (b.r - a.r) * t,
    g: a.g + (b.g - a.g) * t,
    b: a.b + (b.b - a.b) * t,
    a: a.a,
  })
}

/** Same color at a new alpha. */
export function alpha(color, a) {
  const c = parseHex(color)
  if (!c) return color
  return toCss({ ...c, a: Math.max(0, Math.min(1, a)) })
}

/** WCAG relative luminance (0 = black, 1 = white). */
export function luminance(color) {
  const c = parseHex(color)
  if (!c) return 0
  const ch = v => {
    const s = v / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * ch(c.r) + 0.7152 * ch(c.g) + 0.0722 * ch(c.b)
}

/** WCAG contrast ratio between two opaque colors (1..21). */
export function contrastRatio(fg, bg) {
  const a = luminance(fg)
  const b = luminance(bg)
  const [hi, lo] = a > b ? [a, b] : [b, a]
  return (hi + 0.05) / (lo + 0.05)
}

/** 'dark' when the color reads as a dark surface, else 'light'. */
export function appearanceOf(color) {
  return luminance(color) < 0.22 ? 'dark' : 'light'
}

/** Move a surface color away from the background, i.e. lighter on dark themes
 *  and darker on light themes. `amount` is 0..1. */
export function elevate(color, appearance, amount) {
  return mix(color, appearance === 'dark' ? '#ffffff' : '#000000', amount)
}

// Composer formatting: **bold**, *italic*, ++underline++, ~~strike~~.
//
// Markers are appearance only. The message sent to the model has them
// removed (stripMarkers); the marked text travels separately as display_text
// so the web transcript can render it. Matching is deliberately strict so
// code-like text is left alone: a marker opens only after a non-word
// character and before a non-space, closes only after a non-space and before
// a non-word character, never spans a line, and never applies inside `code`
// or ``` fences. So `**kwargs`, `2*3*4`, `*.py`, `C++` and `__init__` stay
// literal.

export const MARKERS = [
  // Italic toggled inside bold produces ***text***: bold wrapping italic.
  { marker: '***', style: 'bold', inner: 'italic' },
  { marker: '**', style: 'bold' },
  { marker: '++', style: 'underline' },
  { marker: '~~', style: 'strike' },
  { marker: '*', style: 'italic' },
]

const WORD = /[\p{L}\p{N}_]/u
const isWord = ch => ch != null && WORD.test(ch)
const isSpace = ch => ch == null || /\s/.test(ch)
const CODE = /(```[\s\S]*?```|`[^`\n]+`)/

function opens(s, i, marker) {
  if (!s.startsWith(marker, i)) return false
  const before = s[i - 1]
  const after = s[i + marker.length]
  if (isWord(before) || before === marker[0] || before === '\\') return false
  return !isSpace(after) && after !== marker[0]
}

function findClose(s, from, marker) {
  for (let j = from + 1; j < s.length; j++) {
    if (s[j] === '\n') return -1
    if (!s.startsWith(marker, j)) continue
    const before = s[j - 1]
    const after = s[j + marker.length]
    if (isSpace(before) || before === marker[0]) continue
    if (isWord(after) || after === marker[0]) continue
    return j
  }
  return -1
}

function parseInline(s) {
  const nodes = []
  let text = ''
  let i = 0
  while (i < s.length) {
    let hit = null
    for (const { marker, style, inner } of MARKERS) {
      if (!opens(s, i, marker)) continue
      const close = findClose(s, i + marker.length, marker)
      if (close >= 0) { hit = { marker, style, inner, close }; break }
    }
    if (!hit) { text += s[i]; i++; continue }
    if (text) { nodes.push({ text }); text = '' }
    const children = parseInline(s.slice(i + hit.marker.length, hit.close))
    nodes.push({ style: hit.style, children: hit.inner ? [{ style: hit.inner, children }] : children })
    i = hit.close + hit.marker.length
  }
  if (text) nodes.push({ text })
  return nodes
}

/** Parse into nodes: { text } | { code } | { style, children }. */
export function parseFormatted(value) {
  const nodes = []
  // split() with a capture group alternates plain text and code matches.
  String(value || '').split(CODE).forEach((part, index) => {
    if (!part) return
    if (index % 2) nodes.push({ code: part })
    else nodes.push(...parseInline(part))
  })
  return nodes
}

function flatten(nodes) {
  return nodes.map(node => (node.children ? flatten(node.children) : node.code ?? node.text)).join('')
}

/** The message as the model sees it: identical text minus formatting markers. */
export function stripMarkers(value) {
  return flatten(parseFormatted(value))
}

export function hasFormatting(value) {
  return stripMarkers(value) !== String(value || '')
}

/**
 * Request body for a composer message: the model gets the text without
 * markers; the marked text goes along as display_text only when it differs.
 * Slash commands are sent untouched.
 */
export function messageBody(text, attachments = [], mode = 'queue') {
  const body = { text, attachments, mode }
  if (String(text || '').trimStart().startsWith('/')) return body
  const plain = stripMarkers(text)
  if (plain !== text) {
    body.text = plain
    body.display_text = text
  }
  return body
}

/**
 * Wrap the selection in a marker, or unwrap it when already wrapped.
 * Returns { value, start, end } with the new selection.
 */
export function toggleMarker(value, start, end, marker) {
  const m = marker.length
  const inside = value.slice(start, end)
  const outerWrapped = value.slice(start - m, start) === marker && value.slice(end, end + m) === marker
    // An italic toggle must not eat half of a surrounding ** pair.
    && !(marker === '*' && value[start - m - 1] === '*' && value[end + m] === '*' && value.slice(start - 3, start) !== '***')
  if (outerWrapped) {
    return { value: value.slice(0, start - m) + inside + value.slice(end + m), start: start - m, end: end - m }
  }
  if (inside.length > 2 * m && inside.startsWith(marker) && inside.endsWith(marker)) {
    const bare = inside.slice(m, -m)
    return { value: value.slice(0, start) + bare + value.slice(end), start, end: start + bare.length }
  }
  // Markers must touch text: keep surrounding spaces outside the pair.
  const lead = inside.length - inside.trimStart().length
  const trail = inside.length - inside.trimEnd().length
  const core = inside.trim()
  const s = start + lead
  const wrapped = marker + core + marker
  return {
    value: value.slice(0, s) + wrapped + value.slice(end - trail),
    start: s + m,
    end: s + m + core.length,
  }
}

// ── Lists ───────────────────────────────────────────────────────────────

// "(a) ", "(iv) ", "a) ", "i) ", "1. ", "2) ", "- ", "• " at the line start.
const LIST_LINE = /^(\s*)(?:(\()([a-z]+|\d+)\)|([a-z]+|\d+)([.)])|([-•]))(\s+)(.*)$/i
const ROMAN = /^(m{0,3})(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$/i
const ROMAN_VALUES = [[1000, 'm'], [900, 'cm'], [500, 'd'], [400, 'cd'], [100, 'c'], [90, 'xc'], [50, 'l'], [40, 'xl'], [10, 'x'], [9, 'ix'], [5, 'v'], [4, 'iv'], [1, 'i']]

function fromRoman(s) {
  let n = 0
  let rest = s.toLowerCase()
  for (const [value, sym] of ROMAN_VALUES) {
    while (rest.startsWith(sym)) { n += value; rest = rest.slice(sym.length) }
  }
  return n
}

function toRoman(n) {
  let out = ''
  for (const [value, sym] of ROMAN_VALUES) {
    while (n >= value) { out += sym; n -= value }
  }
  return out
}

function nextLetters(s) {
  const lower = s.toLowerCase()
  const chars = lower.split('')
  let k = chars.length - 1
  while (k >= 0 && chars[k] === 'z') { chars[k] = 'a'; k-- }
  if (k < 0) chars.unshift('a')
  else chars[k] = String.fromCharCode(chars[k].charCodeAt(0) + 1)
  return chars.join('')
}

function prevLetter(s) {
  return s.length === 1 && s.toLowerCase() !== 'a' ? String.fromCharCode(s.toLowerCase().charCodeAt(0) - 1) : null
}

function parseListLine(line) {
  const m = LIST_LINE.exec(line)
  if (!m) return null
  const [, indent, open, wrappedLabel, plainLabel, close, bullet, gap, content] = m
  const label = wrappedLabel || plainLabel || ''
  if (label && !/^\d+$/.test(label) && label.length > 1 && !ROMAN.test(label)) return null
  return { indent, open: open || '', label, close: open ? ')' : (close || ''), bullet: bullet || '', gap, content }
}

// "i" after "h" is a letter; on its own, or after "iv", "v" is a numeral.
function isRoman(label, previous) {
  if (!ROMAN.test(label)) return false
  if (label.length > 1) return true
  if (previous) {
    if (prevLetter(label) === previous.toLowerCase()) return false
    if (ROMAN.test(previous) && fromRoman(previous) + 1 === fromRoman(label)) return true
  }
  return label.toLowerCase() === 'i'
}

function nextLabel(label, previous) {
  if (/^\d+$/.test(label)) return String(Number(label) + 1)
  const upper = label === label.toUpperCase()
  const next = isRoman(label, previous) ? toRoman(fromRoman(label) + 1) : nextLetters(label)
  return upper ? next.toUpperCase() : next
}

function previousSiblingLabel(lines, index, item) {
  for (let k = index - 1; k >= 0; k--) {
    const prev = parseListLine(lines[k])
    if (!prev) return null
    if (prev.indent === item.indent && prev.open === item.open && prev.close === item.close) return prev.label
  }
  return null
}

/**
 * Shift+Enter in the composer. Returns { value, cursor } or null to let the
 * newline through unchanged.
 * - On a list line: start the next point with the same indent and style.
 * - On an empty point: remove the marker and end the list.
 * - After a line ending in ":": start an indented line for the first point.
 */
export function continueList(value, cursor) {
  const lineStart = value.lastIndexOf('\n', cursor - 1) + 1
  const nextBreak = value.indexOf('\n', cursor)
  const lineEnd = nextBreak < 0 ? value.length : nextBreak
  const line = value.slice(lineStart, lineEnd)
  const lines = value.slice(0, lineStart).split('\n')
  const item = parseListLine(line)

  if (item) {
    if (!item.content.trim() && cursor === lineEnd) {
      return { value: value.slice(0, lineStart) + value.slice(lineEnd), cursor: lineStart }
    }
    const previous = previousSiblingLabel(lines, lines.length - 1, item)
    const marker = item.bullet || `${item.open}${nextLabel(item.label, previous)}${item.close}`
    const insert = `\n${item.indent}${marker} `
    return { value: value.slice(0, cursor) + insert + value.slice(cursor), cursor: cursor + insert.length }
  }

  const beforeCursor = value.slice(lineStart, cursor)
  if (/:\s*$/.test(beforeCursor) && beforeCursor.trim()) {
    const indent = (/^\s*/.exec(line) || [''])[0] + '   '
    const insert = `\n${indent}`
    return { value: value.slice(0, cursor) + insert + value.slice(cursor), cursor: cursor + insert.length }
  }
  return null
}

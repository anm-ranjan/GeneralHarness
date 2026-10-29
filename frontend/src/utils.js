import { apiUrl } from './api.js'
import { artifactPath } from './artifacts.js'
import katex from 'katex'

export function esc(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

export function truncate(s, n) {
  return s && s.length > n ? s.slice(0, n) + '…' : s || ''
}

export function fmtTokens(n) {
  n = parseInt(n, 10)
  if (isNaN(n)) return n
  return n >= 1000 ? (n / 1000).toFixed(1).replace(/\.0$/, '') + 'k' : String(n)
}

export function relativeTime(dateStr) {
  if (!dateStr) return ''
  const diff = Date.now() - new Date(dateStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'now'
  if (mins < 60) return `${mins}m`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h`
  const days = Math.floor(hrs / 24)
  if (days < 30) return `${days}d`
  const months = Math.floor(days / 30)
  return `${months}mo`
}

export function parseContextUsage(usage) {
  const pctMatch = usage.match(/([\d.]+)%/)
  const numMatch = usage.match(/(\d+)\s*\/\s*(\d+)/)
  return {
    percent: pctMatch ? parseFloat(pctMatch[1]) : 0,
    label: pctMatch ? pctMatch[1] + '%' : usage,
    tokens: numMatch ? `${fmtTokens(numMatch[1])} / ${fmtTokens(numMatch[2])} tokens` : '',
  }
}


export function localImageUrl(src, workspaceRoot) {
  const raw = String(src || '').trim()
  if (!raw || /^https?:\/\//i.test(raw)) return ''
  // Images are served by whichever host owns the workspace, so they carry the
  // active host's base rather than being same-origin relative.
  if (raw.startsWith('/api/')) return apiUrl(raw)

  const versionIndex = raw.search(/[?#]/)
  const sourcePath = versionIndex === -1 ? raw : raw.slice(0, versionIndex)
  const version = versionIndex === -1 ? '' : raw.slice(versionIndex + 1)
  const path = sourcePath.startsWith('file://') ? sourcePath.slice(7) : sourcePath
  const isAbsolute = path.startsWith('/') || /^[A-Za-z]:[\\/]/.test(path) || path.startsWith('\\\\')
  const routedUrl = resolvedPath => {
    const params = new URLSearchParams({ path: resolvedPath })
    if (version) params.set('v', version)
    return apiUrl(`/api/files/image?${params.toString()}`)
  }
  if (isAbsolute) return routedUrl(path)
  if (workspaceRoot && !path.startsWith('http')) {
    const abs = workspaceRoot.replace(/\/+$/, '') + '/' + path
    return routedUrl(abs)
  }
  return ''
}

// Protect math before Markdown parsing so underscores, table pipes and stars
// inside equations never become Markdown. Code and escaped dollars stay literal.
function protectMath(text) {
  const rendered = []
  const source = String(text || '').replace(/\r\n/g, '\n')
  const pattern = /(`{3,}[^\n]*\n[\s\S]*?(?:\n`{3,}[^\n]*(?=\n|$)|$)|`+[^`\n]*`+|\\\$)|\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([^\n]+?)\\\)|(?<![\\\w$])\$(?![\s$])([^\n$]+?)\$(?![\w$])/g
  const protectedText = source.replace(pattern, (match, literal, dollars, brackets, parens, inline) => {
    if (literal) return literal
    const tex = dollars ?? brackets ?? parens ?? inline
    const displayMode = dollars !== undefined || brackets !== undefined
    let html
    try { html = katex.renderToString(tex, { displayMode, throwOnError: true, trust: false, strict: 'ignore', maxExpand: 1000, maxSize: 20 }) }
    catch { html = `<span class="math-source">${esc(match)}</span>` }
    rendered.push(html)
    const marker = `\uE000${rendered.length - 1}\uE001`
    return displayMode ? `\n\n${marker}\n\n` : marker
  })
  return { text: protectedText, restore: html => html.replace(/\uE000(\d+)\uE001/g, (_match, i) => rendered[Number(i)] ?? _match) }
}

// Reports the preview document's height to the chat so the frame fits its content.
const HTML_PREVIEW_SIZER = '<script>(()=>{const post=()=>parent.postMessage({myharnessHtmlPreview:true,height:document.documentElement.scrollHeight},"*");' +
  'addEventListener("load",post);new ResizeObserver(post).observe(document.documentElement);})()</script>'

// Chart.js is bundled (public/vendor) so previews work offline; keep in step
// with CHART_JS_URL in backend/agent/utils.py.
export const CHART_JS_URL = '/vendor/chart-4.5.1.umd.min.js'
// Any remote script URL naming Chart.js core (plugins such as chartjs-adapter-*
// have no "chart.js"/"chart.umd" in their name and are left alone).
const CDN_CHART_JS = /(<script\b[^>]*\bsrc\s*=\s*)(["'])https?:\/\/[^"']*?(?:chart\.js|chart\.umd)[^"']*\2/gi

export function htmlPreviewDocument(source) {
  // Models often reach for a CDN copy out of habit; point it at the bundled one.
  return HTML_PREVIEW_SIZER + String(source).replace(CDN_CHART_JS, `$1$2${CHART_JS_URL}$2`)
}

// `htmlPreview` renders ```html fences as a sandboxed live frame (scripts run
// in an opaque origin with no access to the app) with a toggle back to the code.
export function renderMarkdown(text, workspaceRoot, { htmlPreview = false } = {}) {
  const math = protectMath(text)
  const lines = math.text
    .replace(/\[\]\([^\s)]+\)/g, '')
    .replace(/\r\n/g, '\n')
    .replace(/^([A-Za-z][\w+.-]*)```\s*$/gm, '```$1')
    .split('\n')
  let html = ''
  let paragraph = []
  let listStack = []
  let inCode = false
  let codeLang = ''
  let codeLines = []
  let inTable = false
  let tableRows = []
  let quoteLines = []

  const inline = t => renderInline(t, workspaceRoot)
  const isTableDelimiter = value => {
    const cells = String(value || '').trim().replace(/^\||\|$/g, '').split('|')
    return cells.length >= 2 && cells.every(cell => /^:?-{3,}:?$/.test(cell.trim()))
  }
  const flushParagraph = () => {
    if (!paragraph.length) return
    html += `<p>${inline(paragraph.join(' '))}</p>`
    paragraph = []
  }
  const closeListsTo = indent => {
    while (listStack.length && listStack[listStack.length - 1].indent > indent) {
      html += `</li></${listStack.pop().type}>`
    }
  }
  const closeList = () => closeListsTo(-1)
  const flushCode = () => {
    const lang = esc(codeLang)
    const label = lang || 'text'
    if (htmlPreview && /^html?$/i.test(codeLang) && codeLines.some(line => line.trim())) {
      const source = codeLines.join('\n')
      html += `<div class="code-block html-preview"><div class="code-head"><span class="code-lang">html · preview</span>` +
        `<span class="code-actions"><button type="button" class="code-toggle">Code</button><button type="button" class="code-copy">Copy</button></span></div>` +
        `<iframe class="html-frame" title="HTML preview" sandbox="allow-scripts allow-popups" referrerpolicy="no-referrer" srcdoc="${esc(htmlPreviewDocument(source))}"></iframe>` +
        `<pre hidden><code>${highlightCode(codeLines.join('\n'), codeLang)}</code></pre></div>`
      codeLines = []
      codeLang = ''
      inCode = false
      return
    }
    html += `<div class="code-block"><div class="code-head"><span class="code-lang">${label}</span>` +
      `<button type="button" class="code-copy">Copy</button></div>` +
      `<pre><code>${highlightCode(codeLines.join('\n'), codeLang)}</code></pre></div>`
    codeLines = []
    codeLang = ''
    inCode = false
  }
  const flushTable = () => {
    if (!inTable || !tableRows.length) { inTable = false; tableRows = []; return }
    let t = '<table>'
    for (let i = 0; i < tableRows.length; i++) {
      if (i === 1 && /^[\s|:-]+$/.test(tableRows[i])) continue
      const tag = i === 0 ? 'th' : 'td'
      const cells = tableRows[i].replace(/^\||\|$/g, '').split('|').map(c => c.trim())
      t += '<tr>' + cells.map(c => `<${tag}>${inline(c)}</${tag}>`).join('') + '</tr>'
    }
    t += '</table>'
    html += t
    inTable = false
    tableRows = []
  }
  const flushQuote = () => {
    if (!quoteLines.length) return
    html += `<blockquote>${renderMarkdown(quoteLines.join('\n'), workspaceRoot)}</blockquote>`
    quoteLines = []
  }

  for (let lineIndex = 0; lineIndex < lines.length; lineIndex++) {
    const line = lines[lineIndex]
    if (line.trim().startsWith('```')) {
      if (inCode) {
        flushCode()
      } else {
        flushParagraph()
        closeList()
        flushTable()
        flushQuote()
        inCode = true
        codeLang = line.trim().slice(3).trim().toLowerCase()
      }
      continue
    }
    if (inCode) { codeLines.push(line); continue }

    const trimmed = line.trim()

    const startsTable = trimmed.includes('|') && isTableDelimiter(lines[lineIndex + 1])
    if ((inTable && trimmed.includes('|')) || startsTable) {
      if (!inTable) { flushParagraph(); closeList(); inTable = true }
      tableRows.push(trimmed)
      continue
    }
    if (inTable) flushTable()

    if (!trimmed) { flushParagraph(); closeList(); flushQuote(); continue }

    const quote = trimmed.match(/^>\s?(.*)$/)
    if (quote) {
      flushParagraph()
      closeList()
      quoteLines.push(quote[1])
      continue
    }
    flushQuote()

    if (/^(?:-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      flushParagraph()
      closeList()
      html += '<hr>'
      continue
    }

    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/)
    if (heading) {
      flushParagraph()
      closeList()
      const level = Math.min(heading[1].length, 4)
      html += `<h${level}>${inline(heading[2])}</h${level}>`
      continue
    }

    const item = line.match(/^(\s*)(?:([-*])|(\d+)\.)\s+(.+)$/)
    if (item) {
      flushParagraph()
      const indent = item[1].length
      const type = item[2] ? 'ul' : 'ol'
      const top = () => listStack[listStack.length - 1]
      if (!listStack.length || indent > top().indent) {
        html += `<${type}>`
        listStack.push({ type, indent })
      } else {
        closeListsTo(indent)
        if (!listStack.length) {
          html += `<${type}>`
          listStack.push({ type, indent })
        } else if (top().type !== type) {
          html += `</li></${listStack.pop().type}><${type}>`
          listStack.push({ type, indent })
        } else {
          html += '</li>'
        }
      }
      const task = item[4].match(/^\[( |x|X)\]\s+(.+)$/)
      if (task) {
        const checked = task[1].toLowerCase() === 'x'
        html += `<li class="task-item"><input type="checkbox" disabled${checked ? ' checked' : ''}> ${inline(task[2])}`
      } else {
        html += `<li>${inline(item[4])}`
      }
      continue
    }

    closeList()
    paragraph.push(trimmed)
  }

  if (inCode) flushCode()
  flushParagraph()
  closeList()
  flushTable()
  flushQuote()
  return math.restore(html || '<p></p>')
}

export function renderInline(text, workspaceRoot) {
  // Stash inline code spans so emphasis/strike markers inside them survive.
  const codeSpans = []
  return esc(text)
    .replace(/`([^`]+)`/g, (_match, code) => {
      codeSpans.push(code)
      return `\u0000${codeSpans.length - 1}\u0000`
    })
    .replace(/!\[([^\]]*)\]\(([^\s)]+)\)/g, (_match, alt, src) => {
      const imageSrc = localImageUrl(src, workspaceRoot)
      if (!imageSrc && !/^https?:\/\//i.test(src)) return _match
      return `<img src="${esc(imageSrc || src)}" alt="${esc(alt)}" loading="lazy">`
    })
    .replace(/\[([^\]]+)\]\(([^\s)]+)\)/g, (_match, label, href) => {
      const path = artifactPath(href.replace(/&amp;/g, '&'), workspaceRoot)
      if (path) return `<a href="#" data-artifact-path="${esc(path)}">${label}</a>`
      const safeHref = sanitizeHref(href)
      if (!safeHref) return label
      return `<a href="${safeHref}" title="${safeHref}" rel="noreferrer noopener">${label}</a>`
    })
    .replace(/\*\*\*([^*]+)\*\*\*/g, '<strong><em>$1</em></strong>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>')
    .replace(/(^|[\s(])_([^_\s][^_]*)_(?=$|[\s).,;:!?])/g, '$1<em>$2</em>')
    .replace(/~~([^~]+)~~/g, '<del>$1</del>')
    .replace(/\u0000(\d+)\u0000/g, (_match, i) => {
      const code = codeSpans[Number(i)]
      const path = artifactPath(code.replace(/&amp;/g, '&'), workspaceRoot)
      return path ? `<a href="#" data-artifact-path="${esc(path)}"><code>${code}</code></a>` : `<code>${code}</code>`
    })
}

const HIGHLIGHT_RULES = {
  python: {
    comment: /#[^\n]*/,
    string: /'''[\s\S]*?'''|"""[\s\S]*?"""|'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"/,
    keywords: 'def class return if elif else for while in not and or import from as with try except finally raise pass break continue lambda yield global nonlocal assert del is None True False async await match case self',
  },
  javascript: {
    comment: /\/\/[^\n]*|\/\*[\s\S]*?\*\//,
    string: /`(?:\\.|[^`\\])*`|'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"/,
    keywords: 'const let var function return if else for while do switch case break continue new class extends super this typeof instanceof in of try catch finally throw async await yield import export from default null undefined true false void delete',
  },
  bash: {
    comment: /#[^\n]*/,
    string: /'[^']*'|"(?:\\.|[^"\\])*"/,
    keywords: 'if then else elif fi for while do done case esac function in echo exit return local export source cd set read shift trap',
  },
  json: {
    comment: null,
    string: /"(?:\\.|[^"\\])*"/,
    keywords: 'true false null',
  },
  yaml: {
    comment: /#[^\n]*/,
    string: /'[^']*'|"(?:\\.|[^"\\])*"/,
    keywords: 'true false null yes no',
  },
}
HIGHLIGHT_RULES.py = HIGHLIGHT_RULES.python
HIGHLIGHT_RULES.js = HIGHLIGHT_RULES.javascript
HIGHLIGHT_RULES.jsx = HIGHLIGHT_RULES.javascript
HIGHLIGHT_RULES.ts = HIGHLIGHT_RULES.javascript
HIGHLIGHT_RULES.tsx = HIGHLIGHT_RULES.javascript
HIGHLIGHT_RULES.typescript = HIGHLIGHT_RULES.javascript
HIGHLIGHT_RULES.sh = HIGHLIGHT_RULES.bash
HIGHLIGHT_RULES.shell = HIGHLIGHT_RULES.bash
HIGHLIGHT_RULES.zsh = HIGHLIGHT_RULES.bash
HIGHLIGHT_RULES.yml = HIGHLIGHT_RULES.yaml

export function highlightCode(code, lang) {
  const rules = HIGHLIGHT_RULES[String(lang || '').toLowerCase()]
  if (!rules) return esc(code)
  const keywords = new Set(rules.keywords.split(' '))
  const parts = [
    rules.comment ? `(${rules.comment.source})` : '(\\b\\B)',
    `(${rules.string.source})`,
    '\\b(\\d+(?:\\.\\d+)?)\\b',
    '\\b([A-Za-z_$][\\w$]*)\\b',
  ]
  const tokenizer = new RegExp(parts.join('|'), 'g')
  let out = ''
  let last = 0
  for (let m; (m = tokenizer.exec(code)) !== null;) {
    out += esc(code.slice(last, m.index))
    last = m.index + m[0].length
    if (m[1] !== undefined) out += `<span class="tok-c">${esc(m[1])}</span>`
    else if (m[2] !== undefined) out += `<span class="tok-s">${esc(m[2])}</span>`
    else if (m[3] !== undefined) out += `<span class="tok-n">${esc(m[3])}</span>`
    else if (keywords.has(m[4])) out += `<span class="tok-k">${esc(m[4])}</span>`
    else out += esc(m[4])
  }
  out += esc(code.slice(last))
  return out
}

export function sanitizeHref(href) {
  const value = String(href || '').trim()
  if (!value) return ''
  if (/^(https?:|mailto:)/i.test(value)) return esc(value)
  if (value.startsWith('/') || value.startsWith('#') || value.startsWith('./') || value.startsWith('../')) {
    return esc(value)
  }
  return ''
}

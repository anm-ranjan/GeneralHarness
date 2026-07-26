import assert from 'node:assert/strict'
import test from 'node:test'

import { esc, localImageUrl, renderMarkdown, sanitizeHref } from './utils.js'

test('esc escapes html without browser globals', () => {
  assert.equal(esc('<script>"x"</script>'), '&lt;script&gt;&quot;x&quot;&lt;/script&gt;')
})

test('sanitizeHref allows safe links and rejects scripts', () => {
  assert.equal(sanitizeHref('https://example.com?q=1&x=2'), 'https://example.com?q=1&amp;x=2')
  assert.equal(sanitizeHref('/api/sessions'), '/api/sessions')
  assert.equal(sanitizeHref('javascript:alert(1)'), '')
})

test('renderMarkdown strips unsafe link hrefs', () => {
  const html = renderMarkdown('[bad](javascript:alert(1)) [ok](https://example.com)', '')
  assert.equal(html.includes('javascript:'), false)
  assert.equal(html.includes('href="https://example.com"'), true)
})

test('renderMarkdown removes empty renderer anchor links', () => {
  const html = renderMarkdown('### Result[](https://example.com/#result)')
  assert.equal(html, '<h3>Result</h3>')
})

test('renderMarkdown formats blockquotes as quote boxes', () => {
  const html = renderMarkdown('Before\n\n> A quoted block\n> with **formatting**\n\nAfter', '')
  assert.equal(
    html,
    '<p>Before</p><blockquote><p>A quoted block with <strong>formatting</strong></p></blockquote><p>After</p>',
  )
})

test('localImageUrl keeps cache-busters out of filesystem paths', () => {
  const url = localImageUrl('/workspace/result.png?myharness_v=123', '')
  assert.equal(url, '/api/files/image?path=%2Fworkspace%2Fresult.png&v=myharness_v%3D123')
})

test('localImageUrl leaves already-routed API images alone', () => {
  const url = localImageUrl('/api/files/image?path=%2Fworkspace%2Fresult.png&v=123', '')
  assert.equal(url, '/api/files/image?path=%2Fworkspace%2Fresult.png&v=123')
})

test('renderMarkdown supports italics, strikethrough, and bold-italic', () => {
  const html = renderMarkdown('This is *em* and _also em_ and ~~gone~~ and ***both***', '')
  assert.equal(html.includes('<em>em</em>'), true)
  assert.equal(html.includes('<em>also em</em>'), true)
  assert.equal(html.includes('<del>gone</del>'), true)
  assert.equal(html.includes('<strong><em>both</em></strong>'), true)
})

test('renderMarkdown leaves snake_case and code spans untouched by emphasis', () => {
  const html = renderMarkdown('Use `snake_case_name` and file_name_here stays plain', '')
  assert.equal(html.includes('<code>snake_case_name</code>'), true)
  assert.equal(html.includes('file_name_here'), true)
  assert.equal(html.includes('<em>name</em>'), false)
})

test('renderMarkdown keeps emphasis markers inside inline code literal', () => {
  const html = renderMarkdown('Run `a * b` then `**not bold**`', '')
  assert.equal(html.includes('<code>a * b</code>'), true)
  assert.equal(html.includes('<code>**not bold**</code>'), true)
})

test('renderMarkdown renders horizontal rules', () => {
  assert.equal(renderMarkdown('above\n\n---\n\nbelow', ''), '<p>above</p><hr><p>below</p>')
})

test('renderMarkdown renders nested lists', () => {
  const html = renderMarkdown('- top\n  - child\n- top two', '')
  assert.equal(html, '<ul><li>top<ul><li>child</li></ul></li><li>top two</li></ul>')
})

test('renderMarkdown renders task lists as disabled checkboxes', () => {
  const html = renderMarkdown('- [ ] todo\n- [x] done', '')
  assert.equal(html.includes('<input type="checkbox" disabled> todo'), true)
  assert.equal(html.includes('<input type="checkbox" disabled checked> done'), true)
})

test('renderMarkdown wraps fences with language header and copy button', () => {
  const html = renderMarkdown('```python\nx = 1\n```', '')
  assert.equal(html.includes('<span class="code-lang">python</span>'), true)
  assert.equal(html.includes('class="code-copy"'), true)
  assert.equal(html.includes('<span class="tok-n">1</span>'), true)
})

test('renderMarkdown repairs stray language prefix before fences', () => {
  const html = renderMarkdown('text```\n# Roty, Rotz\n-11.58, -2.79\n```', '')
  assert.equal(html.includes('<p>text'), false)
  assert.equal(html.includes('<h1>Roty'), false)
  assert.equal(html.includes('<span class="code-lang">text</span>'), true)
  assert.equal(html.includes('# Roty, Rotz'), true)
})

test('renderMarkdown highlights keywords, strings, and comments', () => {
  const html = renderMarkdown('```python\ndef f():\n    return "hi"  # note\n```', '')
  assert.equal(html.includes('<span class="tok-k">def</span>'), true)
  assert.equal(html.includes('<span class="tok-s">&quot;hi&quot;</span>'), true)
  assert.equal(html.includes('<span class="tok-c"># note</span>'), true)
})

test('renderMarkdown escapes html inside highlighted code', () => {
  const html = renderMarkdown('```html\n<script>alert(1)</script>\n```', '')
  assert.equal(html.includes('<script>'), false)
  assert.equal(html.includes('&lt;script&gt;'), true)
})

test('renderMarkdown still escapes code in unknown languages', () => {
  const html = renderMarkdown('```\n<b>raw</b>\n```', '')
  assert.equal(html.includes('&lt;b&gt;raw&lt;/b&gt;'), true)
  assert.equal(html.includes('<span class="code-lang">text</span>'), true)
})

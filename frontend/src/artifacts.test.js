import test from 'node:test'
import assert from 'node:assert/strict'
import { artifactPath, artifactUrl } from './artifacts.js'
import { renderMarkdown } from './utils.js'
import { apiUrl, setActiveHost } from './api.js'
import { initialState, reducer } from './context/appReducer.js'

test('local reports resolve against workspace; external URLs stay external', () => {
  assert.equal(artifactPath('report.html', '/workspace'), '/workspace/report.html')
  assert.equal(artifactPath('C:\\work\\report.md'), 'C:\\work\\report.md')
  assert.equal(artifactPath('https://example.com/report.html', '/workspace'), '')
  assert.equal(artifactPath('javascript:report.html', '/workspace'), '')
  assert.equal(artifactPath('//example.com/report.html', '/workspace'), '')
  assert.equal(artifactUrl('C:\\work\\a b.html'), '/api/workspace/view/C%3A/work/a%20b.html')
})

test('markdown file links and inline file paths open local preview', () => {
  assert.match(renderMarkdown('[View](report.html)', '/workspace'), /data-artifact-path="\/workspace\/report.html"/)
  assert.match(renderMarkdown('Saved `/workspace/report.md`'), /data-artifact-path="\/workspace\/report.md"/)
  assert.doesNotMatch(renderMarkdown('[External](https://example.com/report.html)'), /data-artifact-path/)
})

test('preview opens the panel and session changes clear it', () => {
  const opened = reducer(initialState, { type: 'OPEN_ARTIFACT', payload: '/workspace/report.md' })
  assert.equal(opened.workspacePanelOpen, true)
  assert.equal(opened.artifactFile, '/workspace/report.md')
  const selected = reducer(opened, { type: 'SELECT_SESSION', payload: { meta: { id: 'next', status: 'idle' }, workspaceRoot: '/next' } })
  assert.equal(selected.artifactFile, null)
  assert.equal(reducer(opened, { type: 'CLEAR_SESSION' }).artifactFile, null)
})

test('the preview frame loads from the host being viewed, not the one serving the page', () => {
  try {
    assert.equal(apiUrl(artifactUrl('/archive/app/index.html')), '/api/workspace/view/archive/app/index.html')
    setActiveHost({ id: 'jarvis', url: 'http://127.0.0.1:8421' })
    assert.equal(
      apiUrl(`${artifactUrl('/archive/app/index.html')}?interactive=false`),
      'http://127.0.0.1:8421/api/workspace/view/archive/app/index.html?interactive=false',
    )
  } finally {
    setActiveHost({ id: '', url: '' })
  }
})

test('linksToPath recognises absolute, encoded, file:// and relative links', async () => {
  const { linksToPath } = await import('./artifacts.js')
  const path = '/archive/My App/DECISIONS.md'
  assert.equal(linksToPath('[a](/archive/My%20App/DECISIONS.md)', path), true)
  assert.equal(linksToPath('[a](<file:///archive/My App/DECISIONS.md>)', path), true)
  assert.equal(linksToPath('[a](./DECISIONS.md)', path), true)
  assert.equal(linksToPath('Mentions DECISIONS.md without a link', path), false)
  assert.equal(linksToPath('[a](/archive/Other/DECISIONS.md)', path), false)
})

test('linksToPath recognises inline code mentions, which render clickable', async () => {
  const { linksToPath } = await import('./artifacts.js')
  const path = '/ws/out/report.html'
  assert.equal(linksToPath('Saved to `out/report.html`.', path), true)
  assert.equal(linksToPath('Saved to `/ws/out/report.html`.', path), true)
  assert.equal(linksToPath('Saved to out/report.html.', path), false)
  assert.equal(linksToPath('Saved to `other/report.html`.', path), false)
  assert.equal(linksToPath('```\nout/report.html\n```', path), false)
})

test('linksToPath matches Windows paths across slash styles and case', async () => {
  const { linksToPath } = await import('./artifacts.js')
  const path = 'C:\\Work\\out\\report.html'
  assert.equal(linksToPath('[r](out/report.html)', path), true)
  assert.equal(linksToPath('[r](C:/Work/out/report.html)', path), true)
  assert.equal(linksToPath('[r](file:///c:/work/out/report.html)', path), true)
  assert.equal(linksToPath('See `out\\report.html`', path), true)
  assert.equal(linksToPath('[r](D:/Work/out/report.html)', path), false)
})


test('URL-style Windows drive paths match the backslash path', async () => {
  const { linksToPath, embedsImage } = await import('./artifacts.js')
  const path = 'U:\\03_Projects\\Data\\exp_green.png'
  assert.equal(linksToPath('[g](/U:/03_Projects/Data/exp_green.png)', path), true)
  assert.equal(embedsImage('![Green](/U:/03_Projects/Data/exp_green.png)', path), true)
  assert.equal(embedsImage('![Green](file:///u:/03_Projects/Data/exp_green.png)', path), true)
  assert.equal(embedsImage('![Green](exp_green.png)', path), true)
})

test('embedsImage counts only image embeds, not mentions or links', async () => {
  const { embedsImage } = await import('./artifacts.js')
  const path = '/ws/out/plot.png'
  assert.equal(embedsImage('![plot](/ws/out/plot.png?v=1)', path), true)
  assert.equal(embedsImage('![plot](</ws/out/my%20plot.png>)', '/ws/out/my plot.png'), true)
  assert.equal(embedsImage('Saved `out/plot.png`', path), false)
  assert.equal(embedsImage('[plot](/ws/out/plot.png)', path), false)
  assert.equal(embedsImage('![other](/ws/out/other.png)', path), false)
})

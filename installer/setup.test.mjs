/**
 * Tests for the setup installer's config generation and overwrite handling.
 *
 *   node --test installer/
 *
 * `prompts` needs a TTY, so the interactive steps are driven with
 * prompts.inject() and the config writing is exercised through buildConfig,
 * which is pure.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

import prompts from 'prompts';

import {
  buildConfig,
  writeConfig,
  stepExistingConfig,
  ConfigEditor,
  yamlScalar,
  dedent,
  EXAMPLE_CONFIG,
} from './setup.mjs';

const TEMPLATE = readFileSync(EXAMPLE_CONFIG, 'utf8');

// ── a tiny YAML reader, so the test does not trust the writer's own parser ──

/** Read one dotted key out of a YAML document (2-space indent, no flow maps). */
function readKey(text, dotted) {
  const keys = dotted.split('.');
  const lines = text.split('\n');
  let indent = 0;
  let start = 0;
  let end = lines.length;

  for (let depth = 0; depth < keys.length; depth++) {
    let found = -1;
    for (let i = start; i < end; i++) {
      const line = lines[i];
      if (!line.trim() || line.trim().startsWith('#')) continue;
      const lineIndent = line.length - line.trimStart().length;
      if (lineIndent < indent) break;
      if (lineIndent !== indent) continue;
      if (line.trimStart().split(':')[0].trim() === keys[depth]) {
        found = i;
        break;
      }
    }
    assert.notEqual(found, -1, `key not found: ${dotted} (stuck at "${keys[depth]}")`);

    if (depth === keys.length - 1) {
      const raw = lines[found].slice(lines[found].indexOf(':') + 1).trim();
      if (raw.startsWith('|')) {
        const body = [];
        for (let i = found + 1; i < lines.length; i++) {
          if (lines[i].trim() === '') { body.push(''); continue; }
          if (lines[i].length - lines[i].trimStart().length <= indent) break;
          body.push(lines[i].slice(indent + 2));
        }
        return body.join('\n').replace(/\s+$/, '');
      }
      if (raw === '' || raw === undefined) {
        // A block: collect "- item" list entries.
        const items = [];
        for (let i = found + 1; i < lines.length; i++) {
          if (!lines[i].trim()) break;
          if (lines[i].length - lines[i].trimStart().length <= indent) break;
          const item = lines[i].trim();
          if (item.startsWith('- ')) items.push(unquote(item.slice(2).trim()));
        }
        return items;
      }
      if (raw === '[]') return [];
      return unquote(stripComment(raw));
    }

    start = found + 1;
    end = lines.length;
    for (let i = start; i < lines.length; i++) {
      if (!lines[i].trim() || lines[i].trim().startsWith('#')) continue;
      if (lines[i].length - lines[i].trimStart().length <= indent) { end = i; break; }
    }
    indent += 2;
  }
  throw new Error('unreachable');
}

function stripComment(value) {
  if (value.startsWith('"') || value.startsWith("'")) return value;
  const hash = value.indexOf('#');
  return hash === -1 ? value : value.slice(0, hash).trim();
}

function unquote(value) {
  if (value.startsWith('"') && value.endsWith('"')) return JSON.parse(value);
  if (value.startsWith("'") && value.endsWith("'")) return value.slice(1, -1);
  if (value === 'true') return true;
  if (value === 'false') return false;
  if (/^-?\d+$/.test(value)) return Number(value);
  return value;
}

/** Lines that changed, as "key: value" pairs, ignoring comments and blanks. */
function changedLines(before, after) {
  const a = before.split('\n');
  const b = after.split('\n');
  const changed = [];
  const max = Math.max(a.length, b.length);
  for (let i = 0, j = 0; i < max || j < max; ) {
    if (a[i] === b[j]) { i++; j++; continue; }
    break;
  }
  // Simple structural check instead: every non-comment, non-blank line in the
  // output must either be identical to some template line or be a "key:" line.
  for (const line of b) {
    if (!line.trim() || line.trim().startsWith('#')) continue;
    if (a.includes(line)) continue;
    changed.push(line);
  }
  return changed;
}

// ── answer fixtures ───────────────────────────────────────────────────

// Deliberately ragged: leading indent on the first line only (which is why the
// writer pins the block indent with "|2-"), and trailing spaces that the writer
// strips because they are invisible noise in a block scalar.
const ASCII_ART = [
  '  ███╗   ███╗██╗   ██╗',
  '████╗ ████║╚██╗ ██╔╝',
  '██╔████╔██║ ╚████╔╝   ',
].join('\n');

const ASCII_ART_STORED = ASCII_ART.split('\n').map((l) => l.replace(/\s+$/, '')).join('\n');

const EVERYTHING = {
  app: { appName: 'Everything Harness', splashAscii: ASCII_ART },
  codex: true,
  claude: true,
  native: {
    baseUrl: 'https://openrouter.ai/api/v1',
    apiKey: 'sk-or-secret#with"quotes',
    model: 'openai/gpt-5.2',
  },
  audio: {
    enabled: true,
    processor: 'api',
    server: '',
    appDir: '/opt/apps/whisperAudio',
    apiBaseUrl: 'https://api.openai.com/v1',
    apiKey: 'sk-stt-key',
    model: 'whisper-1',
  },
  frontends: { browser: true, desktop: true },
  server: {
    host: '0.0.0.0',
    port: 9100,
    allowedPaths: ['/tmp/workspace-one', '/tmp/workspace two'],
    approvalMode: 'auto_approve',
    verboseTools: true,
    gitWrites: true,
  },
};

const MINIMAL = {
  app: { appName: 'MyHarness', splashAscii: '' },
  codex: false,
  claude: false,
  native: {
    baseUrl: 'https://openrouter.ai/api/v1',
    apiKey: '',
    model: 'qwen/qwen3-coder-next',
  },
  audio: {
    enabled: false,
    processor: 'local',
    server: '',
    appDir: '/opt/apps/whisperAudio',
    apiBaseUrl: '',
    apiKey: '',
    model: 'small',
  },
  frontends: { browser: true, desktop: false },
  server: {
    host: '127.0.0.1',
    port: 8420,
    allowedPaths: ['/tmp/only-workspace'],
    approvalMode: 'always_ask',
    verboseTools: false,
    gitWrites: false,
  },
};

// ── tests ─────────────────────────────────────────────────────────────

test('everything-enabled run emits exactly the answered values', () => {
  const out = buildConfig(EVERYTHING, TEMPLATE);

  assert.equal(readKey(out, 'api.base_url'), 'https://openrouter.ai/api/v1');
  assert.equal(readKey(out, 'api.api_key'), 'sk-or-secret#with"quotes');
  for (const role of ['default', 'read', 'write', 'summary']) {
    assert.equal(readKey(out, `models.${role}`), 'openai/gpt-5.2', role);
  }

  assert.equal(readKey(out, 'permissions.approval_mode'), 'auto_approve');
  assert.deepEqual(readKey(out, 'permissions.allowed_paths'), [
    '/tmp/workspace-one',
    '/tmp/workspace two',
  ]);

  assert.equal(readKey(out, 'server.host'), '0.0.0.0');
  assert.equal(readKey(out, 'server.port'), 9100);

  assert.equal(readKey(out, 'ui.app_name'), 'Everything Harness');
  // The first art line keeps its leading indent; only trailing space is dropped.
  assert.equal(readKey(out, 'ui.splash_ascii'), ASCII_ART_STORED);
  assert.ok(ASCII_ART_STORED.startsWith('  ███'), 'leading indent must survive');
  assert.equal(readKey(out, 'ui.verbose_tools'), true);
  assert.equal(readKey(out, 'ui.git_writes_enabled'), true);

  assert.equal(readKey(out, 'audio.enabled'), true);
  assert.equal(readKey(out, 'audio.transcription.processor'), 'api');
  assert.equal(readKey(out, 'audio.transcription.api_base_url'), 'https://api.openai.com/v1');
  assert.equal(readKey(out, 'audio.transcription.api_key'), 'sk-stt-key');
  assert.equal(readKey(out, 'audio.transcription.model'), 'whisper-1');

  assert.equal(readKey(out, 'codex_app_server.enabled'), true);
  assert.equal(readKey(out, 'claude_agent.enabled'), true);

  assert.equal(readKey(out, 'desktop.enabled'), true);
  // A wildcard bind must not become the desktop's backend URL.
  assert.equal(readKey(out, 'desktop.backend_url'), 'http://127.0.0.1:9100');
});

test('minimal run emits exactly the answered values', () => {
  const out = buildConfig(MINIMAL, TEMPLATE);

  assert.equal(readKey(out, 'api.api_key'), '');
  assert.equal(readKey(out, 'models.default'), 'qwen/qwen3-coder-next');
  assert.equal(readKey(out, 'permissions.approval_mode'), 'always_ask');
  assert.deepEqual(readKey(out, 'permissions.allowed_paths'), ['/tmp/only-workspace']);
  assert.equal(readKey(out, 'server.host'), '127.0.0.1');
  assert.equal(readKey(out, 'server.port'), 8420);
  assert.equal(readKey(out, 'ui.app_name'), 'MyHarness');
  assert.equal(readKey(out, 'ui.splash_ascii'), '');
  assert.equal(readKey(out, 'ui.verbose_tools'), false);
  assert.equal(readKey(out, 'ui.git_writes_enabled'), false);
  assert.equal(readKey(out, 'audio.enabled'), false);
  assert.equal(readKey(out, 'audio.transcription.processor'), 'local');
  assert.equal(readKey(out, 'audio.transcription.api_base_url'), '');
  assert.equal(readKey(out, 'codex_app_server.enabled'), false);
  assert.equal(readKey(out, 'claude_agent.enabled'), false);
  assert.equal(readKey(out, 'desktop.enabled'), false);
  assert.equal(readKey(out, 'desktop.backend_url'), 'http://127.0.0.1:8420');
});

test('output differs from the template only in value lines', () => {
  for (const [name, answers] of [['everything', EVERYTHING], ['minimal', MINIMAL]]) {
    const out = buildConfig(answers, TEMPLATE);
    const templateComments = TEMPLATE.split('\n').filter((l) => l.trim().startsWith('#'));
    const outComments = out.split('\n').filter((l) => l.trim().startsWith('#'));
    assert.deepEqual(outComments, templateComments, `${name}: comments must be preserved verbatim`);

    // Every changed line must be a "key: value", a list item, or block-scalar art.
    for (const line of changedLines(TEMPLATE, out)) {
      const trimmed = line.trim();
      const looksLikeData =
        /^[A-Za-z0-9_.-]+\s*:/.test(trimmed) || trimmed.startsWith('- ') || !/^[a-z_]+:/.test(trimmed);
      assert.ok(looksLikeData, `${name}: unexpected structural change: ${JSON.stringify(line)}`);
    }
  }
});

test('every top-level section of the template survives', () => {
  const sections = (text) =>
    text.split('\n').filter((l) => /^[A-Za-z0-9_]+:/.test(l)).map((l) => l.split(':')[0]);
  for (const answers of [EVERYTHING, MINIMAL]) {
    assert.deepEqual(sections(buildConfig(answers, TEMPLATE)), sections(TEMPLATE));
  }
});

test('yamlScalar quotes hostile strings safely', () => {
  assert.equal(yamlScalar('plain'), '"plain"');
  assert.equal(yamlScalar('has: colon'), '"has: colon"');
  assert.equal(yamlScalar('has "quote"'), '"has \\"quote\\""');
  assert.equal(yamlScalar('#comment'), '"#comment"');
  assert.equal(yamlScalar(true), 'true');
  assert.equal(yamlScalar(8420), '8420');
  assert.equal(yamlScalar(''), '""');
});

test('dedent normalises art indentation without losing relative shape', () => {
  assert.equal(dedent('    a\n      b\n    c'), 'a\n  b\nc');
  assert.equal(dedent('  a\n\n  b'), 'a\n\nb');
});

test('ConfigEditor reports a missing key instead of corrupting the file', () => {
  const editor = new ConfigEditor(TEMPLATE);
  assert.equal(editor.set(['nope', 'missing'], 'x'), false);
  assert.equal(editor.toString(), TEMPLATE, 'a failed set must not modify anything');
});

test('writeConfig writes to the requested target', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'mh-setup-'));
  const target = path.join(dir, 'agent_config.yaml');
  assert.equal(writeConfig(MINIMAL, { example: EXAMPLE_CONFIG, target }), true);
  assert.ok(existsSync(target));
  assert.equal(readKey(readFileSync(target, 'utf8'), 'ui.app_name'), 'MyHarness');
});

test('declining the overwrite leaves the existing config untouched', async () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'mh-setup-'));
  const target = path.join(dir, 'agent_config.yaml');
  writeFileSync(target, 'api:\n  api_key: ORIGINAL\n', 'utf8');

  prompts.inject([false]); // "Overwrite it?" -> no
  const proceed = await stepExistingConfig(target);

  assert.equal(proceed, false, 'setup must stop when the user declines');
  assert.equal(readFileSync(target, 'utf8'), 'api:\n  api_key: ORIGINAL\n');
  assert.equal(existsSync(`${target}.bak`), false, 'no backup when nothing is overwritten');
});

test('accepting the overwrite with a backup writes agent_config.yaml.bak', async () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'mh-setup-'));
  const target = path.join(dir, 'agent_config.yaml');
  writeFileSync(target, 'api:\n  api_key: ORIGINAL\n', 'utf8');

  prompts.inject([true, true]); // overwrite -> yes, back up -> yes
  const proceed = await stepExistingConfig(target);

  assert.equal(proceed, true);
  assert.equal(readFileSync(`${target}.bak`, 'utf8'), 'api:\n  api_key: ORIGINAL\n');

  // And the subsequent write really replaces the original.
  writeConfig(MINIMAL, { example: EXAMPLE_CONFIG, target });
  assert.equal(readKey(readFileSync(target, 'utf8'), 'ui.app_name'), 'MyHarness');
  assert.equal(readFileSync(`${target}.bak`, 'utf8'), 'api:\n  api_key: ORIGINAL\n');
});

test('declining the backup still overwrites and writes no .bak', async () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'mh-setup-'));
  const target = path.join(dir, 'agent_config.yaml');
  writeFileSync(target, 'api:\n  api_key: ORIGINAL\n', 'utf8');

  prompts.inject([true, false]); // overwrite -> yes, back up -> no
  assert.equal(await stepExistingConfig(target), true);
  assert.equal(existsSync(`${target}.bak`), false);
});

test('a fresh install needs no overwrite prompt', async () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'mh-setup-'));
  assert.equal(await stepExistingConfig(path.join(dir, 'agent_config.yaml')), true);
});

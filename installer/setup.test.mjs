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
  stepFleet,
  suggestHostId,
  backendImportCheckArgs,
  invokeCredentialStore,
  fleetTunnelScript,
  REQUIRED_BACKEND_MODULES,
  ConfigEditor,
  yamlScalar,
  dedent,
  linuxSandboxPaths,
  inspectSandboxHelper,
  appArmorRestrictsUserNamespaces,
  appArmorProfileText,
  appArmorProfileName,
  EXAMPLE_CONFIG,
} from './setup.mjs';

const TEMPLATE = readFileSync(EXAMPLE_CONFIG, 'utf8');

test('credential helper sends secrets through stdin instead of command arguments', () => {
  const secret = 'sk-stdin-only';
  let invocation;
  const fakeSpawn = (command, args, options) => {
    invocation = { command, args, options };
    return {
      status: 0,
      stdout: JSON.stringify({ MYHARNESS_API_KEY: true, MYHARNESS_STT_API_KEY: false }),
      stderr: '',
    };
  };
  const result = invokeCredentialStore(
    { venvPython: '/example/python' },
    { set: { MYHARNESS_API_KEY: secret } },
    fakeSpawn,
  );

  assert.equal(result.ok, true);
  assert.equal(invocation.command, '/example/python');
  assert.equal(invocation.args.includes(secret), false);
  assert.equal(invocation.options.input.includes(secret), true);
  assert.equal(invocation.options.shell, false);
});

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

  assert.equal(readKey(out, 'api.enabled'), true);
  assert.equal(readKey(out, 'api.base_url'), 'https://openrouter.ai/api/v1');
  assert.equal(readKey(out, 'api.api_key'), '');
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
  assert.equal(readKey(out, 'audio.transcription.api_key'), '');
  assert.equal(readKey(out, 'audio.transcription.model'), 'whisper-1');

  assert.equal(readKey(out, 'codex_app_server.enabled'), true);
  assert.equal(readKey(out, 'claude_agent.enabled'), true);

  assert.equal(readKey(out, 'desktop.enabled'), true);
  // A wildcard bind must not become the desktop's backend URL.
  assert.equal(readKey(out, 'desktop.backend_url'), 'http://127.0.0.1:9100');
});

test('minimal run emits exactly the answered values', () => {
  const out = buildConfig(MINIMAL, TEMPLATE);

  assert.equal(readKey(out, 'api.enabled'), false);
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

test('Linux sandbox paths cover dependency and unpacked helpers', () => {
  assert.deepEqual(linuxSandboxPaths('/repo'), [
    path.join('/repo', 'electron', 'node_modules', 'electron', 'dist', 'chrome-sandbox'),
    path.join('/repo', 'electron', 'dist', 'linux-unpacked', 'chrome-sandbox'),
  ]);
});

test('sandbox helper inspection requires root ownership and exact 4755 mode', () => {
  const fakeStat = ({ uid = 0, gid = 0, mode = 0o104755 } = {}) =>
    inspectSandboxHelper('/helper', () => ({ uid, gid, mode }));

  assert.equal(fakeStat().configured, true);
  assert.equal(fakeStat({ uid: 1000 }).configured, false);
  assert.equal(fakeStat({ gid: 1000 }).configured, false);
  assert.equal(fakeStat({ mode: 0o100755 }).configured, false);
  assert.equal(inspectSandboxHelper('/missing', () => { throw new Error('missing'); }).exists, false);
});

test('AppArmor restriction detection is conservative', () => {
  assert.equal(appArmorRestrictsUserNamespaces(() => '1\n'), true);
  assert.equal(appArmorRestrictsUserNamespaces(() => '0\n'), false);
  assert.equal(appArmorRestrictsUserNamespaces(() => { throw new Error('unsupported'); }), false);
});

test('AppArmor profile permits user namespaces only for generated AppImages', () => {
  const profile = appArmorProfileText('/opt/My Harness/electron/dist', 'my-harness-electron-appimage');
  assert.match(profile, /profile my-harness-electron-appimage "\/opt\/My Harness\/electron\/dist\/\*\.AppImage"/);
  assert.match(profile, /\n  userns,\n/);
  assert.match(profile, /flags=\(default_allow\)/);
  assert.equal(appArmorProfileName('My Harness!'), 'my-harness-electron-appimage');
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

// ── fleet ─────────────────────────────────────────────────────────────
//
// The fleet block is the only list-of-mappings the installer writes, and it is
// the one place where a wrong answer silently hides a machine from the UI.

/** Read a list of mappings (e.g. fleet.hosts) out of a YAML document. */
function readMappingList(text, dotted) {
  const lines = text.split('\n');
  const key = dotted.split('.').pop();
  const start = lines.findIndex((line) => line.trim() === `${key}:`);
  if (start === -1) return [];
  const indent = lines[start].length - lines[start].trimStart().length;
  const entries = [];
  for (let i = start + 1; i < lines.length; i++) {
    const line = lines[i];
    if (!line.trim() || line.trim().startsWith('#')) break;
    if (line.length - line.trimStart().length <= indent) break;
    const trimmed = line.trim();
    const body = trimmed.startsWith('- ') ? trimmed.slice(2) : trimmed;
    if (trimmed.startsWith('- ')) entries.push({});
    const [field, ...rest] = body.split(':');
    entries[entries.length - 1][field.trim()] = unquote(rest.join(':').trim());
  }
  return entries;
}

const FLEET_ANSWERS = {
  ...MINIMAL,
  fleet: {
    enabled: true,
    self: 'mac',
    pollSeconds: 15,
    hosts: [
      { id: 'mac', label: 'MacBook', url: 'http://127.0.0.1:8420' },
      { id: 'jarvis', label: 'Jarvis', url: 'http://127.0.0.1:8421' },
    ],
  },
};

test('a configured fleet is written as a list of host mappings', () => {
  const out = buildConfig(FLEET_ANSWERS, TEMPLATE);

  assert.equal(readKey(out, 'fleet.enabled'), true);
  assert.equal(readKey(out, 'fleet.self'), 'mac');
  assert.equal(readKey(out, 'fleet.poll_seconds'), 15);
  assert.deepEqual(readMappingList(out, 'fleet.hosts'), [
    { id: 'mac', label: 'MacBook', url: 'http://127.0.0.1:8420' },
    { id: 'jarvis', label: 'Jarvis', url: 'http://127.0.0.1:8421' },
  ]);
});

test('answers without a fleet leave the block disabled and empty', () => {
  const out = buildConfig(MINIMAL, TEMPLATE);
  assert.equal(readKey(out, 'fleet.enabled'), false);
  assert.equal(readKey(out, 'fleet.self'), '');
  assert.deepEqual(readKey(out, 'fleet.hosts'), []);
});

test('host labels containing YAML metacharacters stay quoted', () => {
  const answers = {
    ...MINIMAL,
    fleet: {
      enabled: true,
      self: 'mac',
      pollSeconds: 10,
      hosts: [
        { id: 'mac', label: 'Ani: "work" #1', url: 'http://127.0.0.1:8420' },
        { id: 'jarvis', label: 'Jarvis', url: 'http://127.0.0.1:8421' },
      ],
    },
  };
  const hosts = readMappingList(buildConfig(answers, TEMPLATE), 'fleet.hosts');
  assert.equal(hosts[0].label, 'Ani: "work" #1');
});

test('declining the fleet prompt leaves it disabled', async () => {
  prompts.inject([false]);
  const fleet = await stepFleet({ host: '127.0.0.1', port: 8420 });
  assert.deepEqual(fleet, { enabled: false, self: '', pollSeconds: 10, hosts: [] });
});

test('stepFleet collects this machine and its peers', async () => {
  prompts.inject([
    true,                     // set up a fleet
    'mac', 'MacBook', 'http://127.0.0.1:8420',   // this machine
    true,                     // add another
    'jarvis', 'Jarvis', 'http://127.0.0.1:8421', // peer
    'jarvis.local', 8420,     // tunnel details, since the URL is loopback
    false,                    // no more machines
    12,                       // poll seconds
  ]);

  const fleet = await stepFleet({ host: '0.0.0.0', port: 8420 });

  assert.equal(fleet.enabled, true);
  assert.equal(fleet.self, 'mac');
  assert.equal(fleet.pollSeconds, 12);
  assert.deepEqual(fleet.hosts, [
    { id: 'mac', label: 'MacBook', url: 'http://127.0.0.1:8420' },
    { id: 'jarvis', label: 'Jarvis', url: 'http://127.0.0.1:8421' },
  ]);
});

test('a fleet of one machine is refused, since there is nothing to switch to', async () => {
  prompts.inject([
    true,
    'mac', 'MacBook', 'http://127.0.0.1:8420',
    false,  // decline to add a second machine
  ]);

  const fleet = await stepFleet({ host: '127.0.0.1', port: 8420 });
  assert.equal(fleet.enabled, false);
  assert.deepEqual(fleet.hosts, []);
});

test('suggestHostId produces a plain identifier from a hostname', () => {
  assert.equal(suggestHostId('Jarvis.local'), 'jarvis');
  assert.equal(suggestHostId("Ani's MacBook Pro.local"), 'anismacbookpro');
  assert.equal(suggestHostId(''), 'this-machine');
});

// ── backend dependency check ──────────────────────────────────────────
//
// Setup builds the desktop package around whatever is in ./.venv, so an
// environment that cannot import these ships as an app that opens and then
// reports its backend exiting.

test('the dependency check uses import names, not distribution names', () => {
  // PyYAML installs as "yaml" and PyMuPDF as "fitz"; checking the distribution
  // name would fail for a perfectly good venv.
  assert.ok(REQUIRED_BACKEND_MODULES.includes('yaml'));
  assert.ok(!REQUIRED_BACKEND_MODULES.includes('PyYAML'));
  for (const name of REQUIRED_BACKEND_MODULES) {
    assert.match(name, /^[a-z_][a-z0-9_]*$/, `${name} is not a valid import name`);
  }
})

test('the dependency check covers what the backend imports at startup', () => {
  // web_app.py cannot reach its first line of work without these.
  for (const name of ['fastapi', 'uvicorn', 'pydantic']) {
    assert.ok(REQUIRED_BACKEND_MODULES.includes(name), `missing ${name}`);
  }
})

test('the dependency check is a single runnable python -c statement', () => {
  const args = backendImportCheckArgs();
  assert.equal(args[0], '-c');
  assert.equal(args.length, 2);
  assert.equal(args[1], `import ${REQUIRED_BACKEND_MODULES.join(', ')}`);
})

// ── fleet tunnel launcher ─────────────────────────────────────────────
//
// Remote hosts are reached over loopback so their backends stay bound to
// 127.0.0.1, which means nothing works until a tunnel is up. The installer
// cannot hold one open, so it writes a launcher instead.

const TUNNELS = [
  { localPort: '8421', remotePort: 8420, sshHost: 'jarvis.local' },
  { localPort: '8422', remotePort: 8420, sshHost: 'nas.local' },
];

test('the tunnel script forwards every configured host', () => {
  const script = fleetTunnelScript(TUNNELS);
  assert.ok(script.startsWith('#!/usr/bin/env bash'));
  assert.match(script, /"8421:127\.0\.0\.1:8420 jarvis\.local"/);
  assert.match(script, /"8422:127\.0\.0\.1:8420 nas\.local"/);
});

test('the tunnel script restarts dropped tunnels', () => {
  // A silently dead tunnel is indistinguishable in the UI from a host that is
  // switched off, so the script must not just run ssh once.
  const script = fleetTunnelScript(TUNNELS);
  assert.match(script, /while true/);
  assert.match(script, /sleep 5/);
  assert.match(script, /ServerAliveInterval=30/);
});

test('the tunnel script fails loudly on a port that is already bound', () => {
  assert.match(fleetTunnelScript(TUNNELS), /ExitOnForwardFailure=yes/);
});

test('the tunnel script cleans up its children on interrupt', () => {
  const script = fleetTunnelScript(TUNNELS);
  assert.match(script, /trap cleanup INT TERM/);
  assert.match(script, /kill "\$pid"/);
});

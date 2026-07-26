#!/usr/bin/env node
/**
 * Interactive setup for MyHarness.
 *
 *   npx .            (from the repo root)
 *   npm run setup
 *
 * Asks for a display name, which agent providers to enable, the native API
 * credentials, optional voice dictation, which frontends to build, and the
 * production-facing knobs (bind address, workspaces, approval mode). It then
 * creates ./.venv, installs requirements.txt, and writes
 * backend/agent/agent_config.yaml from agent_config.example.yaml.
 *
 * Every answer has a default, so holding Enter produces a safe local setup.
 */

import { spawnSync } from 'node:child_process';
import { existsSync, copyFileSync, readFileSync, writeFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// Loaded dynamically so a fresh clone that has not run `npm install` gets a
// one-line instruction instead of a raw ERR_MODULE_NOT_FOUND stack trace.
// `npx .` installs these for us; `npm run setup` expects them to be present.
let figlet;
let prompts;
try {
  ({ default: figlet } = await import('figlet'));
  ({ default: prompts } = await import('prompts'));
} catch (error) {
  if (error?.code !== 'ERR_MODULE_NOT_FOUND') throw error;
  console.error('\nThe setup wizard needs its dependencies installed first.\n');
  console.error('  npm install && npm run setup');
  console.error('\nOr let npm handle it for you:\n');
  console.error('  npx .\n');
  process.exit(1);
}

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const AGENT_DIR = path.join(REPO_ROOT, 'backend', 'agent');
const EXAMPLE_CONFIG = path.join(AGENT_DIR, 'agent_config.example.yaml');
const TARGET_CONFIG = path.join(AGENT_DIR, 'agent_config.yaml');
const IS_WINDOWS = process.platform === 'win32';

// ── tiny terminal helpers ────────────────────────────────────────────

const COLOR = process.stdout.isTTY && !process.env.NO_COLOR;
const paint = (code, text) => (COLOR ? `\u001b[${code}m${text}\u001b[0m` : text);
const bold = (text) => paint('1', text);
const dim = (text) => paint('2', text);
const green = (text) => paint('32', text);
const yellow = (text) => paint('33', text);
const red = (text) => paint('31', text);

const notes = [];
let hadFailure = false;

function heading(title) {
  console.log('');
  console.log(bold(`── ${title} ${'─'.repeat(Math.max(0, 58 - title.length))}`));
}

function info(text) {
  console.log(`   ${text}`);
}

function warn(text) {
  console.log(yellow(`   ! ${text}`));
}

function fail(text) {
  hadFailure = true;
  console.log(red(`   x ${text}`));
}

function ok(text) {
  console.log(green(`   + ${text}`));
}

// ── process helpers ──────────────────────────────────────────────────

/** Run a command and capture stdout. Never throws. */
function probe(command, args = []) {
  try {
    const result = spawnSync(command, args, {
      encoding: 'utf8',
      shell: IS_WINDOWS,
      timeout: 30000,
    });
    if (result.error || result.status !== 0) return { ok: false, out: '' };
    return { ok: true, out: String(result.stdout || '').trim() };
  } catch {
    return { ok: false, out: '' };
  }
}

/** Run a command with inherited stdio so the user sees progress. */
function run(command, args, options = {}) {
  console.log(dim(`   $ ${command} ${args.join(' ')}`));
  const result = spawnSync(command, args, {
    stdio: 'inherit',
    shell: IS_WINDOWS,
    cwd: options.cwd || REPO_ROOT,
    env: { ...process.env, ...(options.env || {}) },
  });
  return !result.error && result.status === 0;
}

function npmEnv() {
  // The installer inherits the user's npm cache unless NPM_CACHE overrides it
  // (some machines have a root-owned ~/.npm that breaks unprivileged installs).
  return process.env.NPM_CACHE ? { npm_config_cache: process.env.NPM_CACHE } : {};
}

function npmInstallIn(dir, extraArgs = []) {
  const hasLock = existsSync(path.join(dir, 'package-lock.json'));
  const args = hasLock ? ['ci', ...extraArgs] : ['install', ...extraArgs];
  return run('npm', args, { cwd: dir, env: npmEnv() });
}

// ── YAML text editing ────────────────────────────────────────────────
// The example config is the canonical template. Rather than round-tripping it
// through a YAML parser (which would drop every comment), the answered keys are
// replaced in place, line by line. Indentation is a consistent two spaces.

function indentOf(line) {
  return line.length - line.trimStart().length;
}

function isSkippable(line) {
  const trimmed = line.trim();
  return trimmed === '' || trimmed.startsWith('#');
}

/** Locate `path` (e.g. ['audio', 'transcription', 'model']) in the lines. */
function findKey(lines, keyPath) {
  let start = 0;
  let end = lines.length;
  let indent = 0;
  let index = -1;

  for (let depth = 0; depth < keyPath.length; depth++) {
    const wanted = keyPath[depth];
    index = -1;
    for (let i = start; i < end; i++) {
      const line = lines[i];
      if (isSkippable(line)) continue;
      const lineIndent = indentOf(line);
      if (lineIndent < indent) break;
      if (lineIndent !== indent) continue;
      const match = line.trimStart().match(/^([A-Za-z0-9_.-]+)\s*:/);
      if (match && match[1] === wanted) {
        index = i;
        break;
      }
    }
    if (index === -1) return null;
    if (depth < keyPath.length - 1) {
      start = index + 1;
      end = blockEnd(lines, index, indent);
      indent += 2;
    }
  }
  return { index, indent };
}

/** First line index after the block owned by the key at `index`. */
function blockEnd(lines, index, indent) {
  for (let i = index + 1; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim() === '') continue;
    if (indentOf(line) <= indent) return i;
  }
  return lines.length;
}

/** End of the *value* of the key at `index`: list items, block scalar body. */
function valueEnd(lines, index, indent) {
  for (let i = index + 1; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim() === '') return i;
    if (indentOf(line) <= indent) return i;
  }
  return lines.length;
}

function yamlScalar(value) {
  if (typeof value === 'boolean' || typeof value === 'number') return String(value);
  if (value === null || value === undefined) return 'null';
  // JSON string syntax is a valid YAML double-quoted scalar and escapes safely.
  return JSON.stringify(String(value));
}

class ConfigEditor {
  constructor(text) {
    this.lines = text.split('\n');
  }

  replace(keyPath, newLines) {
    const found = findKey(this.lines, keyPath);
    if (!found) {
      warn(`Could not find "${keyPath.join('.')}" in agent_config.example.yaml; left unchanged.`);
      return false;
    }
    const stop = valueEnd(this.lines, found.index, found.indent);
    this.lines.splice(found.index, stop - found.index, ...newLines);
    return true;
  }

  set(keyPath, value) {
    const found = findKey(this.lines, keyPath);
    if (!found) {
      warn(`Could not find "${keyPath.join('.')}" in agent_config.example.yaml; left unchanged.`);
      return false;
    }
    const pad = ' '.repeat(found.indent);
    const key = keyPath[keyPath.length - 1];
    // Preserve a trailing inline comment when the template value is unquoted
    // (a quoted value could legitimately contain a "#").
    const original = this.lines[found.index];
    const commentMatch = original.includes('"') || original.includes("'")
      ? null
      : original.match(/\s{2,}(#.*)$/);
    const suffix = commentMatch ? `   ${commentMatch[1]}` : '';
    return this.replace(keyPath, [`${pad}${key}: ${yamlScalar(value)}${suffix}`]);
  }

  setList(keyPath, values) {
    const found = findKey(this.lines, keyPath);
    if (!found) {
      warn(`Could not find "${keyPath.join('.')}" in agent_config.example.yaml; left unchanged.`);
      return false;
    }
    const pad = ' '.repeat(found.indent);
    const key = keyPath[keyPath.length - 1];
    if (!values.length) return this.replace(keyPath, [`${pad}${key}: []`]);
    return this.replace(keyPath, [
      `${pad}${key}:`,
      ...values.map((value) => `${pad}  - ${yamlScalar(value)}`),
    ]);
  }

  setBlockText(keyPath, text) {
    const found = findKey(this.lines, keyPath);
    if (!found) {
      warn(`Could not find "${keyPath.join('.')}" in agent_config.example.yaml; left unchanged.`);
      return false;
    }
    const pad = ' '.repeat(found.indent);
    const key = keyPath[keyPath.length - 1];
    const body = dedent(text).replace(/\s+$/, '');
    if (!body) return this.replace(keyPath, [`${pad}${key}: ""`]);
    // "|2-" pins the block indentation two columns past the key, so art whose
    // first line happens to start with spaces cannot confuse the parser.
    return this.replace(keyPath, [
      `${pad}${key}: |2-`,
      ...body.split('\n').map((line) => (line.trim() ? `${pad}  ${line.replace(/\s+$/, '')}` : '')),
    ]);
  }

  toString() {
    return this.lines.join('\n');
  }
}

function dedent(text) {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const widths = lines.filter((line) => line.trim()).map((line) => indentOf(line));
  const shift = widths.length ? Math.min(...widths) : 0;
  return lines.map((line) => (line.trim() ? line.slice(shift) : '')).join('\n');
}

// ── prompt helpers ───────────────────────────────────────────────────

function onCancel() {
  console.log('');
  console.log(yellow('Setup cancelled. Nothing was written.'));
  process.exit(130);
}

const ask = (questions) => prompts(questions, { onCancel });

async function confirm(message, initial = true) {
  const { value } = await ask({ type: 'confirm', name: 'value', message, initial });
  return value;
}

async function text(message, initial = '') {
  const { value } = await ask({ type: 'text', name: 'value', message, initial });
  return String(value ?? '').trim();
}

async function select(message, choices, initial = 0) {
  const { value } = await ask({ type: 'select', name: 'value', message, choices, initial });
  return value;
}

async function integer(message, initial, min = 0, max = Number.MAX_SAFE_INTEGER) {
  for (;;) {
    const raw = await text(message, String(initial));
    const value = Number.parseInt(raw, 10);
    if (Number.isInteger(value) && value >= min && value <= max) return value;
    warn(`Enter an integer between ${min} and ${max}.`);
  }
}

// ── steps ────────────────────────────────────────────────────────────

async function stepExistingConfig(target = TARGET_CONFIG) {
  if (!existsSync(target)) return true;
  heading('Existing configuration');
  info(`${target} already exists.`);
  const overwrite = await confirm('Overwrite it with the answers from this run?', false);
  if (!overwrite) {
    console.log('');
    console.log(yellow('Keeping the existing config. Setup cancelled.'));
    return false;
  }
  if (await confirm('Back it up to agent_config.yaml.bak first?', true)) {
    copyFileSync(target, `${target}.bak`);
    ok(`Backed up to ${target}.bak`);
  }
  return true;
}

async function stepAppName() {
  heading('App name');
  const appName = (await text('Display name for the app', 'MyHarness')) || 'MyHarness';

  let art = '';
  for (const font of ['ANSI Shadow', 'Standard']) {
    try {
      art = figlet.textSync(appName, { font });
      if (art && art.trim()) break;
    } catch {
      art = '';
    }
  }

  if (!art || !art.trim()) {
    warn('Could not render ASCII art; the splash screen will show plain text.');
    return { appName, splashAscii: '' };
  }

  console.log('');
  console.log(art.replace(/\s+$/, ''));
  console.log('');
  const accept = await confirm('Use this ASCII art on the splash screen?', true);
  return { appName, splashAscii: accept ? art : '' };
}

function cliAuthenticated(spec) {
  const result = probe(spec.binary, spec.authStatusArgs);
  if (!result.ok) return false;
  if (spec.authJson) {
    try {
      const parsed = JSON.parse(result.out);
      return parsed.loggedIn === true || parsed.authenticated === true;
    } catch {
      return false;
    }
  }
  return true;
}

async function stepCliProvider(spec) {
  heading(spec.title);
  const found = probe(spec.binary, ['--version']);
  if (found.ok) {
    ok(`${spec.binary} found${found.out ? ` (${found.out.split('\n')[0]})` : ''}.`);
  } else {
    info(`${spec.binary} was not found on PATH.`);
    const want = await confirm(`Do you want to use the ${spec.label} provider?`, false);
    if (!want) return { enabled: false, binary: spec.binary, model: '', timeout: 1800 };

    const install = await confirm(`Install it now with "npm install -g ${spec.pkg}"?`, true);
    if (!install || !run('npm', ['install', '-g', spec.pkg], { env: npmEnv() })) {
      fail(`Installing ${spec.pkg} failed or was skipped. The ${spec.label} provider remains disabled.`);
      return { enabled: false, binary: spec.binary, model: '', timeout: 1800 };
    }
    ok(`${spec.pkg} installed.`);
  }

  if (!cliAuthenticated(spec)) {
    warn(`${spec.label} is installed but not authenticated.`);
    if (await confirm(`Authenticate ${spec.label} now?`, true)) {
      run(spec.binary, spec.authLoginArgs);
    }
  }
  if (!cliAuthenticated(spec)) {
    fail(`${spec.label} authentication could not be verified. The provider remains disabled.`);
    notes.push(`${spec.label}: authenticate with "${spec.binary} ${spec.authLoginArgs.join(' ')}", then re-run setup. ${spec.docs}`);
    return { enabled: false, binary: spec.binary, model: '', timeout: 1800 };
  }

  ok(`${spec.label} authentication verified.`);
  const enable = await confirm(`Enable the ${spec.label} provider?`, true);
  if (!enable) return { enabled: false, binary: spec.binary, model: '', timeout: 1800 };
  const model = await text(`${spec.label} model override (blank uses CLI default)`, '');
  const timeout = await integer(`${spec.label} run timeout in seconds`, 1800, 30, 86400);
  const settings = { enabled: true, binary: spec.binary, model, timeout };
  if (spec.label === 'Codex') {
    settings.reasoning = await select('Codex reasoning effort', [
      { title: 'low', value: 'low' },
      { title: 'medium', value: 'medium' },
      { title: 'high', value: 'high' },
    ]);
  } else {
    settings.maxTurns = await integer('Claude maximum turns (0 means SDK default)', 0, 0, 10000);
    settings.permissionMode = await select('Claude permission mode', [
      { title: 'derive from Harness approval mode', value: '' },
      { title: 'default', value: 'default' },
      { title: 'acceptEdits', value: 'acceptEdits' },
      { title: 'plan', value: 'plan' },
    ]);
  }
  return settings;
}

async function stepNativeProvider() {
  heading('Native provider (OpenAI-compatible API)');
  const enabled = Boolean((process.env.MYHARNESS_API_KEY || '').trim());
  info('Native is enabled only when MYHARNESS_API_KEY is exported; secrets are never written to YAML.');
  if (!enabled) {
    warn('MYHARNESS_API_KEY is not set. Native will be skipped for this installation.');
    notes.push('To enable Native later, export MYHARNESS_API_KEY and set api.enabled: true.');
  } else {
    ok('MYHARNESS_API_KEY is set in this environment.');
  }
  const baseUrl = (await text('API base URL', 'https://openrouter.ai/api/v1')) || 'https://openrouter.ai/api/v1';
  const model = (await text('Default model id', 'openai/gpt-5.2')) || 'openai/gpt-5.2';
  const timeout = await integer('Native API timeout in seconds', 120, 10, 86400);
  const maxIterations = await integer('Native maximum agent iterations', 20, 1, 10000);
  return { enabled, baseUrl, apiKey: '', model, timeout, maxIterations };
}

async function stepAudio(pythonInfo) {
  heading('Voice dictation (speech to text)');
  const enabled = await confirm('Enable voice dictation in the web UI?', false);
  if (!enabled) {
    return {
      enabled: false, processor: 'local', server: '', username: '', keyFile: '',
      appDir: '/opt/apps/whisperAudio', apiBaseUrl: '', apiKey: '', model: 'small',
      language: '', device: 'cpu', timeout: 1800, maxUploadMb: 500,
    };
  }

  const processor = await select('Which transcription backend?', [
    { title: 'local  - faster-whisper on this machine', value: 'local' },
    { title: 'remote - faster-whisper on an SSH compute host', value: 'remote' },
    { title: 'api    - an OpenAI-compatible /audio/transcriptions endpoint', value: 'api' },
  ]);

  const audio = {
    enabled: true,
    processor,
    server: '',
    username: '',
    keyFile: '',
    appDir: '/opt/apps/whisperAudio',
    apiBaseUrl: '',
    apiKey: '',
    model: 'small',
    language: '',
    device: 'cpu',
    timeout: 1800,
    maxUploadMb: 500,
  };

  if (processor === 'local') {
    audio.model = (await text('faster-whisper model size (tiny/base/small/medium/large-v3)', 'small')) || 'small';
    audio.device = await select('Transcription device', [
      { title: 'cpu', value: 'cpu' },
      { title: 'auto (try CUDA, then CPU)', value: 'auto' },
      { title: 'cuda', value: 'cuda' },
    ]);
    if (await confirm('Install faster-whisper into ./.venv now?', true)) {
      if (pythonInfo && pythonInfo.pip) {
        if (run(pythonInfo.pip[0], [...pythonInfo.pip.slice(1), 'install', 'faster-whisper'])) {
          ok('faster-whisper installed.');
        } else {
          fail('Installing faster-whisper failed. Install it into ./.venv manually.');
        }
      } else {
        warn('The venv was not created, so faster-whisper was skipped.');
      }
    }
  } else if (processor === 'remote') {
    audio.server = await text('SSH host name or IP address', '');
    audio.username = await text('SSH username (blank uses the SSH default)', '');
    audio.keyFile = await text('SSH private key path (blank uses ~/.ssh/id_rsa)', '');
    audio.appDir = (await text('Remote app directory (must contain .venv/bin/python)', '/opt/apps/whisperAudio')) || '/opt/apps/whisperAudio';
    audio.model = (await text('faster-whisper model size on the remote host', 'small')) || 'small';
    audio.device = await select('Remote transcription device', [
      { title: 'cuda', value: 'cuda' },
      { title: 'cpu', value: 'cpu' },
      { title: 'auto', value: 'auto' },
    ]);
    notes.push('Remote dictation requires key-based SSH and faster-whisper in the remote app virtual environment.');
  } else {
    audio.apiBaseUrl = (await text('STT API base URL', 'https://api.openai.com/v1')) || 'https://api.openai.com/v1';
    audio.model = (await text('STT model id', 'whisper-1')) || 'whisper-1';
    if (!(process.env.MYHARNESS_STT_API_KEY || '').trim()) {
      warn('MYHARNESS_STT_API_KEY is not set. API dictation will remain unavailable until it is exported.');
      notes.push('Export MYHARNESS_STT_API_KEY before using API voice dictation.');
    } else {
      ok('MYHARNESS_STT_API_KEY is set in this environment.');
    }
  }
  audio.language = await text('Language code (blank enables automatic detection)', '');
  audio.timeout = await integer('STT timeout in seconds', 1800, 10, 86400);
  audio.maxUploadMb = await integer('Maximum voice upload size in MB', 500, 1, 4096);
  return audio;
}

async function stepFrontends() {
  heading('Frontends');
  const browser = await confirm('Set up the browser UI (build frontend/)?', true);
  const desktop = await confirm('Build an installable desktop package for this OS?', false);
  const tui = await confirm('Build the Rust terminal UI?', true);
  return { browser, desktop, tui };
}

function installFrontends({ browser, desktop, tui }, appName) {
  heading('Building clients');
  if (browser || desktop) {
    info('Installing frontend dependencies...');
    if (npmInstallIn(path.join(REPO_ROOT, 'frontend'), ['--legacy-peer-deps'])) {
      ok('Frontend dependencies installed.');
      info('Building the frontend...');
      if (run('npm', ['run', 'build'], { cwd: path.join(REPO_ROOT, 'frontend'), env: npmEnv() })) {
        ok('Frontend built into frontend/dist.');
      } else {
        fail('Frontend build failed. Run "npm run build" in frontend/ to see the error.');
      }
    } else {
      fail('Installing frontend dependencies failed. Run "npm ci --legacy-peer-deps" in frontend/.');
    }
  }

  if (desktop) {
    info('Installing Electron dependencies...');
    if (npmInstallIn(path.join(REPO_ROOT, 'electron'))) {
      ok('Electron dependencies installed.');
      const target = IS_WINDOWS ? 'dist:win' : process.platform === 'darwin' ? 'dist:mac' : 'dist:linux';
      if (run('npm', ['run', target], {
        cwd: path.join(REPO_ROOT, 'electron'),
        env: { ...npmEnv(), MYHARNESS_PRODUCT_NAME: appName },
      })) {
        ok(`Desktop package built in electron/dist for ${process.platform}.`);
      } else {
        fail(`Desktop package build failed. Run "npm run ${target}" in electron/ for details.`);
      }
    } else {
      fail('Installing Electron dependencies failed. Run "npm ci" in electron/.');
    }
  }

  if (tui) {
    if (probe('cargo', ['--version']).ok) {
      if (run('cargo', ['build', '--release', '--manifest-path', path.join(REPO_ROOT, 'tui-rs', 'Cargo.toml')])) {
        ok('Rust TUI built in tui-rs/target/release.');
      } else {
        fail('Rust TUI build failed.');
      }
    } else {
      fail('Rust was not found. Install it from https://rustup.rs and re-run setup.');
    }
  }

}

async function stepServerAndPermissions() {
  heading('Server and permissions');

  const host = (await text('Backend bind address for trusted LAN access', '0.0.0.0')) || '0.0.0.0';
  if (host === '0.0.0.0' || host === '::') {
    warn('The agent API has NO authentication. Binding ' + host + ' lets anyone on your');
    warn('network read and write your allowed workspaces and run shell commands.');
    if (!(await confirm(`Confirm that ${host} is reachable only from a trusted LAN?`, false))) {
      info('Falling back to 127.0.0.1.');
      return stepServerAndPermissions();
    }
    notes.push(`The backend binds ${host} without application authentication. Keep it on a trusted LAN and firewall the port.`);
  }

  let port = 8420;
  for (;;) {
    const raw = (await text('Backend port', '8420')) || '8420';
    port = Number.parseInt(raw, 10);
    if (Number.isInteger(port) && port > 0 && port < 65536) break;
    warn('Enter a port between 1 and 65535.');
  }

  let allowedPaths = [];
  for (;;) {
    const raw = await text('Allowed workspace directories (comma-separated absolute paths)', REPO_ROOT);
    const candidates = raw.split(',').map((entry) => entry.trim()).filter(Boolean);
    if (!candidates.length) {
      warn('At least one workspace directory is required.');
      continue;
    }
    const resolved = [];
    let bad = false;
    for (const candidate of candidates) {
      const full = path.resolve(candidate.replace(/^~(?=$|[/\\])/, process.env.HOME || process.env.USERPROFILE || '~'));
      if (!existsSync(full) || !statSync(full).isDirectory()) {
        warn(`Not an existing directory: ${candidate}`);
        bad = true;
        continue;
      }
      resolved.push(full);
    }
    if (bad || !resolved.length) continue;
    allowedPaths = resolved;
    break;
  }

  const approvalMode = await select('Approval mode', [
    { title: 'always_ask   - confirm file writes and shell commands (recommended)', value: 'always_ask' },
    { title: 'shell_only   - confirm shell commands only', value: 'shell_only' },
    { title: 'auto_approve - never confirm; the agent runs shell commands unprompted', value: 'auto_approve' },
  ]);
  if (approvalMode === 'auto_approve') {
    warn('auto_approve lets the agent write files and run shell commands with no prompt.');
    notes.push('Approval mode is auto_approve: the agent acts on your allowed paths without asking.');
  }

  const verboseTools = await confirm('Show full tool input/output in the UI (verbose tools)?', false);
  const gitWrites = await confirm('Allow the Workspace Git panel to stage and commit?', false);
  const dataDir = (await text('Persistent data directory', path.join(REPO_ROOT, 'data'))) || path.join(REPO_ROOT, 'data');
  const loggingEnabled = await confirm('Enable application logging?', true);
  const logDir = loggingEnabled
    ? (await text('Log directory', path.join(REPO_ROOT, 'logs'))) || path.join(REPO_ROOT, 'logs')
    : '';
  const logLevel = loggingEnabled
    ? await select('Log level', [
      { title: 'info', value: 'info' },
      { title: 'warning', value: 'warning' },
      { title: 'debug', value: 'debug' },
      { title: 'error', value: 'error' },
    ])
    : 'info';
  const logRetentionDays = loggingEnabled
    ? await integer('Log retention in days (0 keeps logs indefinitely)', 30, 0, 3650)
    : 0;

  return {
    host, port, allowedPaths, approvalMode, verboseTools, gitWrites,
    dataDir, loggingEnabled, logDir, logLevel, logRetentionDays,
  };
}

function findPython() {
  const candidates = [process.env.MYHARNESS_PYTHON, 'python3', 'python'].filter(Boolean);
  for (const candidate of candidates) {
    const result = probe(candidate, ['-c', 'import sys; print("%d.%d" % sys.version_info[:2])']);
    if (!result.ok) continue;
    const [major, minor] = result.out.split('.').map((part) => Number.parseInt(part, 10));
    if (major > 3 || (major === 3 && minor >= 10)) return { command: candidate, version: result.out };
  }
  return null;
}

async function stepPythonEnv() {
  heading('Python environment');
  const venvDir = path.join(REPO_ROOT, '.venv');
  const venvPython = IS_WINDOWS
    ? path.join(venvDir, 'Scripts', 'python.exe')
    : path.join(venvDir, 'bin', 'python');

  if (existsSync(venvPython)) {
    ok(`Reusing the existing venv at ${venvDir}`);
  } else {
    const python = findPython();
    if (!python) {
      fail('No Python 3.10+ interpreter found. Install one and re-run setup, or set MYHARNESS_PYTHON.');
      return null;
    }
    info(`Using ${python.command} (Python ${python.version}) to create ./.venv`);
    if (!run(python.command, ['-m', 'venv', venvDir])) {
      fail('Creating ./.venv failed. On Debian/Ubuntu you may need the python3-venv package.');
      return null;
    }
    ok(`Created ${venvDir}`);
  }

  const pip = [venvPython, '-m', 'pip'];
  run(venvPython, ['-m', 'pip', 'install', '--upgrade', 'pip']);
  info('Installing requirements.txt (this can take a few minutes)...');
  if (run(venvPython, ['-m', 'pip', 'install', '-r', path.join(REPO_ROOT, 'requirements.txt')])) {
    ok('Backend dependencies installed.');
  } else {
    fail('Installing requirements.txt failed. Run it manually with ./.venv/bin/python -m pip.');
  }
  return { venvDir, venvPython, pip };
}

/** Apply the answers to the template text and return the finished YAML. */
function buildConfig(answers, templateText) {
  const editor = new ConfigEditor(templateText);
  const codex = typeof answers.codex === 'boolean' ? { enabled: answers.codex } : answers.codex;
  const claude = typeof answers.claude === 'boolean' ? { enabled: answers.claude } : answers.claude;

  editor.set(['api', 'enabled'], answers.native.enabled ?? Boolean(answers.native.apiKey));
  editor.set(['api', 'base_url'], answers.native.baseUrl);
  editor.set(['api', 'api_key'], '');
  editor.set(['api', 'timeout_seconds'], answers.native.timeout ?? 120);
  for (const role of ['default', 'read', 'write', 'summary']) {
    editor.set(['models', role], answers.native.model);
  }

  editor.set(['permissions', 'approval_mode'], answers.server.approvalMode);
  editor.setList(['permissions', 'allowed_paths'], answers.server.allowedPaths);

  editor.set(['server', 'host'], answers.server.host);
  editor.set(['server', 'port'], answers.server.port);
  editor.set(['agent', 'default_provider'], 'native');
  editor.set(['agent', 'max_iterations'], answers.native.maxIterations ?? 20);
  editor.set(['storage', 'data_dir'], answers.server.dataDir || '');
  editor.set(['logging', 'enabled'], answers.server.loggingEnabled ?? true);
  editor.set(['logging', 'log_dir'], answers.server.logDir || '');
  editor.set(['logging', 'level'], answers.server.logLevel || 'info');
  editor.set(['logging', 'retention_days'], answers.server.logRetentionDays ?? 30);

  editor.set(['ui', 'app_name'], answers.app.appName);
  editor.setBlockText(['ui', 'splash_ascii'], answers.app.splashAscii);
  editor.set(['ui', 'verbose_tools'], answers.server.verboseTools);
  editor.set(['ui', 'git_writes_enabled'], answers.server.gitWrites);

  editor.set(['audio', 'enabled'], answers.audio.enabled);
  editor.set(['audio', 'transcription', 'processor'], answers.audio.processor);
  editor.set(['audio', 'transcription', 'server'], answers.audio.server);
  editor.set(['audio', 'transcription', 'username'], answers.audio.username || '');
  editor.set(['audio', 'transcription', 'key_file'], answers.audio.keyFile || '');
  editor.set(['audio', 'transcription', 'app_dir'], answers.audio.appDir);
  editor.set(['audio', 'transcription', 'api_base_url'], answers.audio.apiBaseUrl);
  editor.set(['audio', 'transcription', 'api_key'], '');
  editor.set(['audio', 'transcription', 'model'], answers.audio.model);
  editor.set(['audio', 'transcription', 'language'], answers.audio.language || '');
  editor.set(['audio', 'transcription', 'device'], answers.audio.device || 'cpu');
  editor.set(['audio', 'transcription', 'timeout_seconds'], answers.audio.timeout ?? 1800);
  editor.set(['audio', 'transcription', 'max_upload_mb'], answers.audio.maxUploadMb ?? 500);

  editor.set(['codex_app_server', 'enabled'], Boolean(codex.enabled));
  editor.set(['codex_app_server', 'binary'], codex.binary || 'codex');
  editor.set(['codex_app_server', 'model'], codex.model || null);
  editor.set(['codex_app_server', 'timeout_seconds'], codex.timeout ?? 1800);
  editor.set(['codex_app_server', 'reasoning_effort'], codex.reasoning || 'low');
  editor.set(['claude_agent', 'enabled'], Boolean(claude.enabled));
  editor.set(['claude_agent', 'binary'], claude.binary || 'claude');
  editor.set(['claude_agent', 'model'], claude.model || null);
  editor.set(['claude_agent', 'timeout_seconds'], claude.timeout ?? 1800);
  editor.set(['claude_agent', 'max_turns'], claude.maxTurns ?? 0);
  editor.set(['claude_agent', 'permission_mode'], claude.permissionMode || '');

  editor.set(['desktop', 'enabled'], answers.frontends.desktop);
  editor.set(['desktop', 'backend_url'], `http://${answers.server.host === '0.0.0.0' ? '127.0.0.1' : answers.server.host}:${answers.server.port}`);

  return editor.toString();
}

function writeConfig(answers, { example = EXAMPLE_CONFIG, target = TARGET_CONFIG } = {}) {
  heading('Writing configuration');
  if (!existsSync(example)) {
    fail(`Template not found: ${example}`);
    return false;
  }
  writeFileSync(target, buildConfig(answers, readFileSync(example, 'utf8')), 'utf8');
  ok(`Wrote ${target}`);
  return true;
}

function summary(answers) {
  heading('Done');
  console.log('');
  console.log(`   ${bold('Launch')}`);
  if (answers.frontends.browser) {
    console.log(`     ./run.sh              web UI at http://${answers.server.host}:${answers.server.port}`);
    console.log('     run.cmd               same, on Windows');
    console.log('     ./run.sh --dev        backend + Vite dev server (hot reload)');
  }
  if (answers.frontends.desktop) {
    console.log('     electron/dist/        installable desktop package for this OS');
    console.log('     ./run.sh --electron   unpackaged Electron desktop shell');
  }
  if (answers.frontends.tui) console.log('     ./run.sh --tui        Rust TUI');
  console.log('     ./run.sh --cli        terminal CLI');
  console.log('');
  console.log(`   ${bold('Config')}   ${TARGET_CONFIG}`);
  console.log(`   ${bold('Python')}   ./.venv (run.sh and Electron pick this up automatically)`);
  console.log(`   ${bold('Secrets')}  API keys are not written to YAML; export MYHARNESS_API_KEY and MYHARNESS_STT_API_KEY.`);

  if (notes.length) {
    console.log('');
    console.log(`   ${bold('Before your first run')}`);
    for (const note of notes) console.log(`     - ${note}`);
  }

  console.log('');
  if (hadFailure) {
    console.log(yellow('   Some steps failed above. Fix them, then re-run "npm run setup" if needed.'));
  } else {
    console.log(green('   Setup complete.'));
  }
  console.log('');
}

// ── main ─────────────────────────────────────────────────────────────

async function main() {
  console.log('');
  console.log(bold('MyHarness setup'));
  console.log(dim('A local agent harness. Press Ctrl+C at any point to cancel.'));

  if (!(await stepExistingConfig())) return;

  const app = await stepAppName();

  const codex = await stepCliProvider({
    title: 'Codex CLI provider',
    label: 'Codex',
    binary: 'codex',
    pkg: '@openai/codex',
    authStatusArgs: ['login', 'status'],
    authLoginArgs: ['login'],
    authJson: false,
    docs: 'https://learn.chatgpt.com/docs/codex/cli',
  });

  const claude = await stepCliProvider({
    title: 'Claude Code provider',
    label: 'Claude',
    binary: 'claude',
    pkg: '@anthropic-ai/claude-code',
    authStatusArgs: ['auth', 'status', '--json'],
    authLoginArgs: ['auth', 'login'],
    authJson: true,
    docs: 'https://code.claude.com/docs/en/authentication',
  });

  const native = await stepNativeProvider();
  if (!native.enabled && !codex.enabled && !claude.enabled) {
    warn('No authenticated provider is enabled. Setup will finish, but agent sessions cannot run until one is configured.');
  }
  const pythonInfo = await stepPythonEnv();
  const audio = await stepAudio(pythonInfo);
  const frontends = await stepFrontends();
  const server = await stepServerAndPermissions();

  const answers = { app, codex, claude, native, audio, frontends, server };
  if (!writeConfig(answers)) return;
  installFrontends(frontends, app.appName);
  summary(answers);
}

// Only prompt when executed directly, so the YAML editor can be unit tested.
const invokedDirectly = process.argv[1]
  && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (invokedDirectly) {
  main().catch((error) => {
    console.error('');
    console.error(red(`Setup failed: ${error && error.stack ? error.stack : error}`));
    process.exit(1);
  });
}

export {
  ConfigEditor,
  buildConfig,
  writeConfig,
  stepExistingConfig,
  findKey,
  dedent,
  yamlScalar,
  EXAMPLE_CONFIG,
  TARGET_CONFIG,
};

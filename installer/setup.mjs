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

async function secret(message) {
  const { value } = await ask({ type: 'password', name: 'value', message });
  return String(value ?? '').trim();
}

async function select(message, choices, initial = 0) {
  const { value } = await ask({ type: 'select', name: 'value', message, choices, initial });
  return value;
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

async function stepCliProvider(spec) {
  heading(spec.title);
  const found = probe(spec.binary, ['--version']);
  if (found.ok) {
    ok(`${spec.binary} found${found.out ? ` (${found.out.split('\n')[0]})` : ''}.`);
    const enable = await confirm(`Enable the ${spec.label} provider?`, true);
    if (enable) notes.push(spec.loginNote);
    return enable;
  }

  info(`${spec.binary} was not found on PATH.`);
  const want = await confirm(`Do you want to use the ${spec.label} provider?`, false);
  if (!want) return false;

  const install = await confirm(`Install it now with "npm install -g ${spec.pkg}"?`, true);
  if (install) {
    if (run('npm', ['install', '-g', spec.pkg], { env: npmEnv() })) {
      ok(`${spec.pkg} installed.`);
    } else {
      fail(`Installing ${spec.pkg} failed. Install it manually, then re-run setup.`);
      warn(`The ${spec.label} provider stays enabled in the config, but will not run until the CLI exists.`);
    }
  } else {
    warn(`Install it yourself with: npm install -g ${spec.pkg}`);
  }
  notes.push(spec.loginNote);
  return true;
}

async function stepNativeProvider() {
  heading('Native provider (OpenAI-compatible API)');
  info('Used when neither CLI provider handles a run. Leave the key blank to skip.');
  const baseUrl = (await text('API base URL', 'https://openrouter.ai/api/v1')) || 'https://openrouter.ai/api/v1';
  const apiKey = await secret('API key (stored in agent_config.yaml; MYHARNESS_API_KEY overrides it)');
  const model = (await text('Default model id', 'openai/gpt-5.2')) || 'openai/gpt-5.2';
  if (!apiKey) {
    warn('No API key entered. Export MYHARNESS_API_KEY before launching, or rely on a CLI provider.');
  }
  return { baseUrl, apiKey, model };
}

async function stepAudio(pythonInfo) {
  heading('Voice dictation (speech to text)');
  const enabled = await confirm('Enable voice dictation in the web UI?', false);
  if (!enabled) {
    return { enabled: false, processor: 'local', server: '', appDir: '/opt/apps/whisperAudio', apiBaseUrl: '', apiKey: '', model: 'small' };
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
    appDir: '/opt/apps/whisperAudio',
    apiBaseUrl: '',
    apiKey: '',
    model: 'small',
  };

  if (processor === 'local') {
    audio.model = (await text('faster-whisper model size (tiny/base/small/medium/large-v3)', 'small')) || 'small';
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
    audio.server = await text('SSH host or server name (blank uses the first configured server)', '');
    audio.appDir = (await text('Remote app directory (must contain .venv/bin/python)', '/opt/apps/whisperAudio')) || '/opt/apps/whisperAudio';
    audio.model = (await text('faster-whisper model size on the remote host', 'small')) || 'small';
    notes.push('Remote dictation: list the host in utils/Qsub_Windows/server_config.yaml (copy the .template file) and make sure key-based SSH works.');
  } else {
    audio.apiBaseUrl = (await text('STT API base URL', 'https://api.openai.com/v1')) || 'https://api.openai.com/v1';
    audio.apiKey = await secret('STT API key (MYHARNESS_STT_API_KEY overrides it)');
    audio.model = (await text('STT model id', 'whisper-1')) || 'whisper-1';
    if (!audio.apiKey) {
      warn('No STT key entered. Export MYHARNESS_STT_API_KEY before launching.');
    }
  }
  return audio;
}

async function stepFrontends() {
  heading('Frontends');
  const browser = await confirm('Set up the browser UI (build frontend/)?', true);
  const desktop = await confirm('Set up the Electron desktop shell?', false);

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
    } else {
      fail('Installing Electron dependencies failed. Run "npm ci" in electron/.');
    }
  }

  return { browser, desktop };
}

async function stepServerAndPermissions() {
  heading('Server and permissions');

  const host = (await text('Backend bind address', '127.0.0.1')) || '127.0.0.1';
  if (host === '0.0.0.0' || host === '::') {
    warn('The agent API has NO authentication. Binding ' + host + ' lets anyone on your');
    warn('network read and write your allowed workspaces and run shell commands.');
    if (!(await confirm(`Really bind ${host}?`, false))) {
      info('Falling back to 127.0.0.1.');
      return stepServerAndPermissions();
    }
    notes.push(`The backend binds ${host}. Put it behind a trusted proxy or a firewall.`);
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

  return { host, port, allowedPaths, approvalMode, verboseTools, gitWrites };
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

  editor.set(['api', 'base_url'], answers.native.baseUrl);
  editor.set(['api', 'api_key'], answers.native.apiKey);
  for (const role of ['default', 'read', 'write', 'summary']) {
    editor.set(['models', role], answers.native.model);
  }

  editor.set(['permissions', 'approval_mode'], answers.server.approvalMode);
  editor.setList(['permissions', 'allowed_paths'], answers.server.allowedPaths);

  editor.set(['server', 'host'], answers.server.host);
  editor.set(['server', 'port'], answers.server.port);

  editor.set(['ui', 'app_name'], answers.app.appName);
  editor.setBlockText(['ui', 'splash_ascii'], answers.app.splashAscii);
  editor.set(['ui', 'verbose_tools'], answers.server.verboseTools);
  editor.set(['ui', 'git_writes_enabled'], answers.server.gitWrites);

  editor.set(['audio', 'enabled'], answers.audio.enabled);
  editor.set(['audio', 'transcription', 'processor'], answers.audio.processor);
  editor.set(['audio', 'transcription', 'server'], answers.audio.server);
  editor.set(['audio', 'transcription', 'app_dir'], answers.audio.appDir);
  editor.set(['audio', 'transcription', 'api_base_url'], answers.audio.apiBaseUrl);
  editor.set(['audio', 'transcription', 'api_key'], answers.audio.apiKey);
  editor.set(['audio', 'transcription', 'model'], answers.audio.model);

  editor.set(['codex_app_server', 'enabled'], answers.codex);
  editor.set(['claude_agent', 'enabled'], answers.claude);

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
    console.log('     ./run.sh --electron   Electron desktop shell');
  }
  console.log('     ./run.sh --tui        Rust TUI');
  console.log('     ./run.sh --cli        terminal CLI');
  console.log('');
  console.log(`   ${bold('Config')}   ${TARGET_CONFIG}`);
  console.log(`   ${bold('Python')}   ./.venv (run.sh and Electron pick this up automatically)`);
  console.log(`   ${bold('Secrets')}  MYHARNESS_API_KEY and MYHARNESS_STT_API_KEY override the keys in the config.`);

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
    loginNote: 'Run "codex login" once to authenticate the Codex CLI with your subscription.',
  });

  const claude = await stepCliProvider({
    title: 'Claude Code provider',
    label: 'Claude',
    binary: 'claude',
    pkg: '@anthropic-ai/claude-code',
    loginNote: 'Run "claude" once and complete the login to authenticate with your subscription.',
  });

  const native = await stepNativeProvider();
  const pythonInfo = await stepPythonEnv();
  const audio = await stepAudio(pythonInfo);
  const frontends = await stepFrontends();
  const server = await stepServerAndPermissions();

  const answers = { app, codex, claude, native, audio, frontends, server };
  writeConfig(answers);
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

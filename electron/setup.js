"use strict";

// Desktop setup (main-process side). Opens electron/setup/index.html, which
// walks through the same choices as the terminal wizard (installer/setup.mjs):
//
//   1. Install: copy the app files into a folder the user chooses, create its
//      Python environment and install requirements.txt.
//   2. App name and splash art, providers (Codex/Claude CLIs, the native API
//      key), voice dictation, server and workspaces, an optional fleet, and the
//      optional Rust TUI.
//   3. Write backend/agent/agent_config.yaml in that folder (through the
//      shared installer/lib/config.cjs) and the encrypted credentials.
//
// It runs before the backend on first start, from File → Setup… later, and
// in "update" mode when a newer app build starts against an older install.

const { BrowserWindow, dialog, ipcMain, shell } = require("electron");
const childProcess = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const {
  readInstallPointer,
  writeInstallPointer,
  fileHash,
  checkInstallFolder,
} = require("./install-location.cjs");
const { syncAppFiles } = require("./install-files.cjs");

// Links the setup page may open in the user's browser.
const EXTERNAL_LINKS = new Set([
  "https://rustup.rs",
  "https://www.python.org/downloads/",
  "https://nodejs.org/en/download",
  "https://learn.chatgpt.com/docs/codex/cli",
  "https://code.claude.com/docs/en/authentication",
  "https://antigravity.google",
  "https://policies.google.com/terms",
]);

const YAML_TO_JSON = "import json,sys,yaml\n"
  + "text = open(sys.argv[1], encoding='utf-8').read() if len(sys.argv) > 1 else sys.stdin.read()\n"
  + "print(json.dumps(yaml.safe_load(text) or {}, default=str))";

function createSetup({ bundledRoot, pointerFile, defaultInstallRoot, buildId, appName, envPath, core }) {
  const isWin = process.platform === "win32";
  let win = null;
  let finish = null;
  let loginChild = null;

  const env = () => ({ ...process.env, PATH: envPath() });
  const venvPythonIn = (root) => (isWin
    ? path.join(root, ".venv", "Scripts", "python.exe")
    : path.join(root, ".venv", "bin", "python"));
  const configIn = (root) => path.join(root, "backend", "agent", "agent_config.yaml");
  const currentRoot = () => readInstallPointer(pointerFile)?.root || "";

  function fromWizard(event) {
    return win && !win.isDestroyed() && event.sender === win.webContents;
  }

  function handle(channel, handler) {
    ipcMain.handle(channel, async (event, ...args) => {
      if (!fromWizard(event)) throw new Error("Setup window required");
      return handler(event, ...args);
    });
  }

  function sender(event) {
    return (line) => {
      if (!event.sender.isDestroyed()) event.sender.send("setup:log", String(line));
    };
  }

  // Runs a program without a shell (Windows .cmd shims need one; their
  // arguments here are fixed, never typed by the user).
  function run(command, args, { input, cwd, onLine, timeoutMs = 0, shellOnWindows = false, track } = {}) {
    return new Promise((resolve) => {
      let child;
      try {
        child = childProcess.spawn(command, args, {
          cwd: cwd || os.homedir(),
          env: env(),
          windowsHide: true,
          shell: isWin && shellOnWindows,
        });
      } catch (error) {
        resolve({ code: -1, stdout: "", stderr: String(error.message || error) });
        return;
      }
      if (track) track(child);
      let stdout = "";
      let stderr = "";
      const timer = timeoutMs ? setTimeout(() => child.kill(), timeoutMs) : null;
      const feed = (chunk, sink) => {
        const text = chunk.toString();
        if (sink === "out") stdout += text; else stderr += text;
        if (onLine) text.split(/\r?\n/).filter(Boolean).forEach(onLine);
      };
      child.stdout.on("data", (c) => feed(c, "out"));
      child.stderr.on("data", (c) => feed(c, "err"));
      child.on("error", (error) => { stderr += String(error.message || error); });
      child.on("close", (code) => {
        if (timer) clearTimeout(timer);
        resolve({ code, stdout, stderr });
      });
      if (input !== undefined) child.stdin.end(input); else child.stdin.end();
    });
  }

  function which(name) {
    const result = childProcess.spawnSync(isWin ? "where" : "which", isWin ? [name] : ["-a", name], {
      encoding: "utf-8", env: env(), windowsHide: true,
    });
    if (result.status !== 0) return [];
    return result.stdout.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  }

  // The Windows "py" launcher needs to be told to pick a Python 3.
  function launcherArgs(exe) {
    return isWin && ["py", "py.exe"].includes(path.basename(exe).toLowerCase()) ? ["-3"] : [];
  }

  function pythonGuesses() {
    const names = isWin
      ? ["py", "python", "python3"]
      : ["python3", "python3.13", "python3.12", "python3.11", "python3.10", "python"];
    const found = [];
    for (const name of names) {
      for (const hit of which(name)) {
        // The Windows Store alias opens the Store instead of running Python.
        if (!hit.toLowerCase().includes("windowsapps") && !found.includes(hit)) found.push(hit);
      }
    }
    return found;
  }

  // Candidates with their versions: supported ones first, newest first. The
  // first python3 on PATH is often too old (macOS ships 3.9 in /usr/bin).
  async function rankedPythons() {
    const found = [];
    for (const exe of pythonGuesses()) {
      const info = await pythonVersion(exe);
      if (info) found.push({ path: exe, version: info.version, ok: core.supportedPythonVersion(info.version) });
    }
    const key = (v) => v.split(".").map((n) => Number.parseInt(n, 10) || 0);
    return found.sort((a, b) => {
      if (a.ok !== b.ok) return a.ok ? -1 : 1;
      const [x, y] = [key(a.version), key(b.version)];
      for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return y[i] - x[i];
      return 0;
    });
  }

  async function pythonVersion(exe) {
    const script = "import sys,json;print(json.dumps({'version':'%d.%d.%d'%sys.version_info[:3],'exe':sys.executable}))";
    const result = await run(exe, [...launcherArgs(exe), "-c", script], { timeoutMs: 20_000 });
    try {
      return JSON.parse(result.stdout.trim());
    } catch {
      return null;
    }
  }

  async function toolVersion(command, { shellOnWindows = false } = {}) {
    const result = await run(command, ["--version"], { timeoutMs: 20_000, shellOnWindows });
    return result.code === 0 ? (result.stdout.trim().split(/\r?\n/)[0] || "found") : "";
  }

  async function parseYaml(python, { file, text }) {
    const result = await run(python, ["-c", YAML_TO_JSON, ...(file ? [file] : [])], {
      input: text, timeoutMs: 30_000,
    });
    if (result.code !== 0) {
      throw new Error((result.stderr || "The configuration is not valid YAML.").trim().split(/\r?\n/).pop());
    }
    return JSON.parse(result.stdout.trim() || "{}");
  }

  async function credentialStore(root, payload) {
    const python = venvPythonIn(root);
    const helper = path.join(root, "backend", "agent", "credential_store.py");
    if (!fs.existsSync(python) || !fs.existsSync(helper)) return { ok: false, status: {} };
    const result = await run(python, [helper, payload ? "update" : "status"], {
      input: payload ? JSON.stringify(payload) : undefined, timeoutMs: 30_000,
    });
    if (result.code !== 0) return { ok: false, status: {}, error: result.stderr.trim() };
    try {
      return { ok: true, status: JSON.parse(result.stdout || "{}") };
    } catch {
      return { ok: false, status: {} };
    }
  }

  async function cliStatus(id) {
    const spec = core.CLI_PROVIDERS[id];
    if (!spec) throw new Error(`Unknown provider ${id}`);
    const version = await toolVersion(spec.binary, { shellOnWindows: true });
    if (!version) return { installed: false, authenticated: false, version: "" };
    const auth = await run(spec.binary, spec.authStatusArgs, { timeoutMs: 30_000, shellOnWindows: true });
    return {
      installed: true,
      version,
      authenticated: auth.code === 0 && core.cliAuthOutputOk(spec, auth.stdout),
    };
  }

  // ── IPC ────────────────────────────────────────────────────────────────

  handle("setup:info", async () => {
    const root = currentRoot();
    return {
      platform: process.platform,
      appName,
      home: os.homedir(),
      hostname: os.hostname(),
      installRoot: root,
      defaultInstallRoot,
      configExists: Boolean(root) && fs.existsSync(configIn(root)),
      venvExists: Boolean(root) && fs.existsSync(venvPythonIn(root)),
      pythonGuesses: await rankedPythons(),
      tuiBundled: fs.existsSync(path.join(bundledRoot, "tui-rs", "Cargo.toml")),
    };
  });

  handle("setup:check-folder", async (_event, folder) => ({
    error: checkInstallFolder(folder, { bundledRoot }),
    existingInstall: fs.existsSync(path.join(String(folder || ""), "backend", "web_app.py")),
    hasConfig: fs.existsSync(configIn(String(folder || ""))),
  }));

  handle("setup:check-python", async (_event, exe) => {
    if (!exe) return { ok: false, error: "Choose a Python executable." };
    const info = await pythonVersion(exe);
    if (!info) return { ok: false, error: "This file did not run as Python." };
    const ok = core.supportedPythonVersion(info.version);
    return {
      ok,
      version: info.version,
      executable: info.exe,
      error: ok ? "" : `Python ${info.version} is too old; install Python 3.10 or newer.`,
    };
  });

  // Copy the app into `folder`, build its venv, record it as the install.
  handle("setup:install", async (event, { folder, python, recreate }) => {
    const log = sender(event);
    const root = path.resolve(String(folder || ""));
    const problem = checkInstallFolder(root, { bundledRoot });
    if (problem) return { ok: false, error: problem };
    try {
      syncAppFiles({ from: bundledRoot, to: root, log });
    } catch (error) {
      return { ok: false, error: `Copying the application files failed: ${error.message}` };
    }

    const venvDir = path.join(root, ".venv");
    const venvPython = venvPythonIn(root);
    if (recreate && fs.existsSync(venvDir)) {
      log(`Removing ${venvDir}`);
      fs.rmSync(venvDir, { recursive: true, force: true });
    }
    if (!fs.existsSync(venvPython)) {
      if (!python) return { ok: false, error: "Choose a Python executable first." };
      log(`Creating ${venvDir} with ${python}`);
      const made = await run(python, [...launcherArgs(python), "-m", "venv", venvDir], { onLine: log, cwd: root });
      if (made.code !== 0 || !fs.existsSync(venvPython)) {
        return {
          ok: false,
          error: "Creating the Python environment failed (see log). On Debian/Ubuntu install the python3-venv package.",
        };
      }
    }
    // A venv made by `uv venv` has no pip; bootstrap it rather than failing later.
    if ((await run(venvPython, ["-m", "pip", "--version"], { cwd: root })).code !== 0) {
      log("Adding pip to the environment…");
      if ((await run(venvPython, ["-m", "ensurepip", "--upgrade"], { onLine: log, cwd: root })).code !== 0) {
        return { ok: false, error: "The environment has no pip and ensurepip could not add it. Recreate it from scratch." };
      }
    }
    const requirements = path.join(root, "requirements.txt");
    const requirementsHash = fileHash(requirements);
    const previous = readInstallPointer(pointerFile);
    const upToDate = !recreate && previous && previous.root === root && previous.requirementsHash === requirementsHash
      && (await run(venvPython, core.backendImportCheckArgs(), { cwd: root })).code === 0;
    if (upToDate) {
      log("Python packages are already up to date.");
    } else {
      log("Installing Python packages from requirements.txt (this can take a few minutes)…");
      await run(venvPython, ["-m", "pip", "install", "--upgrade", "pip"], { onLine: log, cwd: root });
      const installed = await run(venvPython, ["-m", "pip", "install", "-r", requirements], { onLine: log, cwd: root });
      if (installed.code !== 0) {
        return { ok: false, error: "Installing the Python packages failed (see log). Check network or proxy access." };
      }
    }
    if ((await run(venvPython, core.backendImportCheckArgs(), { cwd: root })).code !== 0) {
      return { ok: false, error: "The environment still cannot import the backend's packages (see log)." };
    }
    writeInstallPointer(pointerFile, { root, build: buildId, requirementsHash });
    log(`Installed into ${root}`);
    return { ok: true, root, venvPython };
  });

  // The answers to start from: the install's current config, or defaults.
  handle("setup:read", async () => {
    const root = currentRoot();
    if (!root) return { ok: false, error: "Install the application first." };
    const python = venvPythonIn(root);
    let parsed = {};
    const config = configIn(root);
    try {
      if (fs.existsSync(config)) parsed = await parseYaml(python, { file: config });
    } catch (error) {
      return { ok: false, error: `Could not read ${config}: ${error.message}` };
    }
    const answers = core.answersFromConfig(parsed);
    if (!answers.server.dataDir) answers.server.dataDir = path.join(root, "data");
    if (!answers.server.logDir) answers.server.logDir = path.join(root, "logs");
    const credentials = (await credentialStore(root)).status;
    return {
      ok: true,
      root,
      configExists: fs.existsSync(config),
      answers,
      credentials: {
        native: Boolean(credentials.MYHARNESS_API_KEY),
        stt: Boolean(credentials.MYHARNESS_STT_API_KEY),
      },
    };
  });

  handle("setup:ascii", async (_event, name) => {
    const text = String(name || "").slice(0, 40);
    if (!text.trim()) return { art: "" };
    let figlet;
    try {
      figlet = require("figlet");
    } catch {
      return { art: "" };
    }
    for (const font of ["ANSI Shadow", "Standard"]) {
      try {
        const art = figlet.textSync(text, { font });
        if (art && art.trim()) return { art };
      } catch {
        // Try the next font.
      }
    }
    return { art: "" };
  });

  handle("setup:cli-status", async (_event, id) => cliStatus(id));

  handle("setup:cli-install", async (event, id) => {
    const spec = core.CLI_PROVIDERS[id];
    if (!spec) throw new Error(`Unknown provider ${id}`);
    const log = sender(event);
    const npm = isWin ? "npm.cmd" : "npm";
    if (!(await toolVersion(npm, { shellOnWindows: true }))) {
      return { ok: false, error: "npm was not found. Install Node.js first." };
    }
    log(`npm install -g ${spec.pkg}`);
    const result = await run(npm, ["install", "-g", spec.pkg], { onLine: log, shellOnWindows: true });
    if (result.code !== 0) {
      return { ok: false, error: `Installing ${spec.pkg} failed (see log). A global npm install may need permissions on this machine.` };
    }
    return { ok: true, ...(await cliStatus(id)) };
  });

  // Starts the CLI's own login flow, which opens the browser. Its output goes
  // to the log so a printed URL or code is visible.
  handle("setup:cli-login", async (event, id) => {
    const spec = core.CLI_PROVIDERS[id];
    if (!spec) throw new Error(`Unknown provider ${id}`);
    if (loginChild) return { ok: false, error: "A login is already running." };
    const log = sender(event);
    log(`${spec.binary} ${spec.authLoginArgs.join(" ")}`);
    await run(spec.binary, spec.authLoginArgs, {
      onLine: log,
      shellOnWindows: true,
      timeoutMs: 10 * 60_000,
      track: (child) => { loginChild = child; },
    });
    loginChild = null;
    return { ok: true, ...(await cliStatus(id)) };
  });

  handle("setup:cli-login-cancel", async () => {
    if (loginChild) loginChild.kill();
    loginChild = null;
    return { ok: true };
  });

  async function antigravityStatus() {
    const spec = core.ANTIGRAVITY;
    const version = await toolVersion(spec.binary);
    const install = core.antigravityInstallCommand(process.platform, { hasBrew: Boolean(await toolVersion("brew")) });
    return {
      installed: Boolean(version),
      version,
      installCommand: install ? install.display : "",
      loginCommand: `${spec.binary} ${spec.loginArgs.join(" ")}`,
      notice: core.ANTIGRAVITY_NOTICE,
      termsUrl: spec.termsUrl,
      siteUrl: spec.siteUrl,
    };
  }

  handle("setup:agy-status", async () => antigravityStatus());

  // Only after the page has shown the terms notice and the user ticked it.
  handle("setup:agy-install", async (event, { acceptedNotice } = {}) => {
    if (!acceptedNotice) return { ok: false, error: "Confirm the notice about Google's terms first." };
    const install = core.antigravityInstallCommand(process.platform, { hasBrew: Boolean(await toolVersion("brew")) });
    if (!install) return { ok: false, error: `Install the Antigravity CLI from ${core.ANTIGRAVITY.siteUrl}, then press Check again.` };
    const log = sender(event);
    log(install.display);
    const result = await run(install.command, install.args, { onLine: log, timeoutMs: 15 * 60_000 });
    if (result.code !== 0) return { ok: false, error: "Installing Antigravity failed (see log)." };
    const status = await antigravityStatus();
    return status.installed
      ? { ok: true, ...status }
      : { ok: false, ...status, error: `Installed, but ${core.ANTIGRAVITY.binary} is not on PATH yet. Restart the app after adding its folder to PATH.` };
  });

  handle("setup:agy-login", async (event) => {
    if (loginChild) return { ok: false, error: "A login is already running." };
    const spec = core.ANTIGRAVITY;
    const log = sender(event);
    log(`${spec.binary} ${spec.loginArgs.join(" ")}`);
    const result = await run(spec.binary, spec.loginArgs, {
      onLine: log,
      timeoutMs: 10 * 60_000,
      track: (child) => { loginChild = child; },
    });
    loginChild = null;
    return result.code === 0
      ? { ok: true }
      : { ok: false, error: `The sign-in did not finish. Run "${spec.binary} ${spec.loginArgs.join(" ")}" in a terminal instead.` };
  });

  handle("setup:check-cargo", async () => {
    const version = await toolVersion("cargo");
    return { ok: Boolean(version), version };
  });

  handle("setup:build-tui", async (event) => {
    const root = currentRoot();
    if (!root) return { ok: false, error: "Install the application first." };
    const manifest = path.join(root, "tui-rs", "Cargo.toml");
    if (!fs.existsSync(manifest)) return { ok: false, error: "This build does not include the TUI sources." };
    if (!(await toolVersion("cargo"))) {
      return { ok: false, error: "cargo was not found. Install Rust from rustup.rs, then try again." };
    }
    const log = sender(event);
    log("cargo build --release (the first build can take several minutes)…");
    const result = await run("cargo", ["build", "--release", "--manifest-path", manifest], { onLine: log, cwd: path.dirname(manifest) });
    if (result.code !== 0) return { ok: false, error: "The TUI build failed (see log)." };
    const binary = path.join(root, "tui-rs", "target", "release", isWin ? "myharness-tui.exe" : "myharness-tui");
    return { ok: fs.existsSync(binary), binary, error: fs.existsSync(binary) ? "" : "The build finished but the binary is missing." };
  });

  handle("setup:install-whisper", async (event) => {
    const root = currentRoot();
    const python = root ? venvPythonIn(root) : "";
    if (!python || !fs.existsSync(python)) return { ok: false, error: "Install the application first." };
    const log = sender(event);
    log("pip install faster-whisper");
    const result = await run(python, ["-m", "pip", "install", "faster-whisper"], { onLine: log, cwd: root });
    return result.code === 0 ? { ok: true } : { ok: false, error: "Installing faster-whisper failed (see log)." };
  });

  handle("setup:check-dir", async (_event, target) => {
    try {
      const raw = String(target || "").replace(/^~(?=$|[/\\])/, os.homedir());
      const full = path.resolve(raw);
      return { exists: fs.statSync(full).isDirectory(), path: full };
    } catch {
      return { exists: false, path: String(target || "") };
    }
  });

  handle("setup:pick", async (_event, { kind, title, defaultPath } = {}) => {
    const result = await dialog.showOpenDialog(win, {
      title: title || "Choose",
      defaultPath: defaultPath || undefined,
      properties: kind === "dir" ? ["openDirectory", "createDirectory"] : ["openFile"],
    });
    return result.canceled ? "" : result.filePaths[0];
  });

  handle("setup:open-link", async (_event, url) => {
    if (!EXTERNAL_LINKS.has(String(url))) return { ok: false };
    await shell.openExternal(String(url));
    return { ok: true };
  });

  handle("setup:save", async (_event, { answers, credentials = {}, tunnels = [] }) => {
    const root = currentRoot();
    if (!root) return { ok: false, error: "Install the application first." };
    const python = venvPythonIn(root);
    const target = configIn(root);
    const template = path.join(root, "backend", "agent", "agent_config.example.yaml");
    const notes = [];
    // Editing the existing file keeps any setting changed by hand.
    const base = fs.existsSync(target) ? target : template;
    let text;
    try {
      const missing = [];
      text = core.buildConfig(answers, fs.readFileSync(base, "utf-8"), { onMissing: (keyPath) => missing.push(keyPath.join(".")) });
      if (missing.length) notes.push(`Not in ${path.basename(base)}, left unchanged: ${missing.join(", ")}`);
      await parseYaml(python, { text });
    } catch (error) {
      return { ok: false, error: `The configuration could not be generated: ${error.message}` };
    }
    if (fs.existsSync(target)) fs.copyFileSync(target, `${target}.bak`);
    fs.writeFileSync(target, text, "utf-8");

    const replacements = {};
    if (credentials.native) replacements.MYHARNESS_API_KEY = String(credentials.native);
    if (credentials.stt) replacements.MYHARNESS_STT_API_KEY = String(credentials.stt);
    if (Object.keys(replacements).length) {
      const stored = await credentialStore(root, { set: replacements });
      if (!stored.ok) notes.push("The API keys could not be saved. Enter them later in Settings.");
    }

    if (tunnels.length) {
      if (isWin) {
        for (const tunnel of tunnels) {
          notes.push(`Open a tunnel before switching hosts: ssh -N -L ${tunnel.localPort}:127.0.0.1:${tunnel.remotePort} ${tunnel.sshHost}`);
        }
      } else {
        const script = path.join(root, "scripts", "fleet-tunnels.sh");
        fs.mkdirSync(path.dirname(script), { recursive: true });
        fs.writeFileSync(script, core.fleetTunnelScript(tunnels), "utf-8");
        fs.chmodSync(script, 0o755);
        notes.push(`Run ${script} to open the SSH tunnels, and leave it running while you switch hosts.`);
      }
    }
    return { ok: true, configPath: target, backupPath: base === target ? `${target}.bak` : "", notes };
  });

  handle("setup:finish", async (_event, { saved } = {}) => {
    const done = finish;
    finish = null;
    if (win && !win.isDestroyed()) win.close();
    if (done) done(Boolean(saved));
    return { ok: true };
  });

  // Resolves true when setup completed, false when the window was closed.
  // mode: "first" (no install yet), "rerun" (File → Setup…), "update".
  function open({ mode = "first", parent = null } = {}) {
    if (win && !win.isDestroyed()) {
      win.focus();
      return Promise.resolve(false);
    }
    return new Promise((resolve) => {
      finish = resolve;
      win = new BrowserWindow({
        width: 1000,
        height: 780,
        minWidth: 780,
        minHeight: 600,
        title: `${appName} setup`,
        parent: parent || undefined,
        webPreferences: {
          preload: path.join(__dirname, "setup-preload.js"),
          contextIsolation: true,
          nodeIntegration: false,
          sandbox: true,
        },
      });
      win.setMenuBarVisibility(false);
      win.webContents.on("will-navigate", (event) => event.preventDefault());
      win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
      win.loadFile(path.join(__dirname, "setup", "index.html"), { query: { mode } });
      win.on("closed", () => {
        win = null;
        if (loginChild) loginChild.kill();
        if (finish) {
          const done = finish;
          finish = null;
          done(false);
        }
      });
    });
  }

  return { open };
}

module.exports = { createSetup };

"use strict";

// Desktop setup renderer. Talks to the main process only through window.setupApi.
const api = window.setupApi;
const $ = (id) => document.getElementById(id);
const mode = new URLSearchParams(location.search).get("mode") || "first";

const state = {
  info: null,
  answers: null,
  credentials: { native: false, stt: false },
  step: 0,
  installed: false,
  loaded: false,
  busy: false,
  saved: false,
  cli: { codex: null, claude: null },
  agy: null,
};

const allSteps = [...document.querySelectorAll(".step")];
const steps = allSteps.filter((el) => el.dataset.step !== "update");

function setStatus(id, text, tone = "") {
  const el = $(id);
  el.textContent = text || "";
  el.className = `status ${tone}`;
}

function log(line) {
  const el = $("log");
  el.hidden = false;
  el.textContent += `${line}\n`;
  el.scrollTop = el.scrollHeight;
}

function busy(on) {
  state.busy = on;
  $("next").disabled = on;
  $("back").disabled = on || state.step === 0;
}

const num = (id, fallback) => {
  const value = Number.parseInt($(id).value, 10);
  return Number.isInteger(value) ? value : fallback;
};

// ── navigation ────────────────────────────────────────────────────────────

function renderStepList() {
  const list = $("step-list");
  list.innerHTML = "";
  steps.forEach((el, i) => {
    const li = document.createElement("li");
    li.textContent = el.dataset.title;
    if (i === state.step) li.className = "current";
    else if (i < state.step) li.className = "done";
    list.appendChild(li);
  });
}

function show(index) {
  state.step = index;
  steps.forEach((el, i) => { el.hidden = i !== index; });
  renderStepList();
  const name = steps[index].dataset.step;
  $("back").disabled = index === 0;
  $("next").textContent = name === "review" ? (state.saved ? "Launch" : "Save") : "Next";
  if (name === "providers") refreshClis();
  if (name === "tui") refreshCargo();
  if (name === "review") renderSummary();
}

async function next() {
  if (state.busy) return;
  const name = steps[state.step].dataset.step;
  const error = await validate(name);
  if (error) return;
  if (name === "install" && !state.loaded) {
    const loaded = await loadAnswers();
    if (!loaded) return;
  }
  if (name === "review") {
    if (state.saved) {
      await api.finish({ saved: true });
      return;
    }
    await save();
    return;
  }
  show(state.step + 1);
}

async function validate(name) {
  if (name === "install") {
    if (!state.installed) {
      setStatus("install-status", "Install first, or choose an existing install and press Install.", "bad");
      return "install";
    }
  }
  if (name === "app" && !$("app-name").value.trim()) {
    $("app-name").classList.add("bad");
    return "app";
  }
  if (name === "server") {
    const paths = allowedPaths();
    if (!paths.length) {
      setStatus("paths-status", "Add at least one folder the agent may work in.", "bad");
      return "server";
    }
    const port = num("server-port", 0);
    if (!(port > 0 && port < 65536)) {
      $("server-port").classList.add("bad");
      return "server";
    }
    if ($("server-host").value === "0.0.0.0" && !$("lan-confirm").checked) {
      $("lan-confirm").focus();
      setStatus("paths-status", "Confirm the LAN warning, or bind to 127.0.0.1.", "bad");
      return "server";
    }
    const db = $("db-name").value.trim();
    if (!db || /[\\/]|\.\./.test(db)) {
      $("db-name").classList.add("bad");
      return "server";
    }
    setStatus("paths-status", "");
  }
  if (name === "fleet" && $("fleet-enabled").checked) {
    const problem = fleetProblem();
    if (problem) {
      setStatus("fleet-status", problem, "bad");
      return "fleet";
    }
    setStatus("fleet-status", "");
  }
  return "";
}

// ── 1. install ────────────────────────────────────────────────────────────

let folderCheck = 0;
async function checkFolder() {
  const folder = $("install-folder").value.trim();
  const ticket = ++folderCheck;
  const result = await api.checkFolder(folder);
  if (ticket !== folderCheck) return result;
  if (result.error) setStatus("folder-status", result.error, "bad");
  else if (result.existingInstall) {
    setStatus("folder-status", result.hasConfig
      ? "An existing install: its configuration, data and environment are kept."
      : "An existing install without a configuration.", "ok");
  } else setStatus("folder-status", "A new install goes here.", "");
  return result;
}

async function checkPython() {
  const exe = $("python-path").value.trim();
  if (!exe) {
    setStatus("python-status", state.info?.venvExists ? "Not needed: the install already has its environment." : "Choose a Python executable.", state.info?.venvExists ? "ok" : "bad");
    return Boolean(state.info?.venvExists);
  }
  setStatus("python-status", "Checking…");
  const result = await api.checkPython(exe);
  setStatus("python-status", result.ok ? `Python ${result.version} (${result.executable})` : result.error, result.ok ? "ok" : "bad");
  return result.ok;
}

async function install() {
  const folder = $("install-folder").value.trim();
  const folderResult = await checkFolder();
  if (folderResult.error) return;
  const sameInstall = state.info?.installRoot && state.info.installRoot === folder && state.info.venvExists;
  if (!sameInstall && !$("recreate").checked && !(await checkPython())) {
    // A folder that already holds an environment does not need a Python.
    if (!folderResult.existingInstall) return;
  }
  busy(true);
  $("install").disabled = true;
  setStatus("install-status", "Installing…");
  const result = await api.install({ folder, python: $("python-path").value.trim(), recreate: $("recreate").checked });
  $("install").disabled = false;
  busy(false);
  if (!result.ok) {
    setStatus("install-status", result.error, "bad");
    return;
  }
  state.installed = true;
  state.loaded = false;
  state.info = await api.info();
  $("recreate").checked = false;
  setStatus("install-status", `Installed in ${result.root}. Press Next.`, "ok");
}

// ── loading and filling the answers ──────────────────────────────────────

async function loadAnswers() {
  const result = await api.read();
  if (!result.ok) {
    setStatus("install-status", result.error, "bad");
    return false;
  }
  state.answers = result.answers;
  state.credentials = result.credentials;
  state.loaded = true;
  fill(result.answers);
  return true;
}

function fill(a) {
  $("app-name").value = a.app.appName;
  $("use-ascii").checked = mode === "first" || Boolean(a.app.splashAscii);
  refreshAscii();

  $("codex-model").value = a.codex.model;
  $("codex-reasoning").value = a.codex.reasoning;
  $("codex-timeout").value = a.codex.timeout;
  $("codex-enabled").checked = a.codex.enabled;
  $("claude-model").value = a.claude.model;
  $("claude-permission").value = a.claude.permissionMode;
  $("claude-timeout").value = a.claude.timeout;
  $("claude-max-turns").value = a.claude.maxTurns;
  $("claude-enabled").checked = a.claude.enabled;
  $("agy-accept").checked = a.acp.enabled;
  $("agy-enabled").checked = a.acp.enabled;
  $("native-url").value = a.native.baseUrl;
  $("native-model").value = a.native.model;
  $("native-timeout").value = a.native.timeout;
  $("native-iterations").value = a.native.maxIterations;
  $("native-badge").textContent = state.credentials.native ? "key stored" : "no key";
  $("native-badge").className = `badge ${state.credentials.native ? "ok" : ""}`;
  $("native-key").placeholder = state.credentials.native ? "••••••• (stored; blank keeps it)" : "";
  updatePermissionNote();

  const audio = a.audio;
  $("audio-enabled").checked = audio.enabled;
  $("audio-processor").value = audio.processor;
  const whisper = audio.processor === "api" ? "small" : audio.model;
  $("audio-model-whisper").value = whisper;
  $("audio-model-api").value = audio.processor === "api" ? audio.model : "whisper-1";
  $("audio-device").value = audio.device;
  $("audio-server").value = audio.server;
  $("audio-username").value = audio.username;
  $("audio-keyfile").value = audio.keyFile;
  $("audio-appdir").value = audio.appDir;
  $("audio-api-url").value = audio.apiBaseUrl || "https://api.openai.com/v1";
  $("audio-language").value = audio.language;
  $("audio-timeout").value = audio.timeout;
  $("audio-max-upload").value = audio.maxUploadMb;
  $("stt-key").placeholder = state.credentials.stt ? "••••••• (stored; blank keeps it)" : "";
  updateAudio();

  const server = a.server;
  $("allowed-paths").innerHTML = "";
  server.allowedPaths.forEach(addAllowedPath);
  $("approval-mode").value = server.approvalMode;
  $("server-host").value = server.host === "0.0.0.0" || server.host === "::" ? "0.0.0.0" : "127.0.0.1";
  $("lan-confirm").checked = $("server-host").value === "0.0.0.0" && mode !== "first";
  $("server-port").value = server.port;
  $("verbose-tools").checked = server.verboseTools;
  $("git-writes").checked = server.gitWrites;
  $("data-dir").value = server.dataDir;
  $("db-name").value = server.databaseFilename;
  $("logging-enabled").checked = server.loggingEnabled;
  $("log-dir").value = server.logDir;
  $("log-level").value = server.logLevel;
  $("log-retention").value = server.logRetentionDays;
  updateServer();

  const fleet = a.fleet;
  const host = state.info.hostname || "";
  $("fleet-enabled").checked = fleet.enabled;
  const self = fleet.hosts.find((h) => h.id === fleet.self);
  $("fleet-self-id").value = fleet.self || suggestHostId(host);
  $("fleet-self-label").value = self?.label || host.split(".")[0] || "";
  $("fleet-self-url").value = self?.url || `http://127.0.0.1:${server.port}`;
  $("fleet-poll").value = fleet.pollSeconds;
  $("fleet-hosts").innerHTML = "";
  fleet.hosts.filter((h) => h.id !== fleet.self).forEach((h) => addHostRow(h));
  updateFleet();
}

function suggestHostId(name) {
  return String(name || "").toLowerCase().split(".")[0].replace(/[^a-z0-9_-]/g, "") || "this-machine";
}

function collect() {
  const a = state.answers;
  const processor = $("audio-processor").value;
  const fleetOn = $("fleet-enabled").checked;
  const selfId = $("fleet-self-id").value.trim();
  const others = fleetRows();
  return {
    app: {
      appName: $("app-name").value.trim() || "MyHarness",
      splashAscii: $("use-ascii").checked ? $("ascii-preview").textContent : "",
    },
    codex: {
      enabled: $("codex-enabled").checked,
      binary: a.codex.binary,
      model: $("codex-model").value.trim(),
      timeout: num("codex-timeout", 1800),
      reasoning: $("codex-reasoning").value,
    },
    claude: {
      enabled: $("claude-enabled").checked,
      binary: a.claude.binary,
      model: $("claude-model").value.trim(),
      timeout: num("claude-timeout", 1800),
      maxTurns: num("claude-max-turns", 0),
      permissionMode: $("claude-permission").value,
    },
    acp: { enabled: $("agy-accept").checked && $("agy-enabled").checked },
    native: {
      enabled: state.credentials.native || Boolean($("native-key").value.trim()),
      baseUrl: $("native-url").value.trim() || "https://openrouter.ai/api/v1",
      model: $("native-model").value.trim() || "openai/gpt-5.2",
      timeout: num("native-timeout", 120),
      maxIterations: num("native-iterations", 20),
    },
    audio: {
      enabled: $("audio-enabled").checked,
      processor,
      server: $("audio-server").value.trim(),
      username: $("audio-username").value.trim(),
      keyFile: $("audio-keyfile").value.trim(),
      appDir: $("audio-appdir").value.trim() || "/opt/apps/whisperAudio",
      apiBaseUrl: processor === "api" ? $("audio-api-url").value.trim() : a.audio.apiBaseUrl,
      model: processor === "api" ? ($("audio-model-api").value.trim() || "whisper-1") : ($("audio-model-whisper").value.trim() || "small"),
      language: $("audio-language").value.trim(),
      device: $("audio-device").value,
      timeout: num("audio-timeout", 1800),
      maxUploadMb: num("audio-max-upload", 500),
    },
    frontends: { desktop: true },
    server: {
      host: $("server-host").value,
      port: num("server-port", 8420),
      allowedPaths: allowedPaths(),
      approvalMode: $("approval-mode").value,
      verboseTools: $("verbose-tools").checked,
      gitWrites: $("git-writes").checked,
      dataDir: $("data-dir").value.trim(),
      databaseFilename: $("db-name").value.trim() || "myharness.sqlite3",
      loggingEnabled: $("logging-enabled").checked,
      logDir: $("logging-enabled").checked ? $("log-dir").value.trim() : "",
      logLevel: $("log-level").value,
      logRetentionDays: num("log-retention", 30),
    },
    fleet: fleetOn
      ? {
          enabled: true,
          self: selfId,
          pollSeconds: num("fleet-poll", 10),
          hosts: [
            { id: selfId, label: $("fleet-self-label").value.trim() || selfId, url: $("fleet-self-url").value.trim().replace(/\/+$/, "") },
            ...others.map(({ id, label, url }) => ({ id, label: label || id, url })),
          ],
        }
      : { enabled: false, self: "", pollSeconds: 10, hosts: [] },
  };
}

// ── 2. app name ──────────────────────────────────────────────────────────

let asciiTimer = null;
function refreshAscii() {
  clearTimeout(asciiTimer);
  asciiTimer = setTimeout(async () => {
    const { art } = await api.ascii($("app-name").value.trim());
    $("ascii-preview").textContent = (art || "").replace(/\s+$/, "");
    $("ascii-preview").hidden = !art;
  }, 200);
}

// ── 3. providers ─────────────────────────────────────────────────────────

function cliCard(id) {
  const card = document.querySelector(`[data-cli="${id}"]`);
  const part = (role) => card.querySelector(`[data-role="${role}"]`);
  return { card, part };
}

function renderCli(id, status) {
  const { part } = cliCard(id);
  state.cli[id] = status;
  const badge = part("badge");
  if (!status) {
    badge.textContent = "checking…";
    badge.className = "badge";
    return;
  }
  badge.textContent = !status.installed ? "not installed" : status.authenticated ? `logged in · ${status.version}` : "not logged in";
  badge.className = `badge ${status.authenticated ? "ok" : status.installed ? "warn" : ""}`;
  part("install").hidden = status.installed;
  part("login").hidden = !status.installed || status.authenticated;
  const enable = part("enable");
  enable.disabled = !status.authenticated;
  if (!status.authenticated) enable.checked = false;
}

async function refreshCli(id) {
  renderCli(id, null);
  renderCli(id, await api.cliStatus(id));
}

function refreshClis() {
  refreshCli("codex");
  refreshCli("claude");
  refreshAgy();
}

// ── Antigravity ──────────────────────────────────────────────────────────

function renderAgy() {
  const status = state.agy;
  const accepted = $("agy-accept").checked;
  const badge = $("agy-badge");
  if (!status) {
    badge.textContent = "checking…";
    badge.className = "badge";
    return;
  }
  $("agy-notice").textContent = status.notice;
  badge.textContent = status.installed ? `installed · ${status.version}` : "not installed";
  badge.className = `badge ${status.installed ? "ok" : ""}`;
  $("agy-install").hidden = status.installed || !status.installCommand;
  $("agy-install").disabled = !accepted || state.busy;
  $("agy-install").title = status.installCommand;
  $("agy-login").hidden = !status.installed;
  $("agy-login").disabled = !accepted || state.busy;
  $("agy-enabled").disabled = !accepted || !status.installed;
  if (!accepted || !status.installed) $("agy-enabled").checked = false;
  $("agy-hint").textContent = !status.installed
    ? (status.installCommand
      ? `Install runs: ${status.installCommand}`
      : `Install the Antigravity CLI from ${status.siteUrl}, then press Check again.`)
    : `Sign in once with ${status.loginCommand}. Setup cannot check the sign-in; if a thread says Antigravity is not signed in, sign in and send the message again.`;
}

async function refreshAgy() {
  state.agy = null;
  renderAgy();
  state.agy = await api.agyStatus();
  renderAgy();
}

async function agyAction(action) {
  busy(true);
  renderAgy();
  setStatus("cli-status", action === "login" ? "Finish the Google sign-in in the browser window that opens." : "Installing Antigravity…");
  if (action === "login") $("agy-cancel").hidden = false;
  const result = action === "login"
    ? await api.agyLogin()
    : await api.agyInstall({ acceptedNotice: $("agy-accept").checked });
  $("agy-cancel").hidden = true;
  busy(false);
  setStatus("cli-status", result.ok ? (action === "login" ? "Sign-in finished." : "Antigravity installed.") : result.error, result.ok ? "ok" : "bad");
  state.agy = result.installed === undefined ? await api.agyStatus() : result;
  if (result.ok && action === "install") $("agy-enabled").checked = true;
  renderAgy();
}

async function cliAction(id, action) {
  const { part } = cliCard(id);
  busy(true);
  setStatus("cli-status", action === "login"
    ? "Finish the login in the browser window that opens. Output is shown below."
    : "Installing…");
  if (action === "login") part("cancel").hidden = false;
  const result = action === "login" ? await api.cliLogin(id) : await api.cliInstall(id);
  part("cancel").hidden = true;
  busy(false);
  if (!result.ok) {
    setStatus("cli-status", result.error, "bad");
  } else {
    setStatus("cli-status", result.authenticated ? "Logged in." : (action === "login"
      ? "The login could not be verified. Try again, or run the login command in a terminal and press Check again."
      : "Installed."), result.authenticated ? "ok" : "warn");
    if (result.authenticated) part("enable").checked = true;
  }
  renderCli(id, result.ok ? result : await api.cliStatus(id));
}

function updatePermissionNote() {
  $("claude-permission-note").hidden = !$("claude-permission").value;
}

// ── 4. voice ─────────────────────────────────────────────────────────────

function updateAudio() {
  const on = $("audio-enabled").checked;
  $("audio-box").hidden = !on;
  const processor = $("audio-processor").value;
  document.querySelectorAll("[data-processor]").forEach((el) => {
    el.hidden = !el.dataset.processor.split(" ").includes(processor);
  });
}

// ── 5. server ────────────────────────────────────────────────────────────

function allowedPaths() {
  return [...$("allowed-paths").querySelectorAll("li")].map((li) => li.dataset.path);
}

function addAllowedPath(folder) {
  if (!folder || allowedPaths().includes(folder)) return;
  const li = document.createElement("li");
  li.dataset.path = folder;
  const span = document.createElement("span");
  span.textContent = folder;
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "link";
  remove.textContent = "Remove";
  remove.addEventListener("click", () => li.remove());
  li.append(span, remove);
  $("allowed-paths").appendChild(li);
  setStatus("paths-status", "");
}

function updateServer() {
  $("lan-box").hidden = $("server-host").value !== "0.0.0.0";
  $("approval-note").hidden = $("approval-mode").value !== "auto_approve";
}

// ── 6. fleet ─────────────────────────────────────────────────────────────

const HOST_FIELDS = [
  { key: "id", placeholder: "workstation" },
  { key: "label", placeholder: "Workstation" },
  { key: "url", placeholder: "http://127.0.0.1:8421" },
  { key: "sshHost", placeholder: "workstation.local" },
  { key: "remotePort", placeholder: "8420" },
];

function addHostRow(values = {}) {
  const tr = document.createElement("tr");
  for (const field of HOST_FIELDS) {
    const td = document.createElement("td");
    const input = document.createElement("input");
    input.placeholder = field.placeholder;
    input.spellcheck = false;
    input.dataset.key = field.key;
    input.value = values[field.key] ?? "";
    td.appendChild(input);
    tr.appendChild(td);
  }
  const td = document.createElement("td");
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "link";
  remove.textContent = "Remove";
  remove.addEventListener("click", () => tr.remove());
  td.appendChild(remove);
  tr.appendChild(td);
  $("fleet-hosts").appendChild(tr);
}

function fleetRows() {
  return [...$("fleet-hosts").querySelectorAll("tr")]
    .map((tr) => Object.fromEntries([...tr.querySelectorAll("input")].map((i) => [i.dataset.key, i.value.trim()])))
    .filter((row) => row.id || row.url)
    .map((row) => ({ ...row, url: row.url.replace(/\/+$/, "") }));
}

const LOOPBACK = /^https?:\/\/(?:127\.0\.0\.1|localhost)(?::(\d+))?/;

function fleetTunnels() {
  if (!$("fleet-enabled").checked) return [];
  return fleetRows().flatMap((row) => {
    const match = row.url.match(LOOPBACK);
    if (!match) return [];
    return [{ localPort: match[1] || "80", remotePort: Number.parseInt(row.remotePort, 10) || 8420, sshHost: row.sshHost }];
  });
}

function fleetProblem() {
  const selfId = $("fleet-self-id").value.trim();
  const rows = fleetRows();
  if (!rows.length) return "A fleet needs at least one other machine.";
  const ids = [selfId, ...rows.map((r) => r.id)];
  for (const id of ids) {
    if (!id) return "Every machine needs an id.";
    if (id.includes(":")) return `Ids cannot contain ":" (${id}).`;
  }
  if (new Set(ids).size !== ids.length) return "Every machine needs a different id.";
  for (const url of [$("fleet-self-url").value.trim(), ...rows.map((r) => r.url)]) {
    if (!/^https?:\/\/[^/\s]+/.test(url)) return `Enter a full URL such as http://127.0.0.1:8421 (${url || "blank"}).`;
  }
  for (const row of rows) {
    if (LOOPBACK.test(row.url) && !row.sshHost) return `${row.id} is reached over 127.0.0.1, so give its SSH host for the tunnel.`;
  }
  return "";
}

function updateFleet() {
  $("fleet-box").hidden = !$("fleet-enabled").checked;
}

// ── 7. TUI ───────────────────────────────────────────────────────────────

async function refreshCargo() {
  if (!state.info?.tuiBundled) {
    setStatus("cargo-status", "This build of the app does not include the terminal UI sources.", "warn");
    $("build-tui").disabled = true;
    return;
  }
  setStatus("cargo-status", "Looking for cargo…");
  const result = await api.checkCargo();
  $("build-tui").disabled = !result.ok;
  setStatus("cargo-status", result.ok
    ? `Found ${result.version}.`
    : "cargo was not found. Install Rust from rustup.rs, then press Check for cargo again.", result.ok ? "ok" : "warn");
}

async function buildTui() {
  busy(true);
  $("build-tui").disabled = true;
  setStatus("tui-status", "Building… the first build can take several minutes.");
  const result = await api.buildTui();
  $("build-tui").disabled = false;
  busy(false);
  if (!result.ok) {
    setStatus("tui-status", result.error, "bad");
    return;
  }
  state.tuiBinary = result.binary;
  const url = `http://127.0.0.1:${num("server-port", 8420)}`;
  setStatus("tui-status", `Built ${result.binary}. Start it while the app is running; set MYHARNESS_BACKEND_URL (default ${url}) to attach to another machine.`, "ok");
}

// ── 8. review and save ───────────────────────────────────────────────────

function renderSummary() {
  const a = collect();
  const providers = [
    a.native.enabled && "Native",
    a.codex.enabled && "Codex",
    a.claude.enabled && "Claude",
    a.acp.enabled && "Antigravity",
  ].filter(Boolean);
  const rows = [
    ["Install folder", state.info.installRoot],
    ["App name", a.app.appName],
    ["Providers", providers.length ? providers.join(", ") : "none yet: threads cannot run until one is enabled"],
    ["Native model", `${a.native.model} at ${a.native.baseUrl}`],
    ["Voice dictation", a.audio.enabled ? a.audio.processor : "off"],
    ["Workspaces", a.server.allowedPaths.join("\n")],
    ["Approval mode", a.server.approvalMode],
    ["Backend", `${a.server.host}:${a.server.port}`],
    ["Data folder", a.server.dataDir],
    ["Fleet", a.fleet.enabled ? a.fleet.hosts.map((h) => h.id).join(", ") : "off"],
    ["Terminal UI", state.tuiBinary || "not built"],
  ];
  const list = $("summary");
  list.innerHTML = "";
  for (const [label, value] of rows) {
    const li = document.createElement("li");
    const b = document.createElement("b");
    b.textContent = label;
    const span = document.createElement("span");
    span.textContent = value;
    li.append(b, span);
    list.appendChild(li);
  }
  if (!state.saved) setStatus("save-status", providers.length ? "" : "No provider is enabled. You can still save and add one later.", providers.length ? "" : "warn");
}

async function save() {
  busy(true);
  setStatus("save-status", "Saving…");
  const result = await api.save({
    answers: collect(),
    credentials: { native: $("native-key").value.trim(), stt: $("stt-key").value.trim() },
    tunnels: fleetTunnels(),
  });
  busy(false);
  if (!result.ok) {
    setStatus("save-status", result.error, "bad");
    return;
  }
  state.saved = true;
  $("native-key").value = "";
  $("stt-key").value = "";
  setStatus("save-status", `Saved ${result.configPath}${result.backupPath ? ` (previous version in ${result.backupPath})` : ""}.`, "ok");
  const notes = $("save-notes");
  notes.innerHTML = "";
  for (const note of result.notes || []) {
    const li = document.createElement("li");
    li.textContent = note;
    notes.appendChild(li);
  }
  notes.hidden = !(result.notes || []).length;
  $("next").textContent = "Launch";
}

// ── update mode ──────────────────────────────────────────────────────────

async function runUpdate() {
  $("update-retry").hidden = true;
  setStatus("update-status", "Copying the new files…");
  const result = await api.install({ folder: state.info.installRoot });
  if (result.ok) {
    setStatus("update-status", "Done.", "ok");
    await api.finish({ saved: true });
    return;
  }
  setStatus("update-status", result.error, "bad");
  $("update-retry").hidden = false;
}

// ── wiring ───────────────────────────────────────────────────────────────

function wire() {
  $("next").addEventListener("click", next);
  $("back").addEventListener("click", () => { if (!state.busy && state.step > 0) show(state.step - 1); });
  $("cancel").addEventListener("click", () => api.finish({ saved: state.saved }));
  document.querySelectorAll("[data-link]").forEach((el) => el.addEventListener("click", () => api.openLink(el.dataset.link)));

  $("install-folder").addEventListener("change", checkFolder);
  $("pick-folder").addEventListener("click", async () => {
    const chosen = await api.pick({ kind: "dir", title: "Install folder", defaultPath: $("install-folder").value.trim() });
    if (chosen) { $("install-folder").value = chosen; checkFolder(); }
  });
  $("pick-python").addEventListener("click", async () => {
    const chosen = await api.pick({ kind: "file", title: "Python executable" });
    if (chosen) { $("python-path").value = chosen; checkPython(); }
  });
  $("check-python").addEventListener("click", checkPython);
  $("install").addEventListener("click", install);

  $("app-name").addEventListener("input", () => { $("app-name").classList.remove("bad"); refreshAscii(); });

  for (const id of ["codex", "claude"]) {
    const { part } = cliCard(id);
    part("install").addEventListener("click", () => cliAction(id, "install"));
    part("login").addEventListener("click", () => cliAction(id, "login"));
    part("cancel").addEventListener("click", () => api.cliLoginCancel());
    part("recheck").addEventListener("click", () => refreshCli(id));
  }
  $("claude-permission").addEventListener("change", updatePermissionNote);
  $("agy-accept").addEventListener("change", renderAgy);
  $("agy-install").addEventListener("click", () => agyAction("install"));
  $("agy-login").addEventListener("click", () => agyAction("login"));
  $("agy-cancel").addEventListener("click", () => api.cliLoginCancel());
  $("agy-recheck").addEventListener("click", refreshAgy);

  $("audio-enabled").addEventListener("change", updateAudio);
  $("audio-processor").addEventListener("change", updateAudio);
  $("install-whisper").addEventListener("click", async () => {
    busy(true);
    setStatus("whisper-status", "Installing…");
    const result = await api.installWhisper();
    busy(false);
    setStatus("whisper-status", result.ok ? "faster-whisper installed." : result.error, result.ok ? "ok" : "bad");
  });

  $("add-path").addEventListener("click", async () => {
    const chosen = await api.pick({ kind: "dir", title: "Allowed workspace folder", defaultPath: state.info?.home });
    if (chosen) addAllowedPath(chosen);
  });
  $("server-host").addEventListener("change", updateServer);
  $("approval-mode").addEventListener("change", updateServer);
  ["server-port", "db-name"].forEach((id) => $(id).addEventListener("input", () => $(id).classList.remove("bad")));
  document.querySelectorAll("[data-pick-dir]").forEach((button) => button.addEventListener("click", async () => {
    const target = $(button.dataset.pickDir);
    const chosen = await api.pick({ kind: "dir", title: "Choose a folder", defaultPath: target.value.trim() });
    if (chosen) target.value = chosen;
  }));

  $("fleet-enabled").addEventListener("change", updateFleet);
  $("add-host").addEventListener("click", () => addHostRow());

  $("build-tui").addEventListener("click", buildTui);
  $("recheck-cargo").addEventListener("click", refreshCargo);

  $("update-retry").addEventListener("click", runUpdate);

  api.onLog(log);
}

async function start() {
  wire();
  state.info = await api.info();
  $("app-title").textContent = `${state.info.appName} setup`;
  document.title = `${state.info.appName} setup`;

  if (mode === "update") {
    document.body.classList.add("update");
    allSteps.forEach((el) => { el.hidden = el.dataset.step !== "update"; });
    runUpdate();
    return;
  }

  $("install-folder").value = state.info.installRoot || state.info.defaultInstallRoot;
  const guesses = $("python-guesses");
  for (const guess of state.info.pythonGuesses) {
    guesses.appendChild(new Option(`Python ${guess.version}${guess.ok ? "" : " (too old)"}`, guess.path));
  }
  const usable = state.info.pythonGuesses.find((guess) => guess.ok);
  if (usable) $("python-path").value = usable.path;
  else setStatus("python-status", "No Python 3.10 or newer was found. Install one, then choose it here.", "warn");
  if (state.info.installRoot && state.info.venvExists) {
    state.installed = true;
    setStatus("install-status", "Already installed. Press Next to change settings, or Install to refresh the files.", "ok");
  }
  checkFolder();
  show(0);
}

start().catch((error) => setStatus("install-status", String(error?.message || error), "bad"));

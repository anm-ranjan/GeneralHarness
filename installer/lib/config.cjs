"use strict";

/**
 * Config generation shared by both installers: the terminal wizard
 * (installer/setup.mjs) and the desktop app's setup window (electron/setup.js).
 *
 * agent_config.example.yaml is the canonical template. Rather than
 * round-tripping it through a YAML parser (which would drop every comment), the
 * answered keys are replaced in place, line by line. Indentation is a
 * consistent two spaces.
 *
 * CommonJS so the Electron main process can require it from the packaged
 * resources; setup.mjs imports it as an ES module.
 */

const path = require("node:path");

function indentOf(line) {
  return line.length - line.trimStart().length;
}

function isSkippable(line) {
  const trimmed = line.trim();
  return trimmed === "" || trimmed.startsWith("#");
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
    if (line.trim() === "") continue;
    if (indentOf(line) <= indent) return i;
  }
  return lines.length;
}

/** End of the *value* of the key at `index`: list items, block scalar body. */
function valueEnd(lines, index, indent) {
  for (let i = index + 1; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim() === "") return i;
    if (indentOf(line) <= indent) return i;
  }
  return lines.length;
}

function yamlScalar(value) {
  if (typeof value === "boolean" || typeof value === "number") return String(value);
  if (value === null || value === undefined) return "null";
  // JSON string syntax is a valid YAML double-quoted scalar and escapes safely.
  return JSON.stringify(String(value));
}

function normalizeDatabaseFilename(value) {
  let filename = String(value || "myharness.sqlite3").trim();
  const lowered = filename.toLowerCase();
  if (
    !filename
    || filename === "."
    || filename === ".."
    || filename.includes("..")
    || filename.includes("/")
    || filename.includes("\\")
    || filename.includes("\0")
    || ["-wal", "-shm", "-journal"].some((suffix) => lowered.endsWith(suffix))
  ) {
    throw new Error("Enter a filename only, without parent paths or SQLite sidecar suffixes.");
  }
  if (!lowered.endsWith(".sqlite3")) filename += ".sqlite3";
  if (filename === ".sqlite3") throw new Error("Enter a database name.");
  return filename;
}

function readScalarValue(text, keyPath, fallback = "") {
  const lines = String(text || "").split("\n");
  const found = findKey(lines, keyPath);
  if (!found) return fallback;
  const raw = lines[found.index].slice(lines[found.index].indexOf(":") + 1).trim();
  if (!raw) return fallback;
  if (raw.startsWith("\"")) {
    try {
      return JSON.parse(raw);
    } catch {
      return fallback;
    }
  }
  if (raw.startsWith("'") && raw.endsWith("'")) return raw.slice(1, -1).replaceAll("''", "'");
  return raw.replace(/\s+#.*$/, "").trim() || fallback;
}

function readStorageDefaults(configText, repoRoot) {
  return {
    dataDir: readScalarValue(
      configText,
      ["storage", "data_dir"],
      path.join(repoRoot, "data"),
    ) || path.join(repoRoot, "data"),
    databaseFilename: normalizeDatabaseFilename(readScalarValue(
      configText,
      ["storage", "database_filename"],
      "myharness.sqlite3",
    )),
  };
}

function dedent(text) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const widths = lines.filter((line) => line.trim()).map((line) => indentOf(line));
  const shift = widths.length ? Math.min(...widths) : 0;
  return lines.map((line) => (line.trim() ? line.slice(shift) : "")).join("\n");
}

class ConfigEditor {
  /** `onMissing(keyPath)` is told about every key the template lacks. */
  constructor(text, { onMissing = () => {} } = {}) {
    this.lines = text.split("\n");
    this.onMissing = onMissing;
  }

  locate(keyPath) {
    const found = findKey(this.lines, keyPath);
    if (!found) this.onMissing(keyPath);
    return found;
  }

  replace(keyPath, newLines) {
    const found = this.locate(keyPath);
    if (!found) return false;
    const stop = valueEnd(this.lines, found.index, found.indent);
    this.lines.splice(found.index, stop - found.index, ...newLines);
    return true;
  }

  set(keyPath, value) {
    const found = this.locate(keyPath);
    if (!found) return false;
    const pad = " ".repeat(found.indent);
    const key = keyPath[keyPath.length - 1];
    // Preserve a trailing inline comment when the template value is unquoted
    // (a quoted value could legitimately contain a "#").
    const original = this.lines[found.index];
    const commentMatch = original.includes("\"") || original.includes("'")
      ? null
      : original.match(/\s{2,}(#.*)$/);
    const suffix = commentMatch ? `   ${commentMatch[1]}` : "";
    return this.replace(keyPath, [`${pad}${key}: ${yamlScalar(value)}${suffix}`]);
  }

  setList(keyPath, values) {
    const found = this.locate(keyPath);
    if (!found) return false;
    const pad = " ".repeat(found.indent);
    const key = keyPath[keyPath.length - 1];
    if (!values.length) return this.replace(keyPath, [`${pad}${key}: []`]);
    return this.replace(keyPath, [
      `${pad}${key}:`,
      ...values.map((value) => `${pad}  - ${yamlScalar(value)}`),
    ]);
  }

  /**
   * Write a list of mappings, e.g. fleet.hosts:
   *
   *   hosts:
   *     - id: "laptop"
   *       label: "Laptop"
   *
   * `fields` fixes the key order so regenerating a config produces a stable
   * diff rather than reshuffling on object-key order.
   */
  setMappingList(keyPath, entries, fields) {
    const found = this.locate(keyPath);
    if (!found) return false;
    const pad = " ".repeat(found.indent);
    const key = keyPath[keyPath.length - 1];
    if (!entries.length) return this.replace(keyPath, [`${pad}${key}: []`]);

    const body = [];
    for (const entry of entries) {
      const present = fields.filter((field) => entry[field] !== undefined);
      present.forEach((field, i) => {
        const prefix = i === 0 ? `${pad}  - ` : `${pad}    `;
        body.push(`${prefix}${field}: ${yamlScalar(entry[field])}`);
      });
    }
    return this.replace(keyPath, [`${pad}${key}:`, ...body]);
  }

  setBlockText(keyPath, text) {
    const found = this.locate(keyPath);
    if (!found) return false;
    const pad = " ".repeat(found.indent);
    const key = keyPath[keyPath.length - 1];
    const body = dedent(text).replace(/\s+$/, "");
    if (!body) return this.replace(keyPath, [`${pad}${key}: ""`]);
    // "|2-" pins the block indentation two columns past the key, so art whose
    // first line happens to start with spaces cannot confuse the parser.
    return this.replace(keyPath, [
      `${pad}${key}: |2-`,
      ...body.split("\n").map((line) => (line.trim() ? `${pad}  ${line.replace(/\s+$/, "")}` : "")),
    ]);
  }

  toString() {
    return this.lines.join("\n");
  }
}

/** Apply the answers to the template text and return the finished YAML. */
function buildConfig(answers, templateText, { onMissing } = {}) {
  const editor = new ConfigEditor(templateText, { onMissing });
  const codex = typeof answers.codex === "boolean" ? { enabled: answers.codex } : answers.codex;
  const claude = typeof answers.claude === "boolean" ? { enabled: answers.claude } : answers.claude;

  editor.set(["api", "enabled"], answers.native.enabled ?? Boolean(answers.native.apiKey));
  editor.set(["api", "base_url"], answers.native.baseUrl);
  editor.set(["api", "api_key"], "");
  editor.set(["api", "timeout_seconds"], answers.native.timeout ?? 120);
  for (const role of ["default", "read", "write", "summary"]) {
    editor.set(["models", role], answers.native.model);
  }

  editor.set(["permissions", "approval_mode"], answers.server.approvalMode);
  editor.setList(["permissions", "allowed_paths"], answers.server.allowedPaths);

  editor.set(["server", "host"], answers.server.host);
  editor.set(["server", "port"], answers.server.port);
  editor.set(["agent", "default_provider"], "native");
  editor.set(["agent", "max_iterations"], answers.native.maxIterations ?? 20);
  editor.set(["storage", "data_dir"], answers.server.dataDir || "");
  editor.set(
    ["storage", "database_filename"],
    normalizeDatabaseFilename(answers.server.databaseFilename || "myharness.sqlite3"),
  );
  editor.set(["logging", "enabled"], answers.server.loggingEnabled ?? true);
  editor.set(["logging", "log_dir"], answers.server.logDir || "");
  editor.set(["logging", "level"], answers.server.logLevel || "info");
  editor.set(["logging", "retention_days"], answers.server.logRetentionDays ?? 30);

  editor.set(["ui", "app_name"], answers.app.appName);
  editor.setBlockText(["ui", "splash_ascii"], answers.app.splashAscii);
  editor.set(["ui", "verbose_tools"], answers.server.verboseTools);
  editor.set(["ui", "git_writes_enabled"], answers.server.gitWrites);

  editor.set(["audio", "enabled"], answers.audio.enabled);
  editor.set(["audio", "transcription", "processor"], answers.audio.processor);
  editor.set(["audio", "transcription", "server"], answers.audio.server);
  editor.set(["audio", "transcription", "username"], answers.audio.username || "");
  editor.set(["audio", "transcription", "key_file"], answers.audio.keyFile || "");
  editor.set(["audio", "transcription", "app_dir"], answers.audio.appDir);
  editor.set(["audio", "transcription", "api_base_url"], answers.audio.apiBaseUrl);
  editor.set(["audio", "transcription", "api_key"], "");
  editor.set(["audio", "transcription", "model"], answers.audio.model);
  editor.set(["audio", "transcription", "language"], answers.audio.language || "");
  editor.set(["audio", "transcription", "device"], answers.audio.device || "cpu");
  editor.set(["audio", "transcription", "timeout_seconds"], answers.audio.timeout ?? 1800);
  editor.set(["audio", "transcription", "max_upload_mb"], answers.audio.maxUploadMb ?? 500);

  editor.set(["codex_app_server", "enabled"], Boolean(codex.enabled));
  editor.set(["codex_app_server", "binary"], codex.binary || "codex");
  editor.set(["codex_app_server", "model"], codex.model || null);
  editor.set(["codex_app_server", "timeout_seconds"], codex.timeout ?? 1800);
  editor.set(["codex_app_server", "reasoning_effort"], codex.reasoning || "low");
  editor.set(["claude_agent", "enabled"], Boolean(claude.enabled));
  editor.set(["claude_agent", "binary"], claude.binary || "claude");
  editor.set(["claude_agent", "model"], claude.model || null);
  editor.set(["claude_agent", "timeout_seconds"], claude.timeout ?? 1800);
  editor.set(["claude_agent", "max_turns"], claude.maxTurns ?? 0);
  editor.set(["claude_agent", "permission_mode"], claude.permissionMode || "");

  // Only when the installer asked: a config with other ACP agents keeps them.
  if (answers.acp) editor.set(["acp", "enabled"], Boolean(answers.acp.enabled));

  // Absent for a single-machine setup, and for configs written before fleets
  // existed, so fall back to the template's disabled defaults.
  const fleet = answers.fleet || { enabled: false, self: "", pollSeconds: 10, hosts: [] };
  editor.set(["fleet", "enabled"], Boolean(fleet.enabled));
  editor.set(["fleet", "self"], fleet.self || "");
  editor.set(["fleet", "poll_seconds"], fleet.pollSeconds ?? 10);
  editor.setMappingList(["fleet", "hosts"], fleet.hosts || [], ["id", "label", "url"]);

  editor.set(["desktop", "enabled"], answers.frontends.desktop);
  editor.set(["desktop", "backend_url"], `http://${answers.server.host === "0.0.0.0" ? "127.0.0.1" : answers.server.host}:${answers.server.port}`);

  return editor.toString();
}

/**
 * The answers a parsed agent_config.yaml corresponds to, so a setup run on an
 * existing install starts from what is there. `config` is the YAML parsed to a
 * plain object (the desktop setup parses it with the install's own PyYAML).
 */
function answersFromConfig(config) {
  const cfg = config && typeof config === "object" ? config : {};
  const get = (...keys) => keys.reduce((node, key) => (node && typeof node === "object" ? node[key] : undefined), cfg);
  const str = (value, fallback = "") => (value === undefined || value === null ? fallback : String(value));
  const num = (value, fallback) => (Number.isFinite(Number(value)) && value !== "" && value !== null ? Number(value) : fallback);
  const bool = (value, fallback) => (typeof value === "boolean" ? value : fallback);
  const audio = get("audio", "transcription") || {};
  const hosts = Array.isArray(get("fleet", "hosts")) ? get("fleet", "hosts") : [];
  return {
    app: {
      appName: str(get("ui", "app_name"), "MyHarness") || "MyHarness",
      splashAscii: str(get("ui", "splash_ascii")),
    },
    native: {
      baseUrl: str(get("api", "base_url"), "https://openrouter.ai/api/v1"),
      model: str(get("models", "default"), "openai/gpt-5.2"),
      timeout: num(get("api", "timeout_seconds"), 120),
      maxIterations: num(get("agent", "max_iterations"), 20),
    },
    codex: {
      enabled: bool(get("codex_app_server", "enabled"), false),
      binary: str(get("codex_app_server", "binary"), "codex") || "codex",
      model: str(get("codex_app_server", "model")),
      timeout: num(get("codex_app_server", "timeout_seconds"), 1800),
      reasoning: str(get("codex_app_server", "reasoning_effort"), "low") || "low",
    },
    claude: {
      enabled: bool(get("claude_agent", "enabled"), false),
      binary: str(get("claude_agent", "binary"), "claude") || "claude",
      model: str(get("claude_agent", "model")),
      timeout: num(get("claude_agent", "timeout_seconds"), 1800),
      maxTurns: num(get("claude_agent", "max_turns"), 0),
      permissionMode: str(get("claude_agent", "permission_mode")),
    },
    acp: {
      enabled: bool(get("acp", "enabled"), false),
    },
    audio: {
      enabled: bool(get("audio", "enabled"), false),
      processor: str(audio.processor, "local") || "local",
      server: str(audio.server),
      username: str(audio.username),
      keyFile: str(audio.key_file),
      appDir: str(audio.app_dir, "/opt/apps/whisperAudio") || "/opt/apps/whisperAudio",
      apiBaseUrl: str(audio.api_base_url),
      model: str(audio.model, "small") || "small",
      language: str(audio.language),
      device: str(audio.device, "cpu") || "cpu",
      timeout: num(audio.timeout_seconds, 1800),
      maxUploadMb: num(audio.max_upload_mb, 500),
    },
    server: {
      host: str(get("server", "host"), "127.0.0.1") || "127.0.0.1",
      port: num(get("server", "port"), 8420),
      allowedPaths: (Array.isArray(get("permissions", "allowed_paths")) ? get("permissions", "allowed_paths") : [])
        .map(String)
        .filter((entry) => entry && entry !== "/path/to/your/workspace"),
      approvalMode: str(get("permissions", "approval_mode"), "always_ask") || "always_ask",
      verboseTools: bool(get("ui", "verbose_tools"), false),
      gitWrites: bool(get("ui", "git_writes_enabled"), false),
      dataDir: str(get("storage", "data_dir")),
      databaseFilename: str(get("storage", "database_filename"), "myharness.sqlite3") || "myharness.sqlite3",
      loggingEnabled: bool(get("logging", "enabled"), true),
      logDir: str(get("logging", "log_dir")),
      logLevel: str(get("logging", "level"), "info") || "info",
      logRetentionDays: num(get("logging", "retention_days"), 30),
    },
    fleet: {
      enabled: bool(get("fleet", "enabled"), false),
      self: str(get("fleet", "self")),
      pollSeconds: num(get("fleet", "poll_seconds"), 10),
      hosts: hosts
        .filter((host) => host && typeof host === "object" && host.id && host.url)
        .map((host) => ({ id: String(host.id), label: str(host.label, String(host.id)), url: String(host.url) })),
    },
  };
}

/**
 * A launcher for the SSH tunnels a fleet needs.
 *
 * Remote hosts are reached over loopback so their backends can stay bound to
 * 127.0.0.1, which means a tunnel has to be running before the UI can switch
 * to them. An installer cannot hold one open, so it writes this instead: the
 * tunnels are supervised and restarted, because a dropped one looks exactly
 * like a machine that went offline.
 */
function fleetTunnelScript(tunnels) {
  const entries = tunnels.map(
    (tunnel) => `  "${tunnel.localPort}:127.0.0.1:${tunnel.remotePort} ${tunnel.sshHost}"`,
  );
  return [
    "#!/usr/bin/env bash",
    "# Generated by the local agent harness setup. Re-run setup to regenerate.",
    "#",
    "# Opens one SSH tunnel per remote fleet host and keeps them open. Leave this",
    "# running while you want to switch hosts in the UI; Ctrl+C closes them all.",
    "#",
    "# Each entry is \"<local port>:127.0.0.1:<remote port> <ssh host>\", matching a",
    "# url in fleet.hosts in backend/agent/agent_config.yaml.",
    "set -uo pipefail",
    "",
    "TUNNELS=(",
    ...entries,
    ")",
    "",
    "pids=()",
    "cleanup() {",
    "  trap - INT TERM",
    "  for pid in \"${pids[@]:-}\"; do",
    "    [ -n \"$pid\" ] && kill \"$pid\" 2>/dev/null",
    "  done",
    "  exit 0",
    "}",
    "trap cleanup INT TERM",
    "",
    "for entry in \"${TUNNELS[@]}\"; do",
    "  # Supervise each tunnel: ssh exits when the network drops or the remote",
    "  # host reboots, and a silently dead tunnel is indistinguishable in the UI",
    "  # from a host that is switched off.",
    "  (",
    "    while true; do",
    "      # ExitOnForwardFailure makes a port that is already bound fail loudly",
    "      # instead of leaving a tunnel that forwards nothing.",
    "      # $entry is deliberately unquoted: it splits into the -L argument",
    "      # and the ssh host.",
    "      # shellcheck disable=SC2086",
    "      ssh -N \\",
    "        -o ExitOnForwardFailure=yes \\",
    "        -o ServerAliveInterval=30 \\",
    "        -o ServerAliveCountMax=3 \\",
    "        -L ${entry} || true",
    "      echo \"tunnel ${entry} dropped; retrying in 5s\" >&2",
    "      sleep 5",
    "    done",
    "  ) &",
    "  pids+=($!)",
    "  echo \"tunnel up: ${entry}\"",
    "done",
    "",
    "echo \"Press Ctrl+C to close all tunnels.\"",
    "wait",
    "",
  ].join("\n");
}

/** A plain identifier usable as a fleet host id: lowercase, no separators. */
function suggestHostId(name) {
  const cleaned = String(name || "").toLowerCase().split(".")[0].replace(/[^a-z0-9_-]/g, "");
  return cleaned || "this-machine";
}

// Import names, not distribution names: PyYAML installs as `yaml`, PyMuPDF as
// `fitz`. These are the modules backend/web_app.py needs to reach its first
// line of work, so if any is missing the backend cannot start at all.
const REQUIRED_BACKEND_MODULES = ["fastapi", "uvicorn", "pydantic", "yaml", "requests"];

/** Args that import every module the backend cannot start without. */
function backendImportCheckArgs() {
  return ["-c", `import ${REQUIRED_BACKEND_MODULES.join(", ")}`];
}

// The subscription CLIs the installers can install, log in and enable.
const CLI_PROVIDERS = {
  codex: {
    title: "Codex CLI provider",
    label: "Codex",
    binary: "codex",
    pkg: "@openai/codex",
    authStatusArgs: ["login", "status"],
    authLoginArgs: ["login"],
    authJson: false,
    docs: "https://learn.chatgpt.com/docs/codex/cli",
  },
  claude: {
    title: "Claude Code provider",
    label: "Claude",
    binary: "claude",
    pkg: "@anthropic-ai/claude-code",
    authStatusArgs: ["auth", "status", "--json"],
    authLoginArgs: ["auth", "login"],
    authJson: true,
    docs: "https://code.claude.com/docs/en/authentication",
  },
};

// Google's Antigravity CLI, driven over ACP (provider acp:antigravity).
const ANTIGRAVITY = {
  label: "Antigravity",
  binary: "agy",
  loginArgs: ["acp", "--login"],
  brewCask: "antigravity-cli",
  installScriptUrl: "https://antigravity.google/cli/install.sh",
  siteUrl: "https://antigravity.google",
  termsUrl: "https://policies.google.com/terms",
};

// Shown by both installers before Antigravity is installed or enabled.
const ANTIGRAVITY_NOTICE = [
  "Antigravity runs with your personal Google sign-in. Before you install or enable it,",
  "check Google's terms of service and Antigravity's own terms for whether using it",
  "through another application is allowed for your account. Google may restrict or",
  "block accounts that use it in ways its terms do not allow.",
  "MyHarness accepts no responsibility if your Google account is restricted or blocked.",
].join(" ");

/**
 * How to install the Antigravity CLI on this platform: Homebrew on macOS,
 * Google's install script on Linux, nothing automatic elsewhere.
 * Returns { command, args, display } or null.
 */
function antigravityInstallCommand(platform, { hasBrew = false } = {}) {
  if (platform === "darwin" && hasBrew) {
    return { command: "brew", args: ["install", "--cask", ANTIGRAVITY.brewCask], display: `brew install --cask ${ANTIGRAVITY.brewCask}` };
  }
  if (platform === "linux") {
    const display = `curl -fsSL ${ANTIGRAVITY.installScriptUrl} | bash`;
    return { command: "bash", args: ["-c", display], display };
  }
  return null;
}

/** Whether a CLI's auth-status output (exit code 0) means it is logged in. */
function cliAuthOutputOk(spec, stdout) {
  if (!spec.authJson) return true;
  try {
    const parsed = JSON.parse(String(stdout || ""));
    return parsed.loggedIn === true || parsed.authenticated === true;
  } catch {
    return false;
  }
}

/** "3.12" / "3.12.4" → true when it is Python 3.10 or newer. */
function supportedPythonVersion(version) {
  const [major, minor] = String(version || "").split(".").map((part) => Number.parseInt(part, 10));
  return major > 3 || (major === 3 && minor >= 10);
}

module.exports = {
  ConfigEditor,
  buildConfig,
  answersFromConfig,
  findKey,
  dedent,
  yamlScalar,
  normalizeDatabaseFilename,
  readScalarValue,
  readStorageDefaults,
  fleetTunnelScript,
  suggestHostId,
  REQUIRED_BACKEND_MODULES,
  backendImportCheckArgs,
  supportedPythonVersion,
  CLI_PROVIDERS,
  cliAuthOutputOk,
  ANTIGRAVITY,
  ANTIGRAVITY_NOTICE,
  antigravityInstallCommand,
};

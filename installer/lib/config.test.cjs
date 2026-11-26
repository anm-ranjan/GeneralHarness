"use strict";

/**
 *   node --test installer/lib/config.test.cjs
 */

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const {
  answersFromConfig,
  buildConfig,
  readScalarValue,
  supportedPythonVersion,
  cliAuthOutputOk,
  CLI_PROVIDERS,
  ANTIGRAVITY_NOTICE,
  antigravityInstallCommand,
} = require("./config.cjs");

const TEMPLATE = fs.readFileSync(path.join(__dirname, "..", "..", "backend", "agent", "agent_config.example.yaml"), "utf8");

test("defaults from an empty config are a safe local setup", () => {
  const answers = answersFromConfig({});
  assert.equal(answers.server.host, "127.0.0.1");
  assert.equal(answers.server.port, 8420);
  assert.equal(answers.server.approvalMode, "always_ask");
  assert.deepEqual(answers.server.allowedPaths, []);
  assert.equal(answers.codex.enabled, false);
  assert.equal(answers.claude.enabled, false);
  assert.equal(answers.fleet.enabled, false);
});

test("an existing config's values are carried into the answers", () => {
  const answers = answersFromConfig({
    ui: { app_name: "2B|!2B", splash_ascii: "ART", verbose_tools: true },
    server: { host: "0.0.0.0", port: 9000 },
    permissions: { approval_mode: "shell_only", allowed_paths: ["/work", "/path/to/your/workspace"] },
    codex_app_server: { enabled: true, reasoning_effort: "high", timeout_seconds: 60 },
    audio: { enabled: true, transcription: { processor: "api", model: "whisper-1", api_base_url: "https://x/v1" } },
    fleet: { enabled: true, self: "mac", poll_seconds: 5, hosts: [{ id: "mac", label: "Mac", url: "http://127.0.0.1:8420" }, { id: "bad" }] },
  });
  assert.equal(answers.app.appName, "2B|!2B");
  assert.equal(answers.server.host, "0.0.0.0");
  assert.equal(answers.server.port, 9000);
  assert.deepEqual(answers.server.allowedPaths, ["/work"], "the template placeholder is dropped");
  assert.equal(answers.codex.reasoning, "high");
  assert.equal(answers.audio.processor, "api");
  assert.deepEqual(answers.fleet.hosts, [{ id: "mac", label: "Mac", url: "http://127.0.0.1:8420" }]);
});

test("answers read back from a config rebuild the same values", () => {
  const answers = answersFromConfig({});
  answers.app.appName = "Desk";
  answers.server.allowedPaths = ["/Users/me/work"];
  answers.server.dataDir = "/Users/me/harness/data";
  answers.frontends = { desktop: true };
  const text = buildConfig(answers, TEMPLATE);
  assert.equal(readScalarValue(text, ["ui", "app_name"]), "Desk");
  assert.equal(readScalarValue(text, ["storage", "data_dir"]), "/Users/me/harness/data");
  assert.equal(readScalarValue(text, ["desktop", "backend_url"]), "http://127.0.0.1:8420");
  assert.match(text, /allowed_paths:\n\s+- "\/Users\/me\/work"/);
});

test("buildConfig reports keys the template lacks instead of failing", () => {
  const missing = [];
  const answers = { ...answersFromConfig({}), frontends: { desktop: true } };
  buildConfig(answers, "api:\n  enabled: false\n", { onMissing: (keyPath) => missing.push(keyPath.join(".")) });
  assert.ok(missing.includes("server.port"));
  assert.ok(!missing.includes("api.enabled"));
});

test("reasoning replay reads YAML false as off and is written quoted", () => {
  assert.equal(answersFromConfig({}).native.reasoningReplay, "off");
  assert.equal(answersFromConfig({ reasoning_replay: { mode: false } }).native.reasoningReplay, "off");
  assert.equal(answersFromConfig({ reasoning_replay: { mode: "bogus" } }).native.reasoningReplay, "off");
  const read = answersFromConfig({ reasoning_replay: { mode: "KEEP", models: { "inkling-*": "keep", "glm-5.2": "off" } } });
  assert.equal(read.native.reasoningReplay, "keep");
  assert.deepEqual(read.native.reasoningReplayOverrides, ["glm-5.2", "inkling-*"]);

  const answers = { ...answersFromConfig({}), frontends: { desktop: true } };
  answers.native.reasoningReplay = "strip_always";
  const text = buildConfig(answers, TEMPLATE);
  assert.match(text, /reasoning_replay:\n  mode: "strip_always"\n/);
  answers.native.reasoningReplay = "off";
  assert.match(buildConfig(answers, text), /reasoning_replay:\n  mode: "off"\n/);
  assert.match(text, /# Quote "off"/); // template comments survive
});

test("reasoning replay keeps per-model entries and is added to older configs", () => {
  const answers = { ...answersFromConfig({}), frontends: { desktop: true } };
  answers.native.reasoningReplay = "keep";
  const withModels = TEMPLATE.replace("  models: {}\n    # my-thinking-model", "  models:\n    glm-5.2: keep\n    # my-thinking-model");
  assert.match(buildConfig(answers, withModels), /reasoning_replay:\n  mode: "keep"\n  # Exact[^\n]*\n  models:\n    glm-5.2: keep\n/);

  const missing = [];
  answers.native.reasoningReplay = "strip_on_compact";
  const old = buildConfig(answers, "api:\n  base_url: https://gateway.example/v1\n", { onMissing: (k) => missing.push(k.join(".")) });
  assert.ok(!missing.some((k) => k.startsWith("reasoning_replay")));
  assert.match(old, /\nreasoning_replay:\n  mode: "strip_on_compact"\n/);
  assert.equal(readScalarValue(old, ["reasoning_replay", "mode"]), "strip_on_compact");

  // An unknown mode leaves the file alone.
  answers.native.reasoningReplay = "bogus";
  assert.doesNotMatch(buildConfig(answers, "api:\n  base_url: x\n"), /reasoning_replay/);
});

test("Python 3.10 or newer is required", () => {
  assert.equal(supportedPythonVersion("3.10.0"), true);
  assert.equal(supportedPythonVersion("3.13"), true);
  assert.equal(supportedPythonVersion("4.0"), true);
  assert.equal(supportedPythonVersion("3.9.18"), false);
  assert.equal(supportedPythonVersion(""), false);
});

test("Claude's JSON auth status must say it is logged in; Codex only needs exit 0", () => {
  assert.equal(cliAuthOutputOk(CLI_PROVIDERS.claude, "{\"loggedIn\": true}"), true);
  assert.equal(cliAuthOutputOk(CLI_PROVIDERS.claude, "{\"loggedIn\": false}"), false);
  assert.equal(cliAuthOutputOk(CLI_PROVIDERS.claude, "not json"), false);
  assert.equal(cliAuthOutputOk(CLI_PROVIDERS.codex, "Logged in"), true);
});

test("Antigravity is enabled in the config only when the installer asked about it", () => {
  const base = { ...answersFromConfig({}), frontends: { desktop: true } };
  delete base.acp;
  const untouched = buildConfig(base, TEMPLATE);
  assert.equal(readScalarValue(untouched, ["acp", "enabled"]), "false");
  const enabled = buildConfig({ ...base, acp: { enabled: true } }, TEMPLATE);
  assert.equal(readScalarValue(enabled, ["acp", "enabled"]), "true");
  assert.equal(answersFromConfig({ acp: { enabled: true } }).acp.enabled, true);
});

test("Antigravity installs with Homebrew on macOS, Google's script on Linux, by hand elsewhere", () => {
  assert.deepEqual(antigravityInstallCommand("darwin", { hasBrew: true }).args, ["install", "--cask", "antigravity-cli"]);
  assert.equal(antigravityInstallCommand("darwin", { hasBrew: false }), null);
  assert.match(antigravityInstallCommand("linux").display, /antigravity\.google\/cli\/install\.sh \| bash$/);
  assert.equal(antigravityInstallCommand("win32"), null);
});

test("the Antigravity notice points to Google's terms and disclaims responsibility for blocked accounts", () => {
  assert.match(ANTIGRAVITY_NOTICE, /terms of service/);
  assert.match(ANTIGRAVITY_NOTICE, /MyHarness accepts no responsibility if your Google account is restricted or blocked/);
});

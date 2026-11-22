"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { syncAppFiles } = require("./install-files.cjs");

function write(root, rel, text = rel) {
  const file = path.join(root, ...rel.split("/"));
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text);
}

const read = (root, rel) => fs.readFileSync(path.join(root, ...rel.split("/")), "utf-8");
const exists = (root, rel) => fs.existsSync(path.join(root, ...rel.split("/")));

function bundle() {
  const from = fs.mkdtempSync(path.join(os.tmpdir(), "myharness-bundle-"));
  write(from, "backend/web_app.py", "new app");
  write(from, "backend/agent/agent_config.example.yaml", "template");
  write(from, "backend/__pycache__/web_app.cpython-312.pyc");
  write(from, "frontend/dist/index.html", "new ui");
  write(from, "skills/bundled/SKILL.md", "new skill");
  write(from, "agents/site-check/AGENT.md", "new agent");
  write(from, "tui-rs/Cargo.toml", "cargo");
  write(from, "tui-rs/target/release/stale", "from the build machine");
  write(from, "requirements.txt", "fastapi");
  return from;
}

test("a first install copies the app files and creates data and logs", () => {
  const from = bundle();
  const to = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "myharness-root-")), "install");
  syncAppFiles({ from, to });
  assert.equal(read(to, "backend/web_app.py"), "new app");
  assert.equal(read(to, "frontend/dist/index.html"), "new ui");
  assert.equal(read(to, "requirements.txt"), "fastapi");
  assert.ok(exists(to, "tui-rs/Cargo.toml"));
  assert.ok(!exists(to, "tui-rs/target"), "a TUI build is never copied");
  assert.ok(!exists(to, "backend/__pycache__"), "caches are not copied");
  assert.ok(exists(to, "data") && exists(to, "logs"));
});

test("an update replaces app code but keeps everything that belongs to the user", () => {
  const from = bundle();
  const to = fs.mkdtempSync(path.join(os.tmpdir(), "myharness-root-"));
  write(to, "backend/web_app.py", "old app");
  write(to, "backend/removed_module.py", "deleted upstream");
  write(to, "backend/agent/agent_config.yaml", "my config");
  write(to, "backend/agent/agent_config.yaml.bak", "my backup");
  write(to, "backend/agent/agent_config.yaml.bak-acp-20261121", "my other backup");
  write(to, "frontend/dist/assets/old-hash.js", "old bundle");
  write(to, "skills/mine/SKILL.md", "my skill");
  write(to, "agents/my-agent/AGENT.md", "my agent");
  write(to, "agents/site-check/AGENT.md", "old agent");
  write(to, "tui-rs/target/release/myharness-tui", "my build");
  write(to, ".venv/bin/python", "venv");
  write(to, "data/myharness.sqlite3", "db");

  syncAppFiles({ from, to });

  assert.equal(read(to, "backend/web_app.py"), "new app");
  assert.ok(!exists(to, "backend/removed_module.py"), "files a newer build removed do not linger");
  assert.equal(read(to, "backend/agent/agent_config.yaml"), "my config");
  assert.equal(read(to, "backend/agent/agent_config.yaml.bak"), "my backup");
  assert.equal(read(to, "backend/agent/agent_config.yaml.bak-acp-20261121"), "my other backup");
  assert.ok(!exists(to, "frontend/dist/assets/old-hash.js"));
  assert.equal(read(to, "skills/mine/SKILL.md"), "my skill");
  assert.equal(read(to, "agents/my-agent/AGENT.md"), "my agent");
  assert.equal(read(to, "agents/site-check/AGENT.md"), "new agent");
  assert.equal(read(to, "tui-rs/target/release/myharness-tui"), "my build");
  assert.equal(read(to, ".venv/bin/python"), "venv");
  assert.equal(read(to, "data/myharness.sqlite3"), "db");
});

test("a source that carries its own config never overwrites the user's", () => {
  const from = bundle();
  write(from, "backend/agent/agent_config.yaml", "build machine config");
  write(from, "backend/agent/agent_config.yaml.bak-acp-20261121", "build machine backup");
  const to = fs.mkdtempSync(path.join(os.tmpdir(), "myharness-root-"));
  syncAppFiles({ from, to });
  assert.ok(!exists(to, "backend/agent/agent_config.yaml"));
  assert.ok(!exists(to, "backend/agent/agent_config.yaml.bak-acp-20261121"));
  assert.ok(exists(to, "backend/agent/agent_config.example.yaml"), "the template is still copied");
});

test("a bundle missing required files is refused", () => {
  const from = bundle();
  fs.rmSync(path.join(from, "backend"), { recursive: true });
  const to = fs.mkdtempSync(path.join(os.tmpdir(), "myharness-root-"));
  assert.throws(() => syncAppFiles({ from, to }), /missing backend/);
});

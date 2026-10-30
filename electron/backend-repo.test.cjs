"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { chooseRepoRoot, readBackendRepoSetting } = require("./backend-repo.cjs");

const BUNDLED = "/opt/app/resources";
const yaml = (value) => `server:\n  port: 8420\ndesktop:\n  enabled: true\n  backend_repo: ${value}\n  disable_gpu: true\n`;
const exists = (...present) => (file) => present.includes(file);

test("reads the setting from the desktop block, quoted or not, ignoring comments", () => {
  assert.equal(readBackendRepoSetting(yaml("\"/home/a/app\"")), "/home/a/app");
  assert.equal(readBackendRepoSetting(yaml("'/home/a/app'  # my checkout")), "/home/a/app");
  assert.equal(readBackendRepoSetting(yaml("/home/a/app")), "/home/a/app");
  assert.equal(readBackendRepoSetting(yaml("\"\"")), "");
});

test("ignores the key outside the desktop block and a missing key", () => {
  assert.equal(readBackendRepoSetting("other:\n  backend_repo: /x\ndesktop:\n  enabled: true\n"), "");
  assert.equal(readBackendRepoSetting(""), "");
  assert.equal(readBackendRepoSetting(undefined), "");
});

test("uses the configured checkout when it has a backend", () => {
  const choice = chooseRepoRoot({
    bundledRoot: BUNDLED,
    yamlText: yaml("/home/a/app"),
    existsSync: exists("/home/a/app/backend/web_app.py"),
  });
  assert.deepEqual(choice, { root: "/home/a/app", source: "config", rejected: null });
});

test("the environment variable beats the packaged config", () => {
  const choice = chooseRepoRoot({
    bundledRoot: BUNDLED,
    envValue: "/dev/checkout",
    yamlText: yaml("/home/a/app"),
    existsSync: exists("/dev/checkout/backend/web_app.py", "/home/a/app/backend/web_app.py"),
  });
  assert.equal(choice.root, "/dev/checkout");
  assert.equal(choice.source, "env");
});

test("falls back to the bundled copy, saying why, when the checkout is unusable", () => {
  const missing = chooseRepoRoot({ bundledRoot: BUNDLED, yamlText: yaml("/home/a/app"), existsSync: exists() });
  assert.equal(missing.root, BUNDLED);
  assert.equal(missing.source, "bundled");
  assert.match(missing.rejected, /no backend\/web_app\.py/);

  const relative = chooseRepoRoot({ bundledRoot: BUNDLED, yamlText: yaml("app"), existsSync: exists() });
  assert.equal(relative.root, BUNDLED);
  assert.match(relative.rejected, /not an absolute path/);
});

test("nothing configured means the bundled copy, with no complaint", () => {
  const choice = chooseRepoRoot({ bundledRoot: BUNDLED, yamlText: yaml("\"\"") });
  assert.deepEqual(choice, { root: BUNDLED, source: "bundled", rejected: null });
});

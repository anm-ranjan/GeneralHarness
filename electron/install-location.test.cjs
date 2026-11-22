"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  pointerPath,
  readInstallPointer,
  writeInstallPointer,
  buildFingerprint,
  fileHash,
  checkInstallFolder,
} = require("./install-location.cjs");

const tempDir = () => fs.mkdtempSync(path.join(os.tmpdir(), "myharness-install-"));

test("the install pointer round-trips and resolves its root", () => {
  const userData = tempDir();
  const root = path.join(userData, "myharness");
  const file = pointerPath(userData, "myharness");
  writeInstallPointer(file, { root, build: "0.1.0:1:2", requirementsHash: "abc" });
  assert.deepEqual(readInstallPointer(file), { root, build: "0.1.0:1:2", requirementsHash: "abc" });
});

test("differently named builds keep separate install records", () => {
  const userData = tempDir();
  writeInstallPointer(pointerPath(userData, "2b-2b"), { root: "/a" });
  assert.equal(readInstallPointer(pointerPath(userData, "myharness-setup-test")), null);
  assert.equal(readInstallPointer(pointerPath(userData, "2b-2b")).root, path.resolve("/a"));
});

test("a missing, corrupt or relative pointer reads as no install", () => {
  const file = pointerPath(tempDir());
  assert.equal(readInstallPointer(file), null);
  fs.writeFileSync(file, "{not json");
  assert.equal(readInstallPointer(file), null);
  fs.writeFileSync(file, JSON.stringify({ root: "relative/dir" }));
  assert.equal(readInstallPointer(file), null);
});

test("the build fingerprint changes when the packaged archive does", () => {
  const stat = (size, mtimeMs) => () => ({ size, mtimeMs });
  const a = buildFingerprint({ version: "0.1.0", appPath: "/x", statSync: stat(10, 1000) });
  const b = buildFingerprint({ version: "0.1.0", appPath: "/x", statSync: stat(11, 1000) });
  const c = buildFingerprint({ version: "0.1.0", appPath: "/x", statSync: stat(10, 2000) });
  assert.notEqual(a, b);
  assert.notEqual(a, c);
  assert.equal(buildFingerprint({ version: "0.1.0", appPath: "/x", statSync: () => { throw new Error("gone"); } }), "0.1.0");
});

test("file hashes differ by content and are empty for a missing file", () => {
  const dir = tempDir();
  fs.writeFileSync(path.join(dir, "a"), "one");
  fs.writeFileSync(path.join(dir, "b"), "two");
  assert.notEqual(fileHash(path.join(dir, "a")), fileHash(path.join(dir, "b")));
  assert.equal(fileHash(path.join(dir, "missing")), "");
});

test("an install folder must be absolute, outside the app, and empty or an existing install", () => {
  const dir = tempDir();
  assert.match(checkInstallFolder(""), /Choose/);
  assert.match(checkInstallFolder("relative"), /absolute/);
  assert.equal(checkInstallFolder(path.join(dir, "new")), "");
  assert.equal(checkInstallFolder(dir), "");
  fs.writeFileSync(path.join(dir, "notes.txt"), "mine");
  assert.match(checkInstallFolder(dir), /not empty/);
  fs.mkdirSync(path.join(dir, "backend"));
  fs.writeFileSync(path.join(dir, "backend", "web_app.py"), "");
  assert.equal(checkInstallFolder(dir), "");
  assert.match(checkInstallFolder(path.join(dir, "notes.txt")), /file/);
  assert.match(checkInstallFolder("/opt/app/resources/x", { bundledRoot: "/opt/app/resources" }), /outside/);
});

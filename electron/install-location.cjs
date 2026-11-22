"use strict";

// Where a packaged app keeps its installed backend.
//
// The packaged app's own resources are read-only (/Applications, /opt,
// Program Files), so first-run setup copies the backend, skills, agents and UI
// into a folder the user chooses, creates its Python environment there and
// writes agent_config.yaml there. A small JSON file in the app's per-user data
// directory records that folder, along with a fingerprint of the build that
// last installed it, so a newer app build can refresh the installed code.
//
// Every build shares one per-user data directory (Electron names it after the
// package, not the product), so the record is named after the product: two
// differently named builds never share an install.

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

/** The install record for the product `slug` ("myharness", "2b-2b", …). */
function pointerPath(userDataDir, slug = "myharness") {
  return path.join(userDataDir, `install-${slug}.json`);
}

/** { root, build, requirementsHash } or null when missing or unusable. */
function readInstallPointer(file, { readFileSync = fs.readFileSync } = {}) {
  let parsed;
  try {
    parsed = JSON.parse(readFileSync(file, "utf-8"));
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object") return null;
  const root = String(parsed.root || "").trim();
  if (!root || !path.isAbsolute(root)) return null;
  return {
    root: path.resolve(root),
    build: String(parsed.build || ""),
    requirementsHash: String(parsed.requirementsHash || ""),
  };
}

function writeInstallPointer(file, pointer) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const body = {
    root: path.resolve(pointer.root),
    build: String(pointer.build || ""),
    requirementsHash: String(pointer.requirementsHash || ""),
  };
  const target = file;
  const temporary = `${target}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(body, null, 2)}\n`, "utf-8");
  fs.renameSync(temporary, target);
  return body;
}

/**
 * Identifies the app build that is running, so an install made by an older
 * build is refreshed. The version alone is not enough: local rebuilds keep
 * it, so the packaged archive's size and modification time are included.
 */
function buildFingerprint({ version, appPath, statSync = fs.statSync }) {
  try {
    const stat = statSync(appPath);
    return `${version}:${stat.size}:${Math.round(stat.mtimeMs)}`;
  } catch {
    return String(version || "");
  }
}

function fileHash(file, { readFileSync = fs.readFileSync } = {}) {
  try {
    return crypto.createHash("sha256").update(readFileSync(file)).digest("hex");
  } catch {
    return "";
  }
}

/**
 * Problems with a folder chosen as the install location, or "" when usable.
 * The folder may be new; an existing one must be empty or a previous install.
 */
function checkInstallFolder(folder, { bundledRoot, existsSync = fs.existsSync, readdirSync = fs.readdirSync, statSync = fs.statSync } = {}) {
  const raw = String(folder || "").trim();
  if (!raw) return "Choose a folder.";
  if (!path.isAbsolute(raw)) return "Enter an absolute path.";
  const resolved = path.resolve(raw);
  if (bundledRoot) {
    const bundled = path.resolve(bundledRoot);
    if (resolved === bundled || resolved.startsWith(bundled + path.sep)) {
      return "Choose a folder outside the application itself.";
    }
  }
  if (!existsSync(resolved)) return "";
  try {
    if (!statSync(resolved).isDirectory()) return "That path is a file, not a folder.";
  } catch {
    return "That folder cannot be read.";
  }
  if (existsSync(path.join(resolved, "backend", "web_app.py"))) return "";
  const entries = readdirSync(resolved).filter((name) => !name.startsWith("."));
  if (entries.length) {
    return "That folder is not empty. Choose an empty folder or an existing install.";
  }
  return "";
}

module.exports = {
  pointerPath,
  readInstallPointer,
  writeInstallPointer,
  buildFingerprint,
  fileHash,
  checkInstallFolder,
};

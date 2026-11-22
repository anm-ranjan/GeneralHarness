"use strict";

// Which folder the desktop shell runs the backend from.
//
// A packaged app carries its own copy of the backend, skills, config and
// Python environment, which goes stale and sits in a root-owned location. The
// desktop setup installs a user-owned copy (install-location.cjs), and
// desktop.backend_repo (or MYHARNESS_BACKEND_REPO) can name a checkout; the
// shell then runs that folder's backend, config, skills, data and UI. Anything
// unusable falls back to the bundled copy, so a typo never leaves the app
// without a backend.

const fs = require("node:fs");
const path = require("node:path");

/** Value of desktop.backend_repo in the packaged YAML, or "" when unset. */
function readBackendRepoSetting(yamlText) {
  let inDesktop = false;
  for (const line of String(yamlText || "").split(/\r?\n/)) {
    if (/^\S/.test(line)) inDesktop = /^desktop:\s*(?:#.*)?$/.test(line);
    if (!inDesktop) continue;
    const match = line.match(/^\s+backend_repo:\s*(.*?)\s*(?:#.*)?$/);
    if (!match) continue;
    const raw = match[1].trim();
    if ((raw.startsWith("\"") && raw.endsWith("\"")) || (raw.startsWith("'") && raw.endsWith("'"))) {
      return raw.slice(1, -1).trim();
    }
    return raw;
  }
  return "";
}

/**
 * Pick the folder to run the backend from, in order: MYHARNESS_BACKEND_REPO,
 * the folder the desktop setup installed into (`installRoot`), then
 * desktop.backend_repo (baked in at build time as `repoSetting`, or read from
 * a bundled YAML), then the bundled copy.
 * Returns { root, source: "env" | "install" | "config" | "bundled", rejected },
 * where `rejected` explains why a configured folder was not used (or is null).
 */
function chooseRepoRoot({ bundledRoot, envValue = "", installRoot = "", repoSetting = "", yamlText = "", existsSync = fs.existsSync }) {
  const fromEnv = String(envValue || "").trim();
  const fromInstall = String(installRoot || "").trim();
  const configured = fromEnv || fromInstall || String(repoSetting || "").trim() || readBackendRepoSetting(yamlText);
  if (!configured) return { root: bundledRoot, source: "bundled", rejected: null };
  const source = fromEnv ? "env" : fromInstall ? "install" : "config";
  if (!path.isAbsolute(configured)) {
    return { root: bundledRoot, source: "bundled", rejected: `${configured} is not an absolute path` };
  }
  const root = path.resolve(configured);
  if (!existsSync(path.join(root, "backend", "web_app.py"))) {
    return { root: bundledRoot, source: "bundled", rejected: `${root} has no backend/web_app.py` };
  }
  return { root, source, rejected: null };
}

module.exports = { chooseRepoRoot, readBackendRepoSetting };

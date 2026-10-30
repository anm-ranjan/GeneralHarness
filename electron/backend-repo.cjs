"use strict";

// Which folder the desktop shell runs the backend from.
//
// A packaged app carries its own copy of the backend, skills, config and
// Python environment, which goes stale and sits in a root-owned location. If
// desktop.backend_repo (or MYHARNESS_BACKEND_REPO) names a checkout, the shell
// runs that checkout's backend, config, skills, data and UI instead. Anything
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
 * Pick the folder to run the backend from.
 * Returns { root, source: "env" | "config" | "bundled", rejected }, where
 * `rejected` explains why a configured folder was not used (or is null).
 */
function chooseRepoRoot({ bundledRoot, envValue = "", yamlText = "", existsSync = fs.existsSync }) {
  const fromEnv = String(envValue || "").trim();
  const configured = fromEnv || readBackendRepoSetting(yamlText);
  if (!configured) return { root: bundledRoot, source: "bundled", rejected: null };
  const source = fromEnv ? "env" : "config";
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

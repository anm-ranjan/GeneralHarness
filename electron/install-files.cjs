"use strict";

// Copies the application files a packaged app carries into the user's install
// folder, on first setup and again whenever a newer build starts.
//
// What belongs to the user is never touched: agent_config.yaml, the Python
// environment, data/, logs/, routine agents and skills the user added, and a
// built TUI. Folders the app owns outright (backend code, the built UI) are
// replaced, so files a newer build deleted do not linger and shadow modules.

const fs = require("node:fs");
const path = require("node:path");

// mode "replace": the target folder is cleared first, except `keep` paths.
// mode "merge":   bundled files overwrite their copies; other files remain.
const APP_FILES = [
  { path: "backend", mode: "replace", keep: ["agent/agent_config.yaml"] },
  { path: "frontend/dist", mode: "replace" },
  { path: "skills", mode: "merge" },
  { path: "agents", mode: "merge" },
  { path: "tui-rs", mode: "replace", keep: ["target"], skip: ["target"], optional: true },
  { path: "requirements.txt", mode: "file" },
];

// Never copied from the source, whatever it is (a development checkout
// carries its own config and caches).
const SKIPPED_NAMES = new Set(["__pycache__", ".tool_cache", "node_modules"]);
// agent_config.yaml and every local backup of it (.bak, .bak-acp-20261121, …).
const LOCAL_CONFIG = /^agent_config\.yaml($|\.)/;

function copyFilter(item, source) {
  const name = path.basename(source);
  return !SKIPPED_NAMES.has(name) && !LOCAL_CONFIG.test(name)
    && !(item.skip || []).includes(name) && !name.endsWith(".pyc");
}

/** Remove everything under `dir` except the `keep` paths (relative, "/"-separated). */
function clearExcept(dir, keep, prefix = "") {
  if (!fs.existsSync(dir)) return;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
    const full = path.join(dir, entry.name);
    if (keep.includes(rel) || LOCAL_CONFIG.test(entry.name)) continue;
    if (entry.isDirectory() && keep.some((kept) => kept.startsWith(`${rel}/`))) {
      clearExcept(full, keep, rel);
      continue;
    }
    fs.rmSync(full, { recursive: true, force: true });
  }
}

/**
 * Copy the app files from `from` (the packaged resources) into `to`.
 * Returns the entries copied; throws when a required one is missing.
 */
function syncAppFiles({ from, to, log = () => {} }) {
  const copied = [];
  fs.mkdirSync(to, { recursive: true });
  for (const item of APP_FILES) {
    const source = path.join(from, ...item.path.split("/"));
    const target = path.join(to, ...item.path.split("/"));
    if (!fs.existsSync(source)) {
      if (item.optional) continue;
      throw new Error(`The application is missing ${item.path}; reinstall it.`);
    }
    log(`Copying ${item.path}`);
    if (item.mode === "file") {
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.copyFileSync(source, target);
    } else {
      if (item.mode === "replace") clearExcept(target, item.keep || []);
      fs.mkdirSync(target, { recursive: true });
      fs.cpSync(source, target, { recursive: true, force: true, filter: (source) => copyFilter(item, source) });
    }
    copied.push(item.path);
  }
  for (const folder of ["data", "logs"]) fs.mkdirSync(path.join(to, folder), { recursive: true });
  return copied;
}

module.exports = { APP_FILES, syncAppFiles, clearExcept };

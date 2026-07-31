"use strict";

const path = require("path");

const LOGIN_PATH_MARKER = "__MYHARNESS_LOGIN_PATH__";

function appendPath(entries, seen, value) {
  for (const entry of String(value || "").split(path.delimiter)) {
    const clean = entry.trim();
    if (!clean || seen.has(clean)) continue;
    seen.add(clean);
    entries.push(clean);
  }
}

function readLoginShellPath({ platform, env, spawnSync, existsSync }) {
  if (platform === "win32") return "";
  const shell = env.SHELL || (platform === "darwin" ? "/bin/zsh" : "/bin/sh");
  if (path.isAbsolute(shell) && !existsSync(shell)) return "";
  try {
    const result = spawnSync(
      shell,
      ["-ilc", `printf '\\n${LOGIN_PATH_MARKER}%s\\n' "$PATH"`],
      {
        encoding: "utf-8",
        env,
        timeout: 3_000,
        windowsHide: true,
      },
    );
    const stdout = String(result.stdout || "");
    const markerIndex = stdout.lastIndexOf(LOGIN_PATH_MARKER);
    if (markerIndex < 0) return "";
    return stdout.slice(markerIndex + LOGIN_PATH_MARKER.length).split(/\r?\n/, 1)[0].trim();
  } catch {
    return "";
  }
}

function buildExecutablePath({
  platform,
  env,
  homedir,
  spawnSync,
  existsSync,
}) {
  if (platform === "win32") return env.PATH || env.Path || "";

  const entries = [];
  const seen = new Set();
  appendPath(entries, seen, env.PATH);
  appendPath(entries, seen, readLoginShellPath({ platform, env, spawnSync, existsSync }));

  // Finder-launched macOS apps receive a minimal PATH. These cover the common
  // package-manager and per-user locations even when shell startup files are
  // unavailable or produce an error.
  appendPath(entries, seen, [
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
    "/usr/local/sbin",
    path.join(homedir, ".local", "bin"),
    path.join(homedir, ".npm-global", "bin"),
    path.join(homedir, ".cargo", "bin"),
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
  ].join(path.delimiter));
  return entries.join(path.delimiter);
}

module.exports = { buildExecutablePath };

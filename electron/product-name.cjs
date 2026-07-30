"use strict";

// electron-builder strips these from every name it puts on disk -- the .app
// bundle, the executable, and the four "<name> Helper*.app" bundles -- but
// writes the product name into CFBundleName verbatim. At startup Electron
// looks for "<CFBundleName> Helper.app", so a product name containing any of
// them produces a bundle that aborts before opening a window:
//
//   FATAL:electron/shell/app/electron_main_delegate_mac.mm] Unable to find helper app
//
// Sanitizing once, before electron-builder sees the name, keeps the on-disk
// names and CFBundleName identical. The display name the user typed still
// reaches the UI through ui.app_name in agent_config.yaml, which has no
// filesystem constraints.
const RESERVED_IN_FILENAMES = /[\\/:"*?<>|]+/g;

function sanitizeProductName(value) {
  const cleaned = String(value || "")
    .replace(RESERVED_IN_FILENAMES, "")
    // Collapse whitespace left behind by a removed character, and refuse
    // leading/trailing dots, which macOS treats as hidden files.
    .replace(/\s+/g, " ")
    .replace(/^[.\s]+|[.\s]+$/g, "");
  return cleaned || "MyHarness";
}

module.exports = { sanitizeProductName };

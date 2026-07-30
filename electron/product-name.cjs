"use strict";

// The product name becomes filesystem names on every platform: the macOS .app
// bundle, its executable, and the four "<name> Helper*.app" bundles; the
// Windows install directory; the Linux AppImage's executableName and
// productFilename. Each target polices that differently, and getting it wrong
// fails in two distinct ways:
//
//   macOS  - electron-builder silently strips \ / : " * ? < > | from the names
//            it writes but puts the raw name in CFBundleName. Electron then
//            looks for "<CFBundleName> Helper.app", cannot find it, and aborts
//            before opening a window:
//              FATAL:electron/shell/app/electron_main_delegate_mac.mm
//              Unable to find helper app
//   Linux  - the AppImage target validates against an allowlist and refuses to
//            build at all:
//              productFilename contains characters that cannot be safely used
//              in file paths
//
// The AppImage allowlist is the strictest of the three, so applying it
// everywhere yields one name that is valid on every platform. Copied verbatim
// from app-builder-lib/out/targets/appimage/appImageUtil.js
// (validateCriticalPathString): Unicode letters and digits, dot, underscore,
// hyphen, and space. It is Unicode-aware, so accented and non-Latin names such
// as "Café Harness" or "日本語ハーネス" pass through untouched.
const ALLOWED_IN_PRODUCT_NAME = /^[\p{L}\p{N}._\- ]+$/u;

// A run of rejected characters, plus any whitespace hugging it, so that
// "Harness | Desktop" becomes "Harness-Desktop" rather than "Harness - Desktop".
const REJECTED_RUN = /\s*[^\p{L}\p{N}._\- ]+\s*/gu;

function sanitizeProductName(value) {
  const cleaned = String(value || "")
    .replace(REJECTED_RUN, "-")
    // Tidy up what the substitution can leave behind.
    .replace(/\s+/g, " ")
    .replace(/-{2,}/g, "-")
    // Leading dots hide the app on macOS and Linux; leading or trailing
    // separators are just untidy.
    .replace(/^[-.\s]+|[-.\s]+$/g, "");

  // The rule above is the contract electron-builder enforces, so verify the
  // result against it rather than trusting the substitution to be exhaustive.
  return ALLOWED_IN_PRODUCT_NAME.test(cleaned) ? cleaned : "MyHarness";
}

module.exports = { sanitizeProductName, ALLOWED_IN_PRODUCT_NAME };

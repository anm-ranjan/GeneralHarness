"use strict";

const fs = require("node:fs");
const path = require("node:path");
const packageJson = require("./package.json");
const { sanitizeProductName } = require("./product-name.cjs");
const { readBackendRepoSetting } = require("./backend-repo.cjs");

// The package no longer carries agent_config.yaml (it asks for its settings on
// first launch), but a build machine that runs its app from a checkout via
// desktop.backend_repo keeps doing so: only that one value is baked in.
// MYHARNESS_PORTABLE_BUILD=1 leaves it out, for installers meant for other
// machines (and for trying first-run setup on this one).
const backendRepo = (() => {
  if (process.env.MYHARNESS_PORTABLE_BUILD === "1") return "";
  try {
    return readBackendRepoSetting(fs.readFileSync(path.join(__dirname, "..", "backend", "agent", "agent_config.yaml"), "utf-8"));
  } catch {
    return "";
  }
})();

const rawProductName = String(process.env.MYHARNESS_PRODUCT_NAME || "MyHarness").trim();
const productName = sanitizeProductName(rawProductName);
const slug = productName.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "myharness";

if (rawProductName && rawProductName !== productName) {
  console.warn(
    `[electron-builder] Packaging as "${productName}": "${rawProductName}" contains characters ` +
    "that cannot appear in a bundle name. The name shown inside the app is unaffected.",
  );
}

module.exports = {
  ...packageJson.build,
  productName,
  appId: `local.${slug}.desktop`,
  // productName is already filesystem-safe, so the artifact name needs no
  // second, differently-spelled sanitization pass.
  artifactName: `${productName}-\${version}-\${os}-\${arch}.\${ext}`,
  extraMetadata: {
    ...(packageJson.build.extraMetadata || {}),
    myharnessBackendRepo: backendRepo,
    myharnessProductSlug: slug,
  },
  mac: {
    ...packageJson.build.mac,
    extendInfo: {
      ...(packageJson.build.mac?.extendInfo || {}),
      CFBundleDisplayName: rawProductName,
    },
  },
  linux: {
    ...packageJson.build.linux,
    desktop: {
      ...(packageJson.build.linux?.desktop || {}),
      entry: {
        ...(packageJson.build.linux?.desktop?.entry || {}),
        Name: rawProductName,
      },
    },
  },
};

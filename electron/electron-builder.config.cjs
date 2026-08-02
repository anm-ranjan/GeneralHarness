"use strict";

const packageJson = require("./package.json");
const { sanitizeProductName } = require("./product-name.cjs");

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

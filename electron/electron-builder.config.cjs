"use strict";

const packageJson = require("./package.json");

const productName = String(process.env.MYHARNESS_PRODUCT_NAME || "MyHarness").trim() || "MyHarness";
const slug = productName.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "myharness";

module.exports = {
  ...packageJson.build,
  productName,
  appId: `local.${slug}.desktop`,
  artifactName: `${productName.replace(/[\\/:"*?<>|]+/g, "-")}-\${version}-\${os}-\${arch}.\${ext}`,
};

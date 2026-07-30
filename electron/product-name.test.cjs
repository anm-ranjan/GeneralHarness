"use strict";

/**
 * The packaged app aborts at startup when CFBundleName and the on-disk helper
 * bundle names disagree, so these pin the sanitization that keeps them equal.
 *
 *   node --test electron/product-name.test.cjs
 */

const assert = require("node:assert/strict");
const { test } = require("node:test");

const { sanitizeProductName } = require("./product-name.cjs");

test("ordinary names pass through untouched", () => {
  assert.equal(sanitizeProductName("MyHarness"), "MyHarness");
  assert.equal(sanitizeProductName("Harness 2B"), "Harness 2B");
});

test("characters electron-builder strips from filenames are removed", () => {
  // The real report: "2B|!2B" packaged as "2B!2B.app" with CFBundleName
  // "2B|!2B", so Electron looked for "2B|!2B Helper.app" and aborted.
  assert.equal(sanitizeProductName("2B|!2B"), "2B!2B");
  for (const char of ["\\", "/", ":", '"', "*", "?", "<", ">", "|"]) {
    assert.equal(
      sanitizeProductName(`A${char}B`),
      "AB",
      `"${char}" must not survive into the bundle name`,
    );
  }
});

test("whitespace left by a removed character is collapsed", () => {
  assert.equal(sanitizeProductName("Harness | Desktop"), "Harness Desktop");
});

test("leading and trailing dots and spaces are refused", () => {
  assert.equal(sanitizeProductName("  .Harness.  "), "Harness");
});

test("a name that sanitizes away falls back to MyHarness", () => {
  assert.equal(sanitizeProductName("///"), "MyHarness");
  assert.equal(sanitizeProductName(""), "MyHarness");
  assert.equal(sanitizeProductName(null), "MyHarness");
});

test("the builder config exposes only electron-builder keys", () => {
  process.env.MYHARNESS_PRODUCT_NAME = "2B|!2B";
  delete require.cache[require.resolve("./electron-builder.config.cjs")];
  const config = require("./electron-builder.config.cjs");

  assert.equal(config.productName, "2B!2B");
  assert.equal(config.appId, "local.2b-2b.desktop");
  assert.equal(config.artifactName, "2B!2B-${version}-${os}-${arch}.${ext}");
  // An unknown key here fails electron-builder's schema validation.
  assert.equal(config.sanitizeProductName, undefined);
});

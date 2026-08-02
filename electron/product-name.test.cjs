"use strict";

/**
 * The product name becomes filesystem names on all three platforms, and a bad
 * one either crashes the packaged macOS app at startup or fails the Linux
 * build outright. These pin the sanitization that prevents both.
 *
 *   node --test electron/product-name.test.cjs
 */

const assert = require("node:assert/strict");
const { test } = require("node:test");

const { sanitizeProductName } = require("./product-name.cjs");

// Copied verbatim from app-builder-lib's validateCriticalPathString, which the
// AppImage target applies to executableName and productFilename. Duplicated
// here on purpose: if a dependency bump changes the rule, this test should
// fail rather than quietly track it.
const ELECTRON_BUILDER_RULE = /^[\p{L}\p{N}._\- ]+$/u;

test("ordinary names pass through untouched", () => {
  for (const name of ["MyHarness", "Harness 2B", "My Harness Pro", "Acme-Agent", "Agent_9", "MyHarness v2.0"]) {
    assert.equal(sanitizeProductName(name), name);
  }
});

test("non-Latin names survive, since the rule is Unicode-aware", () => {
  assert.equal(sanitizeProductName("Café Harness"), "Café Harness");
  assert.equal(sanitizeProductName("日本語ハーネス"), "日本語ハーネス");
  assert.equal(sanitizeProductName("Ωmega"), "Ωmega");
});

test("rejected characters become a single hyphen", () => {
  // The real report: "2B|!2B" packaged on macOS as "2B!2B.app" with
  // CFBundleName "2B|!2B" (startup crash), then failed the Linux AppImage
  // build because "!" is not in the allowlist.
  assert.equal(sanitizeProductName("2B|!2B"), "2B-2B");
  assert.equal(sanitizeProductName("Dev:Harness"), "Dev-Harness");
  assert.equal(sanitizeProductName("A/B Test"), "A-B Test");
});

test("whitespace around a rejected run is absorbed into the hyphen", () => {
  assert.equal(sanitizeProductName("Harness | Desktop"), "Harness-Desktop");
  assert.equal(sanitizeProductName("A  B"), "A B");
});

test("leading and trailing separators are trimmed", () => {
  // A leading dot would hide the app on macOS and Linux.
  assert.equal(sanitizeProductName("  .Harness.  "), "Harness");
  assert.equal(sanitizeProductName("!Harness!"), "Harness");
});

test("a name with nothing usable falls back to MyHarness", () => {
  for (const name of ["", "   ", "///", "!!!", "???", null, undefined]) {
    assert.equal(sanitizeProductName(name), "MyHarness");
  }
});

test("every sanitized name satisfies electron-builder's own rule", () => {
  const names = [
    "MyHarness", "2B|!2B", "Harness | Desktop", "Dev:Harness", "A/B Test", "Who?", ".hidden",
    "Café Harness", "日本語ハーネス", "a\\b", 'q"q', "x*y", "p<q>r", "tab\there", "new\nline",
    "emoji 🎉 name", "", "!!!", "..", "--", "a".repeat(200),
  ];
  for (const name of names) {
    const sanitized = sanitizeProductName(name);
    assert.match(sanitized, ELECTRON_BUILDER_RULE, `rejected for ${JSON.stringify(name)}`);
    // Sanitizing an already-sanitized name must not change it further, or a
    // rebuild would keep renaming the app.
    assert.equal(sanitizeProductName(sanitized), sanitized, `not idempotent for ${JSON.stringify(name)}`);
  }
});

test("the builder config exposes only electron-builder keys", () => {
  process.env.MYHARNESS_PRODUCT_NAME = "2B|!2B";
  delete require.cache[require.resolve("./electron-builder.config.cjs")];
  const config = require("./electron-builder.config.cjs");

  assert.equal(config.productName, "2B-2B");
  assert.equal(config.appId, "local.2b-2b.desktop");
  assert.equal(config.artifactName, "2B-2B-${version}-${os}-${arch}.${ext}");
  assert.equal(config.mac.extendInfo.CFBundleDisplayName, "2B|!2B");
  assert.equal(config.linux.desktop.entry.Name, "2B|!2B");
  // An unknown key here fails electron-builder's schema validation.
  assert.equal(config.sanitizeProductName, undefined);
});

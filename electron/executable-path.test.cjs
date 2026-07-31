"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { buildExecutablePath } = require("./executable-path.cjs");

test("adds login-shell and Homebrew paths for a Finder-launched macOS app", () => {
  const value = buildExecutablePath({
    platform: "darwin",
    env: { PATH: "/usr/bin:/bin", SHELL: "/bin/zsh" },
    homedir: "/Users/tester",
    existsSync: () => true,
    spawnSync: () => ({
      stdout: "shell banner\n__MYHARNESS_LOGIN_PATH__/custom/bin:/usr/bin\n",
    }),
  });
  const entries = value.split(":");

  assert.deepEqual(entries.slice(0, 4), ["/usr/bin", "/bin", "/custom/bin", "/opt/homebrew/bin"]);
  assert.ok(entries.includes("/Users/tester/.local/bin"));
  assert.equal(entries.filter((entry) => entry === "/usr/bin").length, 1);
});

test("falls back to standard paths when the login shell cannot run", () => {
  const value = buildExecutablePath({
    platform: "linux",
    env: { PATH: "" },
    homedir: "/home/tester",
    existsSync: () => true,
    spawnSync: () => {
      throw new Error("shell unavailable");
    },
  });

  assert.ok(value.split(":").includes("/usr/local/bin"));
  assert.ok(value.split(":").includes("/home/tester/.cargo/bin"));
});

test("preserves the Windows executable path without invoking a shell", () => {
  let invoked = false;
  const value = buildExecutablePath({
    platform: "win32",
    env: { Path: "C:\\Windows\\System32" },
    homedir: "C:\\Users\\tester",
    existsSync: () => true,
    spawnSync: () => {
      invoked = true;
    },
  });

  assert.equal(value, "C:\\Windows\\System32");
  assert.equal(invoked, false);
});

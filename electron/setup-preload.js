"use strict";

const { contextBridge, ipcRenderer } = require("electron");

const call = (channel) => (...args) => ipcRenderer.invoke(channel, ...args);

contextBridge.exposeInMainWorld("setupApi", {
  info: call("setup:info"),
  checkFolder: call("setup:check-folder"),
  checkPython: call("setup:check-python"),
  install: call("setup:install"),
  read: call("setup:read"),
  ascii: call("setup:ascii"),
  cliStatus: call("setup:cli-status"),
  cliInstall: call("setup:cli-install"),
  cliLogin: call("setup:cli-login"),
  cliLoginCancel: call("setup:cli-login-cancel"),
  agyStatus: call("setup:agy-status"),
  agyInstall: call("setup:agy-install"),
  agyLogin: call("setup:agy-login"),
  checkCargo: call("setup:check-cargo"),
  buildTui: call("setup:build-tui"),
  installWhisper: call("setup:install-whisper"),
  checkDir: call("setup:check-dir"),
  pick: call("setup:pick"),
  openLink: call("setup:open-link"),
  save: call("setup:save"),
  finish: call("setup:finish"),
  onLog: (callback) => {
    const listener = (_event, line) => callback(line);
    ipcRenderer.on("setup:log", listener);
    return () => ipcRenderer.removeListener("setup:log", listener);
  },
});

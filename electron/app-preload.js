"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("myharnessDesktop", {
  getStatus: () => ipcRenderer.invoke("myharness:desktop-status"),
  quit: () => ipcRenderer.invoke("myharness:quit"),
  setBadgeCount: (count) => ipcRenderer.invoke("myharness:set-badge-count", count),
});

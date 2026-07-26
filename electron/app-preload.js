"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("myharnessDesktop", {
  getStatus: () => ipcRenderer.invoke("myharness:desktop-status"),
  quit: () => ipcRenderer.invoke("myharness:quit"),
});

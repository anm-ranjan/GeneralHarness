"use strict";

const { app, BrowserWindow, Menu, Tray, dialog, ipcMain, nativeImage, shell } = require("electron");
const childProcess = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const util = require("util");
const { buildExecutablePath } = require("./executable-path.cjs");

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8420";

const repoRoot = app.isPackaged ? process.resourcesPath : path.resolve(__dirname, "..");
const backendScript = path.join(repoRoot, "backend", "web_app.py");
const appPreload = path.join(__dirname, "app-preload.js");
const configPath = path.join(repoRoot, "backend", "agent", "agent_config.yaml");
const electronLogPath = process.env.MYHARNESS_ELECTRON_LOG
  || path.join(repoRoot, "logs", "deploy_logs", "electron.log");

let mainWindow = null;
let appTray = null;
let backendProcess = null;
let backendStartupError = null;
let backendMode = "unknown";
let activeBackendUrl = DEFAULT_BACKEND_URL;
let isQuitting = false;
let displayName = process.env.MYHARNESS_PRODUCT_NAME || "MyHarness";
const TRAY_ICON_DATA_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAKklEQVR4AWP4//8/AyUYTFhYGJqYmP4TjBqMGoQajBqMGoQajBoAAHkCErL0fR93AAAAAElFTkSuQmCC";

function logLine(level, args) {
  try {
    fs.mkdirSync(path.dirname(electronLogPath), { recursive: true });
    const rendered = args.map((arg) => {
      if (arg instanceof Error) return arg.stack || arg.message;
      if (typeof arg === "string") return arg;
      return util.inspect(arg, { depth: 4, breakLength: 160 });
    }).join(" ");
    fs.appendFileSync(
      electronLogPath,
      `${new Date().toISOString()} [${level}] ${rendered}\n`,
      "utf-8",
    );
  } catch {
    // Logging must never become a startup dependency.
  }
}

function safeProcessWrite(stream, text) {
  try {
    stream.write(text);
  } catch (error) {
    logLine("ERROR", ["Process stream write failed", error]);
  }
}

for (const level of ["log", "warn", "error"]) {
  const original = console[level].bind(console);
  console[level] = (...args) => {
    logLine(level.toUpperCase(), args);
    original(...args);
  };
}

process.on("uncaughtException", (error) => {
  console.error("Uncaught exception", error);
});

process.on("unhandledRejection", (reason) => {
  console.error("Unhandled rejection", reason);
});

function parseScalar(value) {
  const raw = String(value || "").trim();
  if (raw === "true") return true;
  if (raw === "false") return false;
  if (raw === "[]") return [];
  if ((raw.startsWith("\"") && raw.endsWith("\"")) || (raw.startsWith("'") && raw.endsWith("'"))) {
    return raw.slice(1, -1);
  }
  return raw;
}

function readDesktopConfig() {
  const defaults = {
    enabled: true,
    backend_url: DEFAULT_BACKEND_URL,
    prefer_existing_backend: true,
    start_local_backend_fallback: true,
    electron_only: false,
    disable_gpu: true,
  };
  if (!fs.existsSync(configPath)) return defaults;
  const lines = fs.readFileSync(configPath, "utf-8").split(/\r?\n/);
  const result = { ...defaults };
  let inDesktop = false;
  for (const line of lines) {
    if (/^\S/.test(line)) inDesktop = /^desktop:\s*$/.test(line);
    if (!inDesktop || !/^\s+[^#:\s]+:\s*/.test(line)) continue;
    const match = line.match(/^\s+([^#:\s]+):\s*(.*?)\s*(?:#.*)?$/);
    if (match) result[match[1]] = parseScalar(match[2]);
  }
  if (process.env.MYHARNESS_BACKEND_URL) result.backend_url = process.env.MYHARNESS_BACKEND_URL;
  return result;
}

// Locked-down Windows clients can crash on GPU process launch, so hardware
// acceleration is disabled by default. Set desktop.disable_gpu: false in
// agent_config.yaml to re-enable it and test rendering performance. Must run
// before app "ready", which all module-level code does.
if (process.platform === "win32" && readDesktopConfig().disable_gpu !== false) {
  app.disableHardwareAcceleration();
  app.commandLine.appendSwitch("disable-gpu");
  app.commandLine.appendSwitch("disable-gpu-sandbox");
  app.commandLine.appendSwitch("disable-direct-composition");
  app.commandLine.appendSwitch("disable-features", "RendererCodeIntegrity");
}

function configuredBackendOrigin() {
  try {
    return new URL(String(readDesktopConfig().backend_url || DEFAULT_BACKEND_URL)).origin;
  } catch {
    return new URL(DEFAULT_BACKEND_URL).origin;
  }
}

// Desktop deployments often point Electron at a LAN http:// backend. Chromium
// exposes microphone APIs only to secure contexts, so explicitly trust only the
// configured backend origin inside the packaged shell.
const desktopBackendOrigin = configuredBackendOrigin();
if (desktopBackendOrigin.startsWith("http://")) {
  app.commandLine.appendSwitch("unsafely-treat-insecure-origin-as-secure", desktopBackendOrigin);
}

function backendHealthUrl(baseUrl) {
  return new URL("/api/health", baseUrl).toString();
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body;
}

async function checkBackend(baseUrl) {
  const health = await fetchJson(backendHealthUrl(baseUrl));
  if (health.status !== "ok") throw new Error("Backend health check did not return ok.");
  return health;
}

function pythonCandidates() {
  const configured = process.env.MYHARNESS_PYTHON;
  const repoVenv = process.platform === "win32"
    ? path.join(repoRoot, ".venv", "Scripts", "python.exe")
    : path.join(repoRoot, ".venv", "bin", "python");
  const systemCandidates = process.platform === "win32" ? ["python", "py"] : ["python3", "python"];
  return [configured, repoVenv, ...systemCandidates].filter(Boolean);
}

let cachedExecutablePath = null;

function executablePath() {
  if (cachedExecutablePath === null) {
    cachedExecutablePath = buildExecutablePath({
      platform: process.platform,
      env: process.env,
      homedir: os.homedir(),
      spawnSync: childProcess.spawnSync,
      existsSync: fs.existsSync,
    });
  }
  return cachedExecutablePath;
}

function commandExists(command) {
  if (path.isAbsolute(command)) return fs.existsSync(command);
  const checker = process.platform === "win32" ? "where" : "which";
  const result = childProcess.spawnSync(checker, [command], {
    env: { ...process.env, PATH: executablePath() },
    stdio: "ignore",
    windowsHide: true,
  });
  return result.status === 0;
}

function selectPython() {
  for (const candidate of pythonCandidates()) {
    if (commandExists(candidate)) return candidate;
  }
  throw new Error("No Python interpreter was found. Set MYHARNESS_PYTHON or install Python 3.10+.");
}

function spawnLocalBackend(baseUrl) {
  const url = new URL(baseUrl);
  const env = {
    ...process.env,
    PATH: executablePath(),
    MYHARNESS_WEB_HOST: url.hostname || "127.0.0.1",
    MYHARNESS_WEB_PORT: url.port || "8420",
    MYHARNESS_WEB_DATA_DIR: process.env.MYHARNESS_WEB_DATA_DIR || path.join(repoRoot, "data"),
    MYHARNESS_WEB_STATIC_DIR: process.env.MYHARNESS_WEB_STATIC_DIR || path.join(repoRoot, "frontend", "dist"),
  };
  const python = selectPython();
  console.log(`Provider executable PATH: ${env.PATH}`);
  console.log(`Starting backend sidecar with ${python} on ${url.hostname}:${url.port || "8420"}`);
  const backendCwd = path.join(repoRoot, "backend");
  backendStartupError = null;
  try {
    backendProcess = childProcess.spawn(python, ["-u", backendScript], {
      cwd: backendCwd,
      env,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
  } catch (error) {
    backendStartupError = error;
    console.error(`Backend sidecar spawn threw before process creation. python=${python} cwd=${backendCwd} script=${backendScript}`, error);
    throw error;
  }
  backendProcess.on("error", (error) => {
    backendStartupError = error;
    console.error(`Backend sidecar spawn failed. python=${python} cwd=${backendCwd} script=${backendScript}`, error);
  });
  backendProcess.stdout.on("data", (chunk) => safeProcessWrite(process.stdout, `[backend] ${chunk}`));
  backendProcess.stderr.on("data", (chunk) => safeProcessWrite(process.stderr, `[backend] ${chunk}`));
  backendProcess.on("exit", (code, signal) => {
    const detail = `Backend exited: code=${code} signal=${signal}`;
    if (code || signal) {
      backendStartupError = backendStartupError || new Error(detail);
      console.warn(detail);
    } else {
      console.log(detail);
    }
  });
}

async function waitForBackend(baseUrl, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    if (backendStartupError) throw backendStartupError;
    try {
      return await checkBackend(baseUrl);
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 350));
    }
  }
  throw lastError || new Error("Timed out waiting for backend health.");
}

async function resolveBackend() {
  const config = readDesktopConfig();
  activeBackendUrl = String(config.backend_url || DEFAULT_BACKEND_URL);
  console.log(`Resolving backend at ${activeBackendUrl}`);
  if (config.prefer_existing_backend !== false) {
    try {
      const health = await checkBackend(activeBackendUrl);
      backendMode = "configured";
      console.log(`Using configured backend at ${activeBackendUrl}`);
      return health;
    } catch {
      // Fall through to local sidecar when configured to do so.
    }
  }
  if (config.start_local_backend_fallback === false) {
    throw new Error(`Configured backend is unavailable: ${activeBackendUrl}`);
  }
  spawnLocalBackend(activeBackendUrl);
  const health = await waitForBackend(activeBackendUrl);
  backendMode = "local";
  console.log(`Using local backend sidecar at ${activeBackendUrl}`);
  return health;
}

function applyBranding(health) {
  displayName = String(health?.app_name || displayName).trim() || "MyHarness";
  app.setName(displayName);
}

function isAllowedMainUrl(targetUrl) {
  try {
    return new URL(targetUrl).origin === new URL(activeBackendUrl).origin;
  } catch {
    return false;
  }
}

function guardNavigation(windowRef, allowed, options = {}) {
  windowRef.webContents.on("will-navigate", (event, targetUrl) => {
    console.log(`${options.name || "Window"} navigating to ${targetUrl}`);
    if (!allowed(targetUrl)) {
      event.preventDefault();
      console.warn(`${options.name || "Window"} blocked navigation to ${targetUrl}; opening externally.`);
      shell.openExternal(targetUrl).catch(() => {});
    }
  });
  windowRef.webContents.setWindowOpenHandler(({ url }) => {
    console.log(`${options.name || "Window"} requested popup ${url}`);
    if (allowed(url)) {
      return {
        action: "allow",
        overrideBrowserWindowOptions: {
          title: options.popupTitle || "App",
          webPreferences: {
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: false,
          },
        },
      };
    }
    console.warn(`${options.name || "Window"} blocked popup ${url}; opening externally.`);
    shell.openExternal(url).catch(() => {});
    return { action: "deny" };
  });
  windowRef.webContents.on("render-process-gone", (_event, details) => {
    console.warn(`${options.name || "Window"} renderer exited: reason=${details.reason} exitCode=${details.exitCode}`);
  });
  windowRef.webContents.on("child-process-gone", (_event, details) => {
    console.warn(`${options.name || "Window"} child process exited: type=${details.type} reason=${details.reason} exitCode=${details.exitCode}`);
  });
}

function addDesktopHeader(session, baseUrl) {
  const origin = new URL(baseUrl).origin;
  session.webRequest.onBeforeSendHeaders({ urls: [`${origin}/*`] }, (details, callback) => {
    callback({
      requestHeaders: {
        ...details.requestHeaders,
        "X-MyHarness-Desktop": "1",
      },
    });
  });
}

// Locked-down Windows clients can serve built Vite bundles as text/plain (a
// registry MIME mapping that overrides the backend). Chromium then refuses to
// execute the module script and the app renders black. Rewrite the response
// Content-Type in the renderer's own network stack so the fix holds regardless
// of the backend deploy state, HTTP caching/304 revalidation, or the host OS.
function forceAssetMimeTypes(session, baseUrl) {
  const origin = new URL(baseUrl).origin;
  const forced = [
    [/\.mjs(\?|$)/i, "application/javascript"],
    [/\.js(\?|$)/i, "application/javascript"],
    [/\.css(\?|$)/i, "text/css"],
  ];
  session.webRequest.onHeadersReceived({ urls: [`${origin}/*`] }, (details, callback) => {
    const match = forced.find(([pattern]) => pattern.test(details.url));
    if (!match) {
      callback({ responseHeaders: details.responseHeaders });
      return;
    }
    const headers = {};
    for (const [key, value] of Object.entries(details.responseHeaders || {})) {
      if (key.toLowerCase() !== "content-type") {
        headers[key] = value;
      }
    }
    headers["Content-Type"] = [match[1]];
    callback({ responseHeaders: headers });
  });
}

function allowMyHarnessMediaPermission(session, baseUrl, mainContents) {
  const allowedOrigin = new URL(baseUrl).origin;
  session.setPermissionRequestHandler((webContents, permission, callback, details) => {
    if (permission !== "media") {
      callback(false);
      return;
    }
    const requestingUrl = details.requestingUrl || webContents.getURL();
    let requestingOrigin = "";
    try {
      requestingOrigin = new URL(requestingUrl).origin;
    } catch {
      requestingOrigin = "";
    }
    callback(webContents === mainContents && requestingOrigin === allowedOrigin);
  });
}

function createMainWindow() {
  console.log("Creating Electron window");
  console.log(`Preload path: ${appPreload}; exists=${fs.existsSync(appPreload)}`);
  try {
    mainWindow = new BrowserWindow({
      width: 1420,
      height: 920,
      minWidth: 1100,
      minHeight: 720,
      title: displayName,
      webPreferences: {
        preload: appPreload,
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: false,
        backgroundThrottling: false,
      },
    });
  } catch (error) {
    console.error("Creating BrowserWindow failed", error);
    throw error;
  }
  console.log("BrowserWindow created");
  addDesktopHeader(mainWindow.webContents.session, activeBackendUrl);
  forceAssetMimeTypes(mainWindow.webContents.session, activeBackendUrl);
  allowMyHarnessMediaPermission(mainWindow.webContents.session, activeBackendUrl, mainWindow.webContents);
  console.log("Desktop request header and asset MIME hooks installed");
  guardNavigation(mainWindow, isAllowedMainUrl, { name: "App", popupTitle: "App" });
  mainWindow.webContents.on("console-message", (_event, level, message, line, sourceId) => {
    console.log(`Renderer console level=${level} ${sourceId}:${line} ${message}`);
  });
  mainWindow.webContents.on("preload-error", (_event, preloadPath, error) => {
    console.error(`Preload failed: ${preloadPath}`, error);
  });
  mainWindow.on("closed", () => {
    console.log("Electron window closed");
    mainWindow = null;
    if (process.platform !== "darwin") {
      app.quit();
    }
  });
  mainWindow.webContents.on("did-fail-load", (_event, errorCode, errorDescription, validatedUrl) => {
    console.error(`Window failed to load ${validatedUrl}: ${errorCode} ${errorDescription}`);
  });
  mainWindow.webContents.on("did-finish-load", () => {
    console.log(`Window loaded ${mainWindow?.webContents.getURL() || "closed"}`);
  });
  console.log(`Loading URL ${activeBackendUrl}`);
  // Asset caching is kept for fast startup; the forceAssetMimeTypes header
  // rewrite corrects the Content-Type on every response, so a stale text/plain
  // MIME can no longer cause a black renderer.
  mainWindow.loadURL(activeBackendUrl, { extraHeaders: "X-MyHarness-Desktop: 1\n" })
    .catch((error) => {
      console.error(`loadURL failed for ${activeBackendUrl}`, error);
    });
}

function showMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) {
    createMainWindow();
  }
  mainWindow.show();
  mainWindow.focus();
}

async function requestBackendShutdown() {
  // Only stop a backend sidecar owned by this Electron process. A configured
  // backend may be shared by multiple clients over LAN and must survive client
  // quit.
  if (backendMode !== "local") {
    console.log(`Skipping backend shutdown for ${backendMode} backend at ${activeBackendUrl}`);
    return;
  }
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 4_000);
    await fetch(new URL("/api/shutdown", activeBackendUrl).toString(), {
      method: "POST",
      signal: controller.signal,
    });
    clearTimeout(timer);
  } catch {
    // Quitting still tears down a spawned sidecar via the before-quit handler.
  }
}

function quitApp() {
  requestBackendShutdown().finally(() => app.quit());
}

function buildTray() {
  if (appTray) return;
  const image = nativeImage.createFromDataURL(TRAY_ICON_DATA_URL);
  appTray = new Tray(image);
  appTray.setToolTip(displayName);
  appTray.setContextMenu(Menu.buildFromTemplate([
    { label: `Show ${displayName}`, click: () => showMainWindow() },
    { type: "separator" },
    { label: "Quit", click: () => quitApp() },
  ]));
  appTray.on("click", () => showMainWindow());
}

function buildAppMenu() {
  const isMac = process.platform === "darwin";
  const template = [
    ...(isMac
      ? [{
          label: app.name,
          submenu: [
            { role: "about" },
            { type: "separator" },
            { role: "hide" },
            { role: "hideOthers" },
            { role: "unhide" },
            { type: "separator" },
            { label: "Quit", accelerator: "Cmd+Q", click: () => quitApp() },
          ],
        }]
      : []),
    {
      label: "File",
      submenu: [
        isMac
          ? { role: "close" }
          : { label: "Quit", accelerator: "Ctrl+Q", click: () => quitApp() },
      ],
    },
    { role: "editMenu" },
    {
      label: "View",
      submenu: [
        { label: "Show App", accelerator: "CmdOrCtrl+Shift+A", click: () => showMainWindow() },
        { type: "separator" },
        { role: "reload" },
        { role: "forceReload" },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    { role: "windowMenu" },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function wireIpc() {
  ipcMain.handle("myharness:desktop-status", () => ({
    backendUrl: activeBackendUrl,
    backendMode,
  }));
  ipcMain.handle("myharness:quit", () => {
    quitApp();
    return { ok: true };
  });
}

function wireAppSafetyHandlers() {
  app.on("child-process-gone", (_event, details) => {
    console.warn(`Electron child process exited: type=${details.type} reason=${details.reason} exitCode=${details.exitCode}`);
  });
}

app.whenReady()
  .then(async () => {
    wireAppSafetyHandlers();
    wireIpc();
    const health = await resolveBackend();
    applyBranding(health);
    buildAppMenu();
    buildTray();
    createMainWindow();
  })
  .catch((error) => {
    const message = error instanceof Error ? error.message : String(error);
    console.error(`Electron startup failed: ${message}`);
    dialog.showErrorBox(`${displayName} startup failed`, message);
    app.quit();
  });

app.on("activate", () => {
  showMainWindow();
});

let quitInProgress = false;

async function waitForBackendExit(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (backendProcess && backendProcess.exitCode === null && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

app.on("before-quit", (event) => {
  isQuitting = true;
  if (quitInProgress || !backendProcess) return;
  quitInProgress = true;
  event.preventDefault();
  // Ask the backend to shut down gracefully first (this also kills any
  // background jobs an agent left running - see requestBackendShutdown /
  // /api/shutdown) and only fall back to a hard kill if it doesn't exit
  // in time. A bare kill() would skip that cleanup on the server side.
  requestBackendShutdown()
    .catch(() => {})
    // Give the backend enough room to clear its own budget for stopping
    // background jobs (kill_all_background_jobs' overall_timeout, 12s) plus
    // margin for the request round trip and uvicorn's own graceful shutdown.
    .then(() => waitForBackendExit(15_000))
    .finally(() => {
      if (backendProcess && backendProcess.exitCode === null) backendProcess.kill();
      app.quit();
    });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
